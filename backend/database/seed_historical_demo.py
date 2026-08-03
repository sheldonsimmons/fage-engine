"""Create deterministic, reversible two-year CostPilot history for date testing.

Run from the backend directory:
    python database/seed_historical_demo.py --workspace-id WORKSPACE_ID --dry-run
    python database/seed_historical_demo.py --workspace-id WORKSPACE_ID
    python database/seed_historical_demo.py --workspace-id WORKSPACE_ID --reset
"""

import argparse
import calendar
import json
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from database.db import Base, SessionLocal, engine
from database.models import (
    AuditEvent,
    HistoricalDemoSeedState,
    OrganizationalUnit,
    RegisteredAgent,
    TokenTransaction,
    WorkAccount,
    WorkItem,
    WorkItemAgent,
    WorkItemUser,
    WorkUser,
    WorkspaceAnalyticsSettings,
)


MARKER = "historical_demo_2y_v1"
ENTITY_PREFIX = "HIST2Y"
RANDOM_SEED = 208032026

DEPARTMENTS = {
    "Sales": {"team": "Revenue Operations", "platform": "Salesforce", "weight": 1.15},
    "Support": {"team": "Customer Operations", "platform": "ServiceNow", "weight": 1.25},
    "Operations": {"team": "Business Operations", "platform": "Microsoft Teams", "weight": 1.0},
    "Finance": {"team": "Finance Operations", "platform": "NetSuite", "weight": 0.75},
    "Legal": {"team": "Legal Operations", "platform": "Salesforce", "weight": 0.55},
    "Engineering": {"team": "Engineering Operations", "platform": "Jira", "weight": 0.9},
}

AGENTS = [
    ("Renewal Planning Agent", "Sales", date(2024, 8, 1)),
    ("Support Resolution Agent", "Support", date(2024, 8, 1)),
    ("Workflow Operations Agent", "Operations", date(2024, 8, 1)),
    ("Invoice Review Agent", "Finance", date(2025, 1, 1)),
    ("Contract Intake Agent", "Legal", date(2025, 4, 1)),
    ("Quality Review Agent", "Engineering", date(2025, 7, 1)),
    ("Account Expansion Agent", "Sales", date(2025, 10, 1)),
    ("Incident Analysis Agent", "Support", date(2026, 1, 1)),
]

PEOPLE = [
    ("Maya Chen", "Sales"), ("Jordan Brooks", "Sales"),
    ("Elena Martinez", "Support"), ("Noah Williams", "Support"),
    ("Priya Shah", "Operations"), ("Marcus Reed", "Operations"),
    ("Avery Johnson", "Finance"), ("David Kim", "Finance"),
    ("Olivia Bennett", "Legal"), ("Alex Morgan", "Legal"),
    ("Taylor Robinson", "Engineering"), ("Morgan Lee", "Engineering"),
]

WORK = [
    ("Apex Industrial", "Warranty Claim", "Support", "Case"),
    ("Northstar Health", "Compliance Review", "Legal", "Matter"),
    ("Horizon Manufacturing", "Delivery Plan", "Operations", "Project"),
    ("Meridian Retail", "Annual Renewal", "Sales", "Opportunity"),
    ("Summit Financial", "Invoice Review", "Finance", "Invoice"),
    ("Plant 7", "Equipment Maintenance", "Engineering", "Change Request"),
    ("BluePeak Consulting", "Expansion Plan", "Sales", "Opportunity"),
    ("Canyon Energy", "Service Escalation", "Support", "Case"),
    ("Titan Components", "Vendor Controls", "Finance", "Review"),
    ("Central Distribution", "Workflow Modernization", "Operations", "Project"),
    ("Harbor Medical", "Privacy Assessment", "Legal", "Matter"),
    ("Production Line 4", "Defect Investigation", "Engineering", "Quality Review"),
]

