"""
scripts/backfill_rollup_cases.py -- one-off migration splitting Salesforce
Cases that were previously stuck sharing an account-level "rollup"
WorkItem into their own dedicated WorkItem, matching the deterministic
per-record identity Opportunities already have (and Case now gets too --
see api/routes_agentforce.py's has_deterministic_identity).

Why this is needed: before that fix, a Case reaching CostPilot via the
live Agentforce path (as opposed to bulk import) was merged onto the
account's shared rollup WorkItem instead of getting its own.
WorkItemOutcome has exactly one row per WorkItem, so a second live-path
Case for the same account silently overwrote the first Case's outcome
instead of merely being undercounted -- reproduced live on a real account
with two Cases sharing one rollup WorkItem. This script finds every
rollup WorkItem with Case-origin activity and gives each distinct Case
its own correctly-typed WorkItem, going forward relying on the code fix
(not this script) to keep it that way.

Scope, deliberately: only TokenTransaction and AuditEvent rows (both
tagged with origin_record_id/origin_record_type, so they can be split
per-Case unambiguously) are reassigned. WorkItemAgent/WorkItemUser
assignments are per-WorkItem, not per-origin-record, so there is no
principled way to split "which agent touched which Case" after the fact
if a rollup had multiple Cases -- those assignments are left on the
original (now-corrected-or-surviving) WorkItem rather than guessed at.

Usage:
    cd backend
    python scripts/backfill_rollup_cases.py [--dry-run]
"""
import argparse
import asyncio
import logging
import sys
import os
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _find_rollup_case_groups(db):
    """
    Every (rollup WorkItem, distinct Case) pair -- the unit of work this
    script corrects. A Case can reach a rollup WorkItem two different
    ways, and both need to be detected:

    - Live Agentforce activity: a TokenTransaction tagged
      origin_record_type="Case" on the rollup WorkItem.
    - Bulk import: a WorkItemSourceLink with source_record_type="Case"
      pointing at the rollup WorkItem, with no TokenTransaction at all --
      confirmed live on a real rollup WorkItem that had a Case source
      link (plus, in the same row, an unrelated OpportunityLineItem link
      and a Contact link -- this rollup pattern is not Case-specific, but
      only the Case link is this script's concern).

    WorkItemSourceLink is checked first since it is the more reliable,
    ingestion-path-independent signal; TokenTransaction only adds cases
    that have live activity but, for whatever reason, no source link yet.
    """
    from database.models import WorkItem, WorkItemSourceLink, TokenTransaction

    rollups = (
        db.query(WorkItem)
        .filter(WorkItem.context_type == "account", WorkItem.source_record_type == "Account")
        .all()
    )
    groups = []
    for rollup in rollups:
        by_case_id = {}

        for source_id, source_name in (
            db.query(WorkItemSourceLink.source_record_id, WorkItemSourceLink.source_record_name)
            .filter(
                WorkItemSourceLink.work_item_id == rollup.id,
                WorkItemSourceLink.source_record_type == "Case",
            )
            .distinct()
            .all()
        ):
            by_case_id.setdefault(source_id, source_name)

        for origin_id, origin_name in (
            db.query(TokenTransaction.origin_record_id, TokenTransaction.origin_record_name)
            .filter(
                TokenTransaction.work_item_id == rollup.id,
                TokenTransaction.origin_record_type == "Case",
                TokenTransaction.origin_record_id.isnot(None),
            )
            .distinct()
            .all()
        ):
            by_case_id.setdefault(origin_id, origin_name)

        if not by_case_id:
            continue
        groups.append((rollup, by_case_id))
    return groups


