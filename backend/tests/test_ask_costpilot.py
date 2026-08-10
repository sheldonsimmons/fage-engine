import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from core.ask_costpilot_contracts import (
    canonical_ask_intent,
    validate_ask_answer_contract,
)
from core.analytics_metrics import metric_definition
from core.analytics_periods import comparison_coverage, comparison_plan, resolve_primary_period
from core.analytics_drivers import change_decomposition, dimension_contributors

from api.routes_efficiency import (
    _ask_agent_final_payload,
    _ask_agent_validate_answer,
    _ask_conversation_text,
    _ask_evidence,
    _ask_extract_numbers,
    _ask_fallback_intent,
    _ask_grounded_narrative,
    _ask_has_explicit_named_subject,
    _ask_intent,
    _ask_is_follow_up,
    _ask_named_entity,
    _ask_named_entity_ambiguity,
    _ask_narration_causal_claims,
    _ask_narration_unverified_numbers,
    _ask_period_bounds,
    _ask_rank,
    _ask_reporting_filters,
    _ask_suggested_questions,
    _resolve_ask_intent,
    AskCostPilotMessage,
    AskCostPilotRequest,
    AskCostPilotContext,
    AskCostPilotScreenContext,
    ask_costpilot,
    _validated_ask_intent,
)
from core.analytics_metrics import metric_for_keywords


def _controlled_report():
    return {
        "summary": {
            "request_count": 20,
            "total_tokens": 7000,
            "input_tokens": 5000,
            "output_tokens": 2000,
            "tokens_saved": 1000,
            "spend_usd": 2.0,
            "live_count": 12,
            "simulation_count": 8,
            "people_count": 2,
            "agent_count": 1,
        },
        "period": {
            "date_from": "2026-07-01T00:00:00",
            "date_to": "2026-08-01T00:00:00",
        },
        "filters": {},
        "context_label_plural": "Accounts",
        "people_breakdown": [
            {
                "id": "USER-SHELDON",
                "label": "Sheldon Simmons",
                "request_count": 12,
                "total_tokens": 4321,
                "input_tokens": 3000,
                "output_tokens": 1321,
                "tokens_saved": 600,
                "spend_usd": 0.19,
                "live_count": 12,
                "simulation_count": 0,
            },
            {
                "id": "USER-MARCUS",
                "label": "Marcus Reed",
                "request_count": 8,
                "total_tokens": 2679,
                "input_tokens": 2000,
                "output_tokens": 679,
                "tokens_saved": 400,
                "spend_usd": 1.81,
                "live_count": 0,
                "simulation_count": 8,
            },
        ],
        "agent_breakdown": [],
        "organizational_unit_breakdown": [],
        "project_breakdown": [
            {
                "id": "ACCOUNT-ACME",
                "label": "ACME Test",
                "request_count": 6,
                "total_tokens": 2400,
                "input_tokens": 1800,
                "output_tokens": 600,
                "tokens_saved": 300,
                "spend_usd": 0.42,
                "live_count": 6,
                "simulation_count": 0,
            },
        ],
        "source_platform_breakdown": [],
        "model_breakdown": [],
        "activities": [],
        "activity_count": 0,
        "measurement_note": "Controlled attribution fixture.",
    }


def _run_with_controlled_report(request, report=None, db=None):
    from api import routes_work_items

    calls = []
    original = routes_work_items.project_activity_reporting
    original_narration = os.environ.get("ASK_COSTPILOT_NARRATION_ENABLED")
    os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = "false"

    def fake_reporting(**kwargs):
        calls.append(kwargs)
        if callable(report):
            return report(**kwargs)
        if isinstance(report, list):
            return report[len(calls) - 1]
        return report or _controlled_report()

    routes_work_items.project_activity_reporting = fake_reporting
    try:
        return ask_costpilot(request, db=db), calls
    finally:
        routes_work_items.project_activity_reporting = original
        if original_narration is None:
            os.environ.pop("ASK_COSTPILOT_NARRATION_ENABLED", None)
        else:
            os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = original_narration


def test_employee_token_question_becomes_person_ranking_for_last_week():
    intent = _ask_intent(
        "Who had the highest token spend last week?",
        default_days=30,
    )

    assert intent["intent"] == "ranking"
    assert intent["entity"] == "person"
    assert intent["metric"] == "total_tokens"
    assert intent["days"] == 7
    assert intent["period_key"] == "last_week"
    assert intent["direction"] == "desc"
    assert intent["result_limit"] == 5


def test_highest_token_spend_yesterday_is_an_exact_person_token_ranking():
    intent = _ask_intent(
        "who had the highest token spend yesterday?",
        default_days=30,
    )

    assert intent["intent"] == "ranking"
    assert intent["entity"] == "person"
    assert intent["metric"] == "total_tokens"
    assert intent["period_key"] == "yesterday"


def test_last_year_on_this_date_means_one_calendar_day_not_the_prior_year():
    request = AskCostPilotRequest(
        question="What was my AI spend last year on this date?",
    )
    intent = _ask_intent(request.question, request.days)
    start, end = _ask_period_bounds(request, intent)

    assert intent["metric"] == "spend_usd"
    assert intent["period_key"] == "same_date_last_year"
    assert end - start == timedelta(days=1)
    assert start.year == datetime.utcnow().year - 1


def test_all_time_person_spend_ranking_does_not_use_default_window():
    intent = _ask_intent(
        "Who has the most AI spend of all time?",
        default_days=30,
    )

    assert intent["intent"] == "ranking"
    assert intent["entity"] == "person"
    assert intent["metric"] == "spend_usd"
    assert intent["period_key"] == "all_time"


def test_all_time_endpoint_uses_configured_collection_boundaries(monkeypatch):
    from api import routes_efficiency

    monkeypatch.setattr(
        routes_efficiency,
        "workspace_analytics_settings",
        lambda db, workspace_id: SimpleNamespace(
            collection_started_at=datetime(2024, 8, 3),
            latest_complete_at=datetime(2026, 8, 4),
        ),
    )
    response, calls = _run_with_controlled_report(AskCostPilotRequest(
        question="Who has the most AI spend of all time?",
        workspace_id="SIM-HISTORICAL-2Y",
    ))

    assert calls[0]["date_from"] == datetime(2024, 8, 3)
    assert calls[0]["date_to"] == datetime(2026, 8, 4)
    assert response["assistant_mode"] == "deterministic_period_contract"
    assert response["interpreted_intent"]["period_key"] == "all_time"


def test_savings_question_uses_read_only_optimization_intent():
    intent = _ask_intent(
        "Give me advice on how we can save money.",
        default_days=30,
    )

    assert intent["intent"] == "optimization"
    assert intent["days"] == 30


def test_around_this_time_last_year_uses_same_calendar_period_comparison():
    intent = _ask_intent(
        "What was my token usage around this time last year and compare the two?",
        default_days=30,
    )

    assert intent["intent"] == "comparison"
    assert intent["metric"] == "total_tokens"
    assert intent["period_key"] is None
    assert intent["comparison_key"] == "same_period_previous_year"


def test_exact_change_driver_question_never_falls_back_to_overview():
    intent = _ask_intent(
        "Why did our AI spend or token usage change?",
        default_days=30,
    )
    canonical = canonical_ask_intent(
        "Why did our AI spend or token usage change?"
    )

    assert intent["intent"] == "change_drivers"
    assert canonical["name"] == "token_change_drivers"
    assert canonical["intent"] == "change_drivers"
    assert canonical["metric"] == "total_tokens"


def test_change_decomposition_reconciles_to_total_change():
    result = change_decomposition(
        {"request_count": 20, "total_tokens": 8000},
        {"request_count": 10, "total_tokens": 3000},
        "total_tokens",
    )

    assert result["absolute_change"] == 5000
    assert round(result["request_volume_effect"] + result["per_request_effect"], 6) == 5000
    assert result["method"] == "two-factor Shapley decomposition"