TIER_DETAILS = {
    "Scout": ("gpt-4.1-nano", 0.20, 1.25),
    "Analyst": ("gpt-4.1-mini", 0.75, 4.50),
    "Advisor": ("gpt-4.1", 2.50, 15.00),
    "Strategist": ("claude-opus-4-6", 5.00, 30.00),
}


def _month_starts(start: date, end: date):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        yield cursor
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)


def _weighted_choice(rng, choices):
    roll = rng.random()
    running = 0.0
    for value, weight in choices:
        running += weight
        if roll <= running:
            return value
    return choices[-1][0]


def _tier_weights(day):
    if day < date(2025, 1, 1):
        return [("Scout", .25), ("Analyst", .35), ("Advisor", .30), ("Strategist", .10)]
    if day < date(2026, 1, 1):
        return [("Scout", .36), ("Analyst", .36), ("Advisor", .22), ("Strategist", .06)]
    return [("Scout", .55), ("Analyst", .30), ("Advisor", .12), ("Strategist", .03)]


def _month_volume(month, index):
    base = 285 + index * 13
    seasonal = {1: 1.16, 7: 1.12, 10: 1.18, 12: .78}.get(month.month, 1.0)
    rollout = 1.12 if month >= date(2025, 7, 1) else 1.0
    return round(base * seasonal * rollout)


def _covered_days(month, start, end):
    last = calendar.monthrange(month.year, month.month)[1]
    first_day = max(start, month)
    last_day = min(end, date(month.year, month.month, last))
    return list(range(first_day.day, last_day.day + 1)) if first_day <= last_day else []


def _entity_id(workspace_id, kind, value):
    safe = "".join(ch if ch.isalnum() else "-" for ch in value).strip("-").upper()
    return f"{ENTITY_PREFIX}:{workspace_id}:{kind}:{safe}"[:240]


def _get_or_create_entities(db, workspace_id):
    org_units = {}
    for department, spec in DEPARTMENTS.items():
        external_id = _entity_id(workspace_id, "ORG", department)
        unit = db.query(OrganizationalUnit).filter_by(
            workspace_id=workspace_id, external_id=external_id
        ).first()
        if not unit:
            unit = OrganizationalUnit(
                workspace_id=workspace_id, external_id=external_id,
                name=department, unit_type="department",
                source_platform="CostPilot Historical Demo",
            )
            db.add(unit)
            db.flush()
        org_units[department] = unit

    agents = []
    for name, department, launched_at in AGENTS:
        stored_name = f"Historical Demo [{workspace_id}] — {name}"
        agent = db.query(RegisteredAgent).filter_by(name=stored_name).first()
        if not agent:
            agent = RegisteredAgent(
                name=stored_name, department=f"{workspace_id}:{department}",
                owner_org_unit_id=org_units[department].id,
                source_platform=DEPARTMENTS[department]["platform"],
                permissions="read,write", target_table="historical_demo",
                status="idle", collision_policy="lock", created_at=datetime.combine(launched_at, time()),
                min_tier=1, max_tier=4, pruning_enabled=True, archived=False,
            )
            db.add(agent)
            db.flush()
        agents.append((agent, launched_at, department))

    users = []
    for index, (name, department) in enumerate(PEOPLE, 1):
        external_id = _entity_id(workspace_id, "USER", str(index))
        platform = DEPARTMENTS[department]["platform"]
        user = db.query(WorkUser).filter_by(
            workspace_id=workspace_id, source_platform=platform, external_id=external_id
        ).first()
        if not user:
            user = WorkUser(
                workspace_id=workspace_id, source_platform=platform,
                external_id=external_id, name=name,
                email=f"historical.user{index}@example.com",
                primary_org_unit_id=org_units[department].id,
            )
            db.add(user)
            db.flush()
        users.append((user, department))

    work_items = []
    for index, (account_name, work_name, department, record_type) in enumerate(WORK, 1):
        account_external_id = _entity_id(workspace_id, "ACCOUNT", account_name)
        account = db.query(WorkAccount).filter_by(external_id=account_external_id).first()
        if not account:
            account = WorkAccount(
                external_id=account_external_id, name=account_name,
                department=f"{workspace_id}:{department}", workspace_id=workspace_id,
            )
            db.add(account)
            db.flush()
        external_id = _entity_id(workspace_id, "WORK", str(index))
        item = db.query(WorkItem).filter_by(external_id=external_id).first()
        if not item:
            item = WorkItem(
                external_id=external_id, name=f"{account_name} — {work_name}",
                account_id=account.id, department=f"{workspace_id}:{department}",
                org_unit_id=org_units[department].id, workspace_id=workspace_id,
                context_type="custom", context_template="historical_demo",
                source_platform="CostPilot Historical Demo",
                source_record_type=record_type,
                source_record_id=f"HIST-{index:03d}",
            )
            db.add(item)
            db.flush()
        work_items.append((item, department, account))
    return org_units, agents, users, work_items


