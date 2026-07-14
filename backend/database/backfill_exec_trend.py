"""
One-time historical usage backfill for the executive dashboard story.

This inserts realistic, backdated CostPilot activity into token_transactions and
audit_events so month-over-month charts have a real data trail to render from.
It is idempotent: if the marker already exists, the script exits without adding
duplicate rows.

Run from the backend directory:
    python database/backfill_exec_trend.py
"""

import argparse
import calendar
import json
import random
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from database.db import Base, SessionLocal, engine
from database.models import AuditEvent, RegisteredAgent, TokenTransaction


MARKER = "exec_trend_backfill_v1"
RANDOM_SEED = 7142026


AGENTS = [
    {
        "name": "Renewal Quote Agent",
        "department": "Sales",
        "source_platform": "Salesforce",
        "target_table": "opportunities",
        "scenario": "renewal quote review",
    },
    {
        "name": "Support Resolution Agent",
        "department": "Support",
        "source_platform": "Salesforce Service Cloud",
        "target_table": "tickets",
        "scenario": "customer support response",
    },
    {
        "name": "Content QA Agent",
        "department": "Marketing",
        "source_platform": "Salesforce Service Cloud",
        "target_table": "campaigns",
        "scenario": "campaign message review",
    },
    {
        "name": "Workflow Ops Agent",
        "department": "Operations",
        "source_platform": "Zendesk",
        "target_table": "workflows",
        "scenario": "operations workflow summary",
    },
    {
        "name": "Invoice Review Agent",
        "department": "Finance",
        "source_platform": "Salesforce Agentforce",
        "target_table": "invoices",
        "scenario": "vendor invoice validation",
    },
    {
        "name": "Contract Intake Agent",
        "department": "Legal",
        "source_platform": "HubSpot",
        "target_table": "contracts",
        "scenario": "contract intake review",
    },
    {
        "name": "Quality Review Agent",
        "department": "Engineering",
        "source_platform": "SAP",
        "target_table": "quality_cases",
        "scenario": "quality incident summary",
    },
]


MONTHS = [
    {"year": 2026, "month": 2, "calls": 42, "target_cost": 7.80},
    {"year": 2026, "month": 3, "calls": 54, "target_cost": 12.40},
    {"year": 2026, "month": 4, "calls": 66, "target_cost": 19.75},
    {"year": 2026, "month": 5, "calls": 78, "target_cost": 28.60},
    {"year": 2026, "month": 6, "calls": 90, "target_cost": 39.20},
]


TIER_PLAN = [
    ("Scout", "ROUTINE", "ROUTING", "low", []),
    ("Analyst", "ROUTINE", "ROUTING", "low", []),
    ("Advisor", "COMPLEX", "DECISION", "medium", ["analyze", "forecast"]),
    ("Strategist", "COMPLEX", "DECISION", "high", ["contract", "legal"]),
]


def get_or_create_agent(db, spec):
    agent = db.query(RegisteredAgent).filter_by(name=spec["name"]).first()
    if agent:
        return agent

    agent = RegisteredAgent(
        name=spec["name"],
        department=spec["department"],
        source_platform=spec["source_platform"],
        permissions="read,write",
        target_table=spec["target_table"],
        status="idle",
        collision_policy="lock",
        min_tier=1,
        max_tier=4,
        pruning_enabled=True,
        archived=False,
    )
    db.add(agent)
    db.flush()
    return agent


def noisy_payload(agent_spec, month_label, index):
    customer = [
        "Meridian Retail",
        "Northstar Health",
        "Apex Manufacturing",
        "Summit Logistics",
        "Blue Harbor Finance",
        "Canyon Energy",
    ][index % 6]
    repeated_header = (
        f"From: account.owner{index}@example.com\n"
        f"To: {agent_spec['name'].lower().replace(' ', '.')}@example.com\n"
        "CC: legal@example.com; finance@example.com; operations@example.com\n"
        f"Date: {month_label} 2026\n"
        "Subject: RE: RE: RE: Customer request - follow up needed\n"
        "X-Mailer: Microsoft Outlook 16.0\n"
        "Importance: High\n"
        "Thread-ID: costpilot-demo-thread\n"
    )
    body = (
        f"{customer} needs help with {agent_spec['scenario']}. "
        "Please summarize the request, identify the owner, and recommend the next action. "
        "The useful business details are mixed with forwarded history, signatures, stale "
        "ticket metadata, disclaimers, and repeated reply blocks."
    )
    footer = (
        "\n\n--\n"
        "Confidentiality notice: this demo message may contain internal business context.\n"
        "Sent from mobile. Please excuse typos.\n"
        "This email and any attachments are intended only for the named recipients.\n"
    )
    return "\n".join([repeated_header, body, repeated_header, body, footer, footer])


def clean_payload(agent_spec, index):
    customer = [
        "Meridian Retail",
        "Northstar Health",
        "Apex Manufacturing",
        "Summit Logistics",
        "Blue Harbor Finance",
        "Canyon Energy",
    ][index % 6]
    return (
        f"{customer} needs help with {agent_spec['scenario']}. "
        "Summarize the request, identify the owner, and recommend the next action."
    )


def month_timestamp(year, month, index):
    days = calendar.monthrange(year, month)[1]
    day = 1 + (index * 3) % days
    hour = 8 + (index * 5) % 10
    minute = (index * 11) % 60
    return datetime(year, month, day, hour, minute, 0)


def cost_for_call(target_cost, calls, tier, rng):
    tier_weight = {"Scout": 0.45, "Analyst": 0.8, "Advisor": 1.3, "Strategist": 2.1}[tier]
    jitter = rng.uniform(0.75, 1.25)
    return round((target_cost / calls) * tier_weight * jitter, 6)


