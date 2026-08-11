"""
scripts/sync_all_salesforce_outcomes.py — run outcome sync (see
api/routes_connections.py's _sync_connection_outcomes) for every real,
working Salesforce AND ServiceNow connection across every workspace.

(Filename kept as-is despite now covering both platforms, in case an
external scheduler -- e.g. Heroku Scheduler -- is already configured to
run this exact script path; renaming it would silently break that without
any error, which is a worse failure mode than an inaccurate filename.)

This exists so outcome data can be kept fresh automatically instead of
requiring someone to remember to call POST /connections/{id}/sync-outcomes
by hand. Intentionally NOT a new in-app scheduler (no APScheduler/Celery
added) -- per the original outcome-enrichment design, the smallest viable
approach is an external trigger hitting existing code, e.g. Heroku
Scheduler running this script on an interval.

A workspace can accumulate multiple IntegrationConnection rows for the
same platform over time (re-auth attempts, package-install retries, a
connection that errored and was replaced). Only one -- the most recently
*working* one -- should actually be synced per workspace+org; the others
are either duplicates or dead ends. "Working" is judged the same way the
Business Profile UI's Connected Systems card judges it: a real
last_success_at, and not "error"/"superseded".

Uses _sync_connection_outcomes() (api/routes_connections.py) -- the same
platform-dispatch function the manual /sync-outcomes endpoint calls -- so
this script and that endpoint can never disagree about what "syncing a
connection" means for a given platform.

Usage:
    cd backend
    python scripts/sync_all_salesforce_outcomes.py [--dry-run]
"""
import argparse
import asyncio
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


SYNCABLE_PLATFORMS = ("salesforce", "servicenow")


def _select_connections_to_sync(db):
    from database.models import IntegrationConnection

    candidates = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.platform.in_(SYNCABLE_PLATFORMS),
            IntegrationConnection.access_token_encrypted.isnot(None),
            IntegrationConnection.instance_url.isnot(None),
            IntegrationConnection.status.notin_(["error", "superseded"]),
        )
        .all()
    )
    # Dedup key is (workspace, platform, org) not just workspace -- a single
    # CostPilot workspace can have more than one distinct org connected on
    # the same platform (found in practice: one workspace with both a
    # "cpcom-dev-ed" and an "aicom177-dev-ed" Salesforce connection, each
    # with real, different accounts and Opportunities). Deduping to one
    # connection per workspace silently dropped every org except whichever
    # synced most recently -- accounts that only exist in the other org
    # looked like they had no outcome data at all, when the truth was
    # simply "never queried." Platform is part of the key too, now that
    # this covers more than one platform, so a Salesforce and a ServiceNow
    # connection in the same workspace are never treated as the same org.
    by_workspace_platform_org = {}
    for item in candidates:
        org_key = (item.workspace_id, item.platform, (item.instance_url or "").rstrip("/").lower())
        existing = by_workspace_platform_org.get(org_key)
        if existing is None:
            by_workspace_platform_org[org_key] = item
            continue
        # Within the same org, prefer whichever connection row has actually
        # succeeded more recently (multiple rows accumulate per org from
        # re-auth attempts) -- a connection that has never succeeded loses
        # to one that has.
        item_success = item.last_success_at
        existing_success = existing.last_success_at
        if item_success and (not existing_success or item_success > existing_success):
            by_workspace_platform_org[org_key] = item
    return list(by_workspace_platform_org.values())


async def main(dry_run: bool):
    from database.db import SessionLocal
    from api.routes_connections import _sync_connection_outcomes

    db = SessionLocal()
    try:
        connections = _select_connections_to_sync(db)
        if not connections:
            logger.info("No working Salesforce/ServiceNow connections found to sync.")
            return
        logger.info("Found %d connection(s) to sync.", len(connections))
        for item in connections:
            if dry_run:
                logger.info(
                    "[dry-run] Would sync workspace=%s platform=%s connection_id=%s instance_url=%s",
                    item.workspace_id, item.platform, item.id, item.instance_url,
                )
                continue
            try:
                result = await _sync_connection_outcomes(db, item)
                if result["errors"]:
                    logger.warning(
                        "workspace=%s platform=%s connection_id=%s sync had errors: %s",
                        item.workspace_id, item.platform, item.id, result["errors"],
                    )
                logger.info(
                    "workspace=%s platform=%s connection_id=%s checked=%s updated=%s unchanged=%s errors=%d",
                    item.workspace_id, item.platform, item.id, result["checked"], result["updated"],
                    result["unchanged"], len(result["errors"]),
                )
            except Exception:
                logger.exception(
                    "workspace=%s platform=%s connection_id=%s sync raised an exception, continuing to next connection",
                    item.workspace_id, item.platform, item.id,
                )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
