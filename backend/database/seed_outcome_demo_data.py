"""
database/seed_outcome_demo_data.py — realistic, multi-record test data for
the outcome-enrichment feature (AI Event -> Work Item -> Outcome), spread
across the past several months.

Writes into the SIM-HISTORICAL-2Y workspace only -- entirely separate from
any real Salesforce-synced data in the Production workspace, using the app's
existing "Simulated" workspace convention (same trust boundary the app
already shows via the "Viewing: Simulated" banner). Uses the exact same
tables and columns real synced data uses (WorkAccount, WorkItem,
TokenTransaction, WorkItemOutcome, WorkItemOutcomeEvent) -- every real
feature (Business Profile, Ask CostPilot, the outcome-aware ranking
questions) works on this data identically to how it works on real data.
Only the data's origin differs, not its shape.

Idempotent-ish: uses a fixed external_id prefix and skips accounts that
already exist, so re-running adds nothing new once seeded (use --reset to
wipe and reseed).

Run from the backend folder:
    python database/seed_outcome_demo_data.py [--dry-run] [--reset]
"""
import argparse
import random
import sys
import os
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import engine, SessionLocal, Base
from database.models import (
    WorkAccount, WorkItem, TokenTransaction, WorkItemOutcome, WorkItemOutcomeEvent,
)
from api.routes_work_items import classify_business_purpose_fields

WORKSPACE_ID = "SIM-HISTORICAL-2Y"
PREFIX = "OUTDEMO"
RANDOM_SEED = 20260809

COMPANIES = [
    "Ashgrove Manufacturing", "Bellwether Logistics", "Cascade Health Partners",
    "Driftwood Media Group", "Elmsworth Insurance", "Fernbridge Materials",
    "Granite Peak Financial", "Harborline Shipping", "Ironvale Industrial",
    "Juniper Retail Group", "Kestrel Aerospace", "Larkspur Biotech",
    "Meridian Construction", "Northgate Utilities", "Overlook Hospitality",
    "Palisade Energy", "Quarrystone Mining", "Ridgeline Telecom",
    "Silverbrook Foods", "Timberline Apparel", "Underwood Legal Services",
    "Vantage Point Consulting", "Westfield Automotive", "Yellowstone Agriculture",
    "Zephyr Renewable Power",
]

OPPORTUNITY_NAMES = ["Expansion", "Renewal", "New Business", "Upsell", "Platform Migration"]
CASE_SUBJECTS = [
    "Login access issue", "Billing discrepancy", "Integration failure",
    "Performance degradation", "Feature request escalation", "Data export request",
]

MODELS = [
    ("Scout", "claude-3-5-haiku", 0.80, 4.00),
    ("Analyst", "gpt-4.1-mini", 0.75, 4.50),
    ("Advisor", "gpt-4.1", 2.50, 15.00),
]

DEPARTMENTS = ["Sales", "Support", "Success"]


def _entity_id(*parts):
    return f"{PREFIX}-" + "-".join(str(p).replace(" ", "").upper() for p in parts)


def _random_past_datetime(rng, days_ago_min, days_ago_max):
    days_ago = rng.uniform(days_ago_min, days_ago_max)
    return datetime.utcnow() - timedelta(days=days_ago)


def _make_transactions(rng, db, work_item, department, n, days_ago_min, days_ago_max):
    for _ in range(n):
        tier, model_name, in_cost, out_cost = rng.choice(MODELS)
        input_tokens = rng.randint(200, 2200)
        output_tokens = rng.randint(80, 900)
        tokens_saved = rng.randint(0, 300)
        cost_usd = round((input_tokens * in_cost + output_tokens * out_cost) / 1_000_000, 6)
        origin_record_type = work_item.source_record_type
        business_purpose = classify_business_purpose_fields(
            origin_record_type, work_item.name, work_item.context_type,
            work_item.source_record_type, None,
        )
        db.add(TokenTransaction(
            department=f"{WORKSPACE_ID}:{department}",
            workspace_id=WORKSPACE_ID,
            source_platform="Salesforce",
            work_item_id=work_item.id,
            origin_record_id=work_item.source_record_id,
            origin_record_type=origin_record_type,
            origin_record_name=work_item.name,
            model_tier=tier,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_saved=tokens_saved,
            cost_usd=cost_usd,
            was_pruned=tokens_saved > 0,
            business_purpose=business_purpose,
            is_simulation=True,
            timestamp=_random_past_datetime(rng, days_ago_min, days_ago_max),
        ))


