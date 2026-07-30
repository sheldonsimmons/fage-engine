from api.routes_efficiency import _ask_evidence, _ask_intent, _ask_rank


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
