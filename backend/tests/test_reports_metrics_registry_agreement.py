"""
tests/test_reports_metrics_registry_agreement.py — locks in that
api/routes_reports.py's savings_report()/dept_scorecard() and
core/metrics_query.py's registry produce IDENTICAL savings numbers for
the same data, even though they're two separate code paths (Reports
still loops over raw rows to build a per-day timeline, which the
registry has no time-bucketing support for -- see this session's
metrics-unification audit).

Both now import the same rate constants from core/metrics_query.py, so
this test's job is to catch the case where the FORMULA itself (not just
the numbers) drifts apart -- e.g. someone changes the downgrade formula
in one file without updating the other. If this test ever fails, that's
exactly the kind of silent disagreement the metrics registry was built
to prevent.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_reports import compute_realized_savings, dept_scorecard
from core.metrics_query import run_metrics_query
from database.db import Base
from database.models import TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db, workspace_id="WS-AGREE"):
    now = datetime.utcnow()
    db.add_all([
        # A pruned Scout call -- exercises both pruning_savings and downgrade_savings
        TokenTransaction(
            workspace_id=workspace_id, department="Sales", model_tier="Scout",
            input_tokens=500, output_tokens=100, tokens_saved=200, was_pruned=True,
            cost_usd=0.15, timestamp=now - timedelta(hours=2), is_simulation=False,
            usage_source="estimated", routing_reason="ROUTINE",
        ),
        # An unpruned Advisor (flagship) call -- contributes to total_cost only
        TokenTransaction(
            workspace_id=workspace_id, department="Sales", model_tier="Advisor",
            input_tokens=800, output_tokens=300, tokens_saved=0, was_pruned=False,
            cost_usd=2.40, timestamp=now - timedelta(hours=1), is_simulation=False,
            usage_source="estimated", routing_reason="COMPLEX",
        ),
        # An unpruned Analyst (economy) call in a different department
        TokenTransaction(
            workspace_id=workspace_id, department="Support", model_tier="Analyst",
            input_tokens=300, output_tokens=50, tokens_saved=0, was_pruned=False,
            cost_usd=0.05, timestamp=now - timedelta(hours=3), is_simulation=False,
            usage_source="estimated", routing_reason="MODERATE",
        ),
    ])
    db.commit()


def test_savings_report_totals_match_the_registry():
    db = _session()
    _seed(db)

    report = compute_realized_savings(db, "WS-AGREE", days=1)

    registry = run_metrics_query(
        db, "WS-AGREE",
        metrics=["ai_spend", "pruning_savings", "downgrade_savings", "savings"],
        timeframe={"start": datetime.utcnow() - timedelta(days=1), "end": datetime.utcnow() + timedelta(minutes=1)},
    )
    row = registry.rows[0]

    assert report["total_cost_usd"] == round(row["ai_spend"], 6)
    assert report["pruning_saved_usd"] == round(row["pruning_savings"], 6)
    assert report["downgrade_saved_usd"] == round(row["downgrade_savings"], 6)
    assert report["total_saved_usd"] == round(row["savings"], 6)


def test_dept_scorecard_pruning_savings_matches_the_registry_per_department():
    db = _session()
    _seed(db)

    scorecard = {
        row["department"]: row
        for row in dept_scorecard(days=1, workspace_id="WS-AGREE", date_from=None, date_to=None, db=db)["scorecards"]
    }

    registry = run_metrics_query(
        db, "WS-AGREE",
        metrics=["ai_spend", "pruning_savings"],
        dimensions=["department"],
        timeframe={"start": datetime.utcnow() - timedelta(days=1), "end": datetime.utcnow() + timedelta(minutes=1)},
    )
    by_dept = {row["dimensions"]["department"]: row for row in registry.rows}

    for dept, sc_row in scorecard.items():
        if dept not in by_dept:
            continue  # departments with no registry-side rows in this window
        assert sc_row["total_cost_usd"] == round(by_dept[dept]["ai_spend"], 6)
        assert sc_row["pruning_saved_usd"] == round(by_dept[dept]["pruning_savings"], 6)