def test_dimension_contributors_rank_absolute_measured_deltas():
    rows = dimension_contributors(
        [
            {"id": 1, "label": "Agent A", "total_tokens": 5000},
            {"id": 2, "label": "Agent B", "total_tokens": 1000},
        ],
        [
            {"id": 1, "label": "Agent A", "total_tokens": 1000},
            {"id": 2, "label": "Agent B", "total_tokens": 2000},
        ],
        "total_tokens",
        "agent",
        total_change=3000,
    )

    assert rows[0]["label"] == "Agent A"
    assert rows[0]["absolute_change"] == 4000
    assert rows[1]["absolute_change"] == -1000


def test_endpoint_answers_change_driver_question_with_deterministic_decomposition():
    current = deepcopy(_controlled_report())
    current["summary"].update({
        "request_count": 20,
        "total_tokens": 8000,
        "spend_usd": 3.0,
    })
    current["agent_breakdown"] = [
        {"id": 1, "label": "Support Agent", "request_count": 12, "total_tokens": 6000, "spend_usd": 2.0}
    ]
    prior = deepcopy(_controlled_report())
    prior["summary"].update({
        "request_count": 10,
        "total_tokens": 3000,
        "spend_usd": 1.0,
    })
    prior["agent_breakdown"] = [
        {"id": 1, "label": "Support Agent", "request_count": 5, "total_tokens": 1000, "spend_usd": 0.4}
    ]

    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="Why did our AI spend or token usage change?",
            days=30,
            timezone_name="America/Chicago",
        ),
        report=[current, prior],
    )

    assert len(calls) == 2
    assert response["intent"] == "change_drivers"
    assert response["title"] == "Tokens change drivers"
    assert response["contract_status"] == "passed"
    assert response["calculation"]["absolute_change"] == 5000
    assert response["calculation"]["driver_analysis"]["primary_metric"]["method"] == "two-factor Shapley decomposition"
    assert response["calculation"]["driver_analysis"]["contributors"][0]["label"] == "Support Agent"
    assert "measured contributors" in response["answer"].lower()
    assert "Recorded AI spend moved" in response["answer"]


def test_metric_registry_exposes_versioned_token_contract():
    contract = metric_definition("total_tokens")

    assert contract.formula == "SUM(input_tokens + output_tokens)"
    assert contract.source == "token_transactions"
    assert contract.version == "1.0"


def test_temporal_plan_shifts_exact_workspace_period_back_one_year():
    primary = resolve_primary_period(
        period_key=None,
        days=30,
        timezone_name="America/Chicago",
        now=datetime(2026, 8, 3, 15, 0, 0),
    )
    plan = comparison_plan(primary, "same_period_previous_year")
    contract = plan.contract()

    assert contract["primary"]["label"] == "2026-07-04 through 2026-08-03"
    assert contract["comparison"]["label"] == "2025-07-04 through 2025-08-03"
    assert contract["primary"]["timezone_name"] == "America/Chicago"
    assert contract["primary"]["interval"] == "half_open"


def test_month_over_month_uses_calendar_aligned_month_period():
    intent = _ask_intent("Compare this month vs last month", default_days=30)
    primary = resolve_primary_period(
        period_key=intent["period_key"],
        days=intent["days"],
        timezone_name="UTC",
        now=datetime(2026, 8, 3, 12, 0, 0),
    )
    plan = comparison_plan(primary, intent["comparison_key"]).contract()

    assert intent["comparison_key"] == "previous_month"
    assert plan["primary"]["label"] == "2026-08-01 through 2026-08-03"
    assert plan["comparison"]["label"] == "2026-07-01 through 2026-07-03"


def test_quarter_over_quarter_uses_calendar_aligned_quarter_period():
    intent = _ask_intent("What changed quarter over quarter?", default_days=30)

    assert intent["intent"] == "comparison"
    assert intent["comparison_key"] == "previous_quarter"


def test_comparison_coverage_rejects_live_simulator_scope_mismatch():
    coverage = comparison_coverage(
        {"request_count": 10, "live_count": 10, "simulation_count": 0},
        {"request_count": 8, "live_count": 0, "simulation_count": 8},
    )

    assert coverage["status"] == "traffic_scope_mismatch"
    assert coverage["comparable"] is False
    assert coverage["primary_traffic_scope"] == "live"
    assert coverage["comparison_traffic_scope"] == "simulator"


def test_endpoint_compares_same_period_last_year_with_metric_and_coverage_contracts():
    current = deepcopy(_controlled_report())
    current["summary"]["total_tokens"] = 8000
    current["summary"]["request_count"] = 20
    prior = deepcopy(_controlled_report())
    prior["summary"]["total_tokens"] = 5000
    prior["summary"]["request_count"] = 10

    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="Compare our token usage with around this time last year.",
            days=30,
            timezone_name="America/Chicago",
        ),
        report=[current, prior],
    )

    assert len(calls) == 2
    assert calls[0]["date_from"].year == calls[1]["date_from"].year + 1
    assert calls[0]["source_platform"] == calls[1]["source_platform"]
    assert response["contract_status"] == "passed"
    assert response["calculation"]["metric_contract"]["id"] == "total_tokens"
    assert response["calculation"]["comparison_plan"]["mode"] == "same_period_previous_year"
    assert response["calculation"]["absolute_change"] == 3000
    assert response["calculation"]["percent_change"] == 60.0
    assert response["data_provenance"]["coverage"]["status"] == "observed_both_periods"
    assert len(response["evidence"]) == 2


def test_product_question_uses_costpilot_knowledge_intent():
    intent = _ask_intent(
        "How does CostPilot decide which model tier to use?",
        default_days=30,
    )

    assert intent["intent"] == "product"
    assert intent["entity"] == "overview"


def test_budget_question_targets_departments():
    intent = _ask_intent(
        "Which departments are close to the budget limit?",
        default_days=30,
    )

    assert intent["intent"] == "budget"
    assert intent["entity"] == "department"


def test_all_department_budget_question_requests_complete_budget_list():
    intent = _ask_intent(
        "How much budget does each department have?",
        default_days=30,
    )

    assert intent["intent"] == "budget"
    assert intent["entity"] == "department"
    assert intent["budget_scope"] == "all"


def test_natural_budget_status_follow_up_does_not_fall_back_to_overview():
    intent = _ask_intent("Was the change within budget?", default_days=30)

    assert intent["intent"] == "budget"
    assert intent["entity"] == "department"
    assert intent["budget_scope"] == "status"


def test_budget_remaining_question_uses_remaining_view():
    intent = _ask_intent("How much AI budget is left this month?", default_days=30)

    assert intent["intent"] == "budget"
    assert intent["budget_scope"] == "remaining"


def test_budget_forecast_question_uses_forecast_view():
    intent = _ask_intent("Are we on track to exceed our AI budget this month?", default_days=30)

    assert intent["intent"] == "budget"
    assert intent["budget_scope"] == "forecast"


class _BudgetQueryStub:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class _BudgetDbStub:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *_args, **_kwargs):
        return _BudgetQueryStub(self.rows)


