"""
api/routes_efficiency.py — Bot Efficiency Review  [Step 11]

POST /api/reports/bot-efficiency
  Analyzes every registered agent's transaction history and uses GPT-4o
  (or the configured flagship model) to generate plain-English efficiency
  recommendations with projected savings.

Works with both live and simulated model modes:
  - live:      calls GPT-4o / Claude with real agent data
  - simulated: generates realistic rule-based recommendations without an API call
"""

from datetime import datetime, timedelta
import json
import logging
import os
import re
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from database.db import get_db
from database.models import (
    AuditEvent,
    DepartmentBudget,
    RegisteredAgent,
    TokenTransaction,
    WorkItem,
)

router = APIRouter()
logger = logging.getLogger(__name__)

FLAGSHIP_IN  = 5.00  / 1_000_000
FLAGSHIP_OUT = 15.00 / 1_000_000
MICRO_IN     = 0.50  / 1_000_000
MICRO_OUT    = 1.50  / 1_000_000

_ASK_OPENAI_DISABLED_UNTIL = 0.0


def _ask_env_seconds(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


class AskCostPilotMessage(BaseModel):
    """A bounded prior turn used only to understand follow-up questions."""

    role: Literal["user", "assistant"]
    content: str


class AskCostPilotContext(BaseModel):
    """The last validated reporting intent used for a conversational follow-up."""

    intent: Optional[str] = None
    entity: Optional[str] = None
    metric: Optional[str] = None
    direction: Optional[str] = None
    days: Optional[int] = None
    result_limit: Optional[int] = None
    source_platform: Optional[str] = None
    subject_entity: Optional[str] = None
    subject_filter_name: Optional[str] = None
    subject_filter_value: Optional[str] = None
    model_tier: Optional[str] = None
    period_key: Optional[str] = None


class AskCostPilotRequest(BaseModel):
    """Read-only natural-language question over CostPilot's attributed usage."""

    question: str
    days: int = 30
    workspace_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    project_id: Optional[str] = None
    user_external_id: Optional[str] = None
    agent_id: Optional[int] = None
    account_id: Optional[str] = None
    source_platform: Optional[str] = None
    record_type: Optional[str] = None
    model_tier: Optional[str] = None
    charged_unit: Optional[str] = None
    business_purpose: Optional[str] = None
    conversation: list[AskCostPilotMessage] = Field(default_factory=list)
    context: Optional[AskCostPilotContext] = None


def _ask_intent(question: str, default_days: int) -> dict:
    """Translate common executive questions into a bounded reporting intent."""
    text = " ".join((question or "").lower().split())
    days = max(1, min(int(default_days or 30), 365))
    period_key = None
    if "today" in text:
        days, period_key = 1, "today"
    elif "yesterday" in text:
        days, period_key = 1, "yesterday"
    elif "this week" in text:
        days, period_key = 7, "this_week"
    elif "last week" in text or "past week" in text:
        days, period_key = 7, "last_week"
    elif "this month" in text:
        days, period_key = 31, "this_month"
    elif "last month" in text or "past month" in text:
        days, period_key = 31, "last_month"
    elif "this quarter" in text:
        days, period_key = 92, "this_quarter"
    elif "last quarter" in text or "past quarter" in text:
        days, period_key = 92, "last_quarter"
    elif "this year" in text:
        days, period_key = 365, "this_year"
    elif "last year" in text or "past year" in text:
        days, period_key = 365, "last_year"
    else:
        rolling_match = re.search(
            r"\b(?:last|past)\s+(\d{1,3}|seven|thirty)\s+days?\b",
            text,
        )
        if rolling_match:
            word_days = {"seven": 7, "thirty": 30}
            raw_days = rolling_match.group(1)
            days = max(1, min(word_days.get(raw_days, int(raw_days) if raw_days.isdigit() else days), 365))

    metric = "spend_usd"
    if any(term in text for term in ("prun", "tokens removed", "removed before model")):
        metric = "tokens_saved"
    elif any(term in text for term in ("risk event", "risk events")):
        metric = "risk_event_count"
    elif any(term in text for term in ("average cost", "avg cost", "cost per request")):
        metric = "avg_cost_per_request"
    elif "token" in text:
        metric = "total_tokens"
    elif any(term in text for term in (
        "expensive", "costliest", "highest cost", "most cost", "spend", "spent"
    )):
        metric = "spend_usd"
    elif any(term in text for term in ("request", "call", "volume", "used most", "usage")):
        metric = "request_count"

    entity = "overview"
    if any(term in text for term in ("employee", "person", "people", "user", "who ")):
        entity = "person"
    elif any(term in text for term in ("agent", "bot")):
        entity = "agent"
    elif any(term in text for term in ("department", "team", "business unit", "cost center")):
        entity = "department"
    elif any(term in text for term in (
        "account", "customer", "project", "matter", "opportunity", "business context", "work item"
    )):
        entity = "context"
    elif any(term in text for term in ("platform", "provider", "source system", "source app")):
        entity = "platform"
    elif any(term in text for term in ("model", "tier", "opus", "sonnet", "haiku", "gpt")):
        entity = "model"
    elif "where did" in text and any(term in text for term in ("spend", "cost", "token", "usage")):
        entity = "context"

    ranking_terms = (
        "highest", "most", "top", "largest", "lowest", "least", "fewest",
        "smallest", "bottom",
    )
    intent = "ranking" if entity != "overview" and any(
        term in text for term in ranking_terms
    ) else "overview"
    if any(term in text for term in (
        "why were requests blocked", "why was the request blocked",
        "blocked request", "blocked requests", "request blocked", "requests blocked",
        "request was blocked", "requests were blocked",
        "blocking reason", "block reason",
    )):
        intent = "blocked"
        metric = "request_count"
        entity = "overview"
    elif any(term in text for term in (
        "show risk events", "show the risk events", "latest risk",
        "recent risk", "latest governance event", "recent governance event",
    )):
        intent = "risk_events"
        metric = "request_count"
        entity = "overview"
    elif any(term in text for term in (
        "show all ai activity", "show me the supporting activity",
        "show supporting activity", "supporting activity", "latest activity",
        "recent activity", "who used ai on", "which agents worked on",
        "which agents contributed", "activity connected to",
    )):
        intent = "activity"
    elif any(term in text for term in (
        "not been used", "unused agent", "unused agents", "inactive agent",
        "inactive agents", "not used recently",
    )):
        intent = "inactive"
        entity = "agent"
        metric = "request_count"
        direction = "asc"
    elif any(term in text for term in (
        "prun", "tokens removed", "removed before model", "context removed"
    )):
        intent = "pruning"
        metric = "tokens_saved"
        entity = "overview"
    elif any(term in text for term in (
        "live vs simulator", "live and simulator", "simulator vs live",
        "simulated vs live", "test traffic", "simulator data", "live data",
    )):
        intent = "source_mix"
        entity = "overview"
    elif any(term in text for term in (
        "save money", "saving", "reduce cost", "cut cost", "optimize",
        "recommend", "advice", "cheaper model", "expensive model",
        "cost-saving opportunit"
    )):
        intent = "optimization"
    elif any(term in text for term in (
        "near budget", "close to budget", "budget limit", "over budget", "budget warning"
    )):
        intent = "budget"
    elif any(term in text for term in (
        "routed to the", "routed to strategist", "routed to advisor",
        "strategist tier", "advisor tier", "scout tier", "analyst tier",
    )):
        intent = "tier_usage"
        entity = "model"
    elif any(term in text for term in (
        "compare ", " compared ", " versus ", " vs. ", " vs "
    )):
        intent = "comparison"
    elif (
        any(term in text for term in ("how much", "how many", "what is our", "what's our"))
        and not any(term in text for term in ranking_terms)
    ):
        intent = "total"
    elif any(term in text for term in ("overview", "summary", "where is", "breakdown")):
        intent = "overview"

    direction = "asc" if any(term in text for term in (
        "fewest", "least", "lowest", "smallest", "bottom"
    )) else "desc"
    if intent == "inactive":
        direction = "asc"
    number_words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "twenty": 20,
    }
    number_pattern = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twenty)"
    count_match = re.search(
        rf"\b(?:top|bottom|first|last)\s+(?:the\s+)?{number_pattern}\b(?!\s+days?\b)",
        text,
    ) or re.search(
        rf"\b{number_pattern}\s+(?:highest|lowest|top|bottom|most|least|fewest)\b",
        text,
    )
    raw_count = count_match.group(1) if count_match else None
    result_limit = (
        int(raw_count) if raw_count and raw_count.isdigit()
        else number_words.get(raw_count or "", 5)
    )
    result_limit = max(1, min(result_limit, 20))
    source_platform = None
    for platform in (
        "salesforce", "servicenow", "hubspot", "slack", "zendesk",
        "sap", "netsuite", "microsoft teams", "shopify",
    ):
        if platform in text:
            source_platform = platform
            break
    model_tier = None
    for tier in ("strategist", "advisor", "analyst", "scout", "flagship"):
        if tier in text:
            model_tier = tier
            break

    return {
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "days": days,
        "direction": direction,
        "result_limit": result_limit,
        "period_key": period_key,
        "source_platform": source_platform,
        "model_tier": model_tier,
    }


