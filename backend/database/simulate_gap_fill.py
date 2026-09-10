"""
database/simulate_gap_fill.py -- extend synthetic traffic-simulator
activity for a workspace up to "now", reusing whatever real agents,
people, and work items already exist there.

Run from the backend directory:
    python -m database.simulate_gap_fill --workspace-id SIM-HISTORICAL-2Y --dry-run
    python -m database.simulate_gap_fill --workspace-id SIM-HISTORICAL-2Y

Intended to run once daily via Heroku Scheduler so the real gap since the
last simulated activity never grows large enough to produce an implausible
burst the next time someone manually runs the Traffic Simulator UI, and so
the gap itself is never large enough on its own to look suspicious (a
demo dataset with several fully silent days followed by a spike is exactly
what tipped off this fix in the first place -- see the "What Changed"
widget showing a 765% spend swing that was really just an irregularly-
clicked simulator, not any real business signal).

Deliberately does NOT reuse database/seed_historical_demo.py -- that
script's own state table (historical_demo_seed_state) does not even exist
in production, and its entity set (fictional agents like "Renewal
Planning Agent") has never matched what is actually registered in this
workspace (real agents like "Pipeline Coach Agent", "Support Deflection
Agent"). It is unused, disconnected code for this workspace; extending it
would create a second, parallel entity set nothing else in the app would
recognize. This script instead queries the real, already-registered
agents/people/work items and adds more activity for them, exactly like
frontend/js/traffic_simulator.js's live, browser-driven equivalent
already does -- just spread across the real gap directly at the DB level
instead of one HTTP call per record.
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
from database.models import RegisteredAgent, TokenTransaction, WorkItem, WorkUser

TIER_DETAILS = {
    "Scout": ("gpt-4.1-nano", 0.20, 1.25),
    "Analyst": ("gpt-4.1-mini", 0.75, 4.50),
    "Advisor": ("gpt-4.1", 2.50, 15.00),
    "Strategist": ("claude-opus-4-6", 5.00, 30.00),
}
TIER_WEIGHTS = [("Scout", .50), ("Analyst", .32), ("Advisor", .14), ("Strategist", .04)]
# Matches this workspace's own observed steady-state baseline (see the
# smooth pre-burst period this fix is restoring continuity with) --
# deliberately a range, not a fixed number, so consecutive days still
# look like organic day-to-day variation rather than a repeating constant.
MIN_GAP_TO_FILL = timedelta(hours=6)
TRAILING_LOOKBACK_DAYS = 14


def _weighted_choice(rng, choices):
    roll = rng.random()
    running = 0.0
    for value, weight in choices:
        running += weight
        if roll <= running:
            return value
    return choices[-1][0]


def _trailing_daily_volume(db, workspace_id, before, lookback_days=TRAILING_LOOKBACK_DAYS):
    """
    Average daily request count in the lookback_days immediately before
    `before` (the last real timestamp). Anchoring the fill to this instead
    of a fixed guess is what actually fixes the burst problem at its root
    -- confirmed live: a fixed (35, 75) range would have made Aug 20
    onward drop straight from the real ~90/day it was actually running at
    down to a much lower fixed range, itself a visible, unexplained cliff
    of the same kind the whole fix exists to remove, just smaller.
    """
    window_start = before - timedelta(days=lookback_days)
    count = db.query(func.count(TokenTransaction.id)).filter(
        TokenTransaction.workspace_id == workspace_id,
        TokenTransaction.timestamp >= window_start,
        TokenTransaction.timestamp < before,
        TokenTransaction.is_simulation.is_(True),
    ).scalar() or 0
    return max(count / lookback_days, 5.0)


def _load_entities(db, workspace_id):
    agents = db.query(RegisteredAgent).filter(
        RegisteredAgent.department.like(f"{workspace_id}:%"),
    ).filter(
        (RegisteredAgent.archived.is_(False)) | (RegisteredAgent.archived.is_(None))
    ).all()
    users = db.query(WorkUser).filter(WorkUser.workspace_id == workspace_id).all()
    work_items = db.query(WorkItem).filter(WorkItem.workspace_id == workspace_id).all()
    return agents, users, work_items


def simulate_gap_fill(
    db, workspace_id, now=None, dry_run=False,
):
    """
    Inserts TokenTransaction rows spread across [last_timestamp, now] at a
    steady daily rate, reusing real existing entities for this workspace.
    A no-op (status="no_gap") when the gap is too small to be worth
    filling -- e.g. run again minutes after a manual Traffic Simulator
    session, or run again the same day it already ran.
    """
    now = now or datetime.utcnow()
    # is_simulation=True only -- a handful of real, non-simulation rows can
    # exist in the same window (this workspace does have live traffic mixed
    # in) and must never anchor this fill. Confirmed live: without this
    # filter, a handful of real rows scattered inside an otherwise-deleted
    # burst window made this think the gap was only 2 days when the actual
    # synthetic timeline's last point was 22 days earlier, so the fill
    # silently covered only a sliver of the real gap.
    last_timestamp = db.query(func.max(TokenTransaction.timestamp)).filter(
        TokenTransaction.workspace_id == workspace_id,
        TokenTransaction.is_simulation.is_(True),
    ).scalar()
    if not last_timestamp or (now - last_timestamp) < MIN_GAP_TO_FILL:
        return {
            "status": "no_gap",
            "last_timestamp": last_timestamp.isoformat() if last_timestamp else None,
        }

    agents, users, work_items = _load_entities(db, workspace_id)
    if not agents or not work_items:
        return {
            "status": "no_entities",
            "message": f"No existing registered agents/work items found for workspace '{workspace_id}'.",
        }

    rng = random.Random()
    gap_days = max((now.date() - last_timestamp.date()).days, 1)
    transaction_batch = []
    inserted = 0

    baseline = _trailing_daily_volume(db, workspace_id, last_timestamp)

    for day_offset in range(gap_days):
        day = last_timestamp.date() + timedelta(days=day_offset + 1)
        if day > now.date():
            break
        # +/-20% day-to-day jitter around the measured trailing baseline --
        # organic daily variation, not a robotic constant, while staying
        # anchored to whatever this workspace was actually running at
        # rather than an arbitrary fixed guess.
        count = max(1, round(baseline * rng.uniform(0.8, 1.2)))
        for index in range(count):
            timestamp = datetime.combine(day, datetime.min.time()) + timedelta(
                hours=8 + index % 11, minutes=(index * 17) % 60, seconds=rng.randint(0, 59),
            )
            if timestamp > now:
                continue
            agent = rng.choice(agents)
            department_key = agent.department  # already "workspace_id:Department"
            user = rng.choice(users) if users else None
            work_item = rng.choice(work_items)
            tier = _weighted_choice(rng, TIER_WEIGHTS)
            model_name, input_rate, output_rate = TIER_DETAILS[tier]
            raw_tokens = rng.randint(700, 2600)
            tokens_saved = round(raw_tokens * rng.uniform(0.30, 0.55))
            input_tokens = max(100, raw_tokens - tokens_saved)
            output_tokens = rng.randint(120, 700)
            cost = round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
            request_id = f"gapfill:{workspace_id}:{timestamp.strftime('%Y%m%d%H%M%S%f')}:{index}"

            transaction_batch.append(TokenTransaction(
                governed_request_id=request_id,
                agent_id=agent.id,
                work_item_id=work_item.id,
                work_user_id=user.id if user else None,
                actor_external_id=user.external_id if user else None,
                actor_name=user.name if user else None,
                actor_email=user.email if user else None,
                actor_source_platform=user.source_platform if user else None,
                workspace_id=workspace_id,
                department=department_key,
                source_platform=agent.source_platform,
                model_tier=tier, model_name=model_name, resolved_model_tier=tier,
                model_source="gap_fill", is_simulation=True,
                input_tokens=input_tokens, output_tokens=output_tokens,
                usage_source="estimated", cost_usd=cost, timestamp=timestamp,
                routing_reason="COMPLEX" if tier in {"Advisor", "Strategist"} else "ROUTINE",
                routing_policy_version="gap-fill-v1",
                execution_status="succeeded",
                was_pruned=True, tokens_saved=tokens_saved,
            ))
            inserted += 1

    if not dry_run:
        db.bulk_save_objects(transaction_batch)
        db.commit()

    return {
        # last_timestamp already covers today (e.g. run twice the same day,
        # or a >6h gap that hasn't yet crossed into a new calendar day) --
        # inserted stays 0 by construction (the day loop starts at the day
        # *after* last_timestamp), which is correct, not a failure.
        "status": "filled" if inserted else "no_gap",
        "workspace_id": workspace_id,
        "gap_days": gap_days,
        "transactions": inserted,
        "last_timestamp_before": last_timestamp.isoformat(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = simulate_gap_fill(db, args.workspace_id, dry_run=args.dry_run)
        if args.dry_run:
            db.rollback()
            result["status"] = f"dry_run:{result['status']}"
        print(json.dumps(result, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