def test_budget_forecast_uses_filtered_activity_spend_not_stale_budget_counter():
    report = _controlled_report()
    report["summary"].update({"request_count": 20, "spend_usd": 0.5})
    report["organizational_unit_breakdown"] = [
        {"id": "WORKSPACE-1:Sales", "label": "Sales", "request_count": 12, "spend_usd": 0.4},
        {"id": "WORKSPACE-1:Operations", "label": "Operations", "request_count": 8, "spend_usd": 0.1},
    ]
    db = _BudgetDbStub([
        SimpleNamespace(
            department="WORKSPACE-1:Sales", monthly_cap_usd=100.0,
            current_spend_usd=0.0, throttled=False,
        ),
        SimpleNamespace(
            department="WORKSPACE-1:Operations", monthly_cap_usd=50.0,
            current_spend_usd=0.0, throttled=False,
        ),
    ])

    response, _calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="Are we on track to exceed our AI budget this month?",
            workspace_id="WORKSPACE-1",
        ),
        report=report,
        db=db,
    )

    assert response["intent"] == "budget"
    assert response["data_provenance"]["budget_coverage"]["matched_requests"] == 20
    assert response["data_provenance"]["budget_coverage"]["matched_spend_usd"] == 0.5
    assert response["data_provenance"]["budget_coverage"]["complete"] is True
    assert "projected at $0.00" not in response["answer"]
    assert response["evidence"][0]["detail"].startswith("$0.4000 used")


def test_budget_forecast_refuses_false_zero_when_activity_is_unattributed():
    report = _controlled_report()
    report["summary"].update({"request_count": 20, "spend_usd": 0.5})
    report["organizational_unit_breakdown"] = [
        {"id": "WORKSPACE-1:Unknown", "label": "Unknown", "request_count": 20, "spend_usd": 0.5},
    ]
    db = _BudgetDbStub([
        SimpleNamespace(
            department="WORKSPACE-1:Sales", monthly_cap_usd=100.0,
            current_spend_usd=0.0, throttled=False,
        ),
    ])

    response, _calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="Are we on track to exceed our AI budget this month?",
            workspace_id="WORKSPACE-1",
        ),
        report=report,
        db=db,
    )

    assert "cannot be calculated" in response["answer"]
    assert response["data_provenance"]["budget_coverage"]["matched_requests"] == 0
    assert response["data_provenance"]["budget_coverage"]["unmatched_requests"] == 20


def test_agent_adoption_overview_uses_default_low_usage_threshold():
    intent = _ask_intent(
        "What have we built, and is anyone using it?",
        default_days=30,
    )

    assert intent["intent"] == "agent_adoption"
    assert intent["entity"] == "agent"
    assert intent["usage_status"] == "all"
    assert intent["usage_threshold"] == 10


def test_low_usage_agent_question_accepts_explicit_threshold():
    intent = _ask_intent(
        "Show low usage agents with fewer than 5 requests.",
        default_days=30,
    )

    assert intent["intent"] == "agent_adoption"
    assert intent["usage_status"] == "low"
    assert intent["usage_threshold"] == 5


def test_never_been_used_question_is_exact_agent_adoption_query():
    intent = _ask_intent(
        "Which agents have never been used?",
        default_days=30,
    )

    assert intent["intent"] == "agent_adoption"
    assert intent["entity"] == "agent"
    assert intent["metric"] == "request_count"
    assert intent["usage_status"] == "never"

    resolved, mode = _resolve_ask_intent(
        AskCostPilotRequest(question="Which agents have never been used?")
    )
    assert resolved["intent"] == "agent_adoption"
    assert resolved["usage_status"] == "never"
    assert mode == "canonical_intent"


def test_demo_critical_never_used_paraphrases_share_one_contract():
    questions = (
        "Which agents have never been used?",
        "Show me agents with zero lifetime usage.",
        "Which registered agents have no lifetime activity?",
        "Are there bots nobody has run?",
        "Show unused agents.",
        "Which bots have never executed?",
    )

    for question in questions:
        intent = _ask_intent(question, default_days=30)
        assert intent["canonical_intent"] == "agents_never_used", question
        assert intent["intent"] == "agent_adoption", question
        assert intent["entity"] == "agent", question
        assert intent["usage_status"] == "never", question
        assert "zero lifetime" in intent["interpreted_as"].lower(), question


def test_demo_critical_questions_use_canonical_contracts():
    cases = (
        ("Which agents cost the most this month?", "agent_cost_ranking", "agent", "spend_usd"),
        ("Which department spent the most this month?", "department_spend_ranking", "department", "spend_usd"),
        ("Which model generated the highest cost?", "model_cost_ranking", "model", "spend_usd"),
        ("Why were requests blocked?", "blocked_reasons", "overview", "request_count"),
        ("How much did pruning save?", "pruning_impact", "overview", "tokens_saved"),
        ("Which departments are close to budget?", "budget_alerts", "department", "spend_usd"),
        ("How much has CostPilot saved?", "savings_total", "overview", "spend_usd"),
        ("Compare live activity with simulator traffic.", "source_mix", "overview", "request_count"),
    )

    for question, contract, entity, metric in cases:
        intent = _ask_intent(question, default_days=30)
        assert intent["canonical_intent"] == contract, question
        assert intent["entity"] == entity, question
        assert intent["metric"] == metric, question
        resolved, mode = _resolve_ask_intent(AskCostPilotRequest(question=question))
        assert resolved["canonical_intent"] == contract, question
        assert mode == "canonical_intent", question


def test_never_used_answer_contract_rejects_project_spend_response():
    parsed = {
        **canonical_ask_intent("Which agents have never been used?"),
        "canonical_intent": "agents_never_used",
    }
    wrong_payload = {
        "intent": "overview",
        "entity": "context",
        "metric": "spend_usd",
        "evidence": [{
            "label": "Apex Industrial — Warranty Claim",
            "value": "$0.2376",
            "metric_label": "AI spend",
            "detail": "9 requests",
            "filter_name": "project_id",
        }],
    }

    issues = validate_ask_answer_contract(parsed, wrong_payload)

    assert issues
    assert any("non-agent evidence" in issue for issue in issues)


def test_never_used_answer_contract_accepts_zero_lifetime_agent_rows():
    parsed = {
        **canonical_ask_intent("Which agents have never been used?"),
        "canonical_intent": "agents_never_used",
    }
    payload = {
        "intent": "agent_adoption",
        "entity": "agent",
        "metric": "request_count",
        "evidence": [{
            "label": "Contract Review Agent",
            "value": "0",
            "metric_label": "requests in period",
            "detail": "Never used · Salesforce · Legal · 0 lifetime requests",
            "filter_name": "agent_id",
        }],
    }

    assert validate_ask_answer_contract(parsed, payload) == []


def test_threshold_followup_preserves_prior_adoption_status():
    request = AskCostPilotRequest(
        question="Change threshold to five.",
        days=30,
        context=AskCostPilotContext(
            intent="agent_adoption",
            entity="agent",
            metric="request_count",
            direction="asc",
            days=30,
            result_limit=5,
            usage_status="low",
            usage_threshold=10,
        ),
    )

    intent = _ask_fallback_intent(request)

    assert intent["intent"] == "agent_adoption"
    assert intent["usage_status"] == "low"
    assert intent["usage_threshold"] == 5


def test_pruning_question_uses_exact_pruning_intent():
    intent = _ask_intent(
        "How many tokens did pruning remove?",
        default_days=31,
    )

    assert intent["intent"] == "pruning"
    assert intent["entity"] == "overview"
    assert intent["metric"] == "tokens_saved"
    assert intent["days"] == 31
    assert intent["direction"] == "desc"
    assert intent["result_limit"] == 5


def test_blocked_question_uses_governance_intent():
    intent = _ask_intent(
        "Why were requests blocked?",
        default_days=31,
    )

    assert intent["intent"] == "blocked"
    assert intent["entity"] == "overview"
    assert intent["metric"] == "request_count"


def test_blocked_count_question_is_not_treated_as_generic_request_total():
    intent = _ask_intent(
        "How many requests were blocked this month?",
        default_days=31,
    )

    assert intent["intent"] == "blocked"
    assert intent["entity"] == "overview"
    assert intent["metric"] == "request_count"
    assert intent["period_key"] == "this_month"


def test_latest_risk_events_uses_risk_event_intent():
    intent = _ask_intent(
        "Show the latest risk events.",
        default_days=31,
    )

    assert intent["intent"] == "risk_events"
    assert intent["entity"] == "overview"
    assert intent["metric"] == "request_count"