_ASK_INTENTS = {
    "ranking", "overview", "savings", "budget", "pruning", "source_mix",
    "blocked", "risk_events", "total", "comparison", "activity", "inactive",
    "tier_usage", "optimization",
}
_ASK_ENTITIES = {
    "person", "agent", "department", "context", "platform", "model", "overview"
}
_ASK_METRICS = {
    "spend_usd", "total_tokens", "request_count", "tokens_saved",
    "risk_event_count", "avg_cost_per_request",
}
_ASK_DIRECTIONS = {"asc", "desc"}
_ASK_PERIOD_KEYS = {
    "today", "yesterday", "this_week", "last_week", "this_month",
    "last_month", "this_quarter", "last_quarter", "this_year", "last_year",
}


def _validated_ask_intent(candidate: dict, fallback: dict) -> dict:
    """Accept only the small reporting vocabulary CostPilot can calculate."""
    candidate = candidate if isinstance(candidate, dict) else {}
    result = dict(fallback)
    if candidate.get("intent") in _ASK_INTENTS:
        result["intent"] = candidate["intent"]
    if candidate.get("entity") in _ASK_ENTITIES:
        result["entity"] = candidate["entity"]
    if candidate.get("metric") in _ASK_METRICS:
        result["metric"] = candidate["metric"]
    if candidate.get("direction") in _ASK_DIRECTIONS:
        result["direction"] = candidate["direction"]
    if candidate.get("period_key") in _ASK_PERIOD_KEYS:
        result["period_key"] = candidate["period_key"]
    for field in (
        "source_platform", "model_tier", "subject_entity",
        "subject_filter_name", "subject_filter_value",
    ):
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            result[field] = value.strip().lower()
    try:
        result["days"] = max(1, min(int(candidate.get("days")), 365))
    except (TypeError, ValueError):
        pass
    try:
        result["result_limit"] = max(
            1, min(int(candidate.get("result_limit")), 20)
        )
    except (TypeError, ValueError):
        pass
    return result


def _ask_conversation_text(request: AskCostPilotRequest) -> str:
    """Create a small, non-sensitive transcript for conversational reference."""
    turns = []
    for message in request.conversation[-8:]:
        content = " ".join((message.content or "").split())[:1200]
        if content:
            turns.append(f"{message.role.upper()}: {content}")
    turns.append(f"USER: {' '.join((request.question or '').split())[:2000]}")
    return "\n".join(turns)


def _ask_fallback_intent(request: AskCostPilotRequest) -> dict:
    """Resolve deterministic follow-ups using only prior validated report context."""
    current = _ask_intent(request.question, request.days)
    text = " ".join((request.question or "").lower().split())
    context = request.context.model_dump(exclude_none=True) if request.context else {}

    # Governance questions are complete, explicit intents. A prior ranking
    # context must never turn "show the latest risk events" into another model
    # or person ranking.
    if current["intent"] in {"blocked", "risk_events"}:
        return current

    explicit_metric = (
        "token" in text
        or any(term in text for term in (
            "cost", "spend", "spent", "dollar", "money",
            "request", "call", "volume", "usage",
        ))
    )
    explicit_entity = any(term in text for term in (
        "employee", "person", "people", "user", "who ", "agent", "bot",
        "department", "team", "business unit", "cost center", "account",
        "customer", "project", "matter", "opportunity", "business context",
        "work item", "platform", "provider", "source system", "model", "tier",
    ))
    explicit_direction = any(term in text for term in (
        "highest", "most", "top", "largest", "lowest", "least", "fewest",
        "smallest", "bottom",
    ))
    explicit_period = any(term in text for term in (
        "today", "yesterday", "day", "week", "month", "quarter", "year",
    ))
    explicit_count = bool(re.search(
        r"\b(?:top|bottom|first|last)\s+(?:the\s+)?(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twenty)\b(?!\s+days?\b)"
        r"|\b(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twenty)\s+"
        r"(?:highest|lowest|top|bottom|most|least|fewest)\b",
        text,
    ))

    if context:
        if not explicit_metric and context.get("metric") in _ASK_METRICS:
            current["metric"] = context["metric"]
        if not explicit_entity and context.get("entity") in _ASK_ENTITIES:
            current["entity"] = context["entity"]
        if current["intent"] == "overview" and context.get("intent") in _ASK_INTENTS:
            current["intent"] = context.get("intent", current["intent"])
        if not explicit_direction and context.get("direction") in _ASK_DIRECTIONS:
            current["direction"] = context["direction"]
        if not explicit_period and context.get("days"):
            current["days"] = max(1, min(int(context["days"]), 365))
            current["period_key"] = context.get("period_key")
        if not explicit_count and context.get("result_limit"):
            current["result_limit"] = max(1, min(int(context["result_limit"]), 20))
        if not current.get("source_platform") and context.get("source_platform"):
            current["source_platform"] = context["source_platform"]
        if not current.get("model_tier") and context.get("model_tier"):
            current["model_tier"] = context["model_tier"]
        for field in ("subject_entity", "subject_filter_name", "subject_filter_value"):
            if not current.get(field) and context.get(field):
                current[field] = context[field]
        # An explicit new ranking of the same entity starts a fresh scope. For
        # example, "top five users" after looking up Sheldon must not remain
        # restricted to Sheldon.
        if (
            current.get("intent") == "ranking"
            and current.get("entity") == current.get("subject_entity")
            and explicit_entity
        ):
            current.pop("subject_entity", None)
            current.pop("subject_filter_name", None)
            current.pop("subject_filter_value", None)
        return current

    if explicit_metric:
        return current

    for message in reversed(request.conversation[-8:]):
        if message.role != "user":
            continue
        prior_text = " ".join((message.content or "").lower().split())
        if "token" in prior_text:
            current["metric"] = "total_tokens"
            break
        if any(term in prior_text for term in ("cost", "spend", "spent", "dollar", "money")):
            current["metric"] = "spend_usd"
            break
        if any(term in prior_text for term in ("request", "call", "volume", "usage")):
            current["metric"] = "request_count"
            break
    return current


def _ask_reporting_filters(request: AskCostPilotRequest, parsed: dict) -> dict:
    """
    Keep cross-cutting report filters while removing a stale same-entity scope.

    A request for a people ranking cannot remain restricted to the person from
    the previous lookup. The same rule applies to agents, departments, and
    business contexts.
    """
    filters = {
        "project_id": request.project_id,
        "user_external_id": request.user_external_id,
        "agent_id": request.agent_id,
        "account_id": request.account_id,
        "source_platform": parsed.get("source_platform") or request.source_platform,
        "record_type": request.record_type,
        "model_tier": parsed.get("model_tier") or request.model_tier,
        "charged_unit": request.charged_unit,
        "business_purpose": request.business_purpose,
    }
    if parsed.get("intent") != "ranking":
        return filters

    entity = parsed.get("entity")
    if entity == "person":
        filters["user_external_id"] = None
    elif entity == "agent":
        filters["agent_id"] = None
    elif entity == "department":
        filters["charged_unit"] = None
    elif entity == "context":
        filters["project_id"] = None
        filters["account_id"] = None
    return filters


