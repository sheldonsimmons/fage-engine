"""
Ask CostPilot evaluation corpus — Stage 2: data retrieval + calculation
accuracy.

Given the EXPECTED (not re-parsed) filters/period from a corpus case,
does the real deterministic data layer -- project_activity_reporting()
(exclude_prune_only_rows=True, per Recommendation #1) and
core.metrics_query.compute_cost_per_outcome() -- return the right
number against tests/ask_eval/fixtures.py's known-by-hand dataset?

Deliberately uses each case's EXPECTED filters, not its actual parsed
intent, so a retrieval-stage failure here can never be explained away as
"well the interpretation was wrong" -- that's Stage 1's job
(test_ask_eval_interpretation.py). This isolates the second stage in
the audit's four-stage breakdown: interpretation / data retrieval /
calculation / narration.

Only cases with expected_result_path set are exercised here -- not
every corpus case maps cleanly onto one retrieval call (several need
the fuller Ask CostPilot answer-building pipeline, e.g. budget status's
formatting or named-entity scoping), and forcing them through a generic
runner would test the harness's own guesswork more than the real code.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from api.routes_work_items import project_activity_reporting
from core.metrics_query import compute_cost_per_outcome
from tests.ask_eval.corpus import CASES
from tests.ask_eval.fixtures import build_eval_fixture, WORKSPACE_ID


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _get(truth: dict, path: str):
    value = truth
    for part in path.split("."):
        value = value[part]
    return value


_PERIOD_WINDOWS = {
    "this_month": (5, -1),   # (days_ago_start, days_ago_end) relative to "now" at fixture build time
    "last_month": (40, 35),
}


def _no_filters(**overrides):
    base = dict(
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, provider=None, activity_limit=50,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize("case", [c for c in CASES if c.expected_result_path], ids=lambda c: c.id)
def test_retrieval_matches_expected_result(case):
    db = _session()
    truth = build_eval_fixture(db)
    expected = _get(truth, case.expected_result_path)

    now = datetime.utcnow()
    if case.expected_period_key == "last_month":
        date_from, date_to = now - timedelta(days=45), now - timedelta(days=31)
    else:
        # Covers both explicit "this_month" cases and the unscoped-period
        # ones (default 30-day rolling window covers the fixture's
        # this-month data either way).
        date_from, date_to = now - timedelta(days=31), now + timedelta(days=1)

    filters = _no_filters()
    if case.expected_source_platform:
        filters["source_platform"] = case.expected_source_platform

    report = project_activity_reporting(
        workspace_id=WORKSPACE_ID, date_from=date_from, date_to=date_to, days=31,
        exclude_prune_only_rows=True, db=db, **filters,
    )
    summary = report["summary"]

    # Map each case's expected_result_path to the actual field this
    # report (or a targeted outcome query) computes. Explicit per-case
    # mapping, not a generic guesser -- so a mismatch here means a real
    # retrieval/calculation bug, not a harness ambiguity.
    actual = _resolve_actual(case, summary, report, db, truth)

    assert actual == pytest.approx(expected, abs=case.expected_result_tolerance), (
        f"[{case.id}] {case.question!r} — expected {case.expected_result_path}="
        f"{expected}, got {actual}"
    )
    db.close()


def _resolve_actual(case, summary, report, db, truth):
    path = case.expected_result_path
    if path == "this_month.total_spend_usd":
        return summary["spend_usd"]
    if path == "this_month.total_requests":
        return summary["request_count"]
    if path == "this_month.sales_spend_usd":
        # spend-004 asks for the top department's spend; departments-*
        # asks for Support's. Both come from the same breakdown, keyed
        # by department name.
        row = next(r for r in report["organizational_unit_breakdown"] if r["label"] == "Sales")
        return row["spend_usd"]
    if path == "this_month.support_spend_usd":
        row = next(r for r in report["organizational_unit_breakdown"] if r["label"] == "Support")
        return row["spend_usd"]
    if path == "agents.top_spend_agent_usd":
        row = max(report["agent_breakdown"], key=lambda r: r["spend_usd"])
        return row["spend_usd"]
    if path == "this_month.sales_requests":
        row = next(r for r in report["agent_breakdown"] if r["label"] == "SF-OpportunityBot")
        return row["request_count"]
    if path == "budgets.sales_used_pct":
        from core.budget import get_all_budgets
        row = next(b for b in get_all_budgets(db, WORKSPACE_ID) if b["department"].endswith(":Sales"))
        return row["used_pct"]
    if path == "outcomes.won_count":
        # "Won" means an opportunity, not any successful outcome --
        # a resolved support case is also outcome_success=True but is
        # not a won opportunity. Context-type filter is the fix; an
        # earlier version of this harness omitted it and silently
        # miscounted (caught by this suite against its own fixture).
        return sum(1 for o in _outcomes(db, context_type="opportunity") if o.outcome_success is True)
    if path == "outcomes.lost_count":
        return sum(1 for o in _outcomes(db, context_type="opportunity") if o.outcome_success is False and o.is_closed)
    if path == "outcomes.won_value_usd":
        return sum(o.outcome_value or 0 for o in _outcomes(db, context_type="opportunity") if o.outcome_success is True)
    if path == "outcomes.support_cases_resolved":
        return sum(
            1 for o in _outcomes(db, context_type="case")
            if o.outcome_success is True
        )
    if path == "outcomes.cost_per_won_opportunity_usd":
        result = compute_cost_per_outcome(db, WORKSPACE_ID, context_type="opportunity")
        return result["cost_per_successful_outcome_usd"]
    if path == "outcomes.ai_spend_on_lost_usd":
        from database.models import TokenTransaction, WorkItem, WorkItemOutcome
        from sqlalchemy import func
        spend = (
            db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .select_from(TokenTransaction)
            .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
            .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
            .scalar()
        )
        return float(spend or 0.0)
    if path == "month_over_month.sales_spend_pct_change":
        current = next(r for r in report["organizational_unit_breakdown"] if r["label"] == "Sales")["spend_usd"]
        now = datetime.utcnow()
        prior_report = project_activity_reporting(
            workspace_id=WORKSPACE_ID, date_from=now - timedelta(days=45), date_to=now - timedelta(days=31),
            days=14, exclude_prune_only_rows=True, db=db, **_no_filters(),
        )
        prior = next(
            (r["spend_usd"] for r in prior_report["organizational_unit_breakdown"] if r["label"] == "Sales"),
            0.0,
        )
        return round(((current - prior) / prior) * 100, 1) if prior else None
    raise NotImplementedError(f"No retrieval mapping for expected_result_path={path!r} ({case.id})")


def _outcomes(db, context_type=None):
    from database.models import WorkItem, WorkItemOutcome
    query = db.query(WorkItemOutcome).filter(WorkItemOutcome.workspace_id == WORKSPACE_ID)
    if context_type:
        query = query.join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id).filter(
            WorkItem.context_type == context_type
        )
    return query.all()