def test_governance_question_is_not_overwritten_by_conversation_context():
    request = AskCostPilotRequest(
        question="Show the latest risk events.",
        days=31,
        conversation=[
            AskCostPilotMessage(
                role="user",
                content="Which models handled the most requests?",
            ),
            AskCostPilotMessage(
                role="assistant",
                content="Claude Haiku handled the most requests.",
            ),
        ],
    )

    intent = _ask_fallback_intent(request)

    assert intent["intent"] == "risk_events"


def test_rank_and_evidence_are_deterministic_and_drillable():
    rows = [
        {
            "id": "USER-2",
            "label": "Blair",
            "request_count": 5,
            "total_tokens": 900,
            "spend_usd": 1.25,
        },
        {
            "id": "USER-1",
            "label": "Alex",
            "request_count": 8,
            "total_tokens": 1200,
            "spend_usd": 1.25,
        },
    ]

    ranked = _ask_rank(rows, "spend_usd")
    evidence = _ask_evidence(rows, "spend_usd", "user_external_id")

    assert ranked[0]["label"] == "Alex"
    assert evidence[0]["filter_name"] == "user_external_id"
    assert evidence[0]["filter_value"] == "USER-1"
    assert evidence[0]["value"] == "$1.2500"


def test_fewest_token_question_sorts_people_ascending():
    intent = _ask_intent(
        "No. Who had the fewest token usage?",
        default_days=31,
    )
    rows = [
        {"id": "USER-1", "label": "Marcus", "request_count": 3, "total_tokens": 2621},
        {"id": "USER-2", "label": "Avery", "request_count": 2, "total_tokens": 850},
    ]

    ranked = _ask_rank(rows, intent["metric"], intent["direction"])

    assert intent["direction"] == "asc"
    assert ranked[0]["label"] == "Avery"


def test_top_five_question_preserves_requested_result_count():
    intent = _ask_intent(
        "Show me the top 5 token users",
        default_days=31,
    )
    rows = [
        {
            "id": f"USER-{index}",
            "label": f"Person {index}",
            "request_count": index,
            "total_tokens": index * 100,
        }
        for index in range(1, 8)
    ]

    evidence = _ask_evidence(
        rows,
        intent["metric"],
        "user_external_id",
        direction=intent["direction"],
        limit=intent["result_limit"],
    )

    assert intent["result_limit"] == 5
    assert len(evidence) == 5
    assert evidence[0]["label"] == "Person 7"


def test_plural_followup_inherits_prior_token_metric():
    request = AskCostPilotRequest(
        question="Now show me the five lowest users",
        days=31,
        conversation=[
            AskCostPilotMessage(
                role="user",
                content="How many tokens has Sheldon used?",
            ),
            AskCostPilotMessage(
                role="assistant",
                content="Sheldon Simmons used 5,558 tokens.",
            ),
        ],
    )

    intent = _ask_fallback_intent(request)

    assert intent["intent"] == "ranking"
    assert intent["entity"] == "person"
    assert intent["metric"] == "total_tokens"
    assert intent["direction"] == "asc"
    assert intent["result_limit"] == 5


def test_people_ranking_drops_stale_person_scope_but_keeps_department():
    request = AskCostPilotRequest(
        question="Now show me the five lowest users",
        user_external_id="USER-SHELDON",
        charged_unit="Sales",
    )

    filters = _ask_reporting_filters(
        request,
        _ask_fallback_intent(request),
    )

    assert filters["user_external_id"] is None
    assert filters["charged_unit"] == "Sales"


def test_named_subject_starts_fresh_scope_but_keeps_cross_cutting_filters():
    request = AskCostPilotRequest(
        question="Show AI usage for ACME Test.",
        days=31,
        project_id="OLD-PROJECT",
        account_id="OLD-ACCOUNT",
        user_external_id="OLD-USER",
        agent_id=668,
        charged_unit="Sales",
        source_platform="Salesforce",
        model_tier="Scout",
    )

    assert _ask_has_explicit_named_subject(request.question) is True
    filters = _ask_reporting_filters(request, _ask_fallback_intent(request))

    assert filters["project_id"] is None
    assert filters["account_id"] is None
    assert filters["user_external_id"] is None
    assert filters["agent_id"] is None
    assert filters["charged_unit"] is None
    assert filters["source_platform"] == "Salesforce"
    assert filters["model_tier"] == "Scout"


def test_pinned_account_survives_named_subject_in_question_text():
    # A Business Profile page's question naturally names its own account
    # ("top agent for Acme Corp") -- that must not be treated as a stale
    # leftover filter the way an unpinned chat-widget account_id would be.
    request = AskCostPilotRequest(
        question="What agent had the highest spend for Cascade Health Partners?",
        account_id="OUTDEMO-ACCOUNT-CASCADEHEALTHPARTNERS",
        account_id_pinned=True,
        project_id="OLD-PROJECT",
        user_external_id="OLD-USER",
    )

    assert _ask_has_explicit_named_subject(request.question) is True
    filters = _ask_reporting_filters(request, _ask_fallback_intent(request))

    assert filters["account_id"] == "OUTDEMO-ACCOUNT-CASCADEHEALTHPARTNERS"
    # The rest of the stale-scope clearing still applies -- only account_id
    # is exempted when pinned.
    assert filters["project_id"] is None
    assert filters["user_external_id"] is None


def test_unpinned_account_still_cleared_by_named_subject_default_behavior():
    request = AskCostPilotRequest(
        question="Show AI usage for ACME Test.",
        account_id="OLD-ACCOUNT",
    )
    filters = _ask_reporting_filters(request, _ask_fallback_intent(request))
    assert filters["account_id"] is None


def test_named_subject_does_not_inherit_prior_optimization_or_subject():
    request = AskCostPilotRequest(
        question="Show AI usage for ACME Test.",
        days=31,
        context=AskCostPilotContext(
            intent="optimization",
            entity="agent",
            metric="spend_usd",
            direction="desc",
            days=7,
            subject_entity="agent",
            subject_filter_name="agent_id",
            subject_filter_value="668",
        ),
    )

    parsed = _ask_fallback_intent(request)

    assert parsed["intent"] == "overview"
    assert parsed["entity"] == "overview"
    assert parsed.get("subject_filter_name") is None
    assert parsed.get("subject_filter_value") is None


def test_generic_subject_phrase_remains_a_follow_up():
    assert _ask_has_explicit_named_subject("Show activity for this account.") is False
    assert _ask_has_explicit_named_subject("Which agents contributed to that?") is False


def test_independent_question_does_not_inherit_stale_context():
    request = AskCostPilotRequest(
        question="Which account had the highest AI spend?",
        days=30,
        context=AskCostPilotContext(
            intent="ranking",
            entity="agent",
            metric="request_count",
            direction="desc",
            days=7,
            result_limit=5,
            period_key="last_week",
            source_platform="salesforce",
            subject_entity="agent",
            subject_filter_name="agent_id",
            subject_filter_value="668",
        ),
    )

    parsed = _ask_fallback_intent(request)

    assert parsed["intent"] == "ranking"
    assert parsed["entity"] == "context"
    assert parsed["metric"] == "spend_usd"
    assert parsed["days"] == 30
    assert parsed.get("period_key") is None
    assert parsed.get("source_platform") is None
    assert parsed.get("subject_filter_name") is None
    assert parsed.get("subject_filter_value") is None


def test_independent_question_hides_prior_transcript_from_planner():
    request = AskCostPilotRequest(
        question="How many tokens did pruning remove?",
        conversation=[
            AskCostPilotMessage(role="user", content="Only show last week."),
            AskCostPilotMessage(role="assistant", content="Last week had no activity."),
        ],
    )

    transcript = _ask_conversation_text(request)

    assert transcript == "USER: How many tokens did pruning remove?"