def _resolve_ask_intent(request: AskCostPilotRequest) -> tuple[dict, str]:
    """
    Let OpenAI interpret the reporting question, then validate the result.

    OpenAI never receives database rows and never calculates the answer. If the
    call is unavailable or invalid, the deterministic parser remains available.
    """
    global _ASK_OPENAI_DISABLED_UNTIL

    fallback = _ask_fallback_intent(request)
    if fallback["intent"] != "overview" or any((
        fallback.get("period_key"),
        fallback.get("source_platform"),
        fallback.get("model_tier"),
    )):
        return fallback, "deterministic"
    enabled = os.getenv("ASK_COSTPILOT_AI_ENABLED", "true").lower() not in {
        "0", "false", "no", "off"
    }
    api_key = os.getenv("OPENAI_API_KEY", "")
    if (
        not enabled
        or not api_key
        or api_key.startswith("YOUR")
        or time.monotonic() < _ASK_OPENAI_DISABLED_UNTIL
    ):
        return fallback, "deterministic_fallback"

    tool = {
        "type": "function",
        "name": "query_costpilot_usage",
        "description": (
            "Select the exact read-only CostPilot report needed to answer the "
            "user's question. This tool selects a report; it does not calculate facts."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": sorted(_ASK_INTENTS),
                },
                "entity": {
                    "type": "string",
                    "enum": sorted(_ASK_ENTITIES),
                },
                "metric": {
                    "type": "string",
                    "enum": sorted(_ASK_METRICS),
                },
                "direction": {
                    "type": "string",
                    "enum": sorted(_ASK_DIRECTIONS),
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                },
                "result_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": [
                "intent", "entity", "metric", "direction", "days", "result_limit"
            ],
            "additionalProperties": False,
        },
    }
    instructions = """You interpret questions for a read-only enterprise AI usage report.
Use the prior conversation to understand corrections and follow-up questions.
Choose ranking for highest, lowest, top, bottom, most, least, or "who" questions.
Choose blocked for questions asking why requests were blocked.
Choose risk_events for questions asking for recent or latest governance risk events.
Choose pruning for questions about tokens or context removed before model calls.
Use person for employees/users/people; agent for AI agents/bots; department for teams;
context for accounts/projects/matters/opportunities/work; platform for source systems;
model for model names or tiers; overview for company totals. Choose source_mix for
questions comparing live activity with simulator or test traffic.
Use total_tokens for token questions, spend_usd for cost/spend, and request_count for calls/usage.
Never infer employee productivity, performance, or business outcomes.
Always call query_costpilot_usage. Do not answer the question yourself."""

    try:
        from openai import OpenAI

        timeout_seconds = _ask_env_seconds(
            "ASK_COSTPILOT_AI_TIMEOUT_SECONDS", 4.0, 1.0, 10.0
        )
        client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        response = client.responses.create(
            model=os.getenv("ASK_COSTPILOT_MODEL", "gpt-4.1-mini"),
            instructions=instructions,
            input=_ask_conversation_text(request),
            tools=[tool],
            tool_choice="required",
        )
        for item in response.output:
            item_type = getattr(item, "type", None)
            item_name = getattr(item, "name", None)
            if item_type == "function_call" and item_name == tool["name"]:
                arguments = getattr(item, "arguments", "{}")
                return (
                    _validated_ask_intent(json.loads(arguments), fallback),
                    "openai",
                )
    except Exception as exc:
        cooldown_seconds = _ask_env_seconds(
            "ASK_COSTPILOT_AI_COOLDOWN_SECONDS", 300.0, 15.0, 3600.0
        )
        _ASK_OPENAI_DISABLED_UNTIL = time.monotonic() + cooldown_seconds
        logger.warning("Ask CostPilot OpenAI interpretation failed: %s", exc)
    return fallback, "deterministic_fallback"


