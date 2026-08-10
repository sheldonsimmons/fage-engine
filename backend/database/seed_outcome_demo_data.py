"""
database/seed_outcome_demo_data.py — realistic, multi-record test data for
the outcome-enrichment feature (AI Event -> Work Item -> Outcome), spread
across the past two years.

Writes into the SIM-HISTORICAL-2Y workspace only -- entirely separate from
any real Salesforce-synced data in the Production workspace, using the app's
existing "Simulated" workspace convention (same trust boundary the app
already shows via the "Viewing: Simulated" banner). Uses the exact same
tables and columns real synced data uses (WorkAccount, WorkItem,
TokenTransaction, WorkItemOutcome, WorkItemOutcomeEvent, WorkUser,
WorkItemUser) -- every real feature (Business Profile, Ask CostPilot, the
outcome-aware ranking questions) works on this data identically to how it
works on real data. Only the data's origin differs, not its shape.

Each Opportunity/Case is assigned a named owner from a shared rep pool (the
way a real territory-based sales/support org works -- a handful of people
covering many accounts, not one person per account), and that owner's
identity is attached to every TokenTransaction generated for that record
(work_user_id/actor_name/actor_email) plus a handful of lightweight
Task/Note-style activity entries -- so activity looks like it's tied to
real people working real records, not anonymous system noise. Every
TokenTransaction is also assigned a RegisteredAgent from a small shared
pool (department-matched), so "which agent had the highest spend" has a
real name to return instead of "Unknown agent". No Project-type work items
are generated (Opportunities and Cases only).

Idempotent-ish: uses a fixed external_id prefix and skips accounts/people
that already exist, so re-running adds nothing new once seeded (use --reset
to wipe and reseed).

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
    WorkUser, WorkItemUser, RegisteredAgent,
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
    "Zephyr Renewable Power", "Alderleaf Pharmaceuticals", "Brightwater Marine",
    "Copperfield Realty", "Duskwood Furniture", "Eastgate Import Export",
    "Foxglove Publishing", "Greystone Architecture", "Hawthorn Distillers",
    "Ivywood Education Group", "Jasperfield Robotics", "Kingsley Waste Management",
    "Lonestar Petroleum", "Mossbrook Dairy Co", "Nightingale Home Health",
    "Oakhaven Furniture Rentals", "Prairiewind Grain", "Quicksilver Courier",
    "Redwood Timber Co", "Stonebridge Capital", "Thistledown Textiles",
    "Umberglen Security", "Violetgrove Cosmetics", "Wrenfield Software",
    "Xander Freight Systems", "Youngblood Sporting Goods", "Ashworth Dental Group",
    "Blackfriar Brewing", "Cobalt Ridge Mining", "Dovetail Furniture Works",
    "Emberline Foods", "Fallowfield Farms", "Goldleaf Jewelers",
    "Hemlock Forestry", "Innsbridge Hospitality Group", "Jacaranda Wellness Spas",
]

OPPORTUNITY_NAMES = ["Expansion", "Renewal", "New Business", "Upsell", "Platform Migration"]
CASE_SUBJECTS = [
    "Login access issue", "Billing discrepancy", "Integration failure",
    "Performance degradation", "Feature request escalation", "Data export request",
]

# A shared rep pool -- a real business has a handful of people covering
# many accounts, not a unique employee per client. Owners are internal to
# the (fictional) company running this CostPilot workspace, so the email
# domain is constant across the pool rather than derived from each client.
REP_EMAIL_DOMAIN = "brightleafsolutions.com"
SALES_REPS = [
    "Jordan Michaels", "Priya Anand", "Marcus Webb",
    "Elena Torres", "Sam O'Connell", "Nina Kowalski",
]
SUPPORT_REPS = [
    "Dana Whitfield", "Malik Johnson", "Chloe Bergstrom",
    "Tariq Osei", "Rachel Kim", "Owen Fitzgerald",
]
SUCCESS_REPS = [
    "Ines Duarte", "Gavin Patel", "Fatima Rahman",
    "Liam Sutherland", "Yuki Tanaka", "Beatriz Costa",
]

# A shared AI agent pool, one department each -- same "handful of workers
# covering many accounts" shape as the rep pool above, so "which agent had
# the highest spend" has a real name to return instead of "Unknown agent".
# RegisteredAgent.name is globally unique (no workspace_id column on that
# table), so these must not collide with seed_historical_demo.py's AGENTS
# list -- kept deliberately distinct.
AGENTS_BY_DEPT = {
    "Sales": ["Opportunity Insights Agent", "Pipeline Coach Agent", "Proposal Drafting Agent"],
    "Support": ["Case Triage Agent", "Support Deflection Agent", "Escalation Assistant"],
    "Success": ["Renewal Outreach Agent", "Customer Health Agent", "Onboarding Assistant"],
}

TASK_NOTES = [
    "Logged follow-up call", "Sent pricing proposal", "Internal note: budget confirmed",
    "Scheduled demo with stakeholders", "Followed up on outstanding questions",
    "Left voicemail, no response yet", "Sent renewal reminder", "Escalated to manager",
    "Confirmed contact details", "Reviewed contract terms with legal",
    "Sent troubleshooting steps", "Scheduled screen-share session",
]

MODELS = [
    ("Scout", "claude-3-5-haiku", 0.80, 4.00),
    ("Analyst", "gpt-4.1-mini", 0.75, 4.50),
    ("Advisor", "gpt-4.1", 2.50, 15.00),
]

DEPARTMENTS = ["Sales", "Support", "Success"]


def _entity_id(*parts):
    return f"{PREFIX}-" + "-".join(str(p).replace(" ", "").upper() for p in parts)


def _rep_email(name):
    local = name.lower().replace("'", "").replace(" ", ".")
    return f"{local}@{REP_EMAIL_DOMAIN}"


def _weighted_past_datetime(rng, days_ago_min, days_ago_max):
    """Bias toward recent dates (denser activity in recent months, the way
    a real, growing business's activity looks) instead of uniform-random
    across the whole window -- same realism goal as
    seed_historical_demo.py's month-over-month volume ramp, expressed here
    as a skewed single draw rather than a per-month loop."""
    span = days_ago_max - days_ago_min
    skewed = rng.random() ** 2.2  # exponent > 1 skews toward 0 (recent)
    days_ago = days_ago_min + skewed * span
    return datetime.utcnow() - timedelta(days=days_ago)


def _seed_reps(db, dry_run_existing):
    """Create (or reuse) the shared WorkUser rep pool, keyed by
    (workspace_id, source_platform, external_id) per WorkUser's unique
    constraint. Returns {department: [WorkUser, ...]}."""
    pools = {"Sales": SALES_REPS, "Support": SUPPORT_REPS, "Success": SUCCESS_REPS}
    reps_by_dept = {}
    for dept, names in pools.items():
        reps = []
        for name in names:
            external_id = _entity_id("REP", dept, name)
            existing = dry_run_existing.get(external_id)
            if existing:
                reps.append(existing)
                continue
            user = WorkUser(
                workspace_id=WORKSPACE_ID, source_platform="Salesforce",
                external_id=external_id, name=name, email=_rep_email(name),
            )
            db.add(user)
            db.flush()
            dry_run_existing[external_id] = user
            reps.append(user)
        reps_by_dept[dept] = reps
    return reps_by_dept


def _seed_agents(db):
    """Create (or reuse) the shared RegisteredAgent pool, keyed by
    (name, department). RegisteredAgent.name is globally unique across the
    whole app -- and this app already has a large pre-existing catalog of
    agent names from unrelated demo/test scenarios, so a name from
    AGENTS_BY_DEPT can collide with one we don't own. Reuse is matched by
    department + name-prefix (covers a disambiguated name from a prior
    run); a genuine collision with someone else's agent falls back to an
    auto-numbered variant instead of crashing the whole seed run. Returns
    {department: [RegisteredAgent, ...]}."""
    agents_by_dept = {}
    for dept, names in AGENTS_BY_DEPT.items():
        agents = []
        for name in names:
            department = f"{WORKSPACE_ID}:{dept}"
            agent = (
                db.query(RegisteredAgent)
                .filter(RegisteredAgent.department == department, RegisteredAgent.name.like(f"{name}%"))
                .first()
            )
            if not agent:
                candidate_name = name
                suffix = 2
                while db.query(RegisteredAgent).filter_by(name=candidate_name).first():
                    candidate_name = f"{name} ({suffix})"
                    suffix += 1
                agent = RegisteredAgent(
                    name=candidate_name, department=department, source_platform="Salesforce",
                    permissions="read,write", target_table="outcome_demo",
                    status="idle", collision_policy="lock",
                    min_tier=1, max_tier=4, pruning_enabled=True, archived=False,
                )
                db.add(agent)
                db.flush()
            agents.append(agent)
        agents_by_dept[dept] = agents
    return agents_by_dept


def _assign_owner(db, work_item, owner, role):
    db.add(WorkItemUser(work_item_id=work_item.id, work_user_id=owner.id, role=role))


def _make_transactions(rng, db, work_item, department, owner, agent, n, days_ago_min, days_ago_max):
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
            work_user_id=owner.id if owner else None,
            agent_id=agent.id if agent else None,
            origin_record_id=work_item.source_record_id,
            origin_record_type=origin_record_type,
            origin_record_name=work_item.name,
            actor_name=owner.name if owner else None,
            actor_email=owner.email if owner else None,
            actor_source_platform="Salesforce" if owner else None,
            model_tier=tier,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_saved=tokens_saved,
            cost_usd=cost_usd,
            was_pruned=tokens_saved > 0,
            business_purpose=business_purpose,
            is_simulation=True,
            timestamp=_weighted_past_datetime(rng, days_ago_min, days_ago_max),
        ))


def _make_activity_notes(rng, db, work_item, department, owner, agent, days_ago_min, days_ago_max):
    """Lightweight Task/Note-style activity per record -- small, cheap AI
    calls (drafting a note, summarizing a call) rather than full work
    sessions, so a record looks actively worked by its owner, not just a
    shell with a handful of big transactions."""
    for _ in range(rng.randint(2, 6)):
        note_text = rng.choice(TASK_NOTES)
        record_kind = rng.choice(["Task", "Note"])
        input_tokens = rng.randint(80, 400)
        output_tokens = rng.randint(30, 150)
        cost_usd = round((input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000, 6)
        business_purpose = classify_business_purpose_fields(
            work_item.source_record_type, work_item.name, work_item.context_type,
            work_item.source_record_type, None,
        )
        db.add(TokenTransaction(
            department=f"{WORKSPACE_ID}:{department}",
            workspace_id=WORKSPACE_ID,
            source_platform="Salesforce",
            work_item_id=work_item.id,
            work_user_id=owner.id if owner else None,
            agent_id=agent.id if agent else None,
            origin_record_id=work_item.source_record_id,
            origin_record_type=record_kind,
            origin_record_name=f"{note_text} — {work_item.name}",
            actor_name=owner.name if owner else None,
            actor_email=owner.email if owner else None,
            actor_source_platform="Salesforce" if owner else None,
            model_tier="Scout", model_name="claude-3-5-haiku",
            input_tokens=input_tokens, output_tokens=output_tokens,
            tokens_saved=0, cost_usd=cost_usd, was_pruned=False,
            business_purpose=business_purpose,
            is_simulation=True,
            timestamp=_weighted_past_datetime(rng, days_ago_min, days_ago_max),
        ))


def seed(db, dry_run=False):
    existing_accounts = {
        a.external_id for a in db.query(WorkAccount).filter(WorkAccount.workspace_id == WORKSPACE_ID).all()
    }
    existing_reps = {
        u.external_id: u for u in db.query(WorkUser).filter(WorkUser.workspace_id == WORKSPACE_ID).all()
    }
    reps_by_dept = _seed_reps(db, existing_reps)
    agents_by_dept = _seed_agents(db)

    work_item_count = 0
    note_count = 0
    outcome_counts = {"won": 0, "lost": 0, "open": 0, "case_closed": 0, "case_open": 0}

    for company in COMPANIES:
        # A per-company RNG, used ONLY for the three counts drawn below
        # (tier, opportunity count, case count) -- never for anything
        # inside the per-item loops. Each work item gets its OWN
        # independent RNG seeded from its own external_id (see item_rng
        # below) instead of continuing to draw from a shared stream.
        #
        # This two-level split matters for idempotency: a shared RNG means
        # re-running after some items already exist skips their (many) rng
        # draws entirely -- transactions, notes, outcome rolls -- which
        # desyncs the shared sequence for every later count/item, so a
        # "no-op" re-run ends up drawing different opportunity/case counts
        # and creating genuinely new external_ids instead of being
        # idempotent. Isolating both company-level counts and per-item
        # content on their own independently-seeded RNGs makes every draw
        # depend only on (company name) or (item external_id), never on
        # what happened to any other item or any previous run.
        company_rng = random.Random(f"{RANDOM_SEED}:{company}")

        # A per-company size tier so deal values look like they belong to
        # one real company (a mining/energy firm's deals cluster larger
        # than a consulting shop's) instead of every deal being an
        # independent uniform-random draw with no relationship to who it's for.
        company_tier = company_rng.choice([("small", 6_000, 60_000), ("mid", 25_000, 180_000), ("large", 80_000, 500_000)])
        tier_label, tier_min, tier_max = company_tier
        opp_count = company_rng.randint(3, 7)
        case_count = company_rng.randint(2, 5)

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
        for i in range(opp_count):
            opp_external_id = _entity_id("OPP", company, i)
            item_rng = random.Random(f"{RANDOM_SEED}:{opp_external_id}")
            opp_name = f"{company} — {item_rng.choice(OPPORTUNITY_NAMES)} {i + 1}"
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

            owner = item_rng.choice(reps_by_dept["Sales"])
            # Isolated RNG so adding agent assignment doesn't shift every
            # other draw for this item (deal value, outcome, dates) --
            # keeps a --reset && reseed reproducing identical prior numbers
            # plus the new agent field, not silently different ones.
            agent = random.Random(f"{RANDOM_SEED}:agent:{opp_external_id}").choice(agents_by_dept["Sales"])
            _assign_owner(db, opp, owner, "Owner")

            outcome_roll = item_rng.random()
            # Real deal amounts are almost never perfectly random floats --
            # round to the nearest $500, the way an actual sales team would
            # enter a negotiated number.
            deal_value = round(item_rng.uniform(tier_min, tier_max) / 500) * 500
            now = datetime.utcnow()
            if outcome_roll < 0.35:
                status, success, closed, value, outcome_counts["won"] = "Closed Won", True, True, deal_value, outcome_counts["won"] + 1
            elif outcome_roll < 0.50:
                status, success, closed, value, outcome_counts["lost"] = "Closed Lost", False, True, deal_value, outcome_counts["lost"] + 1
            else:
                status, success, closed, value = item_rng.choice(
                    ["Qualification", "Needs Analysis", "Proposal", "Negotiation"]
                ), None, False, deal_value
                outcome_counts["open"] += 1
            outcome_date = _weighted_past_datetime(item_rng, 5, 730) if closed else None

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
            _make_transactions(item_rng, db, opp, "Sales", owner, agent, item_rng.randint(6, 22), 3, 730)
            _make_activity_notes(item_rng, db, opp, "Sales", owner, agent, 3, 730)
            note_count += 1

        # 2-5 support cases per account
        for i in range(case_count):
            case_external_id = _entity_id("CASE", company, i)
            item_rng = random.Random(f"{RANDOM_SEED}:{case_external_id}")
            case_name = f"{company} — {item_rng.choice(CASE_SUBJECTS)}"
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

            owner = item_rng.choice(reps_by_dept["Support"] + reps_by_dept["Success"])
            department = "Support" if owner in reps_by_dept["Support"] else "Success"
            agent = random.Random(f"{RANDOM_SEED}:agent:{case_external_id}").choice(agents_by_dept[department])
            _assign_owner(db, case, owner, "Owner")

            now = datetime.utcnow()
            is_closed = item_rng.random() < 0.6
            status = item_rng.choice(["Closed", "Escalated"]) if is_closed else item_rng.choice(["New", "Working"])
            if status == "Closed":
                is_closed = True
            outcome_counts["case_closed" if is_closed else "case_open"] += 1
            outcome_date = _weighted_past_datetime(item_rng, 2, 730) if is_closed else None

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
            _make_transactions(item_rng, db, case, department, owner, agent, item_rng.randint(4, 14), 2, 730)
            _make_activity_notes(item_rng, db, case, department, owner, agent, 2, 730)
            note_count += 1

    print(f"Would create {work_item_count} work items across {len(COMPANIES)} accounts." if dry_run
          else f"Created {work_item_count} work items across {len(COMPANIES)} accounts.")
    print(f"Seeded rep pool: {sum(len(v) for v in reps_by_dept.values())} people across Sales/Support/Success.")
    print(f"Seeded agent pool: {sum(len(v) for v in agents_by_dept.values())} agents across Sales/Support/Success.")
    print(f"Added Task/Note activity for {note_count} work items.")
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
    rep_ids = [
        u.id for u in db.query(WorkUser)
        .filter(WorkUser.workspace_id == WORKSPACE_ID, WorkUser.external_id.like(f"{PREFIX}-%"))
        .all()
    ]
    # RegisteredAgent rows are deliberately NOT deleted on reset: they're
    # small, harmless, reusable metadata (find-or-create by name+department
    # in _seed_agents), and on Postgres (unlike the SQLite used for local
    # testing) foreign keys are actually enforced -- some other real
    # process (live routing, another sync job) can end up referencing the
    # same agent row via its own token_transactions, and deleting it out
    # from under those rows fails with an IntegrityError. Leaving agents in
    # place avoids that entirely and costs nothing.
    if not account_ids and not rep_ids:
        print("Nothing to reset.")
        return
    work_item_ids = [
        w.id for w in db.query(WorkItem).filter(WorkItem.account_id.in_(account_ids)).all()
    ]
    db.query(WorkItemOutcomeEvent).filter(WorkItemOutcomeEvent.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkItemOutcome).filter(WorkItemOutcome.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(TokenTransaction).filter(TokenTransaction.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkItemUser).filter(WorkItemUser.work_item_id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkItem).filter(WorkItem.id.in_(work_item_ids)).delete(synchronize_session=False)
    db.query(WorkAccount).filter(WorkAccount.id.in_(account_ids)).delete(synchronize_session=False)
    db.query(WorkUser).filter(WorkUser.id.in_(rep_ids)).delete(synchronize_session=False)
    db.commit()
    print(f"Removed {len(account_ids)} accounts, {len(work_item_ids)} work items, and {len(rep_ids)} reps.")


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