def test_clear_followup_keeps_prior_transcript_and_validated_context():
    request = AskCostPilotRequest(
        question="Now order them from lowest to highest.",
        days=30,
        conversation=[
            AskCostPilotMessage(role="user", content="Show the top five users."),
            AskCostPilotMessage(role="assistant", content="Here are the top five users."),
        ],
        context=AskCostPilotContext(
            intent="ranking",
            entity="person",
            metric="total_tokens",
            direction="desc",
            days=7,
            result_limit=5,
            period_key="last_week",
            source_platform="salesforce",
        ),
    )

    transcript = _ask_conversation_text(request)
    parsed = _ask_fallback_intent(request)

    assert "Show the top five users." in transcript
    assert parsed["entity"] == "person"
    assert parsed["metric"] == "total_tokens"
    assert parsed["direction"] == "asc"
    assert parsed["days"] == 7
    assert parsed["period_key"] == "last_week"
    assert parsed["source_platform"] == "salesforce"


def test_explicit_question_period_overrides_page_date_scope():
    request = AskCostPilotRequest(
        question="Which agents spent the most this month?",
        date_from=datetime(2026, 7, 20),
        date_to=datetime(2026, 7, 27),
    )
    parsed = _ask_intent(request.question, request.days)

    start, end = _ask_period_bounds(request, parsed)

    assert parsed["period_key"] == "this_month"
    assert start.day == 1
    assert (start, end) != (request.date_from, request.date_to)


def test_last_seven_days_uses_rolling_period_bounds():
    request = AskCostPilotRequest(
        question="Narrow that to the last seven days.",
        date_from=datetime(2026, 7, 1),
        date_to=datetime(2026, 7, 2),
    )
    parsed = _ask_intent(request.question, request.days)

    start, end = _ask_period_bounds(request, parsed)

    assert _ask_is_follow_up(request.question) is True
    assert parsed["period_key"] == "rolling_days"
    assert 6.99 <= (end - start).total_seconds() / 86400 <= 7.01


def test_openai_intent_is_bounded_before_it_reaches_reporting():
    fallback = _ask_intent("Who used the most AI?", default_days=30)
    parsed = _validated_ask_intent(
        {
            "intent": "ranking",
            "entity": "person",
            "metric": "total_tokens",
            "direction": "asc",
            "days": 900,
            "result_limit": 500,
            "unsafe_query": "delete everything",
        },
        fallback,
    )

    assert parsed["intent"] == "ranking"
    assert parsed["entity"] == "person"
    assert parsed["metric"] == "total_tokens"
    assert parsed["days"] == 365
    assert parsed["direction"] == "asc"
    assert parsed["result_limit"] == 20
    assert "unsafe_query" not in parsed


def test_optional_none_values_clear_stale_filters_and_period():
    fallback = {
        **_ask_intent("Who used the most tokens?", default_days=30),
        "period_key": "last_week",
        "source_platform": "salesforce",
        "model_tier": "strategist",
    }

    parsed = _validated_ask_intent(
        {
            "intent": "ranking",
            "entity": "person",
            "metric": "total_tokens",
            "direction": "desc",
            "days": 30,
            "result_limit": 5,
            "period_key": "none",
            "source_platform": "none",
            "model_tier": "none",
            "subject_entity": "none",
            "subject_filter_name": "none",
            "subject_filter_value": "none",
        },
        fallback,
    )

    assert parsed.get("period_key") is None
    assert parsed.get("source_platform") is None
    assert parsed.get("model_tier") is None
    assert parsed.get("subject_entity") is None


def test_last_seven_days_is_a_period_not_a_result_count():
    request = AskCostPilotRequest(
        question="Narrow that to the last seven days.",
        days=31,
        context={
            "intent": "ranking",
            "entity": "person",
            "metric": "total_tokens",
            "days": 31,
            "result_limit": 5,
        },
    )

    intent = _ask_fallback_intent(request)

    assert intent["days"] == 7
    assert intent["result_limit"] == 5
    assert intent["entity"] == "person"
    assert intent["metric"] == "total_tokens"


def test_where_did_spend_go_targets_business_contexts():
    intent = _ask_intent("Where did most of our AI spend go?", default_days=31)

    assert intent["intent"] == "ranking"
    assert intent["entity"] == "context"
    assert intent["metric"] == "spend_usd"


def test_named_person_is_resolved_from_costpilot_breakdown():
    report = {
        "people_breakdown": [
            {
                "id": "USER-1",
                "label": "Sheldon Simmons",
                "request_count": 12,
                "total_tokens": 4321,
                "spend_usd": 0.19,
            },
            {
                "id": "USER-2",
                "label": "Marcus Reed",
                "request_count": 8,
                "total_tokens": 2100,
                "spend_usd": 0.08,
            },
        ],
        "agent_breakdown": [],
        "organizational_unit_breakdown": [],
        "project_breakdown": [],
    }

    match = _ask_named_entity("How many tokens has Sheldon used?", report)

    assert match["entity"] == "person"
    assert match["filter_name"] == "user_external_id"
    assert match["row"]["label"] == "Sheldon Simmons"


def test_different_people_sharing_a_name_are_not_summed_together():
    """
    Confirmed live on the hosted traffic-simulator demo: "Avery Johnson"
    existed as 5 distinct simulated identities across 5 unrelated
    simulated companies/platforms, 4 sharing one synthetic email and one
    with a clearly different email. Ask CostPilot summed all 5 into one
    $7.4483 answer, while the attribution dashboard's exact-identity
    filter showed only one of them (~$0.84) -- a ~4x overstatement that
    looked like "the math is wrong" from the outside. Merging must stop
    treating same-name-different-email rows as the same person; a person
    with a different email is either a real ambiguity (falls to
    clarification) or -- as here -- resolvable once the 4 same-email rows
    correctly merge into one candidate and the 5th, different-email row
    is recognized as a separate one.
    """
    report = {
        "people_breakdown": [
            {
                "id": "SIM-ENTERPRISE-SAAS-USER-010", "label": "Avery Johnson",
                "email": "avery.johnson@example.com", "request_count": 159,
                "spend_usd": 3.188828, "total_tokens": 102223,
            },
            {
                "id": "SIM-PROFESSIONAL-SERVICES-USER-010", "label": "Avery Johnson",
                "email": "avery.johnson@example.com", "request_count": 101,
                "spend_usd": 1.859198, "total_tokens": 58449,
            },
            {
                "id": "SIM-RETAIL-SERVICES-USER-010", "label": "Avery Johnson",
                "email": "avery.johnson@example.com", "request_count": 74,
                "spend_usd": 1.372039, "total_tokens": 45048,
            },
            {
                "id": "SIM-MANUFACTURING-USER-010", "label": "Avery Johnson",
                "email": "avery.johnson@example.com", "request_count": 53,
                "spend_usd": 0.836397, "total_tokens": 28584,
            },
            {
                # A different real identity that happens to share the
                # display name -- distinguished only by a different email.
                "id": "HIST2Y:SIM-HISTORICAL-2Y:USER:7", "label": "Avery Johnson",
                "email": "historical.user7@example.com", "request_count": 65,
                "spend_usd": 0.191864, "total_tokens": 118721,
            },
        ],
        "agent_breakdown": [], "organizational_unit_breakdown": [], "project_breakdown": [],
    }

    match = _ask_named_entity("How much did Avery Johnson spend?", report)

    # Must not silently sum all 5 (~$7.45) -- that's the confirmed bug.
    assert match is None or match["row"]["spend_usd"] < 7.0

    ambiguous = _ask_named_entity_ambiguity("How much did Avery Johnson spend?", report)
    if match is None:
        # The 4 same-email rows should have merged into one candidate that
        # ties with the 5th (different-email) row -- a genuine ambiguity,
        # correctly surfaced rather than guessed at or summed.
        assert len(ambiguous) == 2
        merged_candidate = next(c for c in ambiguous if c["row"]["id"] != "HIST2Y:SIM-HISTORICAL-2Y:USER:7")
        assert round(merged_candidate["row"]["spend_usd"], 4) == round(
            3.188828 + 1.859198 + 1.372039 + 0.836397, 4
        )