def reset_historical_demo(db, workspace_id):
    tx_query = db.query(TokenTransaction).filter(
        TokenTransaction.workspace_id == workspace_id,
        TokenTransaction.usage_source == MARKER,
    )
    request_ids = [row[0] for row in tx_query.with_entities(TokenTransaction.governed_request_id)]
    result = {
        "transactions_deleted": tx_query.delete(synchronize_session=False),
        "audits_deleted": db.query(AuditEvent).filter(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.governed_request_id.in_(request_ids) if request_ids else False,
        ).delete(synchronize_session=False),
    }
    work_ids = [row[0] for row in db.query(WorkItem.id).filter(
        WorkItem.workspace_id == workspace_id,
        WorkItem.external_id.like(f"{ENTITY_PREFIX}:%"),
    )]
    user_ids = [row[0] for row in db.query(WorkUser.id).filter(
        WorkUser.workspace_id == workspace_id,
        WorkUser.external_id.like(f"{ENTITY_PREFIX}:%"),
    )]
    if work_ids:
        db.query(WorkItemAgent).filter(WorkItemAgent.work_item_id.in_(work_ids)).delete(synchronize_session=False)
        db.query(WorkItemUser).filter(WorkItemUser.work_item_id.in_(work_ids)).delete(synchronize_session=False)
        db.query(WorkItem).filter(WorkItem.id.in_(work_ids)).delete(synchronize_session=False)
    if user_ids:
        db.query(WorkUser).filter(WorkUser.id.in_(user_ids)).delete(synchronize_session=False)
    db.query(WorkAccount).filter(
        WorkAccount.workspace_id == workspace_id,
        WorkAccount.external_id.like(f"{ENTITY_PREFIX}:%"),
    ).delete(synchronize_session=False)
    db.query(RegisteredAgent).filter(
        RegisteredAgent.name.like(f"Historical Demo [{workspace_id}] — %"),
        RegisteredAgent.department.like(f"{workspace_id}:%"),
    ).delete(synchronize_session=False)
    db.query(OrganizationalUnit).filter(
        OrganizationalUnit.workspace_id == workspace_id,
        OrganizationalUnit.external_id.like(f"{ENTITY_PREFIX}:%"),
    ).delete(synchronize_session=False)
    seed_state = db.query(HistoricalDemoSeedState).filter_by(
        workspace_id=workspace_id, marker=MARKER
    ).first()
    settings = db.query(WorkspaceAnalyticsSettings).filter_by(workspace_id=workspace_id).first()
    if seed_state:
        if seed_state.settings_existed:
            snapshot = json.loads(seed_state.settings_snapshot or "{}")
            if not settings:
                settings = WorkspaceAnalyticsSettings(workspace_id=workspace_id)
                db.add(settings)
            for key in (
                "timezone_name", "week_starts_on", "fiscal_year_start_month",
                "default_window_days", "collection_started_at", "latest_complete_at",
            ):
                value = snapshot.get(key)
                if key.endswith("_at") and value:
                    value = datetime.fromisoformat(value)
                setattr(settings, key, value)
        elif settings:
            db.delete(settings)
        db.delete(seed_state)
    return result