def seed(db, dry_run=False):
    rng = random.Random(RANDOM_SEED)
    existing_accounts = {
        a.external_id for a in db.query(WorkAccount).filter(WorkAccount.workspace_id == WORKSPACE_ID).all()
    }

    work_item_count = 0
    outcome_counts = {"won": 0, "lost": 0, "open": 0, "case_closed": 0, "case_open": 0}

    for company in COMPANIES:
        # A per-company size tier so deal values look like they belong to
        # one real company (a mining/energy firm's deals cluster larger
        # than a consulting shop's) instead of every deal being an
        # independent uniform-random draw with no relationship to who it's for.
        company_tier = rng.choice([("small", 6_000, 60_000), ("mid", 25_000, 180_000), ("large", 80_000, 500_000)])
        tier_label, tier_min, tier_max = company_tier

        account_external_id = _entity_id("ACCOUNT", company)
        if account_external_id in existing_accounts:
            account = db.query(WorkAccount).filter_by(external_id=account_external_id).first()
        else:
            account = WorkAccount(
                external_id=account_external_id, name=company,
                department="Sales", workspace_id=WORKSPACE_ID,
            )
            db.add(account)
            db.flush()

        # 3-7 opportunities per account
        for i in range(rng.randint(3, 7)):
            opp_name = f"{company} — {rng.choice(OPPORTUNITY_NAMES)} {i + 1}"
            opp_external_id = _entity_id("OPP", company, i)
            if db.query(WorkItem).filter_by(external_id=opp_external_id).first():
                continue
            opp = WorkItem(
                external_id=opp_external_id, name=opp_name, account_id=account.id,
                context_type="opportunity", context_template="salesforce_opportunity",
                source_platform="Salesforce", source_record_type="Opportunity",
                source_record_id=_entity_id("SFID-OPP", company, i),
                workspace_id=WORKSPACE_ID, status="active",
            )
            db.add(opp)
            db.flush()
            work_item_count += 1

            outcome_roll = rng.random()
            # Real deal amounts are almost never perfectly random floats --
            # round to the nearest $500, the way an actual sales team would
            # enter a negotiated number.
            deal_value = round(rng.uniform(tier_min, tier_max) / 500) * 500
            now = datetime.utcnow()
            if outcome_roll < 0.35:
                status, success, closed, value, outcome_counts["won"] = "Closed Won", True, True, deal_value, outcome_counts["won"] + 1
            elif outcome_roll < 0.50:
                status, success, closed, value, outcome_counts["lost"] = "Closed Lost", False, True, deal_value, outcome_counts["lost"] + 1
            else:
                status, success, closed, value = rng.choice(
                    ["Qualification", "Needs Analysis", "Proposal", "Negotiation"]
                ), None, False, deal_value
                outcome_counts["open"] += 1
            outcome_date = _random_past_datetime(rng, 5, 240) if closed else None

            db.add(WorkItemOutcome(
                work_item_id=opp.id, workspace_id=WORKSPACE_ID,
                outcome_status=status, outcome_value=value, outcome_success=success,
                is_closed=closed, source_system="salesforce", source_object="Opportunity",
                external_id=opp.source_record_id, source_modified_at=outcome_date or now,
                last_synced_at=now, retrieval_method="seed",
            ))
            db.add(WorkItemOutcomeEvent(
                work_item_id=opp.id, workspace_id=WORKSPACE_ID,
                outcome_status=status, outcome_value=value, outcome_success=success,
                is_closed=closed, retrieval_method="seed", recorded_at=outcome_date or now,
            ))
            _make_transactions(rng, db, opp, "Sales", rng.randint(6, 22), 3, 260)

        # 2-5 support cases per account
        for i in range(rng.randint(2, 5)):
            case_name = f"{company} — {rng.choice(CASE_SUBJECTS)}"
            case_external_id = _entity_id("CASE", company, i)
            if db.query(WorkItem).filter_by(external_id=case_external_id).first():
                continue
            case = WorkItem(
                external_id=case_external_id, name=case_name, account_id=account.id,
                context_type="case", context_template="salesforce_case",
                source_platform="Salesforce", source_record_type="Case",
                source_record_id=_entity_id("SFID-CASE", company, i),
                workspace_id=WORKSPACE_ID, status="active",
            )
            db.add(case)
            db.flush()
            work_item_count += 1

            now = datetime.utcnow()
            is_closed = rng.random() < 0.6
            status = rng.choice(["Closed", "Escalated"]) if is_closed else rng.choice(["New", "Working"])
            if status == "Closed":
                is_closed = True
            outcome_counts["case_closed" if is_closed else "case_open"] += 1
            outcome_date = _random_past_datetime(rng, 2, 200) if is_closed else None

            db.add(WorkItemOutcome(
                work_item_id=case.id, workspace_id=WORKSPACE_ID,
                outcome_status=status, outcome_value=None, outcome_success=None,
                is_closed=is_closed, source_system="salesforce", source_object="Case",
                external_id=case.source_record_id, source_modified_at=outcome_date or now,
                last_synced_at=now, retrieval_method="seed",
            ))
            db.add(WorkItemOutcomeEvent(
                work_item_id=case.id, workspace_id=WORKSPACE_ID,
                outcome_status=status, outcome_value=None, outcome_success=None,
                is_closed=is_closed, retrieval_method="seed", recorded_at=outcome_date or now,
            ))
            _make_transactions(rng, db, case, "Support", rng.randint(4, 14), 2, 210)

    print(f"Would create {work_item_count} work items across {len(COMPANIES)} accounts." if dry_run
          else f"Created {work_item_count} work items across {len(COMPANIES)} accounts.")
    print(f"Opportunity outcomes: {outcome_counts['won']} won, {outcome_counts['lost']} lost, {outcome_counts['open']} open.")
    print(f"Case outcomes: {outcome_counts['case_closed']} closed, {outcome_counts['case_open']} open.")

    if dry_run:
        db.rollback()
    else:
        db.commit()


def reset(db):
    account_ids = [
        a.id for a in db.query(WorkAccount)
        .filter(WorkAccount.workspace_id == WORKSPACE_ID, WorkAccount.external_id.like(f"{PREFIX}-%"))
        .all()
    ]
    if not account_ids:
        print("Nothing to reset.")
        return
    work_item_ids = [
        w.id for w in db.query(WorkItem).filter(WorkItem.account_id.in_(account_ids)).all()
    ]
    db.query(WorkItemOutcomeEvent).filter(WorkItemOutcomeEvent.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkItemOutcome).filter(WorkItemOutcome.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(TokenTransaction).filter(TokenTransaction.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkItem).filter(WorkItem.id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkAccount).filter(WorkAccount.id.in_(account_ids)).delete(synchronize_session=False)
    db.commit()
    print(f"Removed {len(account_ids)} accounts and {len(work_item_ids)} work items.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.reset:
            reset(db)
        else:
            seed(db, dry_run=args.dry_run)
    finally:
        db.close()