def test_ambiguous_name_is_not_guessed():
    report = {
        "people_breakdown": [
            {"id": "USER-1", "label": "Alex Morgan"},
            {"id": "USER-2", "label": "Alex Johnson"},
        ],
        "agent_breakdown": [],
        "organizational_unit_breakdown": [],
        "project_breakdown": [],
    }

    assert _ask_named_entity("How many tokens has Alex used?", report) is None


def test_won_opportunity_ranking_question_sets_outcome_filter():
    intent = _ask_intent(
        "Which won opportunities had the highest AI spend?",
        default_days=30,
    )
    assert intent["intent"] == "ranking"
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "won"


def test_lost_opportunity_total_question_sets_outcome_filter():
    intent = _ask_intent(
        "How much AI spend was associated with lost opportunities last quarter?",
        default_days=30,
    )
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "lost"


def test_plain_opportunity_question_has_no_outcome_filter():
    intent = _ask_intent(
        "How much did we spend on the Acme opportunity?",
        default_days=30,
    )
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] is None


def _outcome_report():
    return {
        "summary": {
            "request_count": 106, "total_tokens": 8000, "input_tokens": 6000,
            "output_tokens": 2000, "tokens_saved": 500, "spend_usd": 214.0,
            "live_count": 106, "simulation_count": 0,
            "people_count": 2, "agent_count": 1,
        },
        "period": {"date_from": "2026-07-01T00:00:00", "date_to": "2026-08-01T00:00:00", "days": 31},
        "context_label_plural": "Opportunities",
        "people_breakdown": [], "agent_breakdown": [], "organizational_unit_breakdown": [],
        "account_breakdown": [], "source_platform_breakdown": [], "model_breakdown": [],
        "provider_breakdown": [],
        "project_breakdown": [
            {
                "id": "PROJECT-ACME-WON", "label": "Acme Expansion",
                "account_external_id": None, "account_name": "Acme Corp",
                "request_count": 83, "total_tokens": 6500, "input_tokens": 5000,
                "output_tokens": 1500, "tokens_saved": 400, "spend_usd": 196.0,
                "live_count": 83, "simulation_count": 0,
                "outcome_status": "Closed Won", "outcome_value": 600000.0,
                "outcome_success": True, "outcome_is_closed": True,
                "outcome_freshness": "current",
            },
            {
                "id": "PROJECT-BETA-LOST", "label": "Beta Renewal",
                "account_external_id": None, "account_name": "Beta Inc",
                "request_count": 23, "total_tokens": 1500, "input_tokens": 1000,
                "output_tokens": 500, "tokens_saved": 100, "spend_usd": 18.0,
                "live_count": 23, "simulation_count": 0,
                "outcome_status": "Closed Lost", "outcome_value": 150000.0,
                "outcome_success": False, "outcome_is_closed": True,
                "outcome_freshness": "potentially_stale",
            },
        ],
        "activities": [], "activity_count": 0,
        "measurement_note": "Controlled outcome fixture.",
    }


def test_endpoint_ranking_won_opportunities_excludes_lost_and_carries_outcome_facts():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(question="Which won opportunities had the highest AI spend?"),
        report=_outcome_report(),
        db=_FakeDB(),
    )
    assert len(calls) == 1
    assert len(response["evidence"]) == 1
    assert response["evidence"][0]["label"] == "Acme Expansion"
    assert response["evidence"][0]["outcome"]["status"] == "Closed Won"
    assert response["evidence"][0]["outcome"]["value"] == 600000.0
    assert "AI generated" not in response["answer"]
    assert "AI drove" not in response["answer"]


def test_endpoint_total_lost_opportunities_scopes_to_lost_only():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(question="How much AI spend was associated with lost opportunities?"),
        report=_outcome_report(),
        db=_FakeDB(),
    )
    assert len(calls) == 1
    assert len(response["evidence"]) == 1
    assert response["evidence"][0]["label"] == "Beta Renewal"
    assert "$18.0000" in response["answer"] or "18.00" in response["answer"]
    assert "not evidence that the ai activity caused them" in response["answer"].lower()


def test_endpoint_answers_named_person_with_calculation_and_drill_filter():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="How many tokens has Sheldon used?",
            days=31,
        )
    )

    assert len(calls) == 1
    assert response["title"] == "Sheldon Simmons AI usage"
    assert "4,321" in response["answer"]
    assert response["evidence"][0]["filter_name"] == "user_external_id"
    assert response["evidence"][0]["filter_value"] == "USER-SHELDON"
    assert response["calculation_source"] == "CostPilot deterministic attribution engine"
    assert response["data_provenance"]["scope"] == "mixed"


def test_endpoint_followup_applies_source_and_period_without_losing_ranking():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="Only show Salesforce activity for the last seven days.",
            days=31,
            context=AskCostPilotContext(
                intent="ranking",
                entity="person",
                metric="total_tokens",
                direction="desc",
                days=31,
                result_limit=5,
            ),
        )
    )

    assert len(calls) == 1
    assert calls[0]["source_platform"] == "salesforce"
    assert calls[0]["days"] == 7
    assert response["intent"] == "ranking"
    assert response["entity"] == "person"
    assert response["conversation_context"]["source_platform"] == "salesforce"
    assert response["conversation_context"]["days"] == 7


def test_endpoint_pruning_reports_tokens_and_estimated_avoided_cost():
    response, _ = _run_with_controlled_report(
        AskCostPilotRequest(
            question="How much money did pruning save?",
            days=31,
        )
    )

    assert response["title"] == "Pruning impact"
    assert "1,000 tokens" in response["answer"]
    assert "$0.2857" in response["answer"]
    assert response["calculation"]["formula"].startswith("Tokens pruned multiplied")
    assert response["data_provenance"]["live_requests"] == 12
    assert response["data_provenance"]["simulator_requests"] == 8


def test_endpoint_capability_question_does_not_run_stale_data_report():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="What all can you do?",
            days=31,
            context=AskCostPilotContext(
                intent="comparison",
                entity="agent",
                metric="spend_usd",
                days=5,
                result_limit=5,
                subject_filter_name="agent_id",
                subject_filter_value="668",
            ),
        )
    )

    assert calls == []
    assert response["intent"] == "help"
    assert response["title"] == "What Ask CostPilot can answer"
    assert len(response["evidence"]) >= 5
    assert response["data_provenance"]["scope"] == "capabilities"


def test_endpoint_product_question_uses_curated_knowledge_and_screen_context():
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(
            question="What does this chart mean and how does CostPilot calculate savings?",
            screen_context=AskCostPilotScreenContext(
                page_path="/index.html",
                page_title="CostPilot Executive Summary",
                section="Spend and savings",
                visible_metric="Annual Savings",
            ),
        )
    )

    assert calls == []
    assert response["intent"] == "product"
    assert response["assistant_mode"] == "costpilot_knowledge"
    assert response["data_provenance"]["scope"] == "product_knowledge"
    assert response["data_provenance"]["screen_context"]["visible_metric"] == "Annual Savings"
    assert "savings" in response["data_provenance"]["knowledge_topics"]
    assert response["recommendations"]


