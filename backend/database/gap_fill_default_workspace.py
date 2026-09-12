"""
database/gap_fill_default_workspace.py -- extend the "default" (NULL
workspace_id) synthetic dataset forward to "now", the same continuity-
preserving approach as simulate_gap_fill.py, adapted for this specific
workspace's shape.

Why not simulate_gap_fill.py directly: that script assumes a named
workspace_id with "workspace_id:Department"-prefixed RegisteredAgent rows
and anchors to ONE company-wide trailing daily rate. This workspace's real
data (128,396 rows, $5,783.68, Aug 2025-Aug 2026) has workspace_id=NULL,
unprefixed departments, and each department runs its own distinct steady
daily rate and tier mix (Engineering skews much more "flagship" than
Marketing, for example) -- collapsing that into one company-wide rate and
a uniform random agent pick across all departments would distort the very
department proportions this fill is trying to preserve continuity with.

Baselines below are measured, not guessed, from this workspace's own real
trailing 14-day window (2026-08-14 through 2026-08-27, the 14 real days
immediately before the gap) -- see the "measured" comment on each entry.
Re-derive them (same date range, same GROUP BY department, model_tier)
before reusing this script if the gap being filled is not the one this
was written for.
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func

from database.db import SessionLocal
from database.models import AuditEvent, RegisteredAgent, TokenTransaction

# Measured 2026-08-14..2026-08-27 (14 real days) via GROUP BY department,
# model_tier on workspace_id IS NULL. requests_per_day = 14-day count / 14.
# tier_mix is (micro_share, flagship_share) by request count.
DEPARTMENT_BASELINE = {
    "Support":     {"requests_per_day": 2104 / 14, "tier_mix": (1357 / 2104, 747 / 2104)},
    "Sales":       {"requests_per_day": 1754 / 14, "tier_mix": (1122 / 1754, 632 / 1754)},
    "Operations":  {"requests_per_day": 1052 / 14, "tier_mix": (673 / 1052, 379 / 1052)},
    "Marketing":   {"requests_per_day": 1402 / 14, "tier_mix": (1214 / 1402, 188 / 1402)},
    "Engineering": {"requests_per_day": 704 / 14, "tier_mix": (317 / 704, 387 / 704)},
}

# Measured across the same window, per tier (varies little by department --
# using one pooled figure per tier keeps this legible; see module docstring
# to re-derive per-department if that stops being true).
TIER_STATS = {
    "micro":    {"cost_usd": 0.002003, "input_tokens": 2201, "output_tokens": 604, "tokens_saved": 1284},
    "flagship": {"cost_usd": 0.11665, "input_tokens": 12878, "output_tokens": 3484, "tokens_saved": 1273},
}

JITTER = 0.20  # +/-20% day-to-day, matching simulate_gap_fill.py's own jitter band
VALUE_JITTER = 0.15  # +/-15% per-transaction spread around each tier's measured mean


def _weighted_tier(rng, micro_share):
    return "micro" if rng.random() < micro_share else "flagship"


def _jittered(rng, mean, spread=VALUE_JITTER):
    return mean * rng.uniform(1 - spread, 1 + spread)


def _load_agents(db):
    agents = db.query(RegisteredAgent).filter(
        RegisteredAgent.department.in_(DEPARTMENT_BASELINE.keys()),
        (RegisteredAgent.archived.is_(False)) | (RegisteredAgent.archived.is_(None)),
    ).all()
    by_department = {}
    for agent in agents:
        by_department.setdefault(agent.department, []).append(agent)
    return by_department


def gap_fill_default_workspace(db, now=None, dry_run=False):
    now = now or datetime.utcnow()
    last_timestamp = db.query(func.max(TokenTransaction.timestamp)).filter(
        TokenTransaction.workspace_id.is_(None),
        TokenTransaction.usage_source == "estimated",
    ).scalar()
    if not last_timestamp:
        return {"status": "no_baseline", "message": "No existing estimated activity found for workspace_id IS NULL."}
    gap_days = (now.date() - last_timestamp.date()).days
    if gap_days < 1:
        return {"status": "no_gap", "last_timestamp": last_timestamp.isoformat()}

    agents_by_department = _load_agents(db)
    missing = [dept for dept in DEPARTMENT_BASELINE if not agents_by_department.get(dept)]
    if missing:
        return {"status": "no_entities", "message": f"No registered agents found for: {missing}"}

    rng = random.Random()
    transaction_batch = []
    audit_batch = []
    per_department_totals = {dept: {"requests": 0, "cost_usd": 0.0} for dept in DEPARTMENT_BASELINE}

    for day_offset in range(gap_days):
        day = last_timestamp.date() + timedelta(days=day_offset + 1)
        if day > now.date():
            break
        for department, baseline in DEPARTMENT_BASELINE.items():
            count = max(1, round(baseline["requests_per_day"] * rng.uniform(1 - JITTER, 1 + JITTER)))
            department_agents = agents_by_department[department]
            micro_share, _ = baseline["tier_mix"]
            for index in range(count):
                timestamp = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=8 + index % 11, minutes=(index * 17) % 60, seconds=rng.randint(0, 59),
                )
                if timestamp > now:
                    continue
                agent = rng.choice(department_agents)
                tier = _weighted_tier(rng, micro_share)
                stats = TIER_STATS[tier]
                input_tokens = max(50, round(_jittered(rng, stats["input_tokens"])))
                output_tokens = max(20, round(_jittered(rng, stats["output_tokens"])))
                tokens_saved = max(0, round(_jittered(rng, stats["tokens_saved"])))
                cost = round(_jittered(rng, stats["cost_usd"]), 6)
                request_id = f"gapfill-default:{timestamp.strftime('%Y%m%d%H%M%S%f')}:{department}:{index}"

                transaction_batch.append(TokenTransaction(
                    governed_request_id=None,
                    department=department,
                    agent_id=agent.id,
                    source_platform=agent.source_platform,
                    workspace_id=None,
                    model_tier=tier, model_source=None,
                    is_simulation=False,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    usage_source="estimated", cost_usd=cost, timestamp=timestamp,
                    routing_reason="ROUTINE" if tier == "micro" else "COMPLEX",
                    was_pruned=True, tokens_saved=tokens_saved,
                ))
                audit_batch.append(AuditEvent(
                    governed_request_id=request_id,
                    event_type="ROUTING",
                    department=department,
                    agent_id=agent.id,
                    workspace_id=None,
                    model_tier=tier, selected_model_tier=tier,
                    routing_reason_code="ROUTINE" if tier == "micro" else "COMPLEX",
                    execution_status="succeeded",
                    rationale=f"{tier} tier selected for a {'routine' if tier == 'micro' else 'complex'} request.",
                    decision_outcome="ROUTED", cost_usd=cost,
                    risk_level="low", is_simulation=False, timestamp=timestamp,
                ))
                per_department_totals[department]["requests"] += 1
                per_department_totals[department]["cost_usd"] += cost

    if not dry_run:
        db.bulk_save_objects(transaction_batch)
        db.bulk_save_objects(audit_batch)
        db.commit()

    return {
        "status": "filled" if transaction_batch else "no_gap",
        "gap_days": gap_days,
        "last_timestamp_before": last_timestamp.isoformat(),
        "now": now.isoformat(),
        "total_transactions": len(transaction_batch),
        "total_cost_usd": round(sum(v["cost_usd"] for v in per_department_totals.values()), 4),
        "per_department": {
            dept: {"requests": v["requests"], "cost_usd": round(v["cost_usd"], 4)}
            for dept, v in per_department_totals.items()
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = gap_fill_default_workspace(db, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()
            result["status"] = f"dry_run:{result['status']}"
        else:
            db.commit()
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
