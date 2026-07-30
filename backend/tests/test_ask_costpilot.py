from api.routes_efficiency import (
    _ask_evidence,
    _ask_fallback_intent,
    _ask_intent,
    _ask_named_entity,
    _ask_rank,
    _ask_reporting_filters,
    AskCostPilotMessage,
    AskCostPilotRequest,
    _validated_ask_intent,
)


def test_employee_token_question_becomes_person_ranking_for_last_week():
    intent = _ask_intent(
        "Who had the highest token spend last week?",
        default_days=30,
    )

    assert intent == {
        "intent": "ranking",
        "entity": "person",
        "metric": "total_tokens",
        "days": 7,
        "direction": "desc",
        "result_limit": 5,
    }


def test_savings_question_uses_read_only_savings_intent():
    intent = _ask_intent(
        "Give me advice on how we can save money.",
        default_days=30,
    )

    assert intent["intent"] == "savings"
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

    assert intent == {
        "intent": "pruning",
        "entity": "overview",
        "metric": "tokens_saved",
        "days": 31,
        "direction": "desc",
        "result_limit": 5,
    }


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

    assert parsed == {
        "intent": "ranking",
        "entity": "person",
        "metric": "total_tokens",
        "days": 365,
        "direction": "asc",
        "result_limit": 20,
    }


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
