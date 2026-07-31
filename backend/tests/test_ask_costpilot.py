from api.routes_efficiency import (
    _ask_evidence,
    _ask_fallback_intent,
    _ask_intent,
    _ask_named_entity,
    _ask_rank,
    _ask_reporting_filters,
    AskCostPilotMessage,
    AskCostPilotRequest,
    AskCostPilotContext,
    ask_costpilot,
    _validated_ask_intent,
)


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
        "project_breakdown": [],
        "source_platform_breakdown": [],
        "model_breakdown": [],
        "activities": [],
        "activity_count": 0,
        "measurement_note": "Controlled attribution fixture.",
    }


def _run_with_controlled_report(request, report=None):
    from api import routes_work_items

    calls = []
    original = routes_work_items.project_activity_reporting

    def fake_reporting(**kwargs):
        calls.append(kwargs)
        return report or _controlled_report()

    routes_work_items.project_activity_reporting = fake_reporting
    try:
        return ask_costpilot(request, db=None), calls
    finally:
        routes_work_items.project_activity_reporting = original


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


def test_savings_question_uses_read_only_optimization_intent():
    intent = _ask_intent(
        "Give me advice on how we can save money.",
        default_days=30,
    )

    assert intent["intent"] == "optimization"
    assert intent["days"] == 30


def test_budget_question_targets_departments():
    intent = _ask_intent(
        "Which departments are close to the budget limit?",
        default_days=30,
    )

    assert intent["intent"] == "budget"
    assert intent["entity"] == "department"


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