def seed_historical_demo(db, workspace_id, start=date(2024, 8, 3), end=date(2026, 8, 3)):
    existing = db.query(TokenTransaction).filter(
        TokenTransaction.workspace_id == workspace_id,
        TokenTransaction.usage_source == MARKER,
    ).count()
    if existing:
        return {"status": "already_seeded", "transactions": existing, "audits": existing}

    rng = random.Random(RANDOM_SEED)
    org_units, agents, users, work_items = _get_or_create_entities(db, workspace_id)
    inserted = 0
    monthly = {}
    for month_index, month in enumerate(_month_starts(start, end)):
        days = _covered_days(month, start, end)
        full_month_days = calendar.monthrange(month.year, month.month)[1]
        calls = round(_month_volume(month, month_index) * len(days) / full_month_days)
        month_key = month.strftime("%Y-%m")
        monthly[month_key] = {"requests": calls, "tokens": 0, "cost_usd": 0.0}
        available_agents = [entry for entry in agents if entry[1] <= month]
        for index in range(calls):
            timestamp_day = days[index % len(days)]
            timestamp = datetime(month.year, month.month, timestamp_day, 8 + index % 11, (index * 17) % 60)
            agent, _, department = available_agents[(index * 5 + month_index) % len(available_agents)]
            department_users = [entry for entry in users if entry[1] == department]
            user, _ = department_users[index % len(department_users)]
            department_work = [entry for entry in work_items if entry[1] == department]
            item, _, account = department_work[index % len(department_work)]
            tier = _weighted_choice(rng, _tier_weights(timestamp.date()))
            model_name, input_rate, output_rate = TIER_DETAILS[tier]
            volume_growth = 1 + month_index * .018
            raw_tokens = round(rng.randint(850, 3100) * volume_growth)
            prune_low, prune_high = ((.18, .38) if timestamp.date() < date(2026, 1, 1) else (.38, .62))
            tokens_saved = round(raw_tokens * rng.uniform(prune_low, prune_high))
            input_tokens = max(100, raw_tokens - tokens_saved)
            output_tokens = round(rng.randint(120, 820) * (1.35 if tier in {"Advisor", "Strategist"} else 1.0))
            cost = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
            risk_roll = rng.random()
            risk_boost = .10 if date(2025, 9, 1) <= timestamp.date() < date(2025, 10, 1) else 0
            risk_level = "high" if risk_roll < .025 + risk_boost else "medium" if risk_roll < .11 + risk_boost else "low"
            blocked = risk_level == "high" and index % 3 == 0
            request_id = f"{MARKER}:{workspace_id}:{month_key}:{index:04d}"
            department_key = f"{workspace_id}:{department}"
            event_type = "BLOCK" if blocked else "DECISION" if tier in {"Advisor", "Strategist"} else "ROUTING"
            decision = "BLOCKED" if blocked else "ROUTED"
            context = {
                "historical_demo": True,
                "story_period": (
                    "initial_adoption" if timestamp.date() < date(2025, 1, 1)
                    else "department_rollout" if timestamp.date() < date(2026, 1, 1)
                    else "routing_optimization"
                ),
                "raw_tokens": raw_tokens,
                "tokens_saved": tokens_saved,
                "source_marker": MARKER,
            }
            common = dict(
                governed_request_id=request_id, agent_id=agent.id,
                work_item_id=item.id, work_user_id=user.id,
                origin_record_id=item.source_record_id,
                origin_record_type=item.source_record_type,
                origin_record_name=item.name,
                actor_external_id=user.external_id, actor_name=user.name,
                actor_email=user.email, actor_source_platform=user.source_platform,
                workspace_id=workspace_id,
                actor_org_unit_id=org_units[department].id,
                actor_org_unit_name=DEPARTMENTS[department]["team"],
                agent_org_unit_id=org_units[department].id,
                agent_org_unit_name=department,
                work_org_unit_id=org_units[department].id,
                work_org_unit_name=department,
                charged_org_unit_id=org_units[department].id,
                charged_org_unit_name=department,
                attribution_source="historical_demo",
                attribution_confidence="verified",
            )
            db.add(TokenTransaction(
                **common, department=department_key,
                source_platform=DEPARTMENTS[department]["platform"],
                model_tier=tier, model_name=model_name, resolved_model_tier=tier,
                model_source="historical_demo", is_simulation=True,
                input_tokens=input_tokens, output_tokens=output_tokens,
                usage_source=MARKER, cost_usd=cost, timestamp=timestamp,
                routing_reason="BLOCKED_POLICY" if blocked else "COMPLEX" if tier in {"Advisor", "Strategist"} else "ROUTINE",
                routing_policy_version="historical-demo-v1",
                execution_status="blocked" if blocked else "succeeded",
                was_pruned=True, tokens_saved=tokens_saved,
            ))
            db.add(AuditEvent(
                **common, event_type=event_type, department=department_key,
                model_tier=tier, selected_model_name=model_name,
                selected_model_tier=tier, routing_policy_version="historical-demo-v1",
                routing_reason_code="BLOCKED_POLICY" if blocked else "ROUTED",
                execution_status="blocked" if blocked else "succeeded",
                context_snapshot=json.dumps(context),
                prompt_payload=f"Historical demo request for {item.name}",
                matched_keywords_json=json.dumps(["privacy"] if risk_level == "high" else []),
                rationale=(
                    "Blocked by policy during the designed September 2025 risk spike."
                    if blocked else f"{tier} selected from the historical demo routing policy."
                ),
                decision_outcome=decision, cost_usd=cost,
                risk_level=risk_level, is_simulation=True, timestamp=timestamp,
            ))
            inserted += 1
            monthly[month_key]["tokens"] += input_tokens + output_tokens
            monthly[month_key]["cost_usd"] += cost

    settings = db.query(WorkspaceAnalyticsSettings).filter_by(workspace_id=workspace_id).first()
    seed_state = HistoricalDemoSeedState(
        workspace_id=workspace_id,
        marker=MARKER,
        settings_existed=bool(settings),
        settings_snapshot=json.dumps({
            "timezone_name": settings.timezone_name,
            "week_starts_on": settings.week_starts_on,
            "fiscal_year_start_month": settings.fiscal_year_start_month,
            "default_window_days": settings.default_window_days,
            "collection_started_at": settings.collection_started_at.isoformat() if settings.collection_started_at else None,
            "latest_complete_at": settings.latest_complete_at.isoformat() if settings.latest_complete_at else None,
        }) if settings else None,
    )
    db.add(seed_state)
    if not settings:
        settings = WorkspaceAnalyticsSettings(workspace_id=workspace_id)
        db.add(settings)
    settings.timezone_name = "America/Chicago"
    settings.week_starts_on = 0
    settings.fiscal_year_start_month = 1
    settings.default_window_days = 30
    settings.collection_started_at = datetime.combine(start, time())
    settings.latest_complete_at = datetime.combine(end + timedelta(days=1), time())
    return {
        "status": "seeded", "workspace_id": workspace_id,
        "transactions": inserted, "audits": inserted,
        "start": start.isoformat(), "end": end.isoformat(),
        "monthly": {key: {**value, "cost_usd": round(value["cost_usd"], 4)} for key, value in monthly.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2024, 8, 3))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 8, 3))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must be on or after --start-date")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.reset:
            result = reset_historical_demo(db, args.workspace_id)
        else:
            result = seed_historical_demo(db, args.workspace_id, args.start_date, args.end_date)
        if args.dry_run:
            db.rollback()
            result["status"] = "dry_run"
        else:
            db.commit()
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
