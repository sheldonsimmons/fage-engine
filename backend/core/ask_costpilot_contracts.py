"""Canonical demo intents and answer contracts for Ask CostPilot."""

import re
from typing import Optional


_CANONICAL_INTENTS = (
    {
        "name": "request_decision_explanation",
        "patterns": (
            r"\bwhy\b.*\b(?:this|that|the) request\b.*\b(?:rout|block|throttl|model|tier)",
            r"\bexplain\b.*\b(?:this|that|the)\b.*\b(?:routing|decision|request)",
            r"\bwhy did costpilot\b.*\b(?:route|block|throttle|choose|select)",
        ),
        "intent": "decision",
        "entity": "request",
        "metric": "request_count",
        "interpreted_as": "The recorded routing and policy evidence for one governed request",
    },
    {
        "name": "agents_never_used",
        "patterns": (
            r"\bagents?\b.*\bnever (?:been )?used\b",
            r"\bbots?\b.*\bnever (?:been )?(?:used|run|executed)\b",
            r"\b(?:agents?|bots?)\b.*\bzero (?:lifetime )?(?:usage|activity|requests?)\b",
            r"\b(?:agents?|bots?)\b.*\bno (?:lifetime )?(?:usage|activity|requests?)\b",
            r"\b(?:unused|unadopted) (?:agents?|bots?)\b",
            r"\b(?:agents?|bots?) (?:nobody|no one) (?:has )?(?:used|run)\b",
        ),
        "intent": "agent_adoption",
        "entity": "agent",
        "metric": "request_count",
        "usage_status": "never",
        "direction": "asc",
        "interpreted_as": "Registered agents with zero lifetime governed requests",
    },
    {
        "name": "agents_inactive",
        "patterns": (
            r"\b(?:agents?|bots?)\b.*\b(?:not used recently|recently inactive)\b",
            r"\b(?:inactive|unused) (?:agents?|bots?)\b.*\b(?:period|recent|month|week|days?)\b",
        ),
        "intent": "agent_adoption",
        "entity": "agent",
        "metric": "request_count",
        "usage_status": "recently_inactive",
        "direction": "asc",
        "interpreted_as": "Registered agents with historical usage but no activity in the selected period",
    },
    {
        "name": "blocked_reasons",
        "patterns": (
            r"\bwhy\b.*\b(?:request|requests|it|they)\b.*\bblock(?:ed|ing)?\b",
            r"\b(?:block|blocking) reasons?\b",
            r"\bwhat (?:caused|made)\b.*\bblock",
        ),
        "intent": "blocked",
        "entity": "overview",
        "metric": "request_count",
        "interpreted_as": "Recorded reasons governed requests were blocked",
    },
    {
        "name": "pruning_impact",
        "patterns": (
            r"\b(?:pruning|tokens? pruned|tokens? removed)\b.*\b(?:save|saved|impact|cost|how much)\b",
            r"\bhow (?:many|much)\b.*\b(?:pruning|tokens? removed)\b",
        ),
        "intent": "pruning",
        "entity": "overview",
        "metric": "tokens_saved",
        "interpreted_as": "Measured tokens removed by pruning and their estimated cost effect",
    },
    {
        "name": "budget_alerts",
        "patterns": (
            r"\b(?:departments?|teams?)\b.*\b(?:near|close to|over|exceed(?:ed|ing)?)\b.*\bbudget",
            r"\bbudget\b.*\b(?:alerts?|exceptions?|at risk|over cap)\b",
        ),
        "intent": "budget",
        "entity": "department",
        "metric": "spend_usd",
        "budget_scope": "alerts",
        "interpreted_as": "Departments approaching or exceeding their configured budget",
    },
    {
        "name": "agent_cost_ranking",
        "patterns": (
            r"\bwhich (?:agents?|bots?)\b.*\b(?:cost|spend|spent|expensive|costliest)\b.*\b(?:most|highest|top)?",
            r"\b(?:highest|top|most|largest|costliest)\b.*\b(?:cost|spend|expensive)\b.*\b(?:agents?|bots?)\b",
        ),
        "intent": "ranking",
        "entity": "agent",
        "metric": "spend_usd",
        "direction": "desc",
        "interpreted_as": "AI agents ranked by recorded model-call spend",
    },
    {
        "name": "department_spend_ranking",
        "patterns": (
            r"\bwhich (?:departments?|teams?|business units?)\b.*\b(?:spent|spend|cost)\b.*\b(?:most|highest|top)?",
            r"\b(?:highest|top|most|largest)\b.*\b(?:spend|cost)\b.*\b(?:departments?|teams?|business units?)\b",
        ),
        "intent": "ranking",
        "entity": "department",
        "metric": "spend_usd",
        "direction": "desc",
        "interpreted_as": "Departments ranked by recorded model-call spend",
    },
    {
        "name": "model_cost_ranking",
        "patterns": (
            r"\bwhich models?\b.*\b(?:cost|spend|spent|expensive|costliest)\b.*\b(?:most|highest|top)?",
            r"\b(?:highest|top|most|largest|costliest)\b.*\b(?:cost|spend|expensive)\b.*\bmodels?\b",
        ),
        "intent": "ranking",
        "entity": "model",
        "metric": "spend_usd",
        "direction": "desc",
        "interpreted_as": "AI models ranked by recorded model-call spend",
    },
    {
        "name": "savings_total",
        "patterns": (
            r"\bhow much\b.*\b(?:costpilot )?(?:save|saved|savings|avoided spend)\b",
            r"\bwhat (?:are|is|was|were)\b.*\b(?:our )?(?:savings|avoided spend)\b",
            r"\btotal\b.*\b(?:savings|avoided spend)\b",
        ),
        "intent": "savings",
        "entity": "overview",
        "metric": "spend_usd",
        "direction": "desc",
        "interpreted_as": "Measured avoided AI spend for the selected period and scope",
    },
    {
        "name": "source_mix",
        "patterns": (
            r"\bcompare\b.*\blive\b.*\b(?:simulator|simulated|test traffic)\b",
            r"\b(?:live|production)\b.*\b(?:vs|versus|and)\b.*\b(?:simulator|simulated|test traffic)\b",
        ),
        "intent": "source_mix",
        "entity": "overview",
        "metric": "request_count",
        "direction": "desc",
        "interpreted_as": "Governed request activity split between live and simulated sources",
    },
)


