from core.auditor import _build_rationale


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
