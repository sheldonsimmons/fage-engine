"""
tests/test_ask_propose_clarification.py — the intent="propose_clarification"
branch (api/routes_efficiency.py).

Confirmed live 2026-09-18: "Create a proposal for me." (no agent/
department or action named) fell through the deterministic classifier
all the way to intent="overview" -- a company-wide spend total that has
nothing to do with a proposal -- and the LLM narration pass then dressed
it up into something that read like CostPilot had looked up an agent
named "Proposal," with no real tool call behind it at all. This only
happens as a fallback: the agent loop (Claude, with the real
propose_agent_mode_change/propose_agent_tier_bounds_change/
propose_agent_allowed_providers_change/propose_budget_cap_change tools)
is the primary path and normally handles a specific request directly;
this deterministic branch is what the fallback says instead of guessing
when a request is genuinely too vague to act on, or when the agent loop
itself has a transient failure.
"""
from api.routes_efficiency import (
    AskCostPilotRequest,
    _ask_fallback_intent,
    _ask_propose_clarification_response,
    _resolve_ask_intent,
)


def test_vague_create_a_proposal_is_detected():
    req = AskCostPilotRequest(question="Create a proposal for me.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] == "propose_clarification"


def test_vague_make_a_proposal_is_detected():
    req = AskCostPilotRequest(question="Make a proposal.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] == "propose_clarification"


def test_vague_propose_something_is_detected():
    req = AskCostPilotRequest(question="I want to propose something.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] == "propose_clarification"


def test_specific_agent_mode_proposal_is_not_swallowed():
    # A real, actionable request -- the agent loop's own
    # propose_agent_mode_change tool should get first crack at this; the
    # deterministic classifier must not intercept it into a generic
    # clarifying question it doesn't need.
    req = AskCostPilotRequest(question="Propose moving SupportBot-Alpha to Control mode.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] != "propose_clarification"


def test_specific_tier_proposal_is_not_swallowed():
    req = AskCostPilotRequest(question="Cap SupportBot-Alpha to Scout and Advisor tiers only.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] != "propose_clarification"


def test_specific_budget_cap_proposal_is_not_swallowed():
    req = AskCostPilotRequest(question="Propose raising Engineering's budget cap to $500.")
    fallback = _ask_fallback_intent(req)
    assert fallback["intent"] != "propose_clarification"


def test_resolve_ask_intent_bypasses_openai_refinement_for_propose_clarification():
    # Same "confident, narrow phrasing match" short-circuit as help/
    # product/agent_adoption -- the OpenAI planner has no
    # clarification-shaped intent to pick and would only replace this
    # with a worse guess.
    req = AskCostPilotRequest(question="Create a proposal for me.")
    parsed, mode = _resolve_ask_intent(req)
    assert parsed["intent"] == "propose_clarification"
    assert mode == "deterministic_propose_clarification"


def test_propose_clarification_response_names_all_four_action_types():
    req = AskCostPilotRequest(question="Create a proposal for me.")
    parsed, mode = _resolve_ask_intent(req)
    result = _ask_propose_clarification_response(req, parsed, mode)

    assert result["intent"] == "clarification"
    assert result["evidence"] == []
    for phrase in ("Observe and Control", "model tiers", "model providers", "budget cap"):
        assert phrase in result["answer"]
    assert result["read_only"] is True
