"""
tests/test_route_budget_context_dedup.py — api/routes_router.py's
control-mode path used to call effective_budget_context() three times
per request (a full ledger recompute each time). Found via a real,
measured live-latency investigation this session (a synthetic-mode
/api/route call, with the real model call skipped, still took 4-8+
seconds even against a small workspace). Deduped to one pre-commit call
(reused for the raw-payload-logging flags, which are department CONFIG
and don't need post-commit freshness) plus a zero-query read straight
off the already-synced DepartmentBudget row for the response stats
(which DO need post-commit freshness, and get it for free from
sync_one_budget_from_ledger() already having run).

This test proves both things a naive "just reuse whatever's cheap"
fix could get wrong: the response numbers are still CORRECT (include
this request's own cost, not stale pre-commit numbers), and the query
count actually dropped.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.budget as budget_module
from api.routes_router import RouteRequest, route_payload
from database.db import Base
from database.models import DepartmentBudget, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_response_budget_stats_reflect_this_requests_own_cost():
    db = _session()
    db.add(DepartmentBudget(department="Support", monthly_cap_usd=10.0, current_spend_usd=0.0))
    # Pre-existing spend this request's own cost must be added on top of
    db.add(TokenTransaction(
        department="Support", model_tier="Scout", input_tokens=10, output_tokens=10,
        cost_usd=2.0, timestamp=datetime.utcnow() - timedelta(hours=1),
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        workspace_id="default",
    ))
    db.commit()

    response = route_payload(RouteRequest(
        text="Summarize this routine support request.",
        department="Support",
        agent_name="Support Summary Agent",
        source_platform="Salesforce",
        synthetic_simulation=True,
    ), db)

    tx = db.query(TokenTransaction).order_by(TokenTransaction.id.desc()).first()
    budget = db.query(DepartmentBudget).filter_by(department="Support").first()

    # The response's budget numbers must include THIS request's own cost,
    # not just the $2.00 that existed before it ran.
    expected_spend = round(2.0 + tx.cost_usd, 6)
    assert round(budget.current_spend_usd, 6) == expected_spend
    assert round(response.budget_used_pct, 1) == round(expected_spend / 10.0 * 100, 1)
    assert round(response.budget_remaining_usd, 4) == round(10.0 - expected_spend, 4)


def test_only_one_full_budget_ledger_recompute_per_request():
    # recomputed_department_spend() is the expensive part of
    # effective_budget_context()/sync_one_budget_from_ledger() -- it calls
    # project_activity_reporting(), a full multi-query aggregation scan.
    # Before this session's fix, control-mode routing called into that
    # recompute 3 times per request (once each via effective_budget_context()
    # at the raw-payload-logging site and the response-stats site, on top of
    # the one still needed pre-commit for the routing decision, plus
    # sync_one_budget_from_ledger()'s own call after the transaction
    # commits). After the fix, only 2 real recomputes remain: the pre-commit
    # one for the routing decision, and sync_one_budget_from_ledger()'s
    # post-commit one -- the response now reads the freshly-synced budget
    # row directly instead of triggering a 3rd independent recompute.
    db = _session()
    db.add(DepartmentBudget(department="Support", monthly_cap_usd=10.0, current_spend_usd=0.0))
    db.commit()

    with patch.object(
        budget_module, "recomputed_department_spend", wraps=budget_module.recomputed_department_spend
    ) as spy:
        route_payload(RouteRequest(
            text="Summarize this routine support request.",
            department="Support",
            agent_name="Support Summary Agent",
            source_platform="Salesforce",
            synthetic_simulation=True,
        ), db)

    assert spy.call_count <= 2, (
        f"expected at most 2 full ledger recomputes per control-mode request, got {spy.call_count}"
    )
