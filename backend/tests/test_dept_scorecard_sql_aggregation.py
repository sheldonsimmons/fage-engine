"""
tests/test_dept_scorecard_sql_aggregation.py — dept_scorecard()
(api/routes_reports.py) had the same MAX_REPORT_ROWS/per-row-Python-loop
OOM exposure as compute_realized_savings and risk_report (see
test_realized_savings_sql_aggregation.py's docstring for the production
incident this pattern caused). Migrated onto SQL GROUP BY the same way;
the DepartmentBudget merge logic (never the source of the row-count
exposure -- one row per department, not per transaction) is unchanged.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, DepartmentBudget
from core.metrics_query import FLAGSHIP_INPUT_COST
from api.routes_reports import dept_scorecard


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _tx(db, *, department, tier, cost, tokens_saved, was_pruned, days_ago=0):
    db.add(TokenTransaction(
        department=department, model_tier=tier, cost_usd=cost,
        input_tokens=10, output_tokens=10, tokens_saved=tokens_saved, was_pruned=was_pruned,
        timestamp=datetime.utcnow() - timedelta(days=days_ago),
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        workspace_id="default",
    ))


def test_per_department_totals_and_budget_merge_match_hand_computed_expectation():
    db = _session()
    _tx(db, department="Support", tier="Scout", cost=0.10, tokens_saved=20, was_pruned=True, days_ago=0)
    _tx(db, department="Support", tier="Advisor", cost=0.50, tokens_saved=0, was_pruned=False, days_ago=1)
    _tx(db, department="Sales", tier="Scout", cost=0.05, tokens_saved=10, was_pruned=False, days_ago=0)
    db.add(DepartmentBudget(department="Support", monthly_cap_usd=1.00, current_spend_usd=0.60))
    db.add(DepartmentBudget(department="Marketing", monthly_cap_usd=5.00, current_spend_usd=0.00))
    db.commit()

    result = dept_scorecard(days=30, workspace_id="default", date_from=None, date_to=None, db=db, authorization=None)
    by_dept = {row["department"]: row for row in result["scorecards"]}

    support = by_dept["Support"]
    assert support["total_calls"] == 2
    assert support["micro_calls"] == 1
    assert support["flagship_calls"] == 1
    assert round(support["total_cost_usd"], 6) == 0.60
    assert support["tokens_pruned"] == 20  # only the pruned row counts, not the tokens_saved=0 row
    assert support["pruning_saved_usd"] == round(20 * FLAGSHIP_INPUT_COST, 6)
    assert support["monthly_cap_usd"] == 1.00
    assert support["budget_used_pct"] == 60.0

    sales = by_dept["Sales"]
    assert sales["total_calls"] == 1
    assert sales["monthly_cap_usd"] == 0  # no budget row for Sales

    # Marketing has a budget but zero transactions -- must still appear
    # (budget-only departments are a real row, not silently dropped).
    marketing = by_dept["Marketing"]
    assert marketing["total_calls"] == 0
    assert marketing["monthly_cap_usd"] == 5.00

    assert set(result["departments"]) == {"Support", "Sales", "Marketing"}
    active_timeline_days = [d for d in result["timeline"] if len(d) > 1]  # more than just "date"
    assert len(active_timeline_days) == 2
    assert result["truncated"] is False