def _ask_period_bounds(
    request: AskCostPilotRequest,
    parsed: dict,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Return exact calendar boundaries for natural-language periods."""
    if request.date_from or request.date_to:
        return request.date_from, request.date_to

    key = parsed.get("period_key")
    if not key:
        return None, None

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_week = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    this_quarter = today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1)
    this_year = today.replace(month=1, day=1)

    if key == "today":
        return today, now
    if key == "yesterday":
        return today - timedelta(days=1), today
    if key == "this_week":
        return this_week, now
    if key == "last_week":
        return this_week - timedelta(days=7), this_week
    if key == "this_month":
        return this_month, now
    if key == "last_month":
        previous = (this_month - timedelta(days=1)).replace(day=1)
        return previous, this_month
    if key == "this_quarter":
        return this_quarter, now
    if key == "last_quarter":
        previous_end = this_quarter
        previous_start = (previous_end - timedelta(days=1)).replace(
            month=(((previous_end - timedelta(days=1)).month - 1) // 3) * 3 + 1,
            day=1,
        )
        return previous_start, previous_end
    if key == "this_year":
        return this_year, now
    if key == "last_year":
        return this_year.replace(year=this_year.year - 1), this_year
    return None, None


def _ask_row_metric(row: dict, metric: str) -> float:
    """Read or calculate the bounded metric used by rankings and evidence."""
    if metric == "avg_cost_per_request":
        requests = float(row.get("request_count") or 0)
        return float(row.get("spend_usd") or 0) / requests if requests else 0.0
    return float(row.get(metric) or 0)


def _ask_rank(rows: list, metric: str, direction: str = "desc") -> list:
    ascending = direction == "asc"
    return sorted(
        rows or [],
        key=lambda row: (
            _ask_row_metric(row, metric) * (1 if ascending else -1),
            float(row.get("request_count") or 0) * (1 if ascending else -1),
            str(row.get("label") or ""),
        ),
    )


def _ask_metric_value(metric: str, value) -> tuple[str, str]:
    number = float(value or 0)
    if metric == "spend_usd":
        return f"${number:,.4f}", "AI spend"
    if metric == "request_count":
        return f"{int(number):,}", "governed requests"
    if metric == "tokens_saved":
        return f"{int(number):,}", "tokens pruned"
    if metric == "risk_event_count":
        return f"{int(number):,}", "risk events"
    if metric == "avg_cost_per_request":
        return f"${number:,.6f}", "average cost per request"
    return f"{int(number):,}", "tokens"


def _ask_evidence(
    rows: list,
    metric: str,
    filter_name: str,
    direction: str = "desc",
    limit: int = 5,
) -> list:
    evidence = []
    for row in _ask_rank(rows, metric, direction)[:limit]:
        value, metric_label = _ask_metric_value(
            metric, _ask_row_metric(row, metric)
        )
        evidence.append({
            "label": row.get("label") or "Unknown",
            "value": value,
            "metric_label": metric_label,
            "detail": (
                f"{int(row.get('request_count') or 0):,} requests · "
                f"{int(row.get('total_tokens') or 0):,} tokens · "
                f"${float(row.get('spend_usd') or 0):,.4f} · "
                f"{int(row.get('live_count') or 0):,} live / "
                f"{int(row.get('simulation_count') or 0):,} simulator"
            ),
            "filter_name": filter_name,
            "filter_value": row.get("id"),
            "live_count": int(row.get("live_count") or 0),
            "simulation_count": int(row.get("simulation_count") or 0),
        })
    return evidence


def _ask_audit_period(period: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    """Convert the attribution report's inclusive dates to audit boundaries."""
    try:
        start = datetime.strptime(str(period.get("date_from"))[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        start = None
    try:
        end = (
            datetime.strptime(str(period.get("date_to"))[:10], "%Y-%m-%d")
            + timedelta(days=1)
        )
    except (TypeError, ValueError):
        end = None
    return start, end


def _ask_audit_reason(event: AuditEvent) -> str:
    """Give a conservative, evidence-derived reason for a governance event."""
    text = " ".join((
        str(event.event_type or ""),
        str(event.decision_outcome or ""),
        str(event.rationale or ""),
        str(event.matched_keywords_json or ""),
    )).lower()
    if any(term in text for term in ("sensitive", "keyword", "pii", "policy term")):
        return "Sensitive-data or keyword policy"
    if any(term in text for term in ("budget", "cap", "throttle")):
        return "Budget or throttle policy"
    if any(term in text for term in ("collision", "concurr", "queue", "lock")):
        return "Concurrency or collision policy"
    return "Governance policy"


def _ask_governance_events(
    db: Session,
    request: AskCostPilotRequest,
    period: dict,
    intent: str,
    limit: int = 20,
) -> tuple[list[AuditEvent], int, int, int]:
    """Query only meaningful audit events while preserving report scope."""
    query = db.query(AuditEvent)
    start, end = _ask_audit_period(period)
    if start:
        query = query.filter(AuditEvent.timestamp >= start)
    if end:
        query = query.filter(AuditEvent.timestamp < end)
    if request.workspace_id:
        query = query.filter(or_(
            AuditEvent.workspace_id == request.workspace_id,
            AuditEvent.department.like(f"{request.workspace_id}:%"),
        ))
    if request.user_external_id:
        query = query.filter(
            AuditEvent.actor_external_id == request.user_external_id
        )
    if request.agent_id:
        query = query.filter(AuditEvent.agent_id == request.agent_id)
    if request.source_platform:
        query = query.filter(
            func.lower(AuditEvent.actor_source_platform)
            == request.source_platform.lower()
        )
    if request.record_type:
        query = query.filter(
            func.lower(AuditEvent.origin_record_type)
            == request.record_type.lower()
        )
    if request.charged_unit:
        query = query.filter(or_(
            func.lower(AuditEvent.charged_org_unit_name)
            == request.charged_unit.lower(),
            func.lower(AuditEvent.department).like(
                f"%:{request.charged_unit.lower()}"
            ),
            func.lower(AuditEvent.department) == request.charged_unit.lower(),
        ))
    if request.project_id:
        query = query.filter(AuditEvent.work_item.has(or_(
            WorkItem.external_id == request.project_id,
            WorkItem.source_record_id == request.project_id,
        )))

    event_text = func.lower(func.coalesce(AuditEvent.event_type, ""))
    outcome_text = func.lower(func.coalesce(AuditEvent.decision_outcome, ""))
    rationale_text = func.lower(func.coalesce(AuditEvent.rationale, ""))
    if intent == "blocked":
        query = query.filter(or_(
            outcome_text.like("%block%"),
            event_text.like("%block%"),
            rationale_text.like("%request blocked%"),
            rationale_text.like("%blocked by%"),
        ))
    else:
        risk_text = func.lower(func.coalesce(AuditEvent.risk_level, ""))
        query = query.filter(or_(
            risk_text.in_(("medium", "high", "critical")),
            outcome_text.like("%block%"),
            outcome_text.like("%lock%"),
            outcome_text.like("%queue%"),
            outcome_text.like("%skip%"),
            outcome_text.like("%throttle%"),
            event_text.like("%block%"),
            event_text.like("%lock%"),
            event_text.like("%collision%"),
            event_text.like("%throttle%"),
            rationale_text.like("%request blocked%"),
            rationale_text.like("%collision%"),
            rationale_text.like("%throttle%"),
        ))

    total = query.count()
    live_total = query.filter(AuditEvent.is_simulation.isnot(True)).count()
    simulation_total = query.filter(AuditEvent.is_simulation.is_(True)).count()
    return (
        query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .limit(max(1, min(int(limit or 20), 5000)))
        .all(),
        total,
        live_total,
        simulation_total,
    )


def _ask_governance_evidence(events: list[AuditEvent], limit: int) -> list:
    """Render audit-backed evidence with a direct event drill key."""
    evidence = []
    for event in events[:limit]:
        outcome = str(event.decision_outcome or event.event_type or "risk event")
        timestamp = event.timestamp.strftime("%b %d, %Y %I:%M %p") if event.timestamp else "Unknown time"
        subject = (
            event.origin_record_name
            or event.actor_name
            or f"Audit event {event.id}"
        )
        department = str(event.charged_org_unit_name or event.department or "Unassigned")
        evidence.append({
            "label": subject,
            "value": timestamp,
            "metric_label": outcome.replace("_", " ").title(),
            "detail": (
                f"{_ask_audit_reason(event)} · {department} · "
                f"{'simulator' if event.is_simulation else 'live'}"
            ),
            "filter_name": "audit_event_id",
            "filter_value": event.id,
            "live_count": 0 if event.is_simulation else 1,
            "simulation_count": 1 if event.is_simulation else 0,
        })
    return evidence


def _ask_risk_breakdown(events: list[AuditEvent], entity: str) -> list[dict]:
    """Aggregate risk events using only identities present on audit records."""
    rows: dict[str, dict] = {}
    for event in events:
        if entity == "person":
            row_id = event.actor_external_id or event.actor_email or event.actor_name
            label = event.actor_name or event.actor_email or event.actor_external_id
        elif entity == "agent":
            row_id = str(event.agent_id or "")
            label = getattr(event.agent, "name", None) or (
                f"Agent {event.agent_id}" if event.agent_id else None
            )
        elif entity == "department":
            row_id = event.charged_org_unit_name or event.department
            label = str(row_id or "").split(":")[-1] or None
        elif entity == "context":
            row_id = event.origin_record_id or (
                str(event.work_item_id) if event.work_item_id else None
            )
            label = event.origin_record_name or event.origin_record_id or row_id
        elif entity == "platform":
            row_id = event.actor_source_platform
            label = event.actor_source_platform
        elif entity == "model":
            row_id = event.model_tier
            label = event.model_tier
        else:
            continue
        if not row_id:
            continue
        key = str(row_id)
        row = rows.setdefault(key, {
            "id": row_id,
            "label": str(label or row_id),
            "risk_event_count": 0,
            "request_count": 0,
            "count": 0,
            "total_tokens": 0,
            "tokens_saved": 0,
            "spend_usd": 0.0,
            "live_count": 0,
            "simulation_count": 0,
        })
        row["risk_event_count"] += 1
        row["request_count"] += 1
        row["count"] += 1
        if event.is_simulation:
            row["simulation_count"] += 1
        else:
            row["live_count"] += 1
    return list(rows.values())


_ASK_NAME_STOP_WORDS = {
    "about", "account", "agent", "cost", "department", "employee", "has",
    "have", "many", "much", "person", "project", "request", "requests",
    "show", "spend", "team", "token", "tokens", "used", "usage", "user",
    "what", "which", "who", "with",
}


def _ask_name_tokens(value: str) -> set[str]:
    """Return meaningful tokens used to match a named reporting subject."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) >= 3 and token not in _ASK_NAME_STOP_WORDS
    }


def _ask_named_entity(question: str, report: dict) -> Optional[dict]:
    """
    Resolve an explicitly named person, agent, department, or work item.

    Matching happens only against labels already returned by CostPilot's
    deterministic attribution report. A single first/last-name token can
    identify a row only when it is unique across every candidate.
    """
    question_tokens = _ask_name_tokens(question)
    if not question_tokens:
        return None

    candidates = []
    configs = (
        ("person", "people_breakdown", "user_external_id", "person"),
        ("agent", "agent_breakdown", "agent_id", "agent"),
        (
            "department",
            "organizational_unit_breakdown",
            "charged_unit",
            "department or team",
        ),
        (
            "context",
            "project_breakdown",
            "project_id",
            (report.get("context_label_singular") or "business context").lower(),
        ),
        ("platform", "source_platform_breakdown", "source_platform", "platform"),
        ("model", "model_breakdown", "model_name", "model"),
    )
    for entity, breakdown_key, filter_name, entity_label in configs:
        for row in report.get(breakdown_key) or []:
            label = str(row.get("label") or "").strip()
            label_tokens = _ask_name_tokens(label)
            overlap = question_tokens & label_tokens
            if not overlap:
                continue
            candidates.append({
                "entity": entity,
                "entity_label": entity_label,
                "filter_name": filter_name,
                "row": row,
                "score": (
                    len(overlap),
                    1 if label_tokens and label_tokens.issubset(question_tokens) else 0,
                    len(" ".join(overlap)),
                ),
            })

    if not candidates:
        return None
    candidates.sort(key=lambda item: item["score"], reverse=True)
    if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
        return None
    return candidates[0]


def _agent_stats(db: Session, agent: RegisteredAgent, days: int) -> dict:
    """Compute performance metrics for one agent over the given window."""
    since = datetime.utcnow() - timedelta(days=days)

    txs = db.query(TokenTransaction).filter(
        TokenTransaction.agent_id  == agent.id,
        TokenTransaction.timestamp >= since,
    ).all()

    if not txs:
        return None

    total_calls     = len(txs)
    total_cost      = sum(t.cost_usd for t in txs)
    avg_cost        = total_cost / total_calls
    ECONOMY = {"Scout", "Analyst", "micro"}
    flagship_calls  = sum(1 for t in txs if t.model_tier not in ECONOMY)
    micro_calls     = total_calls - flagship_calls
    flagship_pct    = round(flagship_calls / total_calls * 100, 1)
    micro_pct       = round(micro_calls    / total_calls * 100, 1)
    pruned_calls    = sum(1 for t in txs if t.was_pruned)
    prune_rate      = round(pruned_calls / total_calls * 100, 1)
    tokens_saved    = sum(t.tokens_saved for t in txs)
    avg_tokens_saved= round(tokens_saved / total_calls)

    # Call frequency — calls per day
    calls_per_day   = round(total_calls / days, 1)

    # Peak hour analysis (0-23)
    from collections import Counter
    hour_counts = Counter(t.timestamp.hour for t in txs)
    top_hours   = sorted(hour_counts, key=hour_counts.get, reverse=True)[:3]

    # Cost trend: first half vs second half of the period
    mid = since + timedelta(days=days // 2)
    first_half  = [t for t in txs if t.timestamp < mid]
    second_half = [t for t in txs if t.timestamp >= mid]
    first_cost  = sum(t.cost_usd for t in first_half)  / max(len(first_half), 1)
    second_cost = sum(t.cost_usd for t in second_half) / max(len(second_half), 1)
    cost_trend  = "increasing" if second_cost > first_cost * 1.1 else \
                  "decreasing" if second_cost < first_cost * 0.9 else "stable"

    # Routing reason distribution
    from collections import Counter as C2
    reasons = C2(t.routing_reason for t in txs)
    top_reason = reasons.most_common(1)[0][0] if reasons else "ROUTINE"

    # What would full flagship have cost (no routing optimization)
    avg_input  = sum(t.input_tokens  for t in txs) / total_calls
    avg_output = sum(t.output_tokens for t in txs) / total_calls
    flagship_only_cost = (avg_input * FLAGSHIP_IN + avg_output * FLAGSHIP_OUT) * total_calls
    routing_savings    = round(flagship_only_cost - total_cost, 2)

    return {
        "agent_id":        agent.id,
        "agent_name":      agent.name,
        "department":      agent.department,
        "target_table":    agent.target_table,
        "collision_policy":agent.collision_policy,
        "total_calls":     total_calls,
        "calls_per_day":   calls_per_day,
        "total_cost_usd":  round(total_cost, 4),
        "avg_cost_usd":    round(avg_cost, 6),
        "flagship_pct":    flagship_pct,
        "micro_pct":       micro_pct,
        "prune_rate":      prune_rate,
        "avg_tokens_saved":avg_tokens_saved,
        "tokens_saved":    tokens_saved,
        "cost_trend":      cost_trend,
        "top_hours":       top_hours,
        "top_reason":      top_reason,
        "routing_savings": routing_savings,
        "days":            days,
    }


def _generate_simulated_review(stats: dict) -> dict:
    """
    Rule-based efficiency recommendations — used when CostPilot is in simulated mode.
    Produces realistic, specific recommendations without an API call.
    """
    name       = stats["agent_name"]
    dept       = stats["department"]
    cpd        = stats["calls_per_day"]
    fp         = stats["flagship_pct"]
    pr         = stats["prune_rate"]
    cost       = stats["total_cost_usd"]
    avg        = stats["avg_cost_usd"]
    trend      = stats["cost_trend"]
    savings    = stats["routing_savings"]
    days       = stats["days"]

    findings   = []
    recs       = []
    proj_save  = 0.0
    grade      = "A"

    # ── Frequency analysis ────────────────────────────────────────────────────
    if cpd > 200:
        findings.append(f"Running {cpd:.0f} calls/day — extremely high frequency for a {dept} agent.")
        batch_save = round(cost * 0.60, 2)
        recs.append(f"Introduce event-driven triggering instead of polling. Batching similar requests could reduce call volume by 60%, saving ~${batch_save:,.2f} over {days} days with minimal throughput impact.")
        proj_save += batch_save
        grade = "C"
    elif cpd > 50:
        findings.append(f"Running {cpd:.0f} calls/day — moderate-to-high frequency.")
        batch_save = round(cost * 0.30, 2)
        recs.append(f"Consider batching requests into 15-minute windows during peak hours. Estimated savings: ~${batch_save:,.2f} over {days} days.")
        proj_save += batch_save
        grade = "B" if grade == "A" else grade

    # ── Flagship ratio analysis ────────────────────────────────────────────────
    if fp > 60:
        findings.append(f"{fp}% of calls escalated to flagship model — unusually high for this agent type.")
        fp_save = round(cost * 0.45, 2)
        recs.append(f"Review your sensitive term library and routing thresholds. Many COMPLEX decisions may be over-triggered. Tuning escalation criteria could shift 30-40% of calls back to micro, saving ~${fp_save:,.2f} over {days} days.")
        proj_save += fp_save
        grade = "C"
    elif fp < 10 and dept in ("Support", "Operations"):
        findings.append(f"Only {fp}% flagship usage for a {dept} agent — possibly under-escalating high-risk content.")
        recs.append("Review your sensitive term library. Support and Operations agents typically see 15-30% flagship usage. Under-escalation may expose compliance risk.")
        grade = "B" if grade == "A" else grade

    # ── Pruning analysis ──────────────────────────────────────────────────────
    if pr < 50:
        findings.append(f"Only {pr}% of payloads are being pruned — significant noise likely in upstream inputs.")
        prune_save = round(cost * 0.25, 2)
        recs.append(f"Improve upstream data quality before payloads reach CostPilot. Structured Salesforce fields instead of raw case descriptions could push pruning rate to 70%+, saving ~${prune_save:,.2f} over {days} days.")
        proj_save += prune_save
        grade = "B" if grade == "A" else grade
    elif pr > 80:
        findings.append(f"Excellent pruning rate of {pr}% — context sweeper working efficiently.")

    # ── Cost trend analysis ───────────────────────────────────────────────────
    if trend == "increasing":
        findings.append("Cost per call is trending upward — payload complexity growing over time.")
        recs.append("Monitor for prompt injection or expanding input sizes from upstream systems. Consider adding a token budget cap per call.")
        grade = "B" if grade == "A" else grade
    elif trend == "stable":
        findings.append("Cost per call is stable — consistent, predictable workload.")

    # ── Routing savings highlight ─────────────────────────────────────────────
    if savings > 0:
        findings.append(f"Smart routing saved ${savings:,.2f} vs. running all calls at flagship rates.")

    # ── Grade assignment ──────────────────────────────────────────────────────
    if not recs:
        grade = "A"
        summary = f"{name} is operating efficiently. Call volume, model routing, and pruning rates are all within optimal parameters. No changes recommended at this time."
    else:
        if grade == "A": grade = "B"
        summary = f"{name} processed {stats['total_calls']:,} calls over {days} days at ${cost:,.4f} total cost (${avg:.6f}/call). " + " ".join(findings[:2])

    return {
        "agent_name":        name,
        "department":        dept,
        "grade":             grade,
        "summary":           summary,
        "findings":          findings,
        "recommendations":   recs,
        "projected_savings": round(proj_save, 2),
        "stats":             stats,
        "generated_by":      "simulated",
    }


def _generate_live_review(stats: dict) -> dict:
    """
    Uses the configured flagship model to write a plain-English efficiency review
    for one agent based on its actual performance data.
    """
    from core.model_client import MODEL_MODE, PROVIDER, OPENAI_KEY, ANTHROPIC_KEY, \
                                   OPENAI_FLAGSHIP, ANTHROPIC_FLAGSHIP

    prompt = f"""You are a FinOps AI efficiency analyst reviewing bot performance data for an enterprise AI governance platform.

Analyze this agent's performance and provide a concise efficiency review.

Agent: {stats['agent_name']}
Department: {stats['department']}
Target system: {stats['target_table']}
Period: Last {stats['days']} days

Performance data:
- Total calls: {stats['total_calls']:,}
- Calls per day: {stats['calls_per_day']}
- Total cost: ${stats['total_cost_usd']:,.4f}
- Avg cost per call: ${stats['avg_cost_usd']:.6f}
- Flagship model usage: {stats['flagship_pct']}%
- Micro model usage: {stats['micro_pct']}%
- Payload pruning rate: {stats['prune_rate']}% of calls pruned
- Avg tokens saved per call (pruning): {stats['avg_tokens_saved']:,}
- Cost trend: {stats['cost_trend']}
- Most common routing reason: {stats['top_reason']}
- Savings vs all-flagship baseline: ${stats['routing_savings']:,.2f}

Respond in this exact JSON format:
{{
  "grade": "A|B|C|D",
  "summary": "2-3 sentence plain English summary of this agent's efficiency",
  "findings": ["finding 1", "finding 2", "finding 3"],
  "recommendations": ["specific actionable recommendation with projected impact", "..."],
  "projected_savings": <number — estimated USD savings over next 30 days if recommendations followed>
}}

Grading: A=highly efficient, B=good with minor improvements, C=significant optimization opportunity, D=urgent review needed.
Be specific. Reference actual numbers. Keep recommendations actionable."""

    try:
        if PROVIDER == "anthropic" and ANTHROPIC_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            resp = client.messages.create(
                model=ANTHROPIC_FLAGSHIP,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
        else:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_KEY)
            resp = client.chat.completions.create(
                model=OPENAI_FLAGSHIP,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content

        import json
        parsed = json.loads(raw)
        return {
            "agent_name":        stats["agent_name"],
            "department":        stats["department"],
            "grade":             parsed.get("grade", "B"),
            "summary":           parsed.get("summary", ""),
            "findings":          parsed.get("findings", []),
            "recommendations":   parsed.get("recommendations", []),
            "projected_savings": float(parsed.get("projected_savings", 0)),
            "stats":             stats,
            "generated_by":      "ai",
        }
    except Exception as e:
        # Fall back to simulated if live call fails
        result = _generate_simulated_review(stats)
        result["generated_by"] = f"simulated (fallback: {str(e)[:60]})"
        return result


@router.post("/ask")
def ask_costpilot(
    request: AskCostPilotRequest,
    db: Session = Depends(get_db),
):
    """
    Answer executive questions using CostPilot-calculated facts.

    The natural-language layer selects a bounded reporting intent; totals,
    rankings, and evidence always come from the deterministic attribution
    report. This endpoint is read-only and cannot change routing or policy.
    """
    from api.routes_work_items import project_activity_reporting

    question = (request.question or "").strip()
    if not question:
        return {
            "title": "Ask a question about your AI usage",
            "answer": "Enter a question about spend, tokens, people, agents, departments, or business work.",
            "intent": "help",
            "evidence": [],
            "recommendations": [],
        }

    parsed, assistant_mode = _resolve_ask_intent(request)
    reporting_filters = _ask_reporting_filters(request, parsed)
    subject_filter_name = parsed.get("subject_filter_name")
    subject_filter_value = parsed.get("subject_filter_value")
    if (
        subject_filter_name in reporting_filters
        and subject_filter_value not in (None, "")
        and reporting_filters.get(subject_filter_name) in (None, "")
    ):
        if subject_filter_name == "agent_id":
            try:
                subject_filter_value = int(subject_filter_value)
            except (TypeError, ValueError):
                subject_filter_value = None
        reporting_filters[subject_filter_name] = subject_filter_value
    date_from, date_to = _ask_period_bounds(request, parsed)
    report = project_activity_reporting(
        workspace_id=request.workspace_id,
        date_from=date_from,
        date_to=date_to,
        days=parsed["days"],
        **reporting_filters,
        activity_limit=2000,
        db=db,
    )
    summary = report.get("summary") or {}
    period = report.get("period") or {}
    context_plural = report.get("context_label_plural") or "Business Contexts"
    metric = parsed["metric"]
    intent = parsed["intent"]
    entity = parsed["entity"]
    direction = parsed["direction"]
    result_limit = parsed["result_limit"]
    evidence = []
    recommendations = []
    title = "AI usage overview"
    calculation_formula = None
    calculation_row_count = None
    named_entity = _ask_named_entity(question, report)

    entity_config = {
        "person": ("people_breakdown", "user_external_id", "People"),
        "agent": ("agent_breakdown", "agent_id", "Agents"),
        "department": ("organizational_unit_breakdown", "charged_unit", "Departments and teams"),
        "context": ("project_breakdown", "project_id", context_plural),
        "platform": ("source_platform_breakdown", "source_platform", "Platforms"),
        # Model evidence is still useful, but the attribution report does not
        # currently expose a model selector. Do not render a dead drill link.
        "model": ("model_breakdown", None, "Models"),
    }

    period_from = str(period.get("date_from") or "")[:10]
    period_to = str(period.get("date_to") or "")[:10]
    period_label = (
        f"{period_from} through {period_to}"
        if period_from and period_to else f"the last {parsed['days']} days"
    )
    live_count = int(summary.get("live_count") or 0)
    simulation_count = int(summary.get("simulation_count") or 0)
    if live_count and simulation_count:
        data_scope = "mixed"
    elif simulation_count:
        data_scope = "simulator"
    elif live_count:
        data_scope = "live"
    else:
        data_scope = "no_activity"

    if intent == "comparison":
        current_value = _ask_row_metric(summary, metric)
        current_start, current_end = _ask_audit_period(period)
        if current_start and current_end:
            span = max(current_end - current_start, timedelta(days=1))
            previous_end = current_start
            previous_start = previous_end - span
            prior_report = project_activity_reporting(
                workspace_id=request.workspace_id,
                date_from=previous_start,
                date_to=previous_end,
                days=parsed["days"],
                **reporting_filters,
                activity_limit=1,
                db=db,
            )
            prior_summary = prior_report.get("summary") or {}
            prior_value = _ask_row_metric(prior_summary, metric)
            change = current_value - prior_value
            pct = (change / prior_value * 100) if prior_value else None
            current_display, metric_label = _ask_metric_value(metric, current_value)
            prior_display, _ = _ask_metric_value(metric, prior_value)
            change_display, _ = _ask_metric_value(metric, abs(change))
            direction_word = "increased" if change > 0 else "decreased" if change < 0 else "did not change"
            title = f"{metric_label.title()} comparison"
            answer = (
                f"{metric_label.title()} was {current_display} for {period_label}, "
                f"compared with {prior_display} in the immediately preceding period. "
                f"It {direction_word} by {change_display}"
                + (f" ({abs(pct):.1f}%)." if pct is not None else ".")
            )
            evidence = [
                {"label": period_label, "value": current_display, "metric_label": metric_label,
                 "detail": f"{int(summary.get('request_count') or 0):,} governed requests",
                 "filter_name": None, "filter_value": None},
                {"label": "Immediately preceding period", "value": prior_display, "metric_label": metric_label,
                 "detail": f"{int(prior_summary.get('request_count') or 0):,} governed requests",
                 "filter_name": None, "filter_value": None},
            ]
            calculation_formula = "Current period total minus the immediately preceding equal-length period total"
        else:
            title = "Period comparison"
            answer = "Choose a date range or ask for this week, month, quarter, or year so CostPilot can calculate an equal-period comparison."
    elif intent == "activity":
        scoped_report = report
        supported_filters = set(reporting_filters)
        if (
            named_entity
            and named_entity.get("filter_name") in supported_filters
        ):
            scoped_filters = dict(reporting_filters)
            scoped_filters[named_entity["filter_name"]] = named_entity["row"].get("id")
            scoped_report = project_activity_reporting(
                workspace_id=request.workspace_id,
                date_from=date_from,
                date_to=date_to,
                days=parsed["days"],
                **scoped_filters,
                activity_limit=max(25, result_limit),
                db=db,
            )
        activity_rows = scoped_report.get("activities") or []
        text_lower = question.lower()
        if "which agent" in text_lower or "agents contributed" in text_lower:
            rows = scoped_report.get("agent_breakdown") or []
            evidence = _ask_evidence(rows, metric, "agent_id", limit=result_limit)
            title = "Agents contributing to this activity"
        elif "who used" in text_lower or "which user" in text_lower or "which people" in text_lower:
            rows = scoped_report.get("people_breakdown") or []
            evidence = _ask_evidence(rows, metric, "user_external_id", limit=result_limit)
            title = "People contributing to this activity"
        else:
            title = "Supporting AI activity"
            evidence = [{
                "label": row.get("project_name") or row.get("agent_name") or "AI request",
                "value": str(row.get("timestamp") or "")[:19].replace("T", " "),
                "metric_label": row.get("routing_decision") or "governed request",
                "detail": (
                    f"{row.get('user_name') or 'Unknown user'} · {row.get('agent_name') or 'Unknown agent'} · "
                    f"{int(row.get('total_tokens') or 0):,} tokens · ${float(row.get('cost_usd') or 0):,.4f} · "
                    f"{'simulator' if row.get('is_simulation') else 'live'}"
                ),
                "filter_name": "transaction_id",
                "filter_value": row.get("transaction_id"),
                "live_count": 0 if row.get("is_simulation") else 1,
                "simulation_count": 1 if row.get("is_simulation") else 0,
            } for row in activity_rows[:result_limit]]
        answer = (
            f"Showing {len(evidence):,} matching result{'s' if len(evidence) != 1 else ''} "
            f"for {period_label}."
            if evidence else f"No matching AI activity was recorded for {period_label}."
        )
    elif intent == "inactive":
        used_ids = {row.get("id") for row in report.get("agent_breakdown") or []}
        agent_query = db.query(RegisteredAgent)
        if request.workspace_id:
            agent_query = agent_query.filter(
                RegisteredAgent.department.like(f"{request.workspace_id}:%")
            )
        agent_query = agent_query.filter(
            RegisteredAgent.archived.isnot(True)
        )
        inactive_agents = [agent for agent in agent_query.all() if agent.id not in used_ids]
        inactive_agents.sort(key=lambda agent: (agent.last_used_at or datetime.min, agent.name or ""))
        evidence = [{
            "label": agent.name or "Unnamed agent",
            "value": "Never" if not agent.last_used_at else agent.last_used_at.strftime("%b %d, %Y"),
            "metric_label": "last used",
            "detail": f"{agent.source_platform or 'Unknown platform'} · {agent.department or 'Unassigned'}",
            "filter_name": "agent_id",
            "filter_value": agent.id,
        } for agent in inactive_agents[:result_limit]]
        title = "Agents without recent usage"
        answer = f"{len(inactive_agents):,} registered agents had no governed requests for {period_label}."
    elif intent == "tier_usage":
        title = f"{(parsed.get('model_tier') or 'Selected tier').title()} routing usage"
        value, metric_label = _ask_metric_value(metric, _ask_row_metric(summary, metric))
        answer = (
            f"CostPilot routed {int(summary.get('request_count') or 0):,} requests using "
            f"{int(summary.get('total_tokens') or 0):,} tokens and ${float(summary.get('spend_usd') or 0):,.4f} "
            f"in spend to the {(parsed.get('model_tier') or 'selected').title()} tier for {period_label}."
        )
        evidence = _ask_evidence(report.get("model_breakdown") or [], metric, None, limit=result_limit)
    elif named_entity and intent not in {
        "budget", "savings", "optimization", "pruning", "blocked", "risk_events", "ranking"
    }:
        entity = named_entity["entity"]
        row = named_entity["row"]
        filter_name = named_entity["filter_name"]
        entity_label = named_entity["entity_label"]
        value, metric_label = _ask_metric_value(metric, _ask_row_metric(row, metric))
        title = f"{row.get('label') or 'Named entity'} AI usage"
        answer = (
            f"{row.get('label') or 'The selected entity'} used {value} "
            f"{metric_label} for {period_label}. "
            f"That includes {int(row.get('request_count') or 0):,} governed requests, "
            f"{int(row.get('total_tokens') or 0):,} tokens, and "
            f"${float(row.get('spend_usd') or 0):,.4f} in AI spend."
        )
        evidence = _ask_evidence(
            [row],
            metric,
            filter_name,
            limit=1,
        )
        intent = "lookup"
    elif intent == "total":
        value, metric_label = _ask_metric_value(metric, _ask_row_metric(summary, metric))
        title = f"Total {metric_label}"
        answer = (
            f"CostPilot recorded {value} in {metric_label} for {period_label}, across "
            f"{int(summary.get('request_count') or 0):,} governed requests."
        )
        evidence = [{
            "label": period_label,
            "value": value,
            "metric_label": metric_label,
            "detail": (
                f"{int(summary.get('total_tokens') or 0):,} tokens · "
                f"${float(summary.get('spend_usd') or 0):,.4f} · "
                f"{live_count:,} live / {simulation_count:,} simulator"
            ),
            "filter_name": None,
            "filter_value": None,
            "live_count": live_count,
            "simulation_count": simulation_count,
        }]
    elif intent in {"blocked", "risk_events"}:
        (
            governance_events,
            governance_total,
            live_count,
            simulation_count,
        ) = _ask_governance_events(db, request, period, intent)
        evidence = _ask_governance_evidence(governance_events, result_limit)
        calculation_row_count = governance_total
        if intent == "blocked":
            title = "Why requests were blocked"
            calculation_formula = (
                "Count audit events with a blocked decision or blocking rationale"
            )
            if governance_total:
                reasons = {}
                for event in governance_events:
                    reason = _ask_audit_reason(event)
                    reasons[reason] = reasons.get(reason, 0) + 1
                top_reason, top_reason_count = max(
                    reasons.items(),
                    key=lambda item: (item[1], item[0]),
                )
                answer = (
                    f"CostPilot recorded {governance_total:,} blocked "
                    f"request{'s' if governance_total != 1 else ''} for "
                    f"{period_label}. Among the latest "
                    f"{len(governance_events):,} matching events, the most common "
                    f"reason was {top_reason.lower()} "
                    f"({top_reason_count:,} event"
                    f"{'s' if top_reason_count != 1 else ''})."
                )
                recommendations.append({
                    "title": "Review the blocking evidence",
                    "body": (
                        "Open an event below before changing policy. The audit "
                        "record shows the applied control, source, and business context."
                    ),
                })
            else:
                answer = (
                    f"No blocked requests were recorded for {period_label} "
                    "within the active filters."
                )
        else:
            title = "Latest risk events"
            calculation_formula = (
                "Count meaningful audit events with elevated risk or a control outcome"
            )
            if governance_total:
                answer = (
                    f"Showing the latest {len(evidence):,} of "
                    f"{governance_total:,} risk and control events for "
                    f"{period_label}. Routine routing decisions are excluded."
                )
            else:
                answer = (
                    f"No meaningful risk or control events were recorded for "
                    f"{period_label} within the active filters."
                )
    elif intent == "budget":
        budget_query = db.query(DepartmentBudget).filter(
            DepartmentBudget.archived.isnot(True)
        )
        budgets = budget_query.all()
        if request.workspace_id:
            workspace_prefix = f"{request.workspace_id}:"
            scoped = [
                budget for budget in budgets
                if (budget.department or "").startswith(workspace_prefix)
            ]
            budgets = scoped or [
                budget for budget in budgets
                if ":" not in str(budget.department or "")
            ]
        over_limit = any(term in question.lower() for term in (
            "over budget", "exceeded", "over cap", "above budget"
        ))
        threshold = 100 if over_limit else 70
        budget_rows = []
        for budget in budgets:
            cap = float(budget.monthly_cap_usd or 0)
            spent = float(budget.current_spend_usd or 0)
            pct = spent / cap * 100 if cap > 0 else 0
            if pct >= threshold:
                label = (budget.department or "Unassigned").split(":")[-1]
                budget_rows.append({
                    "id": budget.department,
                    "label": label,
                    "pct": pct,
                    "spent": spent,
                    "cap": cap,
                    "throttled": bool(budget.throttled),
                })
        budget_rows.sort(key=lambda row: -row["pct"])
        evidence = [{
            "label": row["label"],
            "value": f"{row['pct']:.1f}%",
            "metric_label": "budget used",
            "detail": f"${row['spent']:,.2f} of ${row['cap']:,.2f}" +
                      (" · throttled" if row["throttled"] else ""),
            "filter_name": "charged_unit",
            "filter_value": row["label"],
        } for row in budget_rows[:5]]
        title = "Budget watch"
        answer = (
            f"{len(budget_rows)} department{'s are' if len(budget_rows) != 1 else ' is'} "
            f"at or above {threshold}% of its monthly AI budget."
            if budget_rows else
            f"No active department is at or above {threshold}% of its monthly AI budget."
        )
    elif intent == "pruning":
        saved_tokens = int(summary.get("tokens_saved") or 0)
        input_tokens = int(summary.get("input_tokens") or 0)
        request_count = int(summary.get("request_count") or 0)
        candidate_tokens = input_tokens + saved_tokens
        reduction_pct = (
            saved_tokens / candidate_tokens * 100
            if candidate_tokens > 0 else 0.0
        )
        spend = float(summary.get("spend_usd") or 0)
        total_tokens = int(summary.get("total_tokens") or 0)
        estimated_saved_usd = (
            saved_tokens * spend / total_tokens if total_tokens > 0 else 0.0
        )
        asks_for_money = any(term in question.lower() for term in (
            "money", "dollar", "cost", "save", "saving"
        ))
        title = "Pruning impact"
        if asks_for_money:
            answer = (
                f"CostPilot removed {saved_tokens:,} tokens before model calls "
                f"for {period_label}, representing an estimated ${estimated_saved_usd:,.4f} "
                f"in avoided model cost at the period's blended token rate. "
                f"That reduced candidate input context by {reduction_pct:.1f}%."
            )
            calculation_formula = (
                "Tokens pruned multiplied by recorded spend divided by tokens sent to models"
            )
        else:
            answer = (
                f"CostPilot removed {saved_tokens:,} tokens before model calls "
                f"for {period_label}. That reduced the candidate "
                f"input context by {reduction_pct:.1f}% across "
                f"{request_count:,} governed requests."
            )
        evidence = [{
            "label": "Tokens removed before model calls",
            "value": (
                f"${estimated_saved_usd:,.4f}" if asks_for_money
                else f"{saved_tokens:,}"
            ),
            "metric_label": (
                "estimated avoided cost" if asks_for_money else "tokens pruned"
            ),
            "detail": (
                f"{input_tokens:,} input tokens reached models · "
                f"{request_count:,} governed requests"
            ),
            "filter_name": None,
            "filter_value": None,
        }]
    elif intent == "source_mix":
        total = live_count + simulation_count
        live_pct = live_count / total * 100 if total else 0.0
        simulator_pct = simulation_count / total * 100 if total else 0.0
        title = "Live and simulator activity"
        answer = (
            f"For {period_label}, {live_count:,} requests ({live_pct:.1f}%) were "
            f"live business activity and {simulation_count:,} ({simulator_pct:.1f}%) "
            "came from the CostPilot simulator."
        )
        evidence = [
            {
                "label": "Live business activity",
                "value": f"{live_count:,}",
                "metric_label": "requests",
                "detail": f"{live_pct:.1f}% of governed activity",
                "filter_name": None,
                "filter_value": None,
                "live_count": live_count,
                "simulation_count": 0,
            },
            {
                "label": "Simulator activity",
                "value": f"{simulation_count:,}",
                "metric_label": "requests",
                "detail": f"{simulator_pct:.1f}% of governed activity",
                "filter_name": "project_id",
                "filter_value": "__simulator__",
                "live_count": 0,
                "simulation_count": simulation_count,
            },
        ]
    elif intent in {"savings", "optimization"}:
        activities = report.get("activities") or []
        premium = [
            row for row in activities
            if str(row.get("model_tier") or "").lower() in {
                "advisor", "strategist", "flagship"
            }
        ]
        premium_spend = sum(float(row.get("cost_usd") or 0) for row in premium)
        premium_requests = len(premium)
        premium_scope = (
            f"Among the {len(activities):,} most recent matching requests, "
            if int(report.get("activity_count") or 0) > len(activities)
            else ""
        )
        total_spend = float(summary.get("spend_usd") or 0)
        saved_tokens = int(summary.get("tokens_saved") or 0)
        top_agents = _ask_rank(report.get("agent_breakdown") or [], "spend_usd")
        title = "Cost-saving opportunities"
        estimated_pruning_savings = (
            saved_tokens * total_spend / int(summary.get("total_tokens") or 0)
            if int(summary.get("total_tokens") or 0) > 0 else 0.0
        )
        period_days = max(1, int(parsed.get("days") or 30))
        projected_annual = estimated_pruning_savings * 365 / period_days
        answer = (
            f"{premium_scope}CostPilot found {premium_requests:,} premium-tier requests representing "
            f"${premium_spend:,.4f} of ${total_spend:,.4f} total spend. "
            f"Pruning removed {saved_tokens:,} tokens before model calls, an estimated "
            f"${estimated_pruning_savings:,.4f} avoided in this period and "
            f"${projected_annual:,.2f} annualized at the same pace."
        )
        if premium_requests:
            recommendations.append({
                "title": "Review premium-tier routing",
                "body": (
                    f"Inspect the {premium_requests:,} Advisor or Strategist requests before "
                    "changing thresholds. This is reviewable spend, not guaranteed savings."
                ),
            })
        if top_agents:
            recommendations.append({
                "title": f"Start with {top_agents[0].get('label') or 'the top-cost agent'}",
                "body": (
                    f"It accounts for ${float(top_agents[0].get('spend_usd') or 0):,.4f} "
                    "in this period. Compare its task mix and routing evidence before acting."
                ),
            })
        if saved_tokens == 0:
            recommendations.append({
                "title": "Check pruning coverage",
                "body": "No pruned tokens were recorded in this scope. Confirm pruning is enabled on the active agents.",
            })
        evidence = _ask_evidence(
            report.get("agent_breakdown") or [], "spend_usd", "agent_id"
        )
    elif intent == "ranking" and entity in entity_config:
        breakdown_key, filter_name, entity_label = entity_config[entity]
        ranking_rows = report.get(breakdown_key) or []
        if metric == "risk_event_count":
            risk_request = request.model_copy(update={
                key: value for key, value in reporting_filters.items()
                if key in AskCostPilotRequest.model_fields
            })
            risk_events, risk_total, risk_live, risk_simulation = (
                _ask_governance_events(
                    db, risk_request, period, "risk_events", limit=5000
                )
            )
            ranking_rows = _ask_risk_breakdown(risk_events, entity)
            calculation_row_count = risk_total
            calculation_formula = (
                "Count meaningful audit events grouped by the selected entity"
            )
            live_count = risk_live
            simulation_count = risk_simulation
        ranked = _ask_rank(ranking_rows, metric, direction)
        evidence = _ask_evidence(
            ranked,
            metric,
            filter_name,
            direction=direction,
            limit=result_limit,
        )
        rank_label = "Lowest" if direction == "asc" else "Top"
        title = f"{rank_label} {entity_label.lower()} by {_ask_metric_value(metric, 0)[1]}"
        if ranked:
            value, metric_label = _ask_metric_value(
                metric, _ask_row_metric(ranked[0], metric)
            )
            if result_limit > 1:
                available = min(result_limit, len(ranked))
                qualifier = (
                    f"the {available} matching {entity_label.lower()}"
                    if available == result_limit
                    else f"all {available} matching {entity_label.lower()} available"
                )
                answer = (
                    f"Showing {qualifier}, ordered from "
                    f"{'lowest to highest' if direction == 'asc' else 'highest to lowest'} "
                    f"{metric_label} for {period_label}. "
                    f"{ranked[0].get('label') or 'Unknown'} is first at {value}."
                )
            else:
                answer = (
                    f"{ranked[0].get('label') or 'Unknown'} had the "
                    f"{'lowest' if direction == 'asc' else 'highest'} {metric_label} "
                    f"for {period_label}: {value}. "
                    f"That includes {int(ranked[0].get('request_count') or 0):,} governed requests."
                )
        else:
            answer = f"No {entity_label.lower()} had attributed AI activity in this period."
    else:
        context_rows = report.get("project_breakdown") or []
        title = "Where your AI usage went"
        answer = (
            f"For {period_label}, CostPilot governed "
            f"{int(summary.get('request_count') or 0):,} requests using "
            f"{int(summary.get('total_tokens') or 0):,} tokens at a cost of "
            f"${float(summary.get('spend_usd') or 0):,.4f}. "
            f"{int(summary.get('people_count') or 0):,} identified people and "
            f"{int(summary.get('agent_count') or 0):,} agents contributed to that usage."
        )
        evidence = _ask_evidence(context_rows, "spend_usd", "project_id")

    if live_count and simulation_count:
        data_scope = "mixed"
    elif simulation_count:
        data_scope = "simulator"
    elif live_count:
        data_scope = "live"
    else:
        data_scope = "no_activity"

    active_filters = {
        key: value for key, value in (report.get("filters") or {}).items()
        if value not in (None, "")
    }
    calculation = {
        "metric": metric,
        "formula": calculation_formula or (
            "Sum of input tokens plus output tokens"
            if metric == "total_tokens"
            else "Sum of tokens removed before model calls"
            if metric == "tokens_saved"
            else "Count of governed AI requests"
            if metric == "request_count"
            else "Count of governance events marked as blocked or risky"
            if metric == "risk_event_count"
            else "Recorded model-call cost divided by governed request count"
            if metric == "avg_cost_per_request"
            else "Sum of recorded model-call cost"
        ),
        "row_count": (
            calculation_row_count
            if calculation_row_count is not None
            else int(summary.get("request_count") or 0)
        ),
        "period_label": period_label,
    }
    conversation_context = {
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "direction": direction,
        "days": int(parsed["days"]),
        "result_limit": int(result_limit),
        "period_key": parsed.get("period_key"),
        "source_platform": parsed.get("source_platform"),
        "model_tier": parsed.get("model_tier"),
    }
    for field in ("subject_entity", "subject_filter_name", "subject_filter_value"):
        if parsed.get(field) not in (None, ""):
            conversation_context[field] = parsed[field]
    if named_entity and named_entity.get("filter_name") in reporting_filters:
        conversation_context.update({
            "subject_entity": named_entity.get("entity"),
            "subject_filter_name": named_entity.get("filter_name"),
            "subject_filter_value": named_entity.get("row", {}).get("id"),
        })

    return {
        "question": question,
        "title": title,
        "answer": answer,
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "period": period,
        "filters": report.get("filters") or {},
        "summary": summary,
        "evidence": evidence,
        "recommendations": recommendations,
        "measurement_note": report.get("measurement_note"),
        "calculation": calculation,
        "calculation_source": "CostPilot deterministic attribution engine",
        "data_provenance": {
            "scope": data_scope,
            "live_requests": live_count,
            "simulator_requests": simulation_count,
            "active_filters": active_filters,
            "period_label": period_label,
        },
        "assistant_mode": assistant_mode,
        "interpreted_intent": parsed,
        "conversation_context": conversation_context,
        "read_only": True,
    }


@router.post("")
def generate_efficiency_review(
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Analyzes all registered agents and returns AI-written efficiency reviews
    with grades, findings, recommendations, and projected savings.
    """
    from core.model_client import MODEL_MODE

    agents = db.query(RegisteredAgent).all()
    if not agents:
        return {"reviews": [], "total_agents": 0, "total_projected_savings": 0,
                "message": "No agents registered. Connect a platform and route some calls first."}

    reviews = []
    total_savings = 0.0

    for agent in agents:
        stats = _agent_stats(db, agent, days)
        if not stats:
            continue  # skip agents with no data in this period

        if MODEL_MODE == "live":
            review = _generate_live_review(stats)
        else:
            review = _generate_simulated_review(stats)

        reviews.append(review)
        total_savings += review.get("projected_savings", 0)

    # Sort: worst grade first (most improvement opportunity at top)
    grade_order = {"D": 0, "C": 1, "B": 2, "A": 3}
    reviews.sort(key=lambda r: grade_order.get(r["grade"], 2))

    # Overall fleet summary
    if reviews:
        grade_counts = {}
        for r in reviews:
            grade_counts[r["grade"]] = grade_counts.get(r["grade"], 0) + 1
        fleet_grade = min(reviews, key=lambda r: grade_order.get(r["grade"], 2))["grade"]
    else:
        grade_counts = {}
        fleet_grade  = "N/A"

    return {
        "reviews":                 reviews,
        "total_agents_analyzed":   len(reviews),
        "total_projected_savings": round(total_savings, 2),
        "fleet_grade":             fleet_grade,
        "grade_counts":            grade_counts,
        "period_days":             days,
        "generated_at":            datetime.utcnow().isoformat(),
        "generated_by":            "ai" if MODEL_MODE == "live" else "simulated",
    }
