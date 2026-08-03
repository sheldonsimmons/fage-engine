import os
import sys
from datetime import datetime
from types import SimpleNamespace

from api.routes_efficiency import (
    _ask_conversation_text,
    _ask_evidence,
    _ask_fallback_intent,
    _ask_grounded_narrative,
    _ask_has_explicit_named_subject,
    _ask_intent,
    _ask_is_follow_up,
    _ask_named_entity,
    _ask_period_bounds,
    _ask_rank,
    _ask_reporting_filters,
    AskCostPilotMessage,
    AskCostPilotRequest,
    AskCostPilotContext,
    AskCostPilotScreenContext,
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


def _run_with_controlled_report(request, report=None):
    from api import routes_work_items

    calls = []
    original = routes_work_items.project_activity_reporting
    original_narration = os.environ.get("ASK_COSTPILOT_NARRATION_ENABLED")
    os.environ["ASK_COSTPILOT_NARRATION_ENABLED"] = "false"

    def fake_reporting(**kwargs):
        calls.append(kwargs)
        return report or _controlled_report()

    routes_work_items.project_activity_reporting = fake_reporting
    try:
        return ask_costpilot(request, db=None), calls
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


def test_savings_question_uses_read_only_optimization_intent():
    intent = _ask_intent(
        "Give me advice on how we can save money.",
        default_days=30,
    )

    assert intent["intent"] == "optimization"
    assert intent["days"] == 30


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