def test_grounded_narrator_can_phrase_facts_but_not_replace_evidence():
    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            assert '"total_tokens":4321' in kwargs["input"]
            return SimpleNamespace(output=[SimpleNamespace(
                type="function_call",
                name="write_grounded_costpilot_answer",
                arguments=(
                    '{"title":"Sheldon token usage",'
                    '"answer":"Sheldon used exactly 4,321 tokens in this period."}'
                ),
            )])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    original_module = sys.modules.get("openai")
    original_key = os.environ.get("OPENAI_API_KEY")
    original_enabled = os.environ.get("ASK_COSTPILOT_NARRATION_ENABLED")
    sys.modules["openai"] = SimpleNamespace(OpenAI=FakeOpenAI)
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = "true"
    payload = {
        "title": "Deterministic title",
        "answer": "Deterministic answer with 4,321 tokens.",
        "period": {"label": "Last 31 days"},
        "filters": {},
        "summary": {"total_tokens": 4321},
        "evidence": [{"label": "Sheldon", "value": "4,321"}],
        "recommendations": [],
        "calculation": {"formula": "Sum of input and output tokens"},
        "data_provenance": {"scope": "live"},
        "measurement_note": "Consumption only.",
    }
    try:
        title, answer, narrated = _ask_grounded_narrative(
            AskCostPilotRequest(question="How many tokens has Sheldon used?"),
            payload,
        )
    finally:
        if original_module is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original_module
        if original_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_key
        if original_enabled is None:
            os.environ.pop("ASK_COSTPILOT_NARRATION_ENABLED", None)
        else:
            os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = original_enabled

    assert narrated is True
    assert title == "Sheldon token usage"
    assert "4,321" in answer
    assert payload["evidence"][0]["value"] == "4,321"


def test_grounded_narrator_rejects_a_number_not_in_the_source_facts():
    """
    If the narration model swaps or invents a figure that never appeared in
    the deterministic facts it was given, the rewrite must be discarded and
    the deterministic title/answer returned instead -- this is the fidelity
    check that used to be entirely absent for the narration path.
    """
    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(output=[SimpleNamespace(
                type="function_call",
                name="write_grounded_costpilot_answer",
                arguments=(
                    '{"title":"Sheldon token usage",'
                    '"answer":"Sheldon used exactly 9,999 tokens in this period."}'
                ),
            )])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

    original_module = sys.modules.get("openai")
    original_key = os.environ.get("OPENAI_API_KEY")
    original_enabled = os.environ.get("ASK_COSTPILOT_NARRATION_ENABLED")
    sys.modules["openai"] = SimpleNamespace(OpenAI=FakeOpenAI)
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = "true"
    payload = {
        "title": "Deterministic title",
        "answer": "Deterministic answer with 4,321 tokens.",
        "period": {"label": "Last 31 days"},
        "filters": {},
        "summary": {"total_tokens": 4321},
        "evidence": [{"label": "Sheldon", "value": "4,321"}],
        "recommendations": [],
        "calculation": {"formula": "Sum of input and output tokens"},
        "data_provenance": {"scope": "live"},
        "measurement_note": "Consumption only.",
    }
    try:
        title, answer, narrated = _ask_grounded_narrative(
            AskCostPilotRequest(question="How many tokens has Sheldon used?"),
            payload,
        )
    finally:
        if original_module is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original_module
        if original_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = original_key
        if original_enabled is None:
            os.environ.pop("ASK_COSTPILOT_NARRATION_ENABLED", None)
        else:
            os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = original_enabled

    assert narrated is False
    assert title == "Deterministic title"
    assert answer == "Deterministic answer with 4,321 tokens."


def test_narration_number_extraction_ignores_small_integers_and_units():
    numbers = _ask_extract_numbers("Top 5 departments spent $12,480.50, up 26.4% from 9 last year.")
    assert 12480.50 in numbers
    assert 26.4 in numbers
    assert 5 not in numbers  # "top 5" is a rank/count, not a fact to verify
    assert 9 not in numbers


def test_narration_unverified_numbers_allows_close_rounding():
    facts = {"summary": {"spend_usd": 12480.4231}}
    unverified = _ask_narration_unverified_numbers(facts, "Spend was $12,480.42 this period.")
    assert unverified == set()


def test_narration_unverified_numbers_flags_invented_figure():
    facts = {"summary": {"spend_usd": 12480.42}}
    unverified = _ask_narration_unverified_numbers(facts, "Spend was $99,999.00 this period.")
    assert 99999.0 in unverified


def test_narration_causal_claims_flags_ai_credited_with_dollar_outcome():
    claims = _ask_narration_causal_claims(
        "AI generated $600,000 in revenue for this deal."
    )
    assert claims

    claims = _ask_narration_causal_claims(
        "The AI agent drove $500K in new business this quarter."
    )
    assert claims


def test_narration_causal_claims_allows_association_without_causation():
    claims = _ask_narration_causal_claims(
        "This $600,000 Closed Won opportunity had $196 of tracked AI "
        "activity across 83 interactions."
    )
    assert claims == []

    claims = _ask_narration_causal_claims(
        "AI was used to help close this deal, which had $196 in tracked spend."
    )
    assert claims == []


def test_metric_for_keywords_matches_pruning_before_generic_usage():
    assert metric_for_keywords("how much did token pruning save us") == "tokens_saved"


def test_metric_for_keywords_matches_request_count_for_volume_language():
    assert metric_for_keywords("which department has the most call volume") == "request_count"


def test_metric_for_keywords_defaults_to_spend():
    assert metric_for_keywords("what happened this quarter") == "spend_usd"


def test_suggested_questions_are_department_specific_when_scoped():
    suggestions = _ask_suggested_questions("ranking", department="Sales")
    assert all("Sales" in s for s in suggestions)


def test_suggested_questions_are_subject_specific_when_named():
    suggestions = _ask_suggested_questions("ranking", subject="Maya Chen")
    assert all("Maya Chen" in s for s in suggestions)


def test_suggested_questions_no_activity_ignores_department_scope():
    # Zero data anywhere makes a department-scoped suggestion equally
    # unanswerable -- the no-activity suggestions must win regardless.
    suggestions = _ask_suggested_questions("no_activity", department="Sales")
    assert suggestions == [
        "Who had the most AI spend yesterday?",
        "Who had the most AI spend in the last 7 days?",
        "Show the latest AI activity.",
    ]


def test_suggested_questions_filters_out_restating_the_asked_question():
    suggestions = _ask_suggested_questions(
        "ranking", asked_question="Which department spent the most on AI?"
    )
    assert "Which department spent the most on AI?" not in suggestions
    assert suggestions  # other suggestions in the same category still remain


def test_suggested_questions_unrecognized_category_returns_empty():
    assert _ask_suggested_questions("some_unrelated_intent") == []


def test_agent_validate_answer_flags_false_all_clear():
    tool_call_log = [(
        "get_budget_status", {},
        {"departments": [{"label": "Sales", "used_pct": 92.0}]},
    )]
    issues = _ask_agent_validate_answer(
        tool_call_log, "Good news -- no departments are currently over their budget."
    )
    assert any("Sales" in issue for issue in issues)


def test_agent_validate_answer_flags_unmentioned_over_budget_department():
    tool_call_log = [(
        "get_budget_status", {},
        {"departments": [{"label": "Sales", "used_pct": 104.0}]},
    )]
    issues = _ask_agent_validate_answer(
        tool_call_log, "Overall spend looks reasonable across the company this month."
    )
    assert any("Sales" in issue and "over budget" in issue for issue in issues)


def test_agent_validate_answer_passes_when_over_budget_department_named():
    tool_call_log = [(
        "get_budget_status", {},
        {"departments": [{"label": "Sales", "used_pct": 104.0}]},
    )]
    issues = _ask_agent_validate_answer(
        tool_call_log, "Sales is over budget at 104% of its monthly cap."
    )
    assert issues == []