def canonical_ask_intent(question: str) -> Optional[dict]:
    """Return a high-confidence intent only when wording matches a buyer-safe contract."""
    text = " ".join((question or "").lower().split())
    for contract in _CANONICAL_INTENTS:
        if any(re.search(pattern, text) for pattern in contract["patterns"]):
            return {key: value for key, value in contract.items() if key != "patterns"}
    return None


def ask_interpretation_label(parsed: dict) -> str:
    if parsed.get("interpreted_as"):
        return str(parsed["interpreted_as"])
    intent = str(parsed.get("intent") or "overview").replace("_", " ")
    entity = str(parsed.get("entity") or "overview").replace("_", " ")
    metric = str(parsed.get("metric") or "activity").replace("_", " ")
    return f"{entity.title()} {intent} using {metric}"


def validate_ask_answer_contract(parsed: dict, payload: dict) -> list[str]:
    """Return violations that make a polished response unsafe to display."""
    canonical_name = parsed.get("canonical_intent")
    issues = []
    if parsed.get("intent") == "comparison":
        calculation = payload.get("calculation") or {}
        plan = calculation.get("comparison_plan") or {}
        metric_contract = calculation.get("metric_contract") or {}
        if not plan.get("primary") or not plan.get("comparison"):
            issues.append("comparison answer is missing explicit paired periods")
        if plan.get("mode") != (parsed.get("comparison_key") or "previous_period"):
            issues.append("comparison answer used the wrong temporal rule")
        if metric_contract.get("id") != parsed.get("metric"):
            issues.append("comparison answer used the wrong metric contract")
        if len(payload.get("evidence") or []) < 2:
            issues.append("comparison answer is missing period evidence")
        elif plan.get("primary") and plan.get("comparison"):
            evidence_labels = [str(item.get("label") or "") for item in payload["evidence"][:2]]
            expected_labels = [plan["primary"].get("label"), plan["comparison"].get("label")]
            if evidence_labels != expected_labels:
                issues.append("comparison evidence does not match the planned periods")
        coverage = (payload.get("data_provenance") or {}).get("coverage")
        if not isinstance(coverage, dict) or not coverage.get("status"):
            issues.append("comparison answer is missing coverage status")
    if not canonical_name:
        return issues
    for field in ("intent", "entity", "metric"):
        expected = parsed.get(field)
        actual = payload.get(field)
        if expected is not None and actual != expected:
            issues.append(f"expected {field}={expected}, received {actual}")
    evidence = payload.get("evidence") or []
    if canonical_name in {"agents_never_used", "agents_inactive", "agent_cost_ranking"}:
        if any(item.get("filter_name") != "agent_id" for item in evidence):
            issues.append("agent question returned non-agent evidence")
    if canonical_name == "agents_never_used":
        for item in evidence:
            detail = str(item.get("detail") or "").lower()
            if "never used" not in detail or "0 lifetime requests" not in detail:
                issues.append("never-used result contains activity outside the lifetime-zero definition")
                break
    if canonical_name == "agent_cost_ranking":
        if any(str(item.get("metric_label") or "").lower() != "ai spend" for item in evidence):
            issues.append("agent cost ranking returned a non-spend metric")
    if canonical_name == "department_spend_ranking":
        if any(item.get("filter_name") != "charged_unit" for item in evidence):
            issues.append("department question returned non-department evidence")
    if canonical_name == "model_cost_ranking":
        if any(str(item.get("metric_label") or "").lower() != "ai spend" for item in evidence):
            issues.append("model cost ranking returned a non-spend metric")
    return issues
