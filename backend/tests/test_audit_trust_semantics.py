from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.auditor import _build_rationale, _extract_cost_usd
from core.budget import effective_budget_context
from database.models import Base, DepartmentBudget


BASE_CONTEXT = {
    "budget_spent_usd": 7.5,
    "budget_cap_usd": 10.0,
    "budget_used_pct": 75.0,
}


def test_explicit_tier_instruction_is_not_described_as_supervisor_override():
    rationale = _build_rationale(
        event_type="ROUTING",
        routing_decision="TIER_OVERRIDE",
        routing_reason="Explicit tier tag used — routed directly to Scout (Tier 1)",
        model_tier="Scout",
        department="Sales",
        matched_keywords=[],
        cost_usd=0.001,
        context=BASE_CONTEXT,
    )

    assert "MODEL TIER OVERRIDE" in rationale
    assert "No supervisor budget override occurred" in rationale
    assert "human supervisor has manually cleared" not in rationale


def test_true_budget_override_is_described_as_human_supervisor_action():
    rationale = _build_rationale(
        event_type="BUDGET",
        routing_decision="BUDGET_OVERRIDE",
        routing_reason="Human supervisor granted a department budget throttle override",
        model_tier=None,
        department="Sales",
        matched_keywords=[],
        cost_usd=0.0,
        context=BASE_CONTEXT,
    )

    assert "SUPERVISOR OVERRIDE GRANTED" in rationale
    assert "human supervisor has manually cleared" in rationale


def test_workspace_budget_context_does_not_leak_global_department_spend():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add_all([
            DepartmentBudget(
                department="Engineering",
                monthly_cap_usd=10.0,
                current_spend_usd=5.0,
            ),
            DepartmentBudget(
                department="WORKSPACE123:Engineering",
                monthly_cap_usd=10.0,
                current_spend_usd=0.0,
            ),
        ])
        db.commit()

        context = effective_budget_context(db, "WORKSPACE123:Engineering")

        assert context["budget_spent_usd"] == 0.0
        assert context["budget_used_pct"] == 0.0
    finally:
        db.close()


def test_legacy_budget_event_does_not_report_budget_snapshot_as_call_cost():
    event = SimpleNamespace(
        event_type="BUDGET",
        cost_usd=None,
        decision_outcome="Budget throttle override granted",
        rationale="Budget remains at 5.3% ($0.5322 spent).",
    )

    assert _extract_cost_usd(event) == 0.0