def test_agent_validate_answer_ignores_non_budget_tool_calls():
    tool_call_log = [(
        "get_usage_report", {},
        {"top_people": [{"label": "Sheldon", "spend_usd": 12.0}]},
    )]
    issues = _ask_agent_validate_answer(tool_call_log, "No departments are over budget at all.")
    assert issues == []


def test_agent_final_payload_rejects_answer_missing_title_or_answer():
    tool_call_log = [("get_usage_report", {}, {"top_people": []})]
    payload = _ask_agent_final_payload(
        AskCostPilotRequest(question="How much did we spend?"),
        db=None,
        final_args={"title": "", "answer": "Some answer", "evidence_ids": []},
        tool_call_log=tool_call_log,
    )
    assert payload is None


def test_agent_final_payload_rejects_answer_with_no_tool_calls():
    payload = _ask_agent_final_payload(
        AskCostPilotRequest(question="How much did we spend?"),
        db=None,
        final_args={"title": "Spend", "answer": "We spent $100.", "evidence_ids": []},
        tool_call_log=[],
    )
    assert payload is None


def test_agent_final_payload_builds_evidence_from_cited_ids():
    tool_call_log = [(
        "get_usage_report", {},
        {
            "period": {"label": "This month"},
            "summary": {"live_count": 5, "simulation_count": 0},
            "data_scope": "live",
            "top_people": [
                {"id": "USER-1", "label": "Sheldon", "spend_usd": 12.5},
                {"id": "USER-2", "label": "Marcus", "spend_usd": 8.0},
            ],
        },
    )]
    payload = _ask_agent_final_payload(
        AskCostPilotRequest(question="How much did Sheldon spend?"),
        db=None,
        final_args={
            "title": "Sheldon's spend",
            "answer": "Sheldon spent $12.50 this month.",
            "evidence_ids": ["USER-1"],
        },
        tool_call_log=tool_call_log,
    )
    assert payload is not None
    assert payload["evidence"] == [{
        "label": "Sheldon", "value": "$12.5000", "metric_label": "AI spend",
        "filter_name": "user_external_id", "filter_value": "USER-1",
    }]


def test_agent_final_payload_falls_back_to_primary_breakdown_when_no_ids_cited():
    tool_call_log = [(
        "get_usage_report", {},
        {
            "period": {"label": "This month"},
            "summary": {"live_count": 5, "simulation_count": 0},
            "data_scope": "live",
            "top_people": [{"id": "USER-1", "label": "Sheldon", "spend_usd": 12.5}],
        },
    )]
    payload = _ask_agent_final_payload(
        AskCostPilotRequest(question="Who spent the most?"),
        db=None,
        final_args={
            "title": "Top spender",
            "answer": "Sheldon spent the most this month.",
            "evidence_ids": [],
        },
        tool_call_log=tool_call_log,
    )
    assert payload is not None
    assert len(payload["evidence"]) == 1
    assert payload["evidence"][0]["label"] == "Sheldon"


def test_named_department_resolves_from_real_department_labels():
    """
    _ask_intent alone can only recognize the literal word "department" --
    it never touches the database, so a question naming an actual
    department ("what did Sales spend") can't be classified there. This
    checks the layer that CAN see real data (_ask_named_department)
    correctly picks a named department out of free text.
    """
    from api.routes_efficiency import _ask_named_department

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def distinct(self):
            return self

        def all(self):
            return [("Sales",), ("Marketing",), ("Support",), ("Operations",)]

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

    db = FakeDB()
    assert _ask_named_department("What did Sales spend last month?", None, db) == "Sales"
    assert _ask_named_department("What about marketing?", None, db) == "Marketing"
    assert _ask_named_department("How much did we spend?", None, db) is None


def test_named_department_promotes_entity_when_no_other_ranking_dimension_named():
    """
    Once a department is resolved from free text, a question with no other
    named ranking dimension ("what did Sales spend") should classify
    entity as "department" instead of leaving it at the generic default --
    this is what makes conversation_context/title narration correctly
    reflect the department as the subject, not just get the number right.
    """
    from api.routes_efficiency import _ask_named_entity

    report = _controlled_report()

    class FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def distinct(self):
            return self

        def all(self):
            return [("Sales",), ("Marketing",)]

        def first(self):
            return None

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

    class FakeDB:
        def query(self, *args, **kwargs):
            return FakeQuery()

    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(question="What did Sales spend last month?", days=30),
        report=report,
        db=FakeDB(),
    )
    assert response["interpreted_intent"]["entity"] == "department"


class _FakeQuery:
    def filter(self, *a, **k):
        return self

    def distinct(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _FakeDB:
    def query(self, *a, **k):
        return _FakeQuery()


def test_ambiguous_named_person_asks_for_clarification_instead_of_silent_total():
    """
    Before this behavior existed, "How much did Chris spend?" with two
    equally-matching Chrises silently answered with the unfiltered,
    company-wide total -- a confident, wrong answer to a question about
    one specific person. This locks in the fix: ambiguity must produce a
    clarification, never a silently-wrong number.
    """
    report = _controlled_report()
    report["people_breakdown"] = [
        {
            "id": "USER-1", "label": "Chris Johnson", "request_count": 10,
            "total_tokens": 3500, "input_tokens": 2500, "output_tokens": 1000,
            "tokens_saved": 500, "spend_usd": 1.0, "live_count": 10, "simulation_count": 0,
        },
        {
            "id": "USER-2", "label": "Chris Smith", "request_count": 10,
            "total_tokens": 3500, "input_tokens": 2500, "output_tokens": 1000,
            "tokens_saved": 500, "spend_usd": 1.0, "live_count": 10, "simulation_count": 0,
        },
    ]
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(question="How much did Chris spend?", days=30),
        report=report,
        db=_FakeDB(),
    )
    assert response["intent"] == "clarification_required"
    assert "Chris Johnson" in response["answer"]
    assert "Chris Smith" in response["answer"]
    # Must not have silently used the unfiltered $2.00 company-wide total.
    assert "2.0000" not in response["answer"] and "2.00" not in response["answer"]


def test_unmatched_name_still_falls_through_to_normal_overview():
    """
    A name that matches nothing at all (as opposed to matching two things
    equally) is a different case -- no real ambiguity exists, so it should
    fall through to the normal company-wide answer rather than block on a
    clarification that has no real candidates to offer.
    """
    response, calls = _run_with_controlled_report(
        AskCostPilotRequest(question="How much did Zzyzx spend?", days=30),
        db=_FakeDB(),
    )
    assert response["intent"] != "clarification_required"


def test_change_decomposition_percent_change_is_none_when_prior_is_zero():
    """
    Phase 8 requirement: never compute a misleading percentage change when
    the comparison value is zero (an infinite/undefined "% increase" from
    nothing). percent_change must be None, not 0, not inf, not a crash.
    """
    current = {"request_count": 10, "spend_usd": 50.0}
    prior = {"request_count": 0, "spend_usd": 0.0}
    result = change_decomposition(current, prior, "spend_usd")
    assert result["percent_change"] is None
    assert result["absolute_change"] == 50.0


def test_change_decomposition_percent_change_computes_normally_otherwise():
    current = {"request_count": 20, "spend_usd": 150.0}
    prior = {"request_count": 10, "spend_usd": 100.0}
    result = change_decomposition(current, prior, "spend_usd")
    assert result["percent_change"] == 50.0


def test_dimension_contributors_net_change_pct_is_none_when_total_change_is_zero():
    current_rows = [{"id": "Sales", "label": "Sales", "spend_usd": 10.0}]
    prior_rows = [{"id": "Sales", "label": "Sales", "spend_usd": 5.0}]
    # total_change=0 despite a real per-dimension delta -- e.g. offset by
    # another dimension decreasing by the same amount elsewhere.
    contributors = dimension_contributors(current_rows, prior_rows, "spend_usd", "department", 0.0)
    assert contributors[0]["net_change_contribution_pct"] is None
