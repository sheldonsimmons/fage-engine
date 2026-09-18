"""
tests/test_realized_savings_sql_aggregation.py — compute_realized_savings()
(api/routes_reports.py) used to load every matching TokenTransaction row
into Python and aggregate with a per-row loop -- the documented cause of
a real production R14/OOM incident (see that file's own MAX_REPORT_ROWS
comment: a 365-day request against a ~135K-row workspace loaded the
whole result set as ORM objects in one request). Migrated onto SQL-side
SUM/COUNT/CASE aggregation instead.

This test locks in the numbers: hand-computed expected totals for a
small, known set of transactions across two days and both tiers,
including pruned and non-pruned rows, asserted against the real
function -- not just "runs without error."
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction
from core.metrics_query import FLAGSHIP_INPUT_COST, FLAGSHIP_OUTPUT_COST, MICRO_INPUT_COST, MICRO_OUTPUT_COST
from api.routes_reports import compute_realized_savings


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _tx(db, *, tier, cost, input_tokens, output_tokens, tokens_saved, was_pruned, days_ago):
    db.add(TokenTransaction(
        department="Support", model_tier=tier, cost_usd=cost,
        input_tokens=input_tokens, output_tokens=output_tokens,
        tokens_saved=tokens_saved, was_pruned=was_pruned,
        timestamp=datetime.utcnow() - timedelta(days=days_ago),
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        workspace_id="default",
    ))


def test_totals_match_hand_computed_expectation_across_tiers_and_days():
    db = _session()
    # Day 0: one Scout (micro) call, pruned.
    _tx(db, tier="Scout", cost=0.01, input_tokens=100, output_tokens=50, tokens_saved=40, was_pruned=True, days_ago=0)
    # Day 0: one Advisor (flagship) call, not pruned.
    _tx(db, tier="Advisor", cost=0.50, input_tokens=200, output_tokens=100, tokens_saved=0, was_pruned=False, days_ago=0)
    # Day 1: one Scout (micro) call, not pruned (tokens_saved present but was_pruned False -- must NOT count toward pruning savings).
    _tx(db, tier="Scout", cost=0.02, input_tokens=150, output_tokens=60, tokens_saved=30, was_pruned=False, days_ago=1)
    db.commit()

    result = compute_realized_savings(db, "default", 30)

    assert result["total_calls"] == 3
    assert result["micro_calls"] == 2
    assert result["flagship_calls"] == 1
    assert round(result["total_cost_usd"], 6) == round(0.01 + 0.50 + 0.02, 6)

    # Only the day-0 Scout call was pruned -- pruning savings = its
    # tokens_saved (40) * FLAGSHIP_INPUT_COST, not the day-1 row's 30.
    expected_pruning_saved = round(40 * FLAGSHIP_INPUT_COST, 6)
    assert result["tokens_pruned"] == 40
    assert result["pruning_saved_usd"] == expected_pruning_saved

    # Downgrade savings: both Scout rows, using (input+tokens_saved) and output.
    expected_downgrade = round(
        ((100 + 40) + (150 + 30)) * (FLAGSHIP_INPUT_COST - MICRO_INPUT_COST)
        + (50 + 60) * (FLAGSHIP_OUTPUT_COST - MICRO_OUTPUT_COST),
        6,
    )
    assert result["downgrade_saved_usd"] == expected_downgrade
    assert result["total_saved_usd"] == round(expected_pruning_saved + expected_downgrade, 6)

    # Timeline: two distinct days present, in the requested 30-day window.
    active_days = [d for d in result["timeline"] if d["calls"] > 0]
    assert len(active_days) == 2
    by_calls = sorted(active_days, key=lambda d: d["calls"])
    # Day 1 (older) has exactly 1 call; day 0 has 2.
    assert by_calls[0]["calls"] == 1
    assert by_calls[1]["calls"] == 2

    # No row-count ceiling anymore -- always False, not a band-aid cap.
    assert result["truncated"] is False


def test_agent_and_department_scoping_narrow_the_same_totals():
    db = _session()
    _tx(db, tier="Scout", cost=0.01, input_tokens=10, output_tokens=10, tokens_saved=5, was_pruned=True, days_ago=0)
    db.add(TokenTransaction(
        department="Engineering", model_tier="Advisor", cost_usd=1.00,
        input_tokens=500, output_tokens=200, tokens_saved=0, was_pruned=False,
        timestamp=datetime.utcnow(), is_simulation=False, usage_source="estimated",
        routing_reason="COMPLEX", workspace_id="default",
    ))
    db.commit()

    all_result = compute_realized_savings(db, "default", 30)
    support_only = compute_realized_savings(db, "default", 30, department_scope="Support")

    assert all_result["total_calls"] == 2
    assert support_only["total_calls"] == 1
    assert round(support_only["total_cost_usd"], 6) == 0.01
