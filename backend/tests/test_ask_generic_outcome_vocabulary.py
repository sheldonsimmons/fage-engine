"""
tests/test_ask_generic_outcome_vocabulary.py — Ask CostPilot's won/lost
outcome filtering used to be Sales-Opportunity-only vocabulary ("won",
"lost", "opportunit", "deal(s)"), even though the underlying data
(WorkItemOutcome.outcome_success) has been generic since Universal
Outcome Ingestion. This tests the generalization: a claims/ticket/
incident question using generic success/failure language ("approved",
"denied") now resolves to entity="context" + outcome_filter, with a
DISPLAY LABEL ("successful"/"unsuccessful") distinct from the still-Sales
-specific "won"/"lost" wording, and response text uses the workspace's
own context_label_plural instead of a hardcoded "opportunities" noun.

Also regression-guards the real conflict this caused and fixed: adding
"incident(s)"/"ticket(s)" as a blanket entity trigger broke 3 real
ServiceNow eval-corpus cases (plain volume questions like "how many
incidents have AI activity on them?", which must stay entity="overview",
not fall into the outcome-filtering path just because they mention
"incidents").
"""
from api.routes_efficiency import AskCostPilotRequest, _ask_intent
from tests.test_ask_costpilot import _run_with_controlled_report


def test_approved_claims_question_sets_context_entity_and_successful_label():
    intent = _ask_intent("Which approved claims had the highest AI spend?", default_days=30)
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "won"
    assert intent["outcome_filter_label"] == "successful"


def test_denied_claims_question_sets_context_entity_and_unsuccessful_label():
    intent = _ask_intent("Show me denied claims and their AI cost.", default_days=30)
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "lost"
    assert intent["outcome_filter_label"] == "unsuccessful"


def test_sales_won_opportunity_question_keeps_won_label_unchanged():
    # Existing Sales-Opportunity phrasing must be completely unaffected --
    # "won"/"lost" stays the display word, not "successful"/"unsuccessful".
    intent = _ask_intent("Which won opportunities had the highest AI spend?", default_days=30)
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "won"
    assert intent["outcome_filter_label"] == "won"


def test_plain_incident_volume_question_stays_overview_not_context():
    # The real conflict found and fixed: "incidents"/"tickets" alone (no
    # outcome-decision language) must NOT trigger entity="context" --
    # these are ordinary volume questions, confirmed against this file's
    # own eval corpus (servicenow-001/002/004).
    intent = _ask_intent("How many ServiceNow incidents have AI activity on them?", default_days=30)
    assert intent["entity"] == "overview"
    assert intent["outcome_filter"] is None


def test_ticket_question_with_outcome_language_does_trigger_context():
    intent = _ask_intent("Which tickets were rejected despite AI assistance?", default_days=30)
    assert intent["entity"] == "context"
    assert intent["outcome_filter"] == "lost"
    assert intent["outcome_filter_label"] == "unsuccessful"


def test_end_to_end_answer_uses_workspace_noun_not_hardcoded_opportunities():
    report = {
        "summary": {"request_count": 2, "total_tokens": 100, "input_tokens": 70, "output_tokens": 30,
                    "tokens_saved": 0, "spend_usd": 1.0, "live_count": 2, "simulation_count": 0,
                    "people_count": 1, "agent_count": 1},
        "period": {"date_from": "2026-08-01T00:00:00", "date_to": "2026-09-01T00:00:00"},
        "filters": {},
        "context_label_plural": "Claims",
        "people_breakdown": [], "agent_breakdown": [], "organizational_unit_breakdown": [],
        "project_breakdown": [
            {"id": "CLAIM-1", "label": "Claim 1", "request_count": 1, "total_tokens": 50,
             "input_tokens": 35, "output_tokens": 15, "tokens_saved": 0, "spend_usd": 0.6,
             "live_count": 1, "simulation_count": 0, "outcome_success": True},
            {"id": "CLAIM-2", "label": "Claim 2", "request_count": 1, "total_tokens": 50,
             "input_tokens": 35, "output_tokens": 15, "tokens_saved": 0, "spend_usd": 0.4,
             "live_count": 1, "simulation_count": 0, "outcome_success": False},
        ],
        "business_purpose_breakdown": [], "source_platform_breakdown": [], "model_breakdown": [],
        "provider_breakdown": [], "activities": [], "activity_count": 2, "activity_limit": 500,
        "evidence_quality": {"request_identity_count": 2, "correlated_request_count": 2, "total_request_count": 2},
        "measurement_note": "", "context_type": "claim",
    }
    response, _ = _run_with_controlled_report(AskCostPilotRequest(
        question="How much AI spend was on approved claims?",
    ), report=report)

    assert "opportunit" not in response["title"].lower()
    assert "opportunit" not in response["answer"].lower()
    assert "claims" in response["title"].lower()
    assert "successful" in response["title"].lower()
