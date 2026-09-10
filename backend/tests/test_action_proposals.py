"""
tests/test_action_proposals.py — first coverage for the Action Proposals
module (core/action_proposals.py, api/routes_action_proposals.py), the
Simulation slice (core.budget.project_department_spend,
simulate_budget_cap_change, simulation_result on a proposal), and the
Measurement slice (core.action_proposals.measure_budget_cap_outcome,
measure_budget_cap_outcome tool) added on top of it.

None of this module had any test coverage before this file.
"""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base
from database.models import ActionProposal, DepartmentBudget, TokenTransaction

from core.budget import project_department_spend
from core.action_proposals import create_proposal, measure_budget_cap_outcome, serialize_proposal
from api.ask_costpilot_tools import (
    run_measure_budget_cap_outcome,
    run_propose_budget_cap_change,
    run_simulate_budget_cap_change,
)
from api.routes_action_proposals import confirm_action, reject_action


def _session():
    """
    Real in-memory SQLite session, StaticPool (not the plain single-URL
    engine) because run_propose_budget_cap_change/run_simulate_budget_cap_change
    both open a fresh connection via get_all_budgets -> sync_current_spend_from_ledger
    -> recomputed_department_spend, which must land on the same in-memory
    database as the fixture rows below -- StaticPool is what makes every
    connection from this engine share one underlying SQLite database
    instead of each getting its own empty one.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed_department(db, department, workspace_id, cap_usd, spend_usd, days_of_spend=5, period_start=None):
    """
    Seed a DepartmentBudget row plus real TokenTransaction rows summing to
    spend_usd. current_spend_usd is NOT set directly on the budget row --
    get_all_budgets() always recomputes it from the real ledger via
    sync_current_spend_from_ledger (see that function's docstring: a
    separately-tracked counter is exactly what was found to drift in
    production), so a fixture that only sets current_spend_usd directly
    would be silently overwritten back to whatever the ledger says (zero,
    with no transactions) the moment any of the code under test runs.
    """
    period_start = period_start or datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    db.add(DepartmentBudget(
        department=f"{workspace_id}:{department}", monthly_cap_usd=cap_usd, current_spend_usd=0.0,
        period_start=period_start, workspace_id=workspace_id,
    ))
    per_day = round(spend_usd / days_of_spend, 6) if days_of_spend else 0
    for i in range(days_of_spend):
        db.add(TokenTransaction(
            department=f"{workspace_id}:{department}", model_tier="Analyst", input_tokens=100, output_tokens=50,
            cost_usd=per_day, timestamp=datetime.utcnow() - timedelta(days=i),
            workspace_id=workspace_id, is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        ))
    db.commit()


# ── project_department_spend: pure math, no DB ──────────────────────────

def test_project_department_spend_will_exceed_with_crossover_date():
    now = datetime(2026, 9, 10)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(
        current_spend_usd=300.0, period_start=period_start, new_cap_usd=500.0, now=now,
    )
    # 300 over 9 elapsed days = ~33.33/day; Sep 10 -> Oct 1 is 21 days
    # remaining -> projected ~1000, comfortably over the 500 cap.
    assert result["days_elapsed"] == 9
    assert result["days_remaining_in_period"] == 21
    assert result["daily_rate_usd"] == 33.3333
    assert result["will_exceed_cap"] is True
    assert result["projected_exceed_date"] is not None
    # Crossover happens before period end, so the exceed date must fall
    # strictly between now and the start of next month.
    exceed_date = datetime.fromisoformat(result["projected_exceed_date"])
    assert now.date() < exceed_date.date() < datetime(2026, 10, 1).date()


def test_project_department_spend_will_not_exceed():
    now = datetime(2026, 9, 10)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(
        current_spend_usd=50.0, period_start=period_start, new_cap_usd=1000.0, now=now,
    )
    assert result["will_exceed_cap"] is False
    assert result["projected_exceed_date"] is None
    assert result["headroom_usd"] > 0


def test_project_department_spend_zero_spend_never_exceeds():
    now = datetime(2026, 9, 15)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(
        current_spend_usd=0.0, period_start=period_start, new_cap_usd=100.0, now=now,
    )
    assert result["daily_rate_usd"] == 0
    assert result["projected_period_end_spend_usd"] == 0
    assert result["will_exceed_cap"] is False


def test_project_department_spend_first_day_of_month_no_div_by_zero():
    now = datetime(2026, 9, 1)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(
        current_spend_usd=25.0, period_start=period_start, new_cap_usd=1000.0, now=now,
    )
    assert result["days_elapsed"] == 1
    assert result["daily_rate_usd"] == 25.0


def test_project_department_spend_cap_already_exceeded_today():
    now = datetime(2026, 9, 10)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(
        current_spend_usd=600.0, period_start=period_start, new_cap_usd=500.0, now=now,
    )
    assert result["will_exceed_cap"] is True
    assert result["projected_exceed_date"] == now.date().isoformat()


def test_project_department_spend_without_new_cap_has_no_cap_fields():
    now = datetime(2026, 9, 10)
    period_start = datetime(2026, 9, 1)
    result = project_department_spend(current_spend_usd=100.0, period_start=period_start, now=now)
    assert "will_exceed_cap" not in result
    assert "proposed_cap_usd" not in result
    assert result["projected_period_end_spend_usd"] > 0


# ── create_proposal / serialize_proposal round-trip simulation_result ───

def test_create_proposal_round_trips_simulation_result():
    db = _session()
    simulation = {"current_period_spend_usd": 100.0, "will_exceed_cap": True}
    proposal = create_proposal(
        db, workspace_id="WS-1", department="WS-1:Support",
        action_type="BUDGET_CAP_SET", target_type="budget_department", target_id="WS-1:Support",
        current_value={"monthly_cap_usd": 1000.0}, proposed_value={"new_cap_usd": 50.0},
        reason="test", estimated_impact={"label": "Estimated", "monthly_cap_delta_usd": -950.0},
        simulation_result=simulation, risk_level="medium", required_permission="manage_budgets",
        user_id=None,
    )
    serialized = serialize_proposal(proposal)
    assert serialized["simulation_result"] == simulation


def test_create_proposal_without_simulation_result_is_none():
    db = _session()
    proposal = create_proposal(
        db, workspace_id="WS-1", department="WS-1:Support",
        action_type="BUDGET_CAP_SET", target_type="budget_department", target_id="WS-1:Support",
        current_value={"monthly_cap_usd": 1000.0}, proposed_value={"new_cap_usd": 50.0},
        reason="test", estimated_impact=None,
        risk_level="low", required_permission="manage_budgets", user_id=None,
    )
    assert serialize_proposal(proposal)["simulation_result"] is None


# ── run_simulate_budget_cap_change: read-only, no proposal created ──────

def test_run_simulate_budget_cap_change_returns_projection_and_creates_nothing():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=100.0)

    from database.models import ActionProposal
    result = run_simulate_budget_cap_change(db, "WS-1", "Support", 50.0)

    assert result["found"] is True
    assert result["current_cap_usd"] == 1000.0
    assert result["current_period_spend_usd"] == 100.0
    assert result["will_exceed_cap"] is True
    assert db.query(ActionProposal).count() == 0


def test_run_simulate_budget_cap_change_not_found():
    db = _session()
    result = run_simulate_budget_cap_change(db, "WS-1", "Nonexistent", 50.0)
    assert result["found"] is False


# ── run_propose_budget_cap_change: real projection attached to a real proposal ──

def test_run_propose_budget_cap_change_attaches_real_simulation():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=100.0)

    result = run_propose_budget_cap_change(db, "WS-1", "Support", 50.0, "cut spend")
    assert result["found"] is True
    proposal = result["proposal"]
    assert proposal["simulation_result"] is not None
    assert proposal["simulation_result"]["will_exceed_cap"] is True
    # The same numbers a standalone "what if" simulation would produce for
    # the identical department/cap pair -- both share _simulate_cap_change,
    # so they must never disagree.
    what_if = run_simulate_budget_cap_change(db, "WS-1", "Support", 50.0)
    assert proposal["simulation_result"]["projected_period_end_spend_usd"] == what_if["projected_period_end_spend_usd"]


# ── Confirm/reject regression: unaffected by the new column ─────────────

def test_confirm_flow_still_works_with_simulation_result_present():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=100.0)
    result = run_propose_budget_cap_change(db, "WS-1", "Support", 2000.0, "raise cap")
    proposal_id = result["proposal"]["id"]

    response = confirm_action(proposal_id, db=db, authorization=None)
    assert response["proposal"]["status"] == "executed"

    from database.models import DepartmentBudget
    budget = db.query(DepartmentBudget).filter_by(department="WS-1:Support").first()
    assert budget.monthly_cap_usd == 2000.0


def test_reject_flow_still_works_with_simulation_result_present():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=100.0)
    result = run_propose_budget_cap_change(db, "WS-1", "Support", 2000.0, "raise cap")
    proposal_id = result["proposal"]["id"]

    response = reject_action(proposal_id, db=db, authorization=None)
    assert response["status"] == "rejected"

    from database.models import DepartmentBudget
    budget = db.query(DepartmentBudget).filter_by(department="WS-1:Support").first()
    assert budget.monthly_cap_usd == 1000.0  # unchanged -- rejection never executes


# ── measure_budget_cap_outcome: post-action measurement ──────────────────

def _make_executed_proposal(db, department, workspace_id, resolved_days_ago, simulation_result=None,
                             current_cap=1000.0, new_cap=500.0):
    """
    Builds a real ActionProposal via create_proposal (so serialization/JSON
    handling is exercised the same as production), then directly overrides
    status/resolved_at -- confirm_action() always stamps resolved_at as
    "now", with no way to backdate it through the route, and backdating is
    exactly what these tests need to simulate "N days have passed since
    the change."
    """
    proposal = create_proposal(
        db, workspace_id=workspace_id, department=f"{workspace_id}:{department}",
        action_type="BUDGET_CAP_SET", target_type="budget_department",
        target_id=f"{workspace_id}:{department}",
        current_value={"monthly_cap_usd": current_cap}, proposed_value={"new_cap_usd": new_cap},
        reason="test change", estimated_impact=None, simulation_result=simulation_result,
        risk_level="low", required_permission="manage_budgets", user_id=None,
    )
    proposal.status = "executed"
    proposal.resolved_at = datetime.utcnow() - timedelta(days=resolved_days_ago)
    db.commit()
    return proposal


def test_measure_budget_cap_outcome_no_executed_proposal():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=0.0, days_of_spend=0)
    result = measure_budget_cap_outcome(db, "WS-1", "WS-1:Support")
    assert result["found"] is False


def test_measure_budget_cap_outcome_too_soon():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=0.0, days_of_spend=0)
    proposal = _make_executed_proposal(db, "Support", "WS-1", resolved_days_ago=0)
    proposal.resolved_at = datetime.utcnow() - timedelta(hours=2)
    db.commit()

    result = measure_budget_cap_outcome(db, "WS-1", "WS-1:Support")
    assert result["found"] is True
    assert result["too_soon"] is True


def test_measure_budget_cap_outcome_computes_actual_vs_projected_and_slowed_trend():
    db = _session()
    db.add(DepartmentBudget(
        department="WS-1:Support", monthly_cap_usd=1000.0, current_spend_usd=0.0,
        period_start=datetime.utcnow().replace(day=1), workspace_id="WS-1",
    ))
    # Spend from BEFORE the change (15-20 days ago) -- must be excluded from
    # the measurement window even though it's real ledger data, or the
    # date_from filtering in recomputed_department_spend isn't actually
    # being exercised by this test.
    for i in range(15, 20):
        db.add(TokenTransaction(
            department="WS-1:Support", model_tier="Analyst", input_tokens=100, output_tokens=50,
            cost_usd=100.0, timestamp=datetime.utcnow() - timedelta(days=i),
            workspace_id="WS-1", is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        ))
    # Spend from AFTER the change (the last 10 days) -- $50 total over 10
    # days = $5/day actual, versus a $10/day projected rate -> a 50% slowdown.
    for i in range(10):
        db.add(TokenTransaction(
            department="WS-1:Support", model_tier="Analyst", input_tokens=100, output_tokens=50,
            cost_usd=5.0, timestamp=datetime.utcnow() - timedelta(days=i),
            workspace_id="WS-1", is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        ))
    db.commit()

    _make_executed_proposal(
        db, "Support", "WS-1", resolved_days_ago=10,
        simulation_result={"daily_rate_usd": 10.0}, current_cap=1000.0, new_cap=500.0,
    )

    result = measure_budget_cap_outcome(db, "WS-1", "WS-1:Support")
    assert result["found"] is True
    assert result["too_soon"] is False
    assert result["actual_spend_since_change_usd"] == 50.0
    assert result["actual_daily_rate_usd"] == 5.0
    assert result["projected_daily_rate_usd"] == 10.0
    assert result["rate_change_pct"] == -50.0
    assert result["trend"] == "slowed"


def test_measure_budget_cap_outcome_without_simulation_result_still_returns_actuals():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=30.0, days_of_spend=5)
    _make_executed_proposal(db, "Support", "WS-1", resolved_days_ago=5, simulation_result=None)

    result = measure_budget_cap_outcome(db, "WS-1", "WS-1:Support")
    assert result["found"] is True
    assert result["too_soon"] is False
    assert "actual_daily_rate_usd" in result
    assert "projected_daily_rate_usd" not in result
    assert "trend" not in result


def test_run_measure_budget_cap_outcome_resolves_department_by_flexible_name():
    db = _session()
    _seed_department(db, "Support", "WS-1", cap_usd=1000.0, spend_usd=0.0, days_of_spend=0)
    _make_executed_proposal(
        db, "Support", "WS-1", resolved_days_ago=5, simulation_result={"daily_rate_usd": 1.0},
    )
    result = run_measure_budget_cap_outcome(db, "WS-1", "Support")
    assert result["found"] is True
    assert result["department"] == "Support"


def test_run_measure_budget_cap_outcome_department_not_found():
    db = _session()
    result = run_measure_budget_cap_outcome(db, "WS-1", "Nonexistent")
    assert result["found"] is False