def token_stats(tier, rng):
    raw_tokens = rng.randint(1100, 4200)
    saved_ratio = rng.uniform(0.18, 0.62)
    tokens_saved = int(raw_tokens * saved_ratio)
    clean_tokens = max(120, raw_tokens - tokens_saved)
    output_tokens = {
        "Scout": rng.randint(120, 360),
        "Analyst": rng.randint(220, 620),
        "Advisor": rng.randint(450, 950),
        "Strategist": rng.randint(800, 1500),
    }[tier]
    return raw_tokens, clean_tokens, tokens_saved, output_tokens


def rationale(agent_spec, tier, reason, cost, raw_tokens, clean_tokens, tokens_saved):
    if reason == "COMPLEX":
        return (
            f"FLAGSHIP MODEL INVOKED. Payload routed to the {tier} model tier for "
            f"the {agent_spec['department']} department after complexity analysis. "
            f"CostPilot pruned repeated context first: {raw_tokens} raw tokens became "
            f"{clean_tokens} clean tokens, saving {tokens_saved} tokens. "
            f"Call cost: ${cost:.6f}. Decision: the stronger model was warranted."
        )
    return (
        f"ROUTINE CALL — {tier} tier selected for {agent_spec['department']} department. "
        f"CostPilot removed repeated headers and stale thread history first: {raw_tokens} "
        f"raw tokens became {clean_tokens} clean tokens, saving {tokens_saved} tokens. "
        f"Call cost: ${cost:.6f}. Decision: lower-cost routing was appropriate."
    )


def insert_backfill(dry_run=False):
    rng = random.Random(RANDOM_SEED)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = (
            db.query(TokenTransaction)
            .filter(TokenTransaction.usage_source == MARKER)
            .count()
        )
        if existing:
            print(f"Backfill marker already present: {existing} rows. Nothing inserted.")
            return

        agent_records = [(spec, get_or_create_agent(db, spec)) for spec in AGENTS]
        inserted_transactions = 0
        inserted_audits = 0
        monthly_totals = {}

        for month_plan in MONTHS:
            year = month_plan["year"]
            month = month_plan["month"]
            calls = month_plan["calls"]
            target_cost = month_plan["target_cost"]
            month_label = datetime(year, month, 1).strftime("%B")
            monthly_totals[f"{year}-{month:02d}"] = 0.0

            for index in range(calls):
                agent_spec, agent = agent_records[index % len(agent_records)]
                tier, routing_reason, event_type, risk_level, keywords = TIER_PLAN[
                    (index + month) % len(TIER_PLAN)
                ]
                raw_tokens, clean_tokens, tokens_saved, output_tokens = token_stats(tier, rng)
                cost = cost_for_call(target_cost, calls, tier, rng)
                timestamp = month_timestamp(year, month, index)
                raw_payload = noisy_payload(agent_spec, month_label, index)
                prompt_payload = clean_payload(agent_spec, index)
                context = {
                    "captured_at": timestamp.isoformat(),
                    "department": agent_spec["department"],
                    "budget_cap_usd": 10.0,
                    "budget_spent_usd": round(monthly_totals[f"{year}-{month:02d}"], 4),
                    "budget_used_pct": round((monthly_totals[f"{year}-{month:02d}"] / 10.0) * 100, 1),
                    "throttled": False,
                    "override_granted": False,
                    "raw_retention_days": 365,
                    "raw_tokens": raw_tokens,
                    "clean_tokens": clean_tokens,
                    "tokens_saved": tokens_saved,
                    "compression_pct": round((tokens_saved / raw_tokens) * 100, 1),
                    "input_tokens": clean_tokens,
                    "output_tokens": output_tokens,
                    "usage_source": MARKER,
                    "cost_usd": cost,
                }

                monthly_totals[f"{year}-{month:02d}"] += cost

                db.add(
                    TokenTransaction(
                        department=agent_spec["department"],
                        source_platform=agent_spec["source_platform"],
                        agent_id=agent.id,
                        model_tier=tier,
                        input_tokens=clean_tokens,
                        output_tokens=output_tokens,
                        usage_source=MARKER,
                        cost_usd=cost,
                        timestamp=timestamp,
                        routing_reason=routing_reason,
                        was_pruned=True,
                        tokens_saved=tokens_saved,
                    )
                )
                inserted_transactions += 1

                db.add(
                    AuditEvent(
                        event_type=event_type,
                        agent_id=agent.id,
                        department=agent_spec["department"],
                        model_tier=tier,
                        context_snapshot=json.dumps(context),
                        prompt_payload=prompt_payload,
                        raw_payload=raw_payload[:5000],
                        raw_logged_at=timestamp,
                        matched_keywords_json=json.dumps(keywords),
                        rationale=rationale(
                            agent_spec, tier, routing_reason, cost,
                            raw_tokens, clean_tokens, tokens_saved
                        ),
                        decision_outcome=(
                            "FLAGSHIP MODEL INVOKED"
                            if routing_reason == "COMPLEX"
                            else "ROUTINE ROUTING"
                        ),
                        risk_level=risk_level,
                        timestamp=timestamp,
                    )
                )
                inserted_audits += 1

        if dry_run:
            db.rollback()
            print("Dry run complete. No rows inserted.")
        else:
            db.commit()
            print("Backfill complete.")

        print(f"Transactions prepared: {inserted_transactions}")
        print(f"Audit events prepared: {inserted_audits}")
        for key, value in monthly_totals.items():
            print(f"{key}: ${value:.2f}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    insert_backfill(dry_run=args.dry_run)
