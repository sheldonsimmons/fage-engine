from api.routes_efficiency import (
    _ask_evidence,
    _ask_intent,
    _ask_rank,
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