def _run(dry_run: bool):
    from database.db import SessionLocal
    from database.models import WorkItem, WorkItemOutcome, WorkItemSourceLink, TokenTransaction, AuditEvent

    db = SessionLocal()
    try:
        groups = _find_rollup_case_groups(db)
        if not groups:
            logger.info("No rollup WorkItems with Case-origin activity found. Nothing to do.")
            return

        corrected_in_place = 0
        created_new = 0
        synced_work_item_ids = []

        for rollup, cases_by_id in groups:
            case_ids = list(cases_by_id.keys())
            logger.info(
                "rollup work_item_id=%s account_id=%s name=%r has %d distinct Case(s): %s",
                rollup.id, rollup.account_id, rollup.name, len(case_ids), case_ids,
            )

            existing_outcome = db.query(WorkItemOutcome).filter_by(work_item_id=rollup.id).first()
            # If the rollup's current outcome already matches one of these
            # Cases (by external_id), that Case keeps the rollup's
            # identity in place -- avoids an unnecessary WorkItem churn
            # for the common single-Case case, and preserves whatever
            # already-correct outcome data exists for it.
            keep_case_id = None
            if existing_outcome and existing_outcome.external_id in cases_by_id:
                keep_case_id = existing_outcome.external_id
            elif len(case_ids) == 1:
                keep_case_id = case_ids[0]

            for case_id in case_ids:
                case_name = cases_by_id[case_id]
                if case_id == keep_case_id:
                    logger.info(
                        "  case_id=%s -> correcting rollup work_item_id=%s IN PLACE (external_id SF-CASE-%s)",
                        case_id, rollup.id, case_id,
                    )
                    if not dry_run:
                        rollup.context_type = "case"
                        rollup.source_record_type = "Case"
                        rollup.source_record_id = case_id
                        rollup.external_id = f"SF-CASE-{case_id}"
                        rollup.name = case_name or rollup.name
                        db.flush()
                    corrected_in_place += 1
                    synced_work_item_ids.append((rollup.id, case_id))
                    continue

                logger.info(
                    "  case_id=%s -> splitting into a NEW dedicated WorkItem (external_id SF-CASE-%s)",
                    case_id, case_id,
                )
                if dry_run:
                    created_new += 1
                    continue

                new_item = WorkItem(
                    external_id=f"SF-CASE-{case_id}",
                    name=case_name or f"Case {case_id}",
                    account_id=rollup.account_id,
                    owner=rollup.owner,
                    department=rollup.department,
                    status="active",
                    source_platform=rollup.source_platform,
                    workspace_id=rollup.workspace_id,
                    context_type="case",
                    context_template=rollup.context_template,
                    source_record_type="Case",
                    source_record_id=case_id,
                )
                db.add(new_item)
                db.flush()

                # A WorkItemSourceLink for this Case may already exist,
                # pointing at the rollup (that's how this Case was found
                # if detected via WorkItemSourceLink rather than
                # TokenTransaction) -- WorkItemSourceLink has a unique
                # constraint on (workspace_id, source_platform,
                # source_record_id), so repoint the existing row instead
                # of inserting a duplicate that would violate it.
                existing_link = (
                    db.query(WorkItemSourceLink)
                    .filter(
                        WorkItemSourceLink.workspace_id == rollup.workspace_id,
                        WorkItemSourceLink.source_platform == rollup.source_platform,
                        WorkItemSourceLink.source_record_id == case_id,
                    )
                    .first()
                )
                if existing_link:
                    existing_link.work_item_id = new_item.id
                    existing_link.source_record_type = "Case"
                    existing_link.source_record_name = case_name or existing_link.source_record_name
                else:
                    db.add(WorkItemSourceLink(
                        work_item_id=new_item.id,
                        workspace_id=rollup.workspace_id,
                        source_platform=rollup.source_platform,
                        source_record_type="Case",
                        source_record_id=case_id,
                        source_record_name=case_name,
                        is_primary=True,
                    ))

                db.query(TokenTransaction).filter(
                    TokenTransaction.work_item_id == rollup.id,
                    TokenTransaction.origin_record_id == case_id,
                    TokenTransaction.origin_record_type == "Case",
                ).update({TokenTransaction.work_item_id: new_item.id}, synchronize_session=False)
                db.query(AuditEvent).filter(
                    AuditEvent.work_item_id == rollup.id,
                    AuditEvent.origin_record_id == case_id,
                    AuditEvent.origin_record_type == "Case",
                ).update({AuditEvent.work_item_id: new_item.id}, synchronize_session=False)

                created_new += 1
                synced_work_item_ids.append((new_item.id, case_id))

            if not dry_run:
                db.commit()

        logger.info(
            "Done. rollups_inspected=%d corrected_in_place=%d created_new=%d%s",
            len(groups), corrected_in_place, created_new,
            " (dry-run, no changes made)" if dry_run else "",
        )

        if dry_run or not synced_work_item_ids:
            return

        logger.info("Syncing real outcome data for %d corrected/new WorkItem(s)...", len(synced_work_item_ids))
        asyncio.run(_sync_corrected_items(synced_work_item_ids))
    finally:
        db.close()


async def _sync_corrected_items(work_item_case_ids: list[tuple[int, str]]):
    """
    Pull each corrected/new Case WorkItem's real, authoritative outcome
    from Salesforce -- don't trust whatever outcome happened to already
    be sitting on the shared rollup row, since it may belong to a
    different Case that shared the same WorkItem.
    """
    from database.db import SessionLocal
    from database.models import WorkItem, IntegrationConnection
    from api.routes_connections import _sync_salesforce_case_outcomes

    db = SessionLocal()
    try:
        by_workspace = defaultdict(list)
        for work_item_id, case_id in work_item_case_ids:
            wi = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
            if wi:
                by_workspace[wi.workspace_id].append(case_id)

        for workspace_id, case_ids in by_workspace.items():
            connection = (
                db.query(IntegrationConnection)
                .filter(
                    IntegrationConnection.workspace_id == workspace_id,
                    IntegrationConnection.platform == "salesforce",
                    IntegrationConnection.status.notin_(["error", "superseded"]),
                    IntegrationConnection.access_token_encrypted.isnot(None),
                )
                .order_by(IntegrationConnection.last_success_at.desc().nullslast())
                .first()
            )
            if not connection:
                logger.warning("workspace=%s: no working Salesforce connection, skipping sync", workspace_id)
                continue
            result = await _sync_salesforce_case_outcomes(db, connection, only_record_ids=case_ids)
            logger.info(
                "workspace=%s checked=%s updated=%s unchanged=%s errors=%s",
                workspace_id, result["checked"], result["updated"], result["unchanged"], result["errors"],
            )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    _run(dry_run=args.dry_run)
