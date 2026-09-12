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
import calendar
import json
import logging
import os
import re
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from core.auth import check_membership
from database.db import get_db
from database.models import (
    AskInteraction,
    AuditEvent,
    DepartmentBudget,
    RegisteredAgent,
    TokenTransaction,
    WorkItem,
    WorkspaceAnalyticsSettings,
)
from core.costpilot_knowledge import search_costpilot_knowledge
from core.ask_costpilot_contracts import (
    ask_interpretation_label,
    canonical_ask_intent,
    validate_ask_answer_contract,
)
from core.analytics_metrics import metric_definition, metric_for_keywords
from core.analytics_periods import (
    comparison_coverage,
    comparison_plan,
    resolve_primary_period,
)
from core.analytics_coverage import (
    comparison_data_coverage,
    workspace_analytics_settings,
    workspace_attention_signals,
    workspace_collection_profile,
)
from core.analytics_drivers import change_decomposition, dimension_contributors

router = APIRouter()
logger = logging.getLogger(__name__)

FLAGSHIP_IN  = 5.00  / 1_000_000
FLAGSHIP_OUT = 15.00 / 1_000_000
MICRO_IN     = 0.50  / 1_000_000
MICRO_OUT    = 1.50  / 1_000_000

_ASK_OPENAI_DISABLED_UNTIL = 0.0
_ASK_WRITER_DISABLED_UNTIL = 0.0
# Keyed by workspace_id ("default" for unset/None) -- was a single float
# disabling the agent loop for every workspace on any one failure, which
# let one workspace's transient provider timeout suppress correct answers
# for every other workspace for the full cooldown window. Reliability fix,
# scoped separately from the tool-dispatch and causal-language bugs fixed
# alongside it this session.
_ASK_AGENT_DISABLED_UNTIL: dict[str, float] = {}


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
    comparison_key: Optional[str] = None
    usage_status: Optional[str] = None
    usage_threshold: Optional[int] = None
    budget_scope: Optional[str] = None


class AskCostPilotScreenContext(BaseModel):
    """Non-sensitive UI context that lets the agent understand "this" and "here"."""

    page_path: Optional[str] = None
    page_title: Optional[str] = None
    section: Optional[str] = None
    visible_metric: Optional[str] = None
    selected_label: Optional[str] = None


class AskCostPilotRequest(BaseModel):
    """Read-only natural-language question over CostPilot's attributed usage."""

    question: str
    days: int = 30
    workspace_id: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    timezone_name: str = "UTC"
    project_id: Optional[str] = None
    user_external_id: Optional[str] = None
    agent_id: Optional[int] = None
    account_id: Optional[str] = None
    # True only when the caller is a page whose entire context IS this
    # account (e.g. Business Profile), as opposed to a multi-turn chat
    # widget where account_id might be a stale filter left over from a
    # previous question. Pinned scope survives _ask_reporting_filters'
    # explicit-named-subject clearing below; unpinned scope does not.
    account_id_pinned: bool = False
    # Generalizes account_id_pinned above to user_external_id/agent_id/
    # charged_unit: which of those fields (if any) is a deliberately
    # resolved choice -- e.g. a disambiguation button the user just
    # clicked -- rather than a stale filter left over from an earlier,
    # unrelated question. Set to the exact field name being pinned (e.g.
    # "charged_unit"); that one field survives _ask_reporting_filters'
    # explicit-named-subject clearing below even though the re-asked
    # question necessarily re-mentions the chosen name.
    pinned_filter_name: Optional[str] = None
    source_platform: Optional[str] = None
    record_type: Optional[str] = None
    model_tier: Optional[str] = None
    charged_unit: Optional[str] = None
    business_purpose: Optional[str] = None
    governed_request_id: Optional[str] = None
    audit_event_id: Optional[int] = None
    conversation: list[AskCostPilotMessage] = Field(default_factory=list)
    context: Optional[AskCostPilotContext] = None
    screen_context: Optional[AskCostPilotScreenContext] = None
    # CostPilot Voice (Phase 1): which surface the question came from.
    # Purely observational (see AskInteraction.modality) -- nothing in
    # the answer pipeline branches on this. "voice" means the question
    # text was produced by /api/voice/ask/transcribe; the question is
    # otherwise answered through this exact same endpoint, unchanged.
    modality: Optional[str] = "text"
    transcription_confidence: Optional[float] = None


class WorkspaceAnalyticsSettingsRequest(BaseModel):
    workspace_id: str
    timezone_name: str = "UTC"
    week_starts_on: int = Field(default=0, ge=0, le=6)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    default_window_days: int = Field(default=30, ge=1, le=365)
    collection_started_at: Optional[datetime] = None
    latest_complete_at: Optional[datetime] = None


def _analytics_settings_payload(item: WorkspaceAnalyticsSettings) -> dict:
    return {
        "workspace_id": item.workspace_id,
        "timezone_name": item.timezone_name,
        "week_starts_on": item.week_starts_on,
        "fiscal_year_start_month": item.fiscal_year_start_month,
        "default_window_days": item.default_window_days,
        "collection_started_at": item.collection_started_at,
        "latest_complete_at": item.latest_complete_at,
        "updated_at": item.updated_at,
    }


@router.get("/analytics-settings")
def get_workspace_analytics_settings(
    workspace_id: str,
    db: Session = Depends(get_db),
):
    item = workspace_analytics_settings(db, workspace_id)
    if not item:
        return {
            "workspace_id": workspace_id,
            "timezone_name": "UTC",
            "week_starts_on": 0,
            "fiscal_year_start_month": 1,
            "default_window_days": 30,
            "collection_started_at": None,
            "latest_complete_at": None,
            "configured": False,
        }
    return {**_analytics_settings_payload(item), "configured": True}


@router.put("/analytics-settings")
def update_workspace_analytics_settings(
    request: WorkspaceAnalyticsSettingsRequest,
    db: Session = Depends(get_db),
):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    workspace_id = request.workspace_id.strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    try:
        ZoneInfo(request.timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Unknown timezone_name") from exc
    if (
        request.collection_started_at
        and request.latest_complete_at
        and request.latest_complete_at < request.collection_started_at
    ):
        raise HTTPException(
            status_code=400,
            detail="latest_complete_at cannot be before collection_started_at",
        )
    item = workspace_analytics_settings(db, workspace_id)
    if not item:
        item = WorkspaceAnalyticsSettings(workspace_id=workspace_id)
        db.add(item)
    for field in (
        "timezone_name", "week_starts_on", "fiscal_year_start_month",
        "default_window_days", "collection_started_at", "latest_complete_at",
    ):
        setattr(item, field, getattr(request, field))
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return {**_analytics_settings_payload(item), "configured": True}


# Small, bounded vocabulary for typo correction -- NOT a general spell
# checker. Corrects a mistyped word only when it's a close match (one
# edit away in practice, via difflib's ratio) to exactly one word in this
# list and isn't already a real word Ask CostPilot recognizes elsewhere
# (checked by the caller before calling this). Scoped to the domain terms
# actual users mistype in practice ("toknes", "spnd", "dept") rather than
# attempting to correct arbitrary English, which risks "fixing" a word
# into the wrong meaning.
_ASK_TYPO_VOCABULARY = (
    "tokens", "token", "spend", "spent", "cost", "costs", "department",
    "departments", "people", "person", "users", "user", "agent", "agents",
    "account", "accounts", "budget", "provider", "platform", "model",
    "models", "pruning", "savings", "requests", "request",
)

# Common, correctly-spelled words that happen to sit close (by edit
# distance) to a vocabulary word above -- e.g. "uses" is a 0.89 ratio match
# for "users", closer than the genuine typo "toknes" is to "tokens"
# (0.83), so the distance metric alone can't tell them apart. Skip
# correcting anything in this set rather than risk turning a correctly
# spelled word into a different one ("which model uses tokens" silently
# becoming "...users tokens..." and misclassifying the whole question).
_ASK_TYPO_PROTECTED_WORDS = {
    "uses", "used", "using", "costing", "modelling", "modeling",
    # "count"/"counts" sit at a 0.83 ratio to the vocabulary word
    # "account" -- above the 0.78 cutoff -- so "request count trend" and
    # "resolution count" were silently corrupted into "...account
    # trend"/"...resolution account", corrupting the entity resolved
    # downstream from a real question. Found via a live corpus-expansion
    # pass; same failure class this set already exists to prevent.
    "count", "counts",
}


# Common short-form workplace abbreviations -- distinct from typos (these
# are intentional shorthand, not misspellings), so handled as a direct
# lookup rather than edit-distance matching, which would be unreliable at
# this length (a 3-letter word is one edit away from dozens of unrelated
# words).
_ASK_ABBREVIATIONS = {
    "ppl": "people", "dept": "department", "acct": "account",
    "req": "request", "reqs": "requests",
}


def _ask_correct_typos(text: str) -> str:
    import difflib

    words = text.split()
    corrected = []
    for word in words:
        bare = word.strip(".,!?;:")
        if not bare:
            corrected.append(word)
            continue
        if bare in _ASK_ABBREVIATIONS:
            corrected.append(_ASK_ABBREVIATIONS[bare])
            continue
        if bare in _ASK_TYPO_VOCABULARY or bare in _ASK_TYPO_PROTECTED_WORDS or len(bare) < 4:
            corrected.append(word)
            continue
        match = difflib.get_close_matches(bare, _ASK_TYPO_VOCABULARY, n=1, cutoff=0.78)
        corrected.append(match[0] if match else word)
    return " ".join(corrected)


# Generic, non-Sales outcome-decision vocabulary -- claims approved/
# denied, cases declined, tickets closed unsuccessful. Shared between the
# entity=="context" detector (a claim/ticket/incident question only
# counts as "about a WorkItem" when it's actually asking about an outcome
# decision, not a plain volume question -- "how many incidents..." stays
# entity="overview") and the outcome_filter detector just below it, so
# the two "does this question care about an outcome" checks can't
# silently drift apart. Deliberately excludes "resolved"/"completed"/
# "delivered" alone -- too common in unrelated project-status phrasing
# having nothing to do with outcome success/failure.
_ASK_OUTCOME_DECISION_WORDS_RE = re.compile(
    r"\b(?:won|lost|approved|denied|rejected|declined|unsuccessful)\b"
)


_ASK_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_ASK_MONTH_NAME_RE = re.compile(r"\b(" + "|".join(_ASK_MONTH_NAMES) + r")\b", re.IGNORECASE)


def _ask_named_month_unsupported(question: str, now: Optional[datetime] = None) -> Optional[str]:
    """
    Detect a named calendar month (e.g. "June", "compare June and July")
    that isn't "this month" or "last month" -- there is no period_key, no
    date parsing, nothing anywhere in this file that resolves an arbitrary
    named historical month into real calendar boundaries. Without this,
    a question like "compare sales usage for June and July" reached the
    OpenAI classifier/narrator with nothing real to map it to, and it
    silently substituted a fallback window (today's rolling period) while
    narrating the substituted dates AS IF they were June and July --
    confirmed live 2026-09-12: a real September/August comparison was
    presented as "a proxy for late June usage" and "a proxy for late July
    usage." That's worse than an "unsupported" answer -- it's a fabricated
    relabeling dressed up to look like it answered the real question.
    Caught here, before the OpenAI classifier is ever called (same
    early-short-circuit point help/product intents already use), so
    there's no chance for a downstream step to paper over the gap.

    Returns the offending month name (for the honest response's wording)
    or None if every named month in the question is one CostPilot can
    actually answer about.
    """
    matches = _ASK_MONTH_NAME_RE.findall(question or "")
    if not matches:
        return None
    reference = now or datetime.utcnow()
    this_month_num = reference.month
    last_month_num = 12 if this_month_num == 1 else this_month_num - 1
    for raw in matches:
        month_num = _ASK_MONTH_NAMES[raw.lower()]
        if month_num not in (this_month_num, last_month_num):
            return raw.capitalize()
    return None


def _ask_intent(question: str, default_days: int) -> dict:
    """Translate common executive questions into a bounded reporting intent."""
    text = " ".join((question or "").lower().split())
    text = _ask_correct_typos(text)
    days = max(1, min(int(default_days or 30), 365))
    period_key = None
    if any(term in text for term in ("all time", "all-time", "ever recorded", "since inception")):
        days, period_key = 365, "all_time"
    elif (
        ("last year" in text or "a year ago" in text)
        and any(term in text for term in ("on this date", "this date", "on this day", "that day"))
    ):
        days, period_key = 1, "same_date_last_year"
    elif any(term in text for term in (
        "same period last year", "this time last year", "around this time last year",
    )):
        # A range version of same_date_last_year: the same rolling window
        # size, anchored to end one year ago instead of now. Ranking
        # questions ("who had the most... this time last year") need this
        # to actually look at last year's data; without it this phrase set
        # no period override at all and silently fell back to the default
        # rolling window ending today.
        period_key = "same_range_last_year"
    elif any(term in text for term in ("month over month", "month-over-month")):
        days, period_key = 31, "this_month"
    elif any(term in text for term in ("quarter over quarter", "quarter-over-quarter")):
        days, period_key = 92, "this_quarter"
    elif any(term in text for term in ("year over year", "year-over-year")):
        days, period_key = 365, "this_year"
    elif "year to date" in text or re.search(r"\bytd\b", text):
        days, period_key = 365, "this_year"
    elif "today" in text:
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
    elif (
        ("last year" in text or "past year" in text)
        and not any(term in text for term in (
            "same period last year", "this time last year", "around this time last year",
        ))
    ):
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
            period_key = "rolling_days"

    metric = metric_for_keywords(text)

    entity = "overview"
    if any(term in text for term in ("employee", "person", "people", "user", "who ", "whose")):
        entity = "person"
    elif any(term in text for term in ("agent", "bot")):
        entity = "agent"
    elif any(term in text for term in ("department", "team", "business unit", "cost center")):
        entity = "department"
    elif any(term in text for term in ("account", "customer")):
        # "account" = a business/customer entity (e.g. a company record in
        # Salesforce) — distinct from "context", which means the work item
        # itself (an opportunity, case, matter, etc.). Conflating them
        # answered "which accounts..." questions with work-item names
        # (e.g. "Summit Financial — Customer Campaign") instead of account
        # names (e.g. "Summit Financial").
        entity = "account"
    elif any(term in text for term in (
        # "opportunit" (stem, no suffix) matches both "opportunity" and
        # "opportunities" -- the full word "opportunity" alone never
        # matched the plural, since "opportunities" doesn't contain it as
        # a substring ("opportunit-y" vs "opportunit-ies").
        "project", "matter", "opportunit", "business context", "work item"
    )) or re.search(r"\bdeals?\b", text) or (
        # claim(s)/ticket(s)/incident(s): the same generic-noun gap for
        # non-Sales domains Universal Outcome Ingestion opened up (a
        # custom claims/support system pushing its own outcome data).
        # Deliberately NOT unconditional the way deal(s) is -- "incident"/
        # "ticket" are also extremely common in plain VOLUME questions
        # ("how many incidents have AI activity on them?", already covered
        # by entity="overview" and confirmed live by this file's own eval
        # corpus), so only counts as entity="context" when outcome-
        # decision language is ALSO present in the same question, e.g.
        # "which approved claims..." -- see _ASK_OUTCOME_DECISION_WORDS_RE
        # just below, shared with the outcome_filter block a few lines
        # down so the two "does this question care about an outcome"
        # checks can't silently drift apart.
        re.search(r"\b(?:claims?|tickets?|incidents?)\b", text)
        and _ASK_OUTCOME_DECISION_WORDS_RE.search(text)
    ):
        # "deal"/"deals" is a very common synonym for "opportunity" in
        # sales-speak, confirmed missing before (fell back to entity=
        # "overview"). Word-boundary regex, not a substring check --
        # "deal" is a substring of "ideal", which must NOT match.
        entity = "context"
    elif any(term in text for term in (
        "provider", "anthropic", "openai", "vendor",
    )):
        # "Provider" (Anthropic/OpenAI/the AI vendor) and "platform"
        # (Salesforce/ServiceNow/the source system a request came from)
        # used to share one entity bucket, so "compare OpenAI and
        # Anthropic spend" and "how does routing use different tiers"
        # resolved to the same "platform" ranking as "which system sends
        # us the most traffic" -- two genuinely different dimensions
        # collapsed into one, answering provider questions with source
        # -system data. See core/model_provider.py for how provider is
        # actually resolved from the recorded model name.
        entity = "provider"
    elif any(term in text for term in (
        "platform", "source system", "source app",
        # "Which integration has the most AI activity flowing through
        # it?" named no literal "platform"/"source system" word, so it
        # fell through to entity="overview" -- confirmed live: the
        # answer became a generic company total instead of a per-
        # platform ranking, since ranking requires entity != "overview"
        # (see intent classification below). "integration"/"connection"
        # are the same concept (Salesforce/ServiceNow/HubSpot) under a
        # different, equally common name.
        "integration", "integrations", "connection", "connections",
    )):
        entity = "platform"
    elif any(term in text for term in ("model", "tier", "opus", "sonnet", "haiku", "gpt", "claude")):
        entity = "model"
    elif "where did" in text and any(term in text for term in ("spend", "cost", "token", "usage")):
        entity = "context"

    # "What's the cost per work item this quarter?" names no "how much"/
    # "how many" trigger phrase and no ranking term, so it fell through
    # every branch below to a generic overview -- confirmed live: the
    # answer was the total spend across ALL work items, mislabeled as if
    # it were already a per-item figure, never actually divided by the
    # number of items. Forces intent="total" below; the answer branch
    # divides by the real distinct-project count instead of guessing.
    per_item_cost_question = bool(re.search(
        r"\b(?:cost|spend)\s+per\s+(?:work\s*item|project|matter|case)\b", text,
    ))

    asks_for_help = any(term in text for term in (
        "what can you do", "what all can you do", "how can you help",
        "what can i ask", "sample question", "example question",
        "help me use", "your capabilities",
        # "What can Ask CostPilot do?" -- one of the app's own suggested
        # questions -- named "ask costpilot" instead of "you", which none
        # of the phrases above match. Confirmed live: it fell through to
        # a generic company overview instead of the capability list this
        # exact question is supposed to trigger.
        "what can ask costpilot do", "what can costpilot do",
    ))
    asks_about_product = any(term in text for term in (
        "how does costpilot", "how costpilot", "what is costpilot", "explain costpilot",
        "what does this mean", "explain this", "this number", "this chart",
        "how is this calculated", "how was this calculated", "why did costpilot",
        "how does routing", "how does pruning", "how do savings", "how is savings",
        # The rest of "Getting to Know CostPilot" -- meta-questions about
        # Ask CostPilot's own answering behavior/policies, not a data
        # lookup. All previously fell through to a generic company
        # overview (or, for the budget one, a real but wrong-question
        # budget-status report) because nothing routed them to
        # _ask_product_response()'s curated knowledge lookup at all.
        "what data do you use", "data do you use to answer", "what data sources",
        "guess or estimate", "make up a number", "invent a number",
        "if you don't know", "don't know the answer",
        "name that could mean", "two different things", "which one did you mean",
        "measured and estimated", "measured or estimated", "measured vs estimated",
        "measured versus estimated",
        "caused by ai", "just associated with it", "caused or associated",
        "ask you to change a budget", "change a budget cap", "propose a budget change",
    ))
    ranking_terms = (
        "highest", "most", "top", "largest", "lowest", "least", "fewest",
        "smallest", "bottom", "rank",
    )
    # "Which opportunities have we lost?" names no ranking_terms word at
    # all, but "which X have we Y" is itself an implicit request for a
    # list, not a single aggregate -- confirmed falling back to overview
    # otherwise despite outcome_filter already being correctly detected.
    # Scoped to "have we" specifically (not bare "which") so this doesn't
    # relabel genuinely unscoped "which X are on platform Y"-style
    # overview questions as rankings.
    implies_ranking = "which" in text and "have we" in text
    intent = "ranking" if entity != "overview" and (
        any(term in text for term in ranking_terms) or implies_ranking
    ) else "overview"
    if asks_for_help:
        intent = "help"
        metric = "request_count"
        entity = "overview"
    elif asks_about_product:
        intent = "product"
        entity = "overview"
    elif any(term in text for term in (
        "connection healthy", "connections healthy", "platform connections",
        "connection issue", "connection issues", "connections having issues",
        "connection health", "integration health", "healthy integration",
        "healthy connection", "last sync", "last synced", "sync status",
    )) or (
        # "Are there any connection health issues I should know about?" /
        # "Are any of our data connections having issues?" -- neither
        # names one of the exact phrases above, but combines a
        # connection/integration noun with a health/status adjective.
        # Confirmed live without this: every one of these questions fell
        # through to a generic company spend overview with zero mention
        # of connection status, or (worse) "When did Salesforce last
        # sync?" got answered with Salesforce's AI SPEND figures -- a
        # confidently-wrong answer to a completely different question,
        # not just an unhelpful one.
        any(term in text for term in ("connection", "connections", "integration", "integrations"))
        and any(term in text for term in (
            "health", "healthy", "issue", "issues", "sync", "synced",
            "working", "broken", "down",
        ))
    ):
        intent = "connection_health"
        entity = "overview"
    elif any(term in text for term in (
        "why were requests blocked", "why was the request blocked",
        "blocked request", "blocked requests", "request blocked", "requests blocked",
        "request was blocked", "requests were blocked",
        "blocking reason", "block reason",
    )):
        intent = "blocked"
        metric = "request_count"
        entity = "overview"
    elif any(term in text for term in (
        "show risk events", "show the risk events", "show me risk events",
        "show me the risk events", "latest risk",
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
    elif entity == "context" and any(term in text for term in (
        "no ai activity", "no activity at all", "zero ai activity",
        "zero activity", "never had any ai activity", "never had any activity",
        "haven't had any activity", "hasn't had any activity",
        "no ai activity at all", "dormant project", "dormant projects",
    )):
        # "Which projects have no AI activity at all?" -- the same
        # full-catalog-minus-activity shape as the agent-inactivity block
        # below, but for work items/projects instead of agents. Placed
        # ahead of that block (and scoped to entity=="context") so it
        # doesn't get swallowed by "never used"/"not been used" below,
        # which is agent-specific phrasing. Confirmed live without this:
        # the question fell through to a plain company overview whose
        # evidence rows were the projects WITH activity -- the exact
        # opposite of what was asked, presented with full confidence.
        intent = "inactive_context"
    elif any(term in text for term in (
        "what have we built", "is anyone using", "agent adoption",
        "agent usage status", "adoption overview",
        "not been used", "never used", "never been used", "unused agent", "unused agents",
        "inactive agent", "inactive agents", "not used recently",
        "recently inactive", "low usage", "low-use", "low use",
        "not being used much", "used infrequently", "high usage",
        "change threshold", "set threshold", "usage threshold",
    )) or ("agent" in text and "inactive" in text):
        # "which agents are inactive" phrases "inactive" after "agent(s)",
        # which the fixed phrases above miss entirely — this question fell
        # through to a generic overview instead of the agent-adoption
        # lookup that actually knows which agents have no activity.
        intent = "agent_adoption"
        entity = "agent"
        metric = "request_count"
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
    elif "budget" in text or any(term in text for term in (
        # A department budget cap is very often asked about without the
        # literal word "budget" -- "spending cap"/"monthly cap" name the
        # concept CostPilot calls a budget by a different, equally natural
        # noun; "near their limit"/"close to their cap" name the alert
        # state instead of the concept. All confirmed falling back to
        # intent="overview" without this.
        "spending cap", "monthly cap", "budget cap", "near their limit",
        "close to their cap", "closest to its cap", "over their cap",
    )):
        intent = "budget"
        entity = "department"
        metric = "spend_usd"
    elif any(term in text for term in (
        "routed to the", "routed to strategist", "routed to advisor",
        "strategist tier", "advisor tier", "scout tier", "analyst tier",
    )):
        intent = "tier_usage"
        entity = "model"
    elif (
        any(term in text for term in (
            "why ", "what drove", "what caused", "contributed to", "what changed",
        ))
        and any(term in text for term in (
            "change", "changed", "increase", "increased", "decrease", "decreased",
            "spike", "spiked", "drop", "dropped", "grew", "fell",
            "jump", "jumped", "surge", "surged", "spiral", "explod", "balloon",
        ))
        and not any(term in text for term in (
            # "What changed quarter over quarter?" names no specific driver
            # to decompose -- just a period-over-period phrase reusing the
            # word "changed" -- so it should resolve as the plain period
            # comparison the "X over X" branch below already handles
            # correctly, not the driver-decomposition intent. "what
            # changed in our usage this month VS last month" (a real
            # change_drivers question) doesn't use this "X over X" idiom,
            # so it's unaffected.
            "month over month", "month-over-month", "quarter over quarter",
            "quarter-over-quarter", "year over year", "year-over-year",
        ))
    ):
        # A metric word (spend/cost/token/...) is common but not required —
        # "what caused the spike yesterday?" names no metric at all, and
        # Ask CostPilot's entire domain is AI spend/usage, so defaulting to
        # the already-default spend_usd metric is the right fallback rather
        # than missing the question's intent entirely for lacking a word
        # that was implicit given the bot's scope.
        intent = "change_drivers"
        entity = "overview"
    elif (
        any(term in text for term in (
            "compare ", " compared ", " versus ", " vs. ", " vs ", "trending",
        )) or any(term in text for term in (
            "same period last year", "this time last year", "around this time last year",
            "year over year", "year-over-year", "month over month", "month-over-month",
            "quarter over quarter", "quarter-over-quarter",
        ))
    ) and intent != "ranking":
        # "Who had the most token usage this time last year?" names a
        # ranking target ("who", "the most") — the user wants a ranked list
        # for a historical window, not a this-year-vs-last-year delta. Only
        # phrases with no ranking language (e.g. "how did spend compare to
        # this time last year") should become a period comparison; a
        # ranking question keeps its ranking intent and instead gets its
        # date window shifted to that historical period below.
        intent = "comparison"
    elif per_item_cost_question or (
        any(term in text for term in (
            "how much", "how many", "what is our", "what's our", "whats our",
        ))
        and not any(term in text for term in ranking_terms)
    ):
        intent = "total"
    elif any(term in text for term in ("overview", "summary", "where is", "breakdown")):
        intent = "overview"

    direction = "asc" if any(term in text for term in (
        "fewest", "least", "lowest", "smallest", "bottom"
    )) else "desc"
    if intent in {"inactive", "agent_adoption"}:
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
    provider = None
    for keyword, provider_name in (
        ("anthropic", "Anthropic"), ("claude", "Anthropic"),
        ("openai", "OpenAI"), ("gpt", "OpenAI"),
        ("gemini", "Google"), ("google", "Google"),
        ("mistral", "Mistral"), ("llama", "Meta"),
    ):
        if keyword in text:
            provider = provider_name
            break

    usage_status = None
    usage_threshold = 10
    if intent == "agent_adoption":
        if "never used" in text or "never been used" in text:
            usage_status = "never"
        elif "recently inactive" in text or "not used recently" in text:
            usage_status = "recently_inactive"
        elif any(term in text for term in (
            "low usage", "low-use", "low use", "not being used much",
            "used infrequently", "fewer than", "less than", "under ", "below ",
        )):
            usage_status = "low"
        elif "high usage" in text or "most used" in text:
            usage_status = "active"
        elif "unused" in text or "not been used" in text or "inactive agent" in text:
            usage_status = "unused"
        else:
            usage_status = "all"
        threshold_match = re.search(
            rf"\b(?:under|below|fewer than|less than|threshold(?:\s+to)?|fewer than)\s+{number_pattern}\b",
            text,
        )
        if threshold_match:
            raw_threshold = threshold_match.group(1)
            usage_threshold = (
                int(raw_threshold) if raw_threshold.isdigit()
                else number_words.get(raw_threshold, 10)
            )
        usage_threshold = max(1, min(usage_threshold, 100000))

    budget_scope = None
    if intent == "budget":
        if period_key is None:
            # monthly_cap_usd is a calendar-month concept, and the Admin
            # Budgets page (core/budget.py get_all_budgets) always computes
            # spend as month-to-date. Without this, a budget question with
            # no explicit date phrase ("was the change within budget?")
            # fell back to the generic 30-day rolling window used by every
            # other intent, silently comparing month-to-date budget caps
            # against a different, longer period's spend — producing usage
            # percentages that didn't match the Admin page for the same
            # departments. An explicit phrase like "last month" earlier in
            # this function already set period_key and is left alone.
            days, period_key = 31, "this_month"
        if any(term in text for term in (
            "each department", "every department", "all department",
            "department budget", "departments budget", "budget by department",
            "all budgets",
        )):
            budget_scope = "all"
        elif any(term in text for term in ("how much", "left", "remaining", "available")):
            budget_scope = "remaining"
        elif any(term in text for term in ("on track", "on pace", "projected", "forecast")):
            budget_scope = "forecast"
        elif "variance" in text or "budget pace" in text:
            budget_scope = "variance"
        elif any(term in text for term in (
            "near budget", "close to budget", "budget limit", "over budget",
            "budget warning", "budget alert", "at risk", "spending cap",
            "close to their cap", "closest to its cap", "near their limit",
            "over their cap",
        )):
            budget_scope = "alerts"
        else:
            budget_scope = "status"

    # "which won opportunities had the highest AI spend" / "AI spend on
    # opportunities we lost" -- narrows project_breakdown rows by
    # WorkItemOutcome.outcome_success without needing a new intent, the
    # same shape as usage_status above. entity=="context" is already the
    # generic "this question is about a WorkItem" bucket (triggered by
    # "project"/"matter"/"work item"/"business context"/"deal(s)"/
    # "claim(s)"/"ticket(s)"/"incident(s)", not a Salesforce-specific
    # word), and outcome data is no longer opportunity-only since
    # Universal Outcome Ingestion.
    #
    # outcome_filter is internally always "won" (outcome_success=True) or
    # "lost" (outcome_success=False) -- that mapping is unchanged and
    # still what every downstream filter compares against. What's new is
    # outcome_filter_label, a SEPARATE display word: "won"/"lost" stays
    # for genuinely Sales-Opportunity-phrased questions (unchanged
    # behavior, existing tests), but a generic success/failure phrasing
    # ("approved claims", "resolved cases", "which tickets failed") sets
    # outcome_filter_label to "successful"/"unsuccessful" instead, so the
    # response doesn't say "won" about a claim or ticket. Response text
    # sites use outcome_filter_label together with the workspace's own
    # context_label_plural (e.g. "Claims") instead of a hardcoded
    # "opportunities" noun -- see _ask_costpilot_answer's context_plural.
    outcome_filter = None
    outcome_filter_label = None
    if entity == "context":
        # \bwon\b / \blost\b, not " won " / " lost " substring checks --
        # the substring form required a literal trailing space after the
        # word, which a naturally-phrased question ending in "?" (e.g.
        # "...has Brightwater Marine won?") never has, so this almost
        # never matched a real question. Word-boundary regex handles
        # "won?", "won.", "won," etc. the same as "won ".
        if any(term in text for term in (
            "closed won", "won opportunit", "we won", "opportunities we won",
        )) or (re.search(r"\bwon\b", text) and (
            "opportunit" in text or re.search(r"\bdeals?\b", text)
        )):
            outcome_filter = "won"
            outcome_filter_label = "won"
        elif any(term in text for term in (
            "closed lost", "lost opportunit", "we lost", "opportunities we lost",
        )) or (re.search(r"\blost\b", text) and (
            "opportunit" in text or re.search(r"\bdeals?\b", text)
        )):
            outcome_filter = "lost"
            outcome_filter_label = "lost"
        # Generic, non-Sales success/failure vocabulary -- claims
        # approved/denied, tickets resolved/failed, cases completed/
        # rejected. Deliberately excludes "resolved" alone as a positive
        # signal (a "reopened" case was also, at some point, "resolved"
        # once already -- too ambiguous without more context) and excludes
        # "completed"/"delivered" alone too (common in unrelated project-
        # status phrasing having nothing to do with outcome success).
        # Kept to words that are rarely used except to describe an actual
        # outcome decision.
        elif any(term in text for term in ("approved", "successful outcome", "successfully closed")):
            outcome_filter = "won"
            outcome_filter_label = "successful"
        elif any(term in text for term in (
            "denied", "rejected", "declined", "unsuccessful", "failed outcome",
        )):
            outcome_filter = "lost"
            outcome_filter_label = "unsuccessful"

    # "Is AI helping us close deals?" / "does AI help us win business?" --
    # a general question about whether AI activity is associated with real
    # business outcomes, distinct from outcome_filter above (which requires
    # a specific won/lost/approved/denied signal). Without this, a question
    # naming no specific outcome word had no deterministic route to
    # run_get_account_outcomes at all, and fell back to a generic activity
    # summary that never touched won/lost/pipeline data -- confirmed live:
    # "Is AI helping us close deals?" answered with request/token counts,
    # not a single opportunity outcome, whenever the agent loop (the only
    # other path that knows about outcomes) had a transient failure.
    # Deliberately narrow, matching outcome_filter's own "kept minimal"
    # precedent -- only fires when no more specific outcome_filter already
    # matched, so "which deals did we lose" keeps using that path.
    general_outcome_question = bool(
        entity == "context" and not outcome_filter
        and re.search(r"\b(?:is|are|does|do|can)\b", text)
        and re.search(r"\bai\b", text)
        and re.search(r"\b(?:help|helping|helps|drive|driving|drives|improv\w*|increas\w*|impact\w*|contribut\w*)\b", text)
        and re.search(r"\b(?:deals?|sales|revenue|business|win(?:s|ning)?|closing)\b", text)
    )

    # "What should I be paying attention to?" -- an open-ended request for
    # a pre-ranked list of what deserves attention (budget risk, biggest
    # spend swings), matching the exact phrasing the agent loop's own
    # get_priority_signals tool is already documented to answer. Without
    # this, the question had no deterministic route to that same trusted
    # computation, so a transient agent-loop failure (confirmed live: a
    # budget_preflight abort, not just a timeout) fell back to a generic
    # company overview -- or, before the help/product override fix above,
    # the unhelpful capability menu.
    attention_question = bool(
        re.search(r"\bpay(?:ing)?\s+attention\s+to\b", text)
        or re.search(r"\b(?:needs?|deserves?)\s+(?:my\s+)?attention\b", text)
        or re.search(r"\bis\s+anything\s+unusual\b", text)
        or re.search(r"\bwhat(?:'s| is)\s+important\s+right\s+now\b", text)
        or re.search(r"\bwhat\s+(?:should|do)\s+i\s+(?:know|watch)\s+(?:about|for)\b", text)
        # "What should I review first?" -- one of Ask CostPilot's own
        # suggested questions (AskCostPilot.tsx's SUGGESTIONS), previously
        # uncovered here: it fell through to a generic company overview
        # instead of this same pre-ranked attention list. Anchored to
        # "what should i" + a look/check/review verb, same style as the
        # know/watch pattern above, so it doesn't widen to unrelated
        # "should I" questions (e.g. "should I increase the budget?").
        or re.search(r"\bwhat\s+should\s+i\s+(?:review|look\s+at|check)(?:\s+first)?\b", text)
    )

    # "Why are we using Sonnet for the Sales agent?" / "who approved this
    # budget change?" -- a governance-rationale question, distinct from
    # change_drivers' own "why did a measured value change" gate above
    # (which requires an explicit increase/decrease word this doesn't
    # have) and from optimization's "what could move to a cheaper tier"
    # recommendation framing (explicitly excluded below so this doesn't
    # collide with that existing intent). Without this, "why are we using
    # X" had no deterministic route to run_get_decision_history at all,
    # and fell through to a plain model/agent spend ranking -- a
    # governance question silently answered with a cost breakdown.
    decision_history_question = bool(
        intent not in ("change_drivers", "optimization")
        and (
            (re.search(r"\bwhy\b", text) and re.search(r"\b(?:are we using|is .+ using|we're using|we are using)\b", text))
            or re.search(r"\bwho approved\b", text)
        )
    )

    # "Show AI activity across Salesforce, HubSpot, and ServiceNow" / "is
    # Salesforce connected?" -- entity=="platform" above only fires on
    # literal words like "platform"/"source system", never a named system,
    # so this had no deterministic route to run_get_data_coverage at all.
    # Without it, a question naming specific platforms silently answered
    # with whatever data happened to be connected, presented as if it
    # covered everything asked about -- the exact failure the tool's own
    # instructions exist to prevent.
    data_coverage_question = bool(
        any(p in text for p in _ASK_NAMED_PLATFORMS)
        and re.search(r"\b(?:across|connected|coverage)\b", text)
    )

    # "Which project's AI spend grew the most this month?" / "Whose AI
    # usage grew the most this month?" -- already correctly resolve to
    # intent="ranking" (entity != "overview" plus the ranking_terms hit
    # on "most"), but the ranking branch ranks by absolute value THIS
    # PERIOD, silently dropping the "grew" comparison the question
    # actually asked for. Confirmed live: "grew the most" returned
    # whichever project had the highest raw spend this month, not the
    # one with the biggest month-over-month increase -- a plausible-
    # looking but wrong answer to a delta question. This flag doesn't
    # change `intent` (ranking is still correct); the ranking answer
    # branch reads it to rank by period-over-period change instead.
    growth_ranking_question = bool(re.search(
        r"\b(?:grew|grow|growth|increased?|jumped?|surged?)\b[^.?!]{0,25}"
        r"\b(?:most|fastest|biggest)\b"
        r"|\b(?:fell|dropped?|decreased?|declined?)\b[^.?!]{0,25}\b(?:most|fastest|biggest)\b",
        text,
    ))

    comparison_key = None
    if intent in {"comparison", "change_drivers"}:
        if any(term in text for term in (
                "same period last year", "this time last year", "around this time last year",
                "year over year", "year-over-year", "year to date", "ytd",
        )):
            comparison_key = "same_period_previous_year"
        elif any(term in text for term in (
            "month over month", "month-over-month", "this month vs last month",
            "this month versus last month",
        )) or ("compar" in text and "last month" in text):
            # "compar" stem (not "compare "/"compared "/etc.) catches
            # "compare", "compares", "comparing", "comparison" -- "How does
            # this month's spend compare to last month?" doesn't match any
            # of the exact phrases above, confirmed falling back to the
            # generic "previous_period" instead of the more precise
            # "previous_month" a human would expect from this phrasing.
            comparison_key = "previous_month"
        elif any(term in text for term in (
            "quarter over quarter", "quarter-over-quarter", "this quarter vs last quarter",
            "this quarter versus last quarter",
        )):
            comparison_key = "previous_quarter"
        else:
            comparison_key = "previous_period"

    result = {
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "days": days,
        "direction": direction,
        "result_limit": result_limit,
        "period_key": period_key,
        "comparison_key": comparison_key,
        "source_platform": source_platform,
        "model_tier": model_tier,
        "provider": provider,
        "usage_status": usage_status,
        "usage_threshold": usage_threshold,
        "budget_scope": budget_scope,
        "outcome_filter": outcome_filter,
        "outcome_filter_label": outcome_filter_label,
        "general_outcome_question": general_outcome_question,
        "attention_question": attention_question,
        "decision_history_question": decision_history_question,
        "data_coverage_question": data_coverage_question,
        "growth_ranking_question": growth_ranking_question,
        "per_item_cost_question": per_item_cost_question,
    }
    canonical = canonical_ask_intent(question)
    if canonical:
        result.update({
            key: value for key, value in canonical.items()
            if key not in {"name"}
        })
        result["canonical_intent"] = canonical["name"]
    return result


_ASK_INTENTS = {
    "ranking", "overview", "savings", "budget", "pruning", "source_mix",
    "blocked", "risk_events", "total", "comparison", "activity", "inactive",
    "agent_adoption", "connection_health", "inactive_context",
    "tier_usage", "optimization", "change_drivers", "help", "product", "decision",
}
# Shared by _ask_intent()'s data_coverage_question detection and
# _ask_costpilot_answer()'s data-coverage branch -- one list so the two
# can't silently drift apart.
_ASK_NAMED_PLATFORMS = (
    "salesforce", "servicenow", "hubspot", "slack", "zendesk",
    "sap", "netsuite", "microsoft teams", "shopify",
)
_ASK_ENTITIES = {
    "person", "agent", "department", "context", "platform", "model", "provider", "overview", "request"
}
# (breakdown_key, filter_name, label) per rankable entity -- "context" is
# added dynamically at call time with a per-request label, see its use in
# _ask_costpilot_answer. Exposed at module level (not just a local dict)
# so core/analytics_dimensions.py's DIMENSION_REGISTRY can be checked
# against it in tests instead of the two silently drifting apart.
_ASK_ENTITY_CONFIG_STATIC = {
    "person": ("people_breakdown", "user_external_id", "People"),
    "agent": ("agent_breakdown", "agent_id", "Agents"),
    "department": ("organizational_unit_breakdown", "charged_unit", "Departments and teams"),
    "account": ("account_breakdown", "account_id", "Accounts"),
    "platform": ("source_platform_breakdown", "source_platform", "Platforms"),
    # Model evidence is still useful, but the attribution report does not
    # currently expose a model selector. Do not render a dead drill link.
    "model": ("model_breakdown", None, "Models"),
    "provider": ("provider_breakdown", "provider", "Providers"),
}
_ASK_METRICS = {
    "spend_usd", "total_tokens", "request_count", "tokens_saved",
    "risk_event_count", "avg_cost_per_request",
}
_ASK_DIRECTIONS = {"asc", "desc"}
_ASK_PERIOD_KEYS = {
    "today", "yesterday", "this_week", "last_week", "this_month",
    "last_month", "this_quarter", "last_quarter", "last_2q", "this_year", "last_year",
    "same_date_last_year", "all_time", "rolling_days",
}
_ASK_COMPARISON_KEYS = {
    "previous_period", "same_period_previous_year", "previous_month", "previous_quarter",
}
_ASK_USAGE_STATUSES = {"all", "unused", "never", "recently_inactive", "low", "active"}
_ASK_BUDGET_SCOPES = {"all", "alerts", "status", "remaining", "forecast", "variance"}


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
    if candidate.get("usage_status") in _ASK_USAGE_STATUSES:
        result["usage_status"] = candidate["usage_status"]
    if candidate.get("budget_scope") in _ASK_BUDGET_SCOPES:
        result["budget_scope"] = candidate["budget_scope"]
    period_key = candidate.get("period_key")
    if period_key in _ASK_PERIOD_KEYS:
        result["period_key"] = period_key
    elif period_key == "none":
        result["period_key"] = None
    comparison_key = candidate.get("comparison_key")
    if comparison_key in _ASK_COMPARISON_KEYS:
        result["comparison_key"] = comparison_key
    elif comparison_key == "none":
        result["comparison_key"] = None
    for field in (
        "source_platform", "model_tier", "subject_entity",
        "subject_filter_name", "subject_filter_value",
    ):
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().lower()
            if normalized == "none":
                result.pop(field, None)
            else:
                result[field] = normalized
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
    try:
        result["usage_threshold"] = max(
            1, min(int(candidate.get("usage_threshold")), 100000)
        )
    except (TypeError, ValueError):
        pass
    return result


def _ask_conversation_text(request: AskCostPilotRequest) -> str:
    """Create a small, non-sensitive transcript for conversational reference."""
    turns = []
    # Previous turns are relevant only when the new prompt clearly refers to
    # them.  Sending the transcript for every request lets an earlier account,
    # agent, or date range silently constrain a new standalone question.
    if _ask_is_follow_up(request.question):
        for message in request.conversation[-12:]:
            content = " ".join((message.content or "").split())[:1200]
            if content:
                turns.append(f"{message.role.upper()}: {content}")
    if request.screen_context:
        screen = request.screen_context.model_dump(exclude_none=True)
        if screen:
            turns.append(f"CURRENT_SCREEN: {json.dumps(screen, separators=(',', ':'))[:1200]}")
    turns.append(f"USER: {' '.join((request.question or '').split())[:2000]}")
    return "\n".join(turns)


def _ask_has_explicit_named_subject(question: str) -> bool:
    """Return true when the user names a new reporting subject explicitly.

    This is deliberately narrower than general entity detection.  A phrase
    such as "this account" is a conversational follow-up, while "ACME Test"
    in "show usage for ACME Test" starts a new subject and must not inherit a
    hidden person, agent, account, or project selector from the prior turn.
    """
    text = " ".join((question or "").strip().split())
    lowered = text.lower()
    if not text:
        return False

    generic_subject = re.compile(
        r"^(?:this|that|the|these|those|current|selected|same|my|our)\b",
        re.IGNORECASE,
    )
    trailing_patterns = (
        r"\b(?:for|on|connected to|associated with|attributed to)\s+(.+?)(?:[?.!]|$)",
        r"\b(?:usage|activity|spend|tokens?|cost|requests?)\s+(?:for|on)\s+(.+?)(?:[?.!]|$)",
    )
    for pattern in trailing_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip(" .?!")
        if candidate and not generic_subject.search(candidate):
            return True

    # Natural person-name construction: "How many tokens has Sheldon used?"
    match = re.search(
        r"\bhas\s+([A-Za-z][A-Za-z'.-]*(?:\s+[A-Za-z][A-Za-z'.-]*){0,3})\s+"
        r"(?:used|spent|generated|consumed)\b",
        text,
        re.IGNORECASE,
    )
    if match and not generic_subject.search(match.group(1)):
        return True

    # Avoid treating ordinary analytical phrases as names.
    return False


def _ask_is_follow_up(question: str) -> bool:
    """Return true only when a question clearly refers to an earlier answer.

    Conversation context is useful for commands such as "order them lowest to
    highest", but it must not silently constrain a new, self-contained question.
    Keep this deliberately conservative so stale dates and entity filters cannot
    leak from an earlier analysis into an unrelated request.
    """
    text = " ".join((question or "").lower().split())
    if not text:
        return False
    if re.search(
        r"\b(?:that|those|them|these|same|previous|prior|above|former|latter)\b",
        text,
    ):
        return True
    if re.match(
        r"^(?:and|also|then|instead|what about|how about|"
        r"only\s+show|narrow|filter|sort|order|reorder|drill\s+down)\b",
        text,
    ):
        return True
    if re.match(r"^now\b", text):
        return True
    if any(term in text for term in (
        "supporting activity", "supporting evidence", "break it down",
        "change threshold", "set threshold", "usage threshold",
        # "I meant the reed spin for Marcus" -- a correction re-stating a
        # prior, misheard/unrecognized term has no pronoun of its own to
        # match the patterns above, yet it depends entirely on the previous
        # turn to be interpretable at all. Confirmed live: without this,
        # the conversation transcript sent to the OpenAI planner was just
        # the bare new sentence with no history, and the planner picked an
        # unrelated intent (agent_adoption) for what was actually a spend
        # question, having no context that "reed spin" was ever raised.
        "i meant", "i mean the", "meant to say", "meant to ask",
        # "Yes, drill into support." -- answering the assistant's own
        # "would you like me to drill into X" question. The anchored
        # drill\s+down pattern above only matches text starting with that
        # phrase; a natural "yes, drill into/down on ..." reply doesn't,
        # and without conversation history this exact case still reproduced
        # the "Support vs Support Agent" ambiguity every time even after
        # the context-hint tie-break was added, since the OpenAI planner
        # never saw the prior turn either.
        "drill into", "drill down on",
    )):
        return True
    # A bare result-count command has no subject of its own and therefore
    # necessarily continues the previous ranking.
    if re.fullmatch(
        r"show(?:\s+me)?\s+(?:the\s+)?(?:top|bottom|first|last)\s+"
        r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|twenty)[?.!]?",
        text,
    ):
        return True
    return False


def _ask_fallback_intent(request: AskCostPilotRequest) -> dict:
    """Resolve deterministic follow-ups using only prior validated report context."""
    current = _ask_intent(request.question, request.days)
    text = " ".join((request.question or "").lower().split())
    is_follow_up = _ask_is_follow_up(request.question)
    context = (
        request.context.model_dump(exclude_none=True)
        if is_follow_up and request.context
        else {}
    )
    fresh_named_subject = _ask_has_explicit_named_subject(request.question)

    # Governance questions are complete, explicit intents. A prior ranking
    # context must never turn "show the latest risk events" into another model
    # or person ranking.
    if current["intent"] in {"blocked", "risk_events", "help"}:
        return current

    explicit_metric = (
        "token" in text
        or any(term in text for term in (
            "cost", "spend", "spent", "dollar", "money",
            "request", "call", "volume", "usage",
        ))
    )
    explicit_entity = any(term in text for term in (
        "employee", "person", "people", "user", "who ", "whose", "agent", "bot",
        "department", "team", "business unit", "cost center", "account",
        "customer", "project", "matter", "opportunit", "business context",
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
        if (
            not fresh_named_subject
            and not explicit_entity
            and context.get("entity") in _ASK_ENTITIES
        ):
            current["entity"] = context["entity"]
        if (
            not fresh_named_subject
            and current["intent"] == "overview"
            and context.get("intent") in _ASK_INTENTS
        ):
            current["intent"] = context.get("intent", current["intent"])
        if not explicit_direction and context.get("direction") in _ASK_DIRECTIONS:
            current["direction"] = context["direction"]
        if not explicit_period and context.get("days"):
            current["days"] = max(1, min(int(context["days"]), 365))
            current["period_key"] = context.get("period_key")
        if (
            current.get("intent") == "comparison"
            and not current.get("comparison_key")
            and context.get("comparison_key") in _ASK_COMPARISON_KEYS
        ):
            current["comparison_key"] = context["comparison_key"]
        if not explicit_count and context.get("result_limit"):
            current["result_limit"] = max(1, min(int(context["result_limit"]), 20))
        if not current.get("source_platform") and context.get("source_platform"):
            current["source_platform"] = context["source_platform"]
        if not current.get("model_tier") and context.get("model_tier"):
            current["model_tier"] = context["model_tier"]
        if current.get("intent") == "agent_adoption":
            explicit_usage_status = any(term in text for term in (
                "never used", "never been used", "recently inactive",
                "not used recently", "low usage", "low-use", "low use",
                "not being used much", "used infrequently", "fewer than",
                "less than", "under ", "below ", "high usage",
                "most used", "unused", "not been used", "inactive agent",
            ))
            explicit_threshold = bool(re.search(
                r"\b(?:under|below|fewer than|less than|threshold(?:\s+to)?)\s+"
                r"(?:\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten|twenty)\b",
                text,
            ))
            if not explicit_usage_status and context.get("usage_status"):
                current["usage_status"] = context["usage_status"]
            if not explicit_threshold and context.get("usage_threshold") is not None:
                current["usage_threshold"] = max(
                    1, min(int(context["usage_threshold"]), 100000)
                )
        if not fresh_named_subject:
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

    if not is_follow_up:
        return current

    for message in reversed(request.conversation[-12:]):
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
        "provider": parsed.get("provider"),
    }
    if _ask_has_explicit_named_subject(request.question):
        # A named subject must be discovered across the filtered workspace.
        # Keep cross-cutting scope (date, platform, model, record type), but do
        # not let an old person/agent/context/department selection hide it.
        # account_id is one exception: a pinned account (the whole page
        # IS this account, e.g. Business Profile) isn't a stale leftover
        # filter the way a chat widget's account_id might be, so a question
        # like "top agent for Acme Corp" on Acme's own profile page should
        # stay scoped to Acme, not search the entire workspace for whichever
        # account "Acme Corp" happens to name. pinned_filter_name generalizes
        # this to person/agent/department: clicking a "which one did you
        # mean?" choice re-asks a question that necessarily re-mentions the
        # chosen name (tripping this same clearing logic), but that name was
        # just deliberately resolved to one exact row, not a stale filter --
        # see the _ask_named_entity_ambiguity consumer below.
        filters["project_id"] = None
        if not request.account_id_pinned:
            filters["account_id"] = None
        if request.pinned_filter_name != "user_external_id":
            filters["user_external_id"] = None
        if request.pinned_filter_name != "agent_id":
            filters["agent_id"] = None
        if request.pinned_filter_name != "charged_unit":
            filters["charged_unit"] = None
        return filters
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

    OpenAI never calculates the answer. It selects one bounded, read-only
    analytics operation. If the call is unavailable or invalid, the
    deterministic parser remains available.
    """
    global _ASK_OPENAI_DISABLED_UNTIL

    named_month = _ask_named_month_unsupported(request.question)
    if named_month:
        return {"intent": "unsupported_period", "named_month": named_month}, "unsupported_named_period"

    fallback = _ask_fallback_intent(request)
    if fallback["intent"] == "help":
        return fallback, "capability_help"
    if fallback["intent"] == "product":
        return fallback, "costpilot_knowledge"
    if fallback.get("canonical_intent"):
        return fallback, "canonical_intent"
    # Adoption-status questions have an exact deterministic meaning and must
    # not be broadened into a general usage overview by the language planner.
    if fallback["intent"] == "agent_adoption":
        return fallback, "deterministic_agent_adoption"
    # "Is AI helping us close deals?"-style business-outcome questions: the
    # regex classifier's entity=="context" detection here is confident and
    # correct (deals?/opportunit\w* is an explicit, deliberate synonym --
    # see _ask_intent), but the OpenAI planner below has no outcome-aware
    # intent to select and can only replace this with a worse guess
    # (confirmed live: it picked entity="agent" for this exact question).
    # Bypassing it here is what actually lets _ask_costpilot_answer's
    # dedicated business-outcomes branch run instead of a generic activity
    # summary whenever the agent loop (the primary path for this question
    # type) has a transient failure and falls back to this path.
    if fallback.get("general_outcome_question"):
        return fallback, "deterministic_business_outcomes"
    # "What should I be paying attention to?"-style questions -- confident,
    # narrow phrasing match; bypassing the OpenAI planner here is what lets
    # _ask_costpilot_answer's dedicated priority-signals branch run instead
    # of a generic overview (or, before the fix above, the help menu)
    # whenever the agent loop has a transient failure and falls back here.
    if fallback.get("attention_question"):
        return fallback, "deterministic_priority_signals"
    # "Why are we using Sonnet for the Sales agent?" -- bypassing the
    # OpenAI planner is what lets _ask_costpilot_answer's dedicated
    # decision-history branch run instead of a plain spend ranking
    # whenever the agent loop falls back here.
    if fallback.get("decision_history_question"):
        return fallback, "deterministic_decision_history"
    # "Show AI activity across Salesforce, HubSpot, and ServiceNow" --
    # bypassing the OpenAI planner is what lets _ask_costpilot_answer's
    # dedicated data-coverage branch run instead of silently answering
    # with only whatever's connected, presented as complete.
    if fallback.get("data_coverage_question"):
        return fallback, "deterministic_data_coverage"
    # Exact calendar and lifetime phrases are reporting contracts. A language
    # model must not widen one day to a year or shrink all history to 30 days.
    if fallback.get("period_key") in {
        "yesterday", "same_date_last_year", "all_time",
    }:
        return fallback, "deterministic_period_contract"
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
                "period_key": {
                    "type": "string",
                    "enum": ["none", *sorted(_ASK_PERIOD_KEYS)],
                },
                "comparison_key": {
                    "type": "string",
                    "enum": ["none", *sorted(_ASK_COMPARISON_KEYS)],
                },
                "source_platform": {
                    "type": "string",
                    "enum": [
                        "none", "salesforce", "servicenow", "hubspot",
                        "slack", "zendesk", "sap", "netsuite",
                        "microsoft teams", "shopify",
                    ],
                },
                "model_tier": {
                    "type": "string",
                    "enum": [
                        "none", "scout", "analyst", "advisor",
                        "strategist", "flagship",
                    ],
                },
                "usage_status": {
                    "type": "string",
                    "enum": ["none", *sorted(_ASK_USAGE_STATUSES)],
                },
                "usage_threshold": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                },
                "budget_scope": {
                    "type": "string",
                    "enum": ["none", *sorted(_ASK_BUDGET_SCOPES)],
                },
            },
            "required": [
                "intent", "entity", "metric", "direction", "days",
                "result_limit", "period_key", "comparison_key", "source_platform", "model_tier",
                "usage_status", "usage_threshold", "budget_scope",
            ],
            "additionalProperties": False,
        },
    }
    instructions = """You interpret questions for a read-only enterprise AI usage report and product guide.
Use the prior conversation to understand corrections and follow-up questions.
Choose help when the user asks what you can do, how you can help, or asks for examples.
Choose product when the user asks how CostPilot works, what a product term means, how a displayed
metric is calculated, or refers to the current screen with words such as this, here, or this chart.
Choose ranking for highest, lowest, top, bottom, most, least, or "who" questions.
Choose blocked for questions asking why requests were blocked.
Choose risk_events for questions asking for recent or latest governance risk events.
Choose pruning for questions about tokens or context removed before model calls.
Use person for employees/users/people; agent for AI agents/bots; department for teams;
context for accounts/projects/matters/opportunities/work; platform for source systems;
model for model names or tiers; overview for company totals. Choose source_mix for
questions comparing live activity with simulator or test traffic.
Use total_tokens for token questions, spend_usd for cost/spend, and request_count for calls/usage.
Choose change_drivers when the user asks why a measured spend, cost, token, request, or usage value
changed, increased, decreased, spiked, or dropped. This selects deterministic contribution analysis;
never invent a business cause.
Choose comparison with comparison_key same_period_previous_year for year-over-year, same-period-last-year,
"around this time last year", and year-to-date versus last-year questions. Choose previous_period for an
immediately preceding equal-length comparison. Never invent date boundaries; the server resolves them.
Choose agent_adoption when the user asks what agents have been built, whether they are being used,
or asks for unused, never-used, inactive, low-usage, or active agents. Use usage_status never for
agents with no governed activity ever; recently_inactive for agents with history but no activity in
the selected period; unused for both groups; low for agents above zero but below usage_threshold;
and all for an adoption overview. The default low-usage threshold is 10 requests.
Choose connection_health for questions about whether a platform connection/integration is healthy,
has issues, or when it last synced -- never overview.
Choose inactive_context for "which projects/work items have no AI activity at all" -- never overview
or ranking.
Choose total (never overview or ranking) for "cost per work item/project" questions -- the server
divides total spend by the distinct project count.
For budget questions, choose budget_scope all when the user asks what budget each department has;
remaining for budget left or available; forecast for projected month-end spend or whether spend is
on track; variance for spend compared with the time-phased budget; alerts for departments nearing
or exceeding their cap; and status for a general within-budget question.
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
                validated = _validated_ask_intent(json.loads(arguments), fallback)
                if validated.get("intent") in ("help", "product"):
                    # fallback already ruled out help/product before OpenAI
                    # was ever called -- both intents short-circuit and
                    # return above (lines ~1275-1278) without reaching this
                    # code at all when the deterministic classifier itself
                    # says help/product. So getting here means fallback's
                    # own intent is something else already -- if OpenAI
                    # nonetheless says help/product, it is by construction
                    # contradicting an already-settled correct answer, never
                    # a legitimate second opinion. Confirmed live: "What
                    # should I be paying attention to?" -- fallback
                    # correctly resolved to "overview", OpenAI incorrectly
                    # overrode it to "help", producing the generic capability
                    # menu instead of a real answer.
                    return fallback, "deterministic_fallback_help_override"
                if (
                    validated.get("intent") == "agent_adoption"
                    and fallback.get("intent") != "agent_adoption"
                ):
                    # Adoption-status phrasing is exact and deterministic
                    # (see the fallback branch above) -- the regex classifier
                    # reliably catches every real adoption question. So if
                    # fallback's own intent is something else entirely, an
                    # OpenAI "agent_adoption" guess here isn't a legitimate
                    # second opinion, it's a fabrication, exactly like the
                    # help/product case above. Confirmed live: "I meant the
                    # reed spin for Marcus" (a garbled voice correction with
                    # no adoption language at all) fell back to a workspace-
                    # wide agent adoption overview, discarding both the
                    # spend question and the named person "Marcus" entirely.
                    return fallback, "deterministic_fallback_adoption_override"
                if (
                    fallback.get("intent") in ("connection_health", "inactive_context")
                    and validated.get("intent") != fallback.get("intent")
                ):
                    # Same class of guard as agent_adoption above, for the two
                    # newer exact-phrase intents (connection/sync health
                    # questions, "which projects have no activity at all").
                    # This OpenAI classifier's own instructions text (above)
                    # was never updated to know these exist, so it reliably
                    # guesses something else (usually "overview") whenever
                    # fallback already correctly identified one of them from
                    # an exact phrase match -- confirmed live: "Which
                    # platform connections are healthy?" only produced its
                    # real connection-status answer because this guard (added
                    # alongside connection_health/inactive_context) stopped
                    # OpenAI's "overview" guess from overriding it.
                    return fallback, "deterministic_fallback_intent_override"
                if (
                    fallback.get("per_item_cost_question")
                    and validated.get("intent") != "total"
                ):
                    # "What's the cost per work item this quarter?" -- the
                    # regex-matched cost-per-item phrasing is exact and
                    # reliable (see per_item_cost_question's definition
                    # above); this classifier's instructions text has no
                    # concept of it and reliably guesses "overview" instead,
                    # which silently drops the per-item division and returns
                    # the plain company total again. Confirmed live on the
                    # deployed app.
                    return fallback, "deterministic_fallback_per_item_cost_override"
                if (
                    fallback.get("growth_ranking_question")
                    and validated.get("intent") != "ranking"
                ):
                    # "Which project's spend grew the most this month?" --
                    # same reasoning: the regex-detected growth-ranking
                    # phrasing is reliable, and this classifier doesn't know
                    # to preserve it, so it can knock the question off the
                    # ranking intent the growth-by-delta answer requires.
                    return fallback, "deterministic_fallback_growth_ranking_override"
                return (
                    validated,
                    "openai_tool_planner",
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
    timezone_name: str = "UTC",
) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Return exact calendar boundaries for natural-language periods.

    Delegates to core.analytics_periods.resolve_primary_period instead of
    keeping its own separate UTC-only date math. That duplication was
    exactly why "today"/"this week"/the default rolling window ignored a
    workspace's configured timezone even when one was correctly set —
    this function always computed against server UTC, while
    resolve_primary_period (already used for the comparison intent)
    properly converted to local time first. A workspace near a UTC day
    boundary (e.g. evening in the US) would see a "today" or default
    30-day window that was a full day ahead of its actual local calendar.
    Both paths now share one implementation and can't disagree.
    """
    from core.analytics_periods import resolve_primary_period

    try:
        period = resolve_primary_period(
            period_key=parsed.get("period_key"),
            days=int(parsed.get("days") or 30),
            timezone_name=timezone_name,
            date_from=request.date_from,
            date_to=request.date_to,
        )
    except ValueError:
        return request.date_from, request.date_to
    return period.start, period.end


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
        detail = (
            f"{int(row.get('request_count') or 0):,} requests · "
            f"{int(row.get('total_tokens') or 0):,} tokens · "
            f"${float(row.get('spend_usd') or 0):,.4f} · "
            f"{int(row.get('live_count') or 0):,} live / "
            f"{int(row.get('simulation_count') or 0):,} simulator"
        )
        entry = {
            "label": row.get("label") or "Unknown",
            "value": value,
            "metric_label": metric_label,
            "detail": detail,
            "filter_name": filter_name,
            "filter_value": row.get("id"),
            "live_count": int(row.get("live_count") or 0),
            "simulation_count": int(row.get("simulation_count") or 0),
        }
        # Outcome is only present on project_breakdown rows (see
        # WorkItemOutcome) -- surfaced as its own facts, not folded into
        # `detail`'s prose, so Ask CostPilot's narrator states the AI figure
        # and the outcome figure as two separate associated facts rather
        # than one blended sentence that risks implying causation.
        if row.get("outcome_status") is not None:
            entry["outcome"] = {
                "status": row.get("outcome_status"),
                "value": row.get("outcome_value"),
                "success": row.get("outcome_success"),
                "is_closed": row.get("outcome_is_closed"),
                "freshness": row.get("outcome_freshness"),
            }
        evidence.append(entry)
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
    # "What should I review first?" (one of Ask CostPilot's own suggested
    # questions) tokenized to {should, review, first} -- none previously
    # stop words -- and "review" alone matched every WorkItem/agent whose
    # name happens to end in "Review" (a common suffix in this
    # workspace's data, e.g. "Billing Review"), producing a false
    # "which one did you mean?" for a question that named no entity at
    # all. Confirmed live via the cockpit's voice input on 2026-09-10.
    "review", "should", "first",
}


def _ask_name_tokens(value: str) -> set[str]:
    """Return meaningful tokens used to match a named reporting subject."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").lower())
        if len(token) >= 3 and token not in _ASK_NAME_STOP_WORDS
    }


def _ask_named_department(question: str, workspace_id: Optional[str], db: Session) -> Optional[str]:
    """
    Detect a department named in the question even when the question's
    *ranking dimension* (entity) is something else entirely — e.g. "what
    models is Sales using" ranks by model, not department, so _ask_intent's
    single `entity` field can never carry "Sales" as a filter; it can only
    be "model" or "department", not both. Without this, that class of
    question silently answered with the company-wide breakdown while the
    narrated text still named the department, mixing two different scopes
    into one answer (confirmed live on 2026-08-08 for exactly this
    question). Matches against the real distinct department labels in this
    workspace's data rather than a hardcoded list, since department names
    are customer-defined. Returns a department only on an unambiguous single
    match — never guesses between two equally-plausible department names.
    """
    from database.models import TokenTransaction
    from core.workspace_scope import workspace_filter

    text = (question or "").lower()
    if not text or db is None:
        return None
    query = db.query(TokenTransaction.department)
    scope = workspace_filter(TokenTransaction, workspace_id)
    # workspace_filter()'s contract is "None means skip filtering, not
    # 'filter to nothing'" -- passing None straight into .filter() instead
    # builds a `WHERE NULL` clause that matches zero rows, silently making
    # every unscoped-workspace lookup (workspace_id=None, e.g. this app's
    # default/legacy workspace) come back empty.
    if scope is not None:
        query = query.filter(scope)
    rows = query.distinct().all()
    labels = {str(row[0]).split(":")[-1].strip() for row in rows if row[0]}
    matches = {label for label in labels if label and label.lower() in text}
    if len(matches) == 1:
        return next(iter(matches))
    return None


_ASK_ENTITY_CONTEXT_CUES = {
    "department": ("department", "departments", "org unit", "organizational unit", "team", "teams"),
    "agent": ("agent", "agents"),
    "person": ("person", "people", "employee", "employees"),
    "account": ("account", "accounts", "customer", "customers", "client", "clients"),
    "platform": ("platform", "platforms", "connected", "source system"),
    "model": ("model", "models", "tier"),
}


def _ask_recent_context_hint(request: "AskCostPilotRequest") -> str:
    """
    Text of the most recent assistant turn, used only to break a genuine
    name-token tie between two entity types sharing a name (e.g. a
    "Support" department and a "Support Agent"). Confirmed live: an
    assistant turn that explicitly called out "the Support spike" in a
    department-budget answer, followed by the user saying "Yes, drill into
    support", re-triggered the exact same "Support vs Support Agent"
    ambiguity every time -- the tie-break had no way to know the
    conversation had already established this as a department, not an
    agent, one turn earlier.
    """
    for message in reversed(request.conversation[-4:]):
        if message.role == "assistant" and message.content:
            return message.content
    return ""


def _ask_named_entity_candidates(question: str, report: dict, context_hint: str = "") -> list:
    """
    Every candidate row (across people/agents/accounts/departments/
    context/platforms/models) whose label shares a name token with the
    question, sorted best match first. Shared by _ask_named_entity (which
    picks the unique winner or abstains) and _ask_named_entity_ambiguity
    (which needs the full tied group to build a clarification message) so
    the two can never define "candidate" or "match score" differently.

    context_hint (optional, usually the prior assistant turn) only ever
    breaks an otherwise-exact tie -- see _ask_recent_context_hint. It can
    never turn a real match into a non-match or change a unique winner.
    """
    question_tokens = _ask_name_tokens(question)
    if not question_tokens:
        return []
    context_hint_lower = (context_hint or "").lower()
    # _ask_name_tokens strips "agent"/"department"/"team" as generic stop
    # words from BOTH the label and the question, so an agent conventionally
    # named "{Department} Agent" reduces to the exact same token set as the
    # bare department name -- confirmed live across this workspace's real
    # data: 7 of 8 departments (Sales, Support, Operations, Finance,
    # Marketing, Engineering, Legal) collide with an identically-named
    # agent this way, so this is the normal case here, not a rare edge
    # case. The one place that word isn't noise is the raw, untokenized
    # question text: if the user actually says "agent" (or "department"/
    # "team"), that is a direct, explicit signal of which entity type they
    # mean, and it deserves to win a tie before falling back to the
    # weaker prior-turn context_hint signal.
    question_hint_lower = (question or "").lower()

    candidates = []
    configs = (
        ("person", "people_breakdown", "user_external_id", "person"),
        ("agent", "agent_breakdown", "agent_id", "agent"),
        ("account", "account_breakdown", "account_id", "account"),
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
    # The same real-world name can appear as more than one row within a
    # single dimension — e.g. an account seeded by the historical demo
    # script and the same account name created later via the traffic
    # simulator's resolver end up as two WorkAccount rows with different
    # external ids. Merge same-entity/same-label rows into one combined
    # row before scoring so that duplication doesn't look like ambiguity
    # between two different real subjects (which is the actual case this
    # tie-check exists to catch, e.g. a person and a department sharing a
    # name).
    #
    # For rows that carry an email (people), fold it into the merge key
    # too. Without this, two DIFFERENT people who happen to share a name
    # got silently summed into one answer instead of merely deduplicating
    # true duplicates -- confirmed live: a demo workspace's traffic
    # simulator generates unrelated synthetic personas across different
    # simulated companies that coincidentally reuse the same display name
    # ("Avery Johnson" existed as 5 distinct simulated identities across 5
    # platforms), and summing them overstated one person's spend by ~4x
    # versus what the attribution dashboard's exact-identity filter showed
    # for any single one of them. Rows lacking an email at all (most
    # non-person dimensions) fall back to the original label-only key,
    # preserving the account-merging behavior this logic was built for.
    _SUM_FIELDS = (
        "request_count", "input_tokens", "output_tokens", "tokens_saved",
        "spend_usd", "simulation_count", "total_tokens", "live_count",
    )
    merged: dict[tuple, dict] = {}
    for entity, breakdown_key, filter_name, entity_label in configs:
        for row in report.get(breakdown_key) or []:
            if entity == "context" and str(row.get("id") or "").startswith("SF-ACCOUNT-"):
                # An account-level AI-activity rollup (created when a
                # Governed AI Request runs directly on an Account record,
                # not a child Opportunity/Case) -- same real-world entity
                # the "account" dimension already represents for this same
                # WorkAccount, under the identical name. Reproduced live:
                # "Dickenson plc" existed as both an account_breakdown row
                # and a project_breakdown row (id "SF-ACCOUNT-..."), tying
                # in match score and triggering a false "which Dickenson
                # plc do you mean?" for a name that was never actually
                # ambiguous. See business_context_json()'s own
                # is_account_rollup check (core/business_context.py) for
                # the same "SF-ACCOUNT-" convention used to detect this.
                continue
            label = str(row.get("label") or "").strip()
            label_tokens = _ask_name_tokens(label)
            overlap = question_tokens & label_tokens
            if not overlap:
                continue
            key = (entity, label, row.get("email") or None)
            existing = merged.get(key)
            if existing is None:
                merged_row = dict(row)
                for field in _SUM_FIELDS:
                    if field in row:
                        merged_row[field] = row.get(field) or 0
                merged[key] = {
                    "entity": entity,
                    "entity_label": entity_label,
                    "filter_name": filter_name,
                    "row": merged_row,
                    "score": (
                        len(overlap),
                        1 if label_tokens and label_tokens.issubset(question_tokens) else 0,
                        len(" ".join(overlap)),
                        # Last two components -- only ever compared when
                        # every prior one is already tied, so these can bias
                        # a genuine tie but never beat a stronger name
                        # match. The user's own current wording ("the sales
                        # AGENT") outranks a mere prior-turn cue.
                        1 if any(
                            cue in question_hint_lower
                            for cue in _ASK_ENTITY_CONTEXT_CUES.get(entity, ())
                        ) else 0,
                        1 if any(
                            cue in context_hint_lower
                            for cue in _ASK_ENTITY_CONTEXT_CUES.get(entity, ())
                        ) else 0,
                        # Last-resort default when a bare name carries no
                        # qualifier or context at all (e.g. "How much did
                        # Finance spend?", with neither "agent" nor
                        # "department" said anywhere). Confirmed live: this
                        # exact "{Department} Agent" naming convention makes
                        # a fully bare mention genuinely tied for 7 of 8
                        # departments in the real workspace data, and it is
                        # the single most natural way to ask this question.
                        # A department name is an ordinary business noun
                        # people say constantly; someone who means the
                        # agent will very rarely omit the word "agent"
                        # entirely, so defaulting to department here is
                        # right far more often than it's wrong -- and it
                        # only ever applies once every stronger, more
                        # specific signal above has already failed to
                        # settle the tie.
                        1 if entity == "department" else 0,
                    ),
                }
            else:
                for field in _SUM_FIELDS:
                    if field in row:
                        existing["row"][field] = (existing["row"].get(field) or 0) + (row.get(field) or 0)

    candidates = list(merged.values())
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _ask_named_entity(question: str, report: dict, context_hint: str = "") -> Optional[dict]:
    """
    Resolve an explicitly named person, agent, department, or work item.

    Matching happens only against labels already returned by CostPilot's
    deterministic attribution report. A single first/last-name token can
    identify a row only when it is unique across every candidate.
    """
    candidates = _ask_named_entity_candidates(question, report, context_hint)
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
        return None
    return candidates[0]


def _ask_named_entity_ambiguity(question: str, report: dict, context_hint: str = "") -> list:
    """
    Return the tied top-scoring candidates when a named subject can't be
    resolved because two or more equally-good matches exist (e.g. two
    people named "Chris") -- empty if there's a unique winner, or if no
    candidate matched at all. Used to build a clarification message
    ("I found two people named Chris...") instead of the alternative that
    was happening before this existed: silently answering with an
    unfiltered, company-wide number as if it had resolved the name.
    """
    candidates = _ask_named_entity_candidates(question, report, context_hint)
    if len(candidates) < 2 or candidates[0]["score"] != candidates[1]["score"]:
        return []
    top_score = candidates[0]["score"]
    return [c for c in candidates if c["score"] == top_score]


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


def _ask_contract_failure_response(
    request: AskCostPilotRequest,
    parsed: dict,
    issues: list[str],
) -> dict:
    """Fail closed instead of showing a valid report for the wrong question."""
    interpreted_as = ask_interpretation_label(parsed)
    return {
        "question": request.question.strip(),
        "title": "I need to verify this analysis",
        "answer": (
            f"I interpreted your question as: {interpreted_as}. "
            "The calculated result did not match that reporting contract, so I did not display it. "
            "Please try again or narrow the subject and time period."
        ),
        "intent": "clarification",
        "entity": parsed.get("entity") or "overview",
        "metric": parsed.get("metric") or "request_count",
        "period": {},
        "filters": {},
        "summary": {},
        "evidence": [],
        "recommendations": [],
        "measurement_note": "No mismatched customer result was displayed.",
        "calculation": {
            "metric": "contract_validation",
            "formula": "Validate the calculated report against the interpreted question before display.",
            "row_count": 0,
            "period_label": None,
        },
        "calculation_source": "Ask CostPilot answer contract",
        "data_provenance": {
            "scope": "clarification_required",
            "live_requests": 0,
            "simulator_requests": 0,
            "active_filters": {},
            "period_label": None,
        },
        "assistant_mode": "contract_guardrail",
        "interpreted_as": interpreted_as,
        "interpreted_intent": parsed,
        "contract_status": "failed",
        "contract_issues": issues,
        "conversation_context": {
            "intent": parsed.get("intent"),
            "entity": parsed.get("entity"),
            "metric": parsed.get("metric"),
            "days": int(parsed.get("days") or request.days),
            "direction": parsed.get("direction") or "desc",
            "result_limit": int(parsed.get("result_limit") or 5),
        },
        "read_only": True,
    }


def _ask_product_response(
    request: AskCostPilotRequest,
    parsed: dict,
    assistant_mode: str,
) -> dict:
    """Answer product questions from curated CostPilot knowledge, not model memory."""
    screen = request.screen_context.model_dump(exclude_none=True) if request.screen_context else {}
    topics = search_costpilot_knowledge(
        request.question,
        page_path=screen.get("page_path"),
        limit=3,
    )
    if not topics and screen.get("page_path"):
        topics = search_costpilot_knowledge(
            f"current page {screen.get('page_title') or ''} {screen.get('section') or ''}",
            page_path=screen.get("page_path"),
            limit=3,
        )
    if not topics:
        topics = search_costpilot_knowledge("dashboard audit routing", limit=3)

    primary = topics[0]
    location = ""
    if screen:
        label = screen.get("section") or screen.get("visible_metric") or screen.get("page_title")
        if label:
            location = f" In the current {label} context, verify the active scope before comparing values."
    answer = f"{primary['summary']} {primary['details']}{location}"
    evidence = [
        {
            "label": topic["title"],
            "value": "CostPilot product knowledge",
            "metric_label": "grounded explanation",
            "detail": topic["details"],
            "filter_name": None,
            "filter_value": None,
            "page": topic["page"],
        }
        for topic in topics
    ]
    return {
        "question": request.question.strip(),
        "title": primary["title"],
        "answer": answer,
        "intent": "product",
        "entity": "overview",
        "metric": "product_knowledge",
        "period": {},
        "filters": {},
        "summary": {},
        "evidence": evidence,
        "recommendations": [
            {"title": "Recommended next step", "body": primary["action"]}
        ],
        "measurement_note": (
            "This explanation comes from CostPilot's curated product knowledge. "
            "Customer-specific numbers require a governed activity query."
        ),
        "calculation": {
            "metric": "product_knowledge",
            "formula": "No customer-data calculation was required.",
            "row_count": 0,
            "period_label": None,
        },
        "calculation_source": "CostPilot product knowledge catalog",
        "data_provenance": {
            "scope": "product_knowledge",
            "live_requests": 0,
            "simulator_requests": 0,
            "active_filters": {},
            "period_label": None,
            "screen_context": screen,
            "knowledge_topics": [topic["id"] for topic in topics],
        },
        "assistant_mode": assistant_mode,
        "interpreted_as": ask_interpretation_label(parsed),
        "contract_status": "passed",
        "interpreted_intent": parsed,
        "conversation_context": {
            "intent": "product",
            "entity": "overview",
            "metric": "product_knowledge",
            "days": int(parsed.get("days") or request.days),
            "direction": "desc",
            "result_limit": len(topics),
            "subject_entity": "product_topic",
            "subject_filter_name": "knowledge_topic",
            "subject_filter_value": primary["id"],
        },
        "read_only": True,
    }


def _ask_decision_response(
    request: AskCostPilotRequest,
    parsed: dict,
    db: Session,
    assistant_mode: str,
) -> dict:
    """Explain one request from correlated cost and immutable audit evidence."""
    audit = None
    if request.audit_event_id:
        audit = db.query(AuditEvent).filter(AuditEvent.id == request.audit_event_id).first()
    if not audit and request.governed_request_id:
        audit = (
            db.query(AuditEvent)
            .filter(AuditEvent.governed_request_id == request.governed_request_id)
            .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
            .first()
        )
    governed_request_id = request.governed_request_id or (
        audit.governed_request_id if audit else None
    )
    tx = None
    if governed_request_id:
        tx = (
            db.query(TokenTransaction)
            .filter(TokenTransaction.governed_request_id == governed_request_id)
            .order_by(TokenTransaction.timestamp.desc(), TokenTransaction.id.desc())
            .first()
        )
    if not audit and not tx:
        return _ask_contract_failure_response(
            request,
            parsed,
            ["a selected governed request or audit event is required"],
        )

    selected_model = (
        (audit.selected_model_name if audit else None)
        or (tx.model_name if tx else None)
        or "No model"
    )
    selected_tier = (
        (audit.selected_model_tier if audit else None)
        or (tx.resolved_model_tier if tx else None)
        or (tx.model_tier if tx else None)
        or "none"
    )
    rationale = (audit.rationale if audit else None) or (
        tx.routing_reason if tx else None
    ) or "No additional rationale was recorded."
    outcome = (audit.decision_outcome if audit else None) or (
        tx.execution_status if tx else None
    ) or "Recorded"
    answer = (
        f"CostPilot selected {selected_model} at the {selected_tier} tier. "
        f"Recorded rationale: {rationale} Outcome: {outcome}."
    )
    evidence = [{
        "label": governed_request_id or f"Audit event {audit.id}",
        "value": selected_model,
        "metric_label": selected_tier,
        "detail": rationale,
        "filter_name": "audit_event_id" if audit else None,
        "filter_value": audit.id if audit else None,
        "governed_request_id": governed_request_id,
    }]
    return {
        "question": request.question.strip(),
        "title": "Why CostPilot made this decision",
        "answer": answer,
        "intent": "decision",
        "entity": "request",
        "metric": "request_count",
        "period": {},
        "filters": {"governed_request_id": governed_request_id},
        "summary": {
            "cost_usd": float(tx.cost_usd or 0.0) if tx else float(audit.cost_usd or 0.0),
            "input_tokens": int(tx.input_tokens or 0) if tx else 0,
            "output_tokens": int(tx.output_tokens or 0) if tx else 0,
        },
        "evidence": evidence,
        "recommendations": [],
        "measurement_note": "Explanation uses the correlated immutable audit and cost records; raw prompt text is not supplied to the assistant.",
        "calculation": {
            "metric": "request_decision",
            "formula": "Join the governed request's latest audit event with its token transaction.",
            "row_count": int(bool(audit)) + int(bool(tx)),
            "period_label": None,
        },
        "calculation_source": "CostPilot governed request record",
        "data_provenance": {
            "scope": "governed_request",
            "live_requests": 0 if (tx and tx.is_simulation) else 1,
            "simulator_requests": 1 if (tx and tx.is_simulation) else 0,
            "active_filters": {"governed_request_id": governed_request_id},
            "period_label": None,
            "audit_event_id": audit.id if audit else None,
        },
        "assistant_mode": assistant_mode,
        "interpreted_as": ask_interpretation_label(parsed),
        "contract_status": "passed",
        "interpreted_intent": parsed,
        "conversation_context": {
            "intent": "decision",
            "entity": "request",
            "metric": "request_count",
            "days": int(parsed.get("days") or request.days),
            "direction": "desc",
            "result_limit": 1,
            "subject_entity": "request",
            "subject_filter_name": "governed_request_id",
            "subject_filter_value": governed_request_id,
        },
        "read_only": True,
    }


def _ask_unsupported_period_response(named_month: str) -> dict:
    """
    Honest decline for a named historical month/period CostPilot has no
    real way to resolve (see _ask_named_month_unsupported) -- states the
    limitation plainly instead of silently substituting a different real
    period and narrating it as if it were the one asked about.
    """
    return {
        "title": "That time period isn't supported yet",
        "answer": (
            f"CostPilot can't look up {named_month} specifically yet -- there's no way to "
            "resolve an arbitrary named month into a calendar range today. "
            "Supported time periods are: today, yesterday, this week, last week, this month, "
            "last month, this quarter, last quarter, the last 2 quarters, this year, last year, "
            "all time, or a specific number of days back (e.g. \"the last 14 days\")."
        ),
        "intent": "unsupported_period",
        "evidence": [],
        "recommendations": [],
        "suggested_questions": [
            "How much did we spend this month?",
            "How much did we spend last month?",
            "Compare this month's AI spend to last month.",
        ],
        "read_only": True,
    }


def _ask_help_response(
    request: AskCostPilotRequest,
    parsed: dict,
    assistant_mode: str,
) -> dict:
    """Return product capabilities without running an unrelated data report."""
    categories = [
        (
            "Spend and usage",
            "How much did we spend this month? Compare token usage with around this time last year.",
        ),
        (
            "Change drivers",
            "Why did our AI spend or token usage change? Which measured contributors drove the difference?",
        ),
        (
            "People and agents",
            "Who used the most tokens? Which agents have not been used recently?",
        ),
        (
            "Accounts and business work",
            "Show AI usage for ACME Test. Who and which agents worked on that account?",
        ),
        (
            "Models, routing, and savings",
            "Which model cost the most? What could move to a cheaper tier?",
        ),
        (
            "Governance, budgets, and risk",
            "Why were requests blocked? Which departments are close to budget?",
        ),
        (
            "Pruning",
            "How many tokens did pruning remove and how much did that save?",
        ),
    ]
    evidence = [
        {
            "label": label,
            "value": example,
            "metric_label": "example question",
            "detail": example,
            "filter_name": None,
            "filter_value": None,
        }
        for label, example in categories
    ]
    return {
        "question": request.question.strip(),
        "title": "What Ask CostPilot can answer",
        "answer": (
            "Ask CostPilot can analyze your governed AI spend, tokens, people, "
            "agents, business contexts, models, routing, pruning, budgets, and "
            "risk. You can ask follow-up questions to narrow the date range, "
            "source platform, person, agent, department, or account."
        ),
        "intent": "help",
        "entity": "overview",
        "metric": "request_count",
        "period": {},
        "filters": {},
        "summary": {},
        "evidence": evidence,
        "recommendations": [],
        "measurement_note": (
            "Answers describe measured AI consumption and governance activity; "
            "they do not score employee productivity or infer business outcomes."
        ),
        "calculation": {
            "metric": "capabilities",
            "formula": "No calculation was required.",
            "row_count": 0,
            "period_label": None,
        },
        "calculation_source": "CostPilot capability catalog",
        "data_provenance": {
            "scope": "capabilities",
            "live_requests": 0,
            "simulator_requests": 0,
            "active_filters": {},
            "period_label": None,
        },
        "assistant_mode": assistant_mode,
        "interpreted_as": "Ask CostPilot capability and example-question guide",
        "contract_status": "passed",
        "interpreted_intent": parsed,
        "conversation_context": {
            "intent": "help",
            "entity": "overview",
            "metric": "request_count",
            "days": int(parsed.get("days") or request.days),
            "direction": "desc",
            "result_limit": 5,
        },
        "read_only": True,
    }


_ASK_NUMBER_PATTERN = re.compile(r"(~\s*)?(?<!\w)-?\$?\d[\d,]*(?:\.\d+)?(%)?([mMkK](?![a-zA-Z]))?(×|[xX]\b)?")


def _ask_extract_numbers(value) -> set[float]:
    """
    Pull every number out of arbitrary text/JSON, normalized to a bare
    float (currency signs, thousands separators, and percent signs
    stripped). Used to compare "what numbers does the narrated answer
    contain" against "what numbers were actually in the source facts",
    since the two need to name the same values even though one is prose
    and the other is JSON.

    Reproduced live (agent-loop numeric-fidelity check false positives):
    a correct, fact-grounded answer's own natural-language rounding --
    "~1.2M tokens" for 1,224,008, "August 2026" for the current date --
    was flagged as fabricated, because the extractor had no concept of an
    M/K magnitude suffix (so "1.2M" became the bare number 1.2, which
    naturally matches nothing) and no notion that a bare 4-digit number
    might be a calendar year rather than a dollar/token figure. Both are
    now handled explicitly rather than left to trip the guardrail meant
    to catch fabricated numbers, not correct paraphrasing.
    """
    numbers: set[float] = set()
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    for full_match in _ASK_NUMBER_PATTERN.finditer(text):
        approx_marker, suffix, ratio_marker = full_match.group(1), full_match.group(3), full_match.group(4)
        if approx_marker:
            # "~1.2M tokens" -- the model itself is flagging this as a
            # rounded approximation, not asserting an exact fact. Verifying
            # it against the precise source figure would only catch the
            # model being honest about rounding, not catch a fabrication --
            # skip it entirely rather than fight over how much tolerance a
            # single "M"/"K"-suffixed significant figure deserves.
            continue
        if ratio_marker:
            # "2.6× last month's total" -- a multiplier the model computed
            # itself from two numbers that ARE in the source facts
            # (current/previous), not a figure asserted to exist in the
            # facts directly. Reproduced live: "more than 2.6x" flagged as
            # unverified even though 3,229,417 / 1,224,008 ~= 2.64 is a
            # correct derivation from data already confirmed present.
            continue
        match = full_match.group(0)
        cleaned = match.replace("$", "").replace(",", "").replace("%", "").strip()
        if suffix:
            cleaned = cleaned[: -len(suffix)].rstrip()
        try:
            number = float(cleaned)
        except ValueError:
            continue
        if suffix and suffix.lower() == "m":
            number *= 1_000_000
        elif suffix and suffix.lower() == "k":
            number *= 1_000
        # Skip tiny integers (0-9): "top 5", "5 departments", list positions,
        # and years/dates would otherwise flood false mismatches for values
        # that were never meant to be verified as a spend/token figure.
        if abs(number) < 10 and number == int(number):
            continue
        # "Sept 1-10" -- a date range's second number, immediately preceded
        # by a hyphen that itself follows another digit (the pattern's own
        # lookbehind above already stops that hyphen from being read as a
        # minus sign, but the bare day-of-month number itself still needs
        # excluding here). Reproduced live: a perfectly accurate answer
        # citing real tool figures also said "Sept 1-10" and "10 days in",
        # and the bare 10 -- outside the tiny-integer guard just above --
        # was flagged as an unverified fact, discarding a correct answer.
        start = full_match.start()
        if (
            start >= 2 and text[start - 1] in "-–—" and text[start - 2].isdigit()
            # "Aug 1–10" -- a well-formatted markdown answer favors a
            # real en dash (–) or em dash (—) over an ASCII
            # hyphen for a date range, which the ASCII-only check above
            # missed entirely (reproduced live: "Aug 1–10 | Sep 1–10"
            # in a table header flagged both "10"s as unverified even
            # though a plain "Sept 1-10" was already excluded).
            and not suffix and "$" not in match and "%" not in match
        ):
            continue
        # "10 days in" / "3 weeks ago" -- a duration description, not a
        # spend/token/count claim the guardrail is meant to verify.
        following = text[full_match.end():full_match.end() + 12]
        if (
            not suffix and "$" not in match and "%" not in match
            and re.match(r"[\s-]*(?:days?|hours?|minutes?|weeks?|months?|years?)\b", following, re.IGNORECASE)
        ):
            continue
        # A bare 4-digit integer in a plausible calendar-year range (no
        # currency/percent sign, no magnitude suffix) is almost always a
        # date mention ("August 2026"), never a real spend/token figure --
        # those are either much larger (raw token counts) or carry a $/%.
        if not suffix and "$" not in match and "%" not in match and 2000 <= number <= 2099 and number == int(number):
            continue
        numbers.add(round(number, 2))
    return numbers


def _ask_narration_unverified_numbers(facts: dict, narrated_answer: str) -> set[float]:
    """
    Return any number appearing in the model's rewritten answer that does
    not approximately match a number present in the source facts it was
    given. The prompt already tells the model to "preserve exact numbers",
    but nothing previously checked that — this is the fidelity check that
    was missing, catching a fluent rewrite that silently swaps or drops a
    figure before it reaches the user.
    """
    source_numbers = _ask_extract_numbers(facts)
    narrated_numbers = _ask_extract_numbers(narrated_answer)
    unverified = set()
    for number in narrated_numbers:
        if any(abs(number - source) <= max(0.01, abs(source) * 0.005) for source in source_numbers):
            continue
        unverified.add(number)
    return unverified


# Verbs that assert AI as the cause of a business result, rather than
# merely reporting that both figures exist. Matched near a currency/percent
# figure so "AI generated the report" (no dollar amount nearby) isn't
# flagged, but "AI generated $600,000" is. This is a category the numeric-
# fidelity check above cannot catch: the number can be perfectly correct
# while the causal claim around it is still unsupported by the facts (see
# database/models.py WorkItemOutcome and core/outcome_adapters -- CostPilot
# tracks AI activity alongside a business outcome, it never establishes
# that the AI activity caused it).
_ASK_CAUSAL_VERBS = (
    "generated", "drove", "caused", "resulted in", "produced", "created",
    "delivered", "led to", "responsible for",
    # "closed" as a bare verb (e.g. "the agent closed this $500K deal") --
    # guarded against matching inside "closed-won"/"closed-lost", Cost per
    # Outcome's own outcome-status vocabulary (Business Impact
    # investigation, Phase B). "won" was here too, but as a bare word it's
    # structurally indistinguishable from "AI spend associated with won
    # opportunities" (legitimate, adjective use) vs. "AI won the contract"
    # (causal, verb use) -- proximity matching can't tell those apart, and
    # every Cost per Outcome answer legitimately says "won"/"closed-won"
    # near "AI" and a dollar figure. Reproduced live: correct, carefully-
    # worded answers were silently discarded three separate ways by this
    # pattern before "won" was removed. The remaining verbs still catch
    # the primary risk (AI generated/caused/produced/delivered a result).
    r"closed(?!-won|-lost)",
)
_ASK_CAUSAL_CLAIM_PATTERN = re.compile(
    r"\b(ai|the model|claude|gpt|the agent)\b[^.?!]{0,40}\b("
    + "|".join(verb if verb.startswith(("closed", "(?<!")) else re.escape(verb) for verb in _ASK_CAUSAL_VERBS)
    + r")\b[^.?!]{0,40}[\$\d]",
    re.IGNORECASE,
)


# A clause boundary the negation check below must not cross: without this,
# an unrelated earlier hedge ("We're not sure why costs dropped, but AI
# generated $50,000 independently") would wrongly suppress a real, unhedged
# claim in a later clause joined by a reversal word.
_ASK_CAUSAL_CLAUSE_BOUNDARY_RE = re.compile(
    r"[.?!;]|\b(?:but|however|although|yet|whereas|though)\b", re.IGNORECASE
)
# The system prompts governing both narration paths explicitly instruct the
# model to write exactly this kind of hedge ("never say AI caused/generated/
# produced/drove a dollar amount") -- so a well-instructed model routinely
# produces sentences like "this does not mean AI generated the $600,000" or
# "I won't claim AI caused the $50,000 outcome". Confirmed live: the naive
# subject+verb+number proximity match above flags these exactly like a real,
# unhedged claim, discarding a correct, appropriately-cautious answer. "n't"
# catches every contraction (won't/doesn't/isn't/wasn't/shouldn't/...) in one
# check rather than enumerating each.
_ASK_CAUSAL_NEGATION_TERMS = (
    "not ", "n't", "never ", "no evidence", "not to say", "not saying",
    "not claiming", "incorrect to say", "should not", "would not",
    "cannot conclude", "can't conclude", "wrong to say", "misleading to say",
)


def _ask_narration_causal_claims(narrated_answer: str) -> list[str]:
    """
    Return any sentence fragment where the narration credits AI itself with
    causing a dollar/percent outcome (e.g. "AI generated $600,000" or
    "the agent closed this $500K deal"). CostPilot can state that AI
    activity and a business outcome co-occurred; it cannot claim the AI
    activity caused the outcome -- see "measure contribution before
    attribution" in the CostPilot Universal design notes.

    Skips a match whose own clause (since the last sentence end or reversal
    word like "but"/"however") contains a negation -- the model correctly
    denying causation ("this does not mean AI generated the $600,000") must
    not be treated the same as actually asserting it.
    """
    claims = []
    for match in _ASK_CAUSAL_CLAIM_PATTERN.finditer(narrated_answer):
        preceding = narrated_answer[:match.start()]
        boundaries = list(_ASK_CAUSAL_CLAUSE_BOUNDARY_RE.finditer(preceding))
        clause_start = boundaries[-1].end() if boundaries else 0
        clause = narrated_answer[clause_start:match.start()].lower()
        if any(term in clause for term in _ASK_CAUSAL_NEGATION_TERMS):
            continue
        claims.append(match.group(0))
    return claims


def _ask_grounded_narrative(
    request: AskCostPilotRequest,
    payload: dict,
) -> tuple[str, str, bool]:
    """Phrase verified CostPilot facts naturally without delegating calculation."""
    global _ASK_WRITER_DISABLED_UNTIL

    enabled = os.getenv(
        "ASK_COSTPILOT_NARRATION_ENABLED", "true"
    ).lower() not in {"0", "false", "no", "off"}
    api_key = os.getenv("OPENAI_API_KEY", "")
    if (
        not enabled
        or not api_key
        or api_key.startswith("YOUR")
        or time.monotonic() < _ASK_WRITER_DISABLED_UNTIL
    ):
        return payload["title"], payload["answer"], False

    response_tool = {
        "type": "function",
        "name": "write_grounded_costpilot_answer",
        "description": "Write a concise answer using only the supplied CostPilot facts.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "answer": {"type": "string"},
            },
            "required": ["title", "answer"],
            "additionalProperties": False,
        },
    }
    facts = {
        "question": request.question,
        "deterministic_title": payload.get("title"),
        "deterministic_answer": payload.get("answer"),
        "period": payload.get("period"),
        "filters": payload.get("filters"),
        "summary": payload.get("summary"),
        "evidence": (payload.get("evidence") or [])[:10],
        "recommendations": (payload.get("recommendations") or [])[:6],
        "calculation": payload.get("calculation"),
        "data_provenance": payload.get("data_provenance"),
        "measurement_note": payload.get("measurement_note"),
    }
    instructions = """You are Ask CostPilot, an enterprise AI usage analyst.
Answer the user's exact question in clear natural language using only the supplied JSON facts.
CostPilot already performed every calculation. Never calculate a different value, invent a row,
change a date range, or claim information not present in the facts. Preserve exact names and numbers.
State when the scope is live, simulator, mixed, or has no activity when that distinction matters.
Do not score employee productivity or infer business outcomes. Be concise and directly useful.
If the facts include a business outcome (outcome_status/outcome_value/outcome_success) alongside
AI spend, state both figures side by side as associated facts only -- e.g. "this $600,000 Closed
Won opportunity had $196 of tracked AI activity across 83 interactions." Never say AI generated,
drove, caused, produced, or was responsible for a dollar amount or business result; AI activity and
a business outcome occurring on the same work item is not evidence that one caused the other.
If an outcome's freshness is "potentially_stale" or "unavailable", say so rather than presenting it
as current.
Call write_grounded_costpilot_answer exactly once."""
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            timeout=_ask_env_seconds(
                "ASK_COSTPILOT_NARRATION_TIMEOUT_SECONDS", 4.0, 1.0, 10.0
            ),
            max_retries=0,
        )
        response = client.responses.create(
            model=os.getenv(
                "ASK_COSTPILOT_NARRATOR_MODEL",
                os.getenv("ASK_COSTPILOT_MODEL", "gpt-4.1-mini"),
            ),
            instructions=instructions,
            input=json.dumps(facts, default=str, separators=(",", ":")),
            tools=[response_tool],
            tool_choice="required",
        )
        for item in response.output:
            if (
                getattr(item, "type", None) == "function_call"
                and getattr(item, "name", None) == response_tool["name"]
            ):
                written = json.loads(getattr(item, "arguments", "{}"))
                title = str(written.get("title") or "").strip()
                answer = str(written.get("answer") or "").strip()
                if title and answer:
                    unverified = _ask_narration_unverified_numbers(facts, answer)
                    if unverified:
                        logger.warning(
                            "Ask CostPilot grounded narration rejected: numbers %s in the "
                            "rewritten answer do not appear in the source facts",
                            sorted(unverified),
                        )
                        return payload["title"], payload["answer"], False
                    causal_claims = _ask_narration_causal_claims(answer)
                    if causal_claims:
                        logger.warning(
                            "Ask CostPilot grounded narration rejected: causal claim(s) %s "
                            "credit AI with a business outcome the facts only associate it with",
                            causal_claims,
                        )
                        return payload["title"], payload["answer"], False
                    return title[:180], answer[:4000], True
    except Exception as exc:
        _ASK_WRITER_DISABLED_UNTIL = time.monotonic() + _ask_env_seconds(
            "ASK_COSTPILOT_NARRATION_COOLDOWN_SECONDS", 60.0, 5.0, 600.0
        )
        logger.warning("Ask CostPilot grounded narration failed: %s", exc)
    return payload["title"], payload["answer"], False


def _ask_agent_mode_enabled() -> bool:
    return os.getenv("ASK_COSTPILOT_AGENT_MODE", "true").lower() not in {
        "0", "false", "no", "off"
    }


def _ask_debug_enabled() -> bool:
    return os.getenv("ASK_COSTPILOT_DEBUG", "false").lower() not in {
        "0", "false", "no", "off"
    }


def _ask_debug_log(request: "AskCostPilotRequest", stage: str, payload: dict) -> None:
    """
    Internal diagnostic trace only — never returned to the client. Gated by
    ASK_COSTPILOT_DEBUG so it can be turned on in a support/debug session to
    see USER QUESTION -> INTENT/PLAN -> ENTITY RESOLUTION -> TOOL CALLS ->
    RESULTS -> FINAL ANSWER for a specific failing question, without
    exposing any of this to end users on every request.
    """
    if not _ask_debug_enabled():
        return
    try:
        # logger.warning, not .info: nothing in this app calls
        # logging.basicConfig, so the root logger sits at the default
        # WARNING level and every .info call is silently dropped — an
        # .info-level trace here would never actually print no matter how
        # many call sites used it. Severity is bumped, not the gating: this
        # still only fires when ASK_COSTPILOT_DEBUG is on.
        logger.warning(
            "ASK_COSTPILOT_TRACE[%s] question=%r %s",
            stage, request.question, json.dumps(payload, default=str)[:4000],
        )
    except Exception:
        pass


def _ask_agent_reporting_filters(request: "AskCostPilotRequest") -> dict:
    return {
        "project_id": request.project_id,
        "user_external_id": request.user_external_id,
        "agent_id": request.agent_id,
        "account_id": request.account_id,
        "source_platform": request.source_platform,
        "record_type": request.record_type,
        "model_tier": request.model_tier,
        "charged_unit": request.charged_unit,
        "business_purpose": request.business_purpose,
    }


def _ask_run_agent_tool(
    name: str, args: dict, db: Session, request: "AskCostPilotRequest", reporting_filters: dict,
    department_scope: Optional[str] = None, user_id: Optional[int] = None,
) -> dict:
    """
    department_scope (Phase 2 slice 1): when present, forced onto the 3
    tools below that support a department filter today -- it always wins
    over whatever the model itself chose to filter by, so a department-
    scoped user can't ask their way into another department's numbers by
    naming it explicitly. The other 5 tools are not scoped yet (see
    ask_costpilot's docstring for the full list).
    """
    from api.ask_costpilot_tools import EXECUTORS

    executor = EXECUTORS.get(name)
    if executor is None:
        return {"error": f"unknown tool: {name}"}
    if name == "get_usage_report":
        return executor(
            db, request.workspace_id, reporting_filters,
            int(args.get("days") or 30), args.get("period_key") or "none",
            args.get("entity_name") or None, int(args.get("limit") or 5),
            department_scope or (args.get("department") or None), args.get("provider") or None,
        )
    if name == "get_change_drivers":
        return executor(
            db, request.workspace_id, reporting_filters,
            int(args.get("days") or 30), args.get("period_key") or "none",
            args.get("comparison_key") or "previous_period",
            args.get("metric") or "spend_usd",
            args.get("dimension") or "organizational_unit_breakdown",
            department_scope or (args.get("department") or None), args.get("provider") or None,
        )
    if name == "get_budget_status":
        return executor(db, request.workspace_id, bool(args.get("alerts_only")), department_scope=department_scope)
    if name == "get_product_help":
        return executor(args.get("topic") or request.question)
    if name == "get_agent_adoption":
        return executor(
            db, request.workspace_id,
            args.get("status") or "all", int(args.get("usage_threshold") or 10),
            int(args.get("days") or 30), args.get("period_key") or "none",
            department_scope=department_scope,
        )
    if name == "get_account_outcomes":
        return executor(
            db, request.workspace_id, args.get("entity_name") or None,
            department_scope=department_scope,
        )
    if name == "get_cost_per_outcome":
        return executor(
            db, request.workspace_id,
            args.get("context_type") or None, args.get("entity_name") or None,
            department_scope=department_scope,
        )
    if name == "get_data_coverage":
        return executor(db, request.workspace_id)
    if name == "query_metrics":
        query_filters = dict(args.get("filters") or {})
        if department_scope:
            query_filters["charged_unit"] = department_scope
        return executor(
            db, request.workspace_id,
            metrics=args.get("metrics") or [],
            dimensions=args.get("dimensions") or [],
            filters=query_filters,
            days=int(args.get("days") or 30),
            period_key=args.get("period_key") or "none",
            compare_to=args.get("compare_to") or None,
            sort=args.get("sort") or None,
            limit=int(args.get("limit") or 20),
        )
    if name == "get_priority_signals":
        return executor(db, request.workspace_id, days=int(args.get("days") or 7), department_scope=department_scope)
    if name == "get_decision_history":
        return executor(
            db, request.workspace_id,
            agent_name=args.get("agent_name") or None,
            model_name=args.get("model_name") or None,
            keyword=args.get("keyword") or None,
            event_type=args.get("event_type") or None,
            limit=int(args.get("limit") or 10),
            department_scope=department_scope,
        )
    if name == "propose_budget_cap_change":
        return executor(
            db, request.workspace_id,
            department=args.get("department") or "",
            new_cap_usd=float(args.get("new_cap_usd") or 0),
            reason=args.get("reason") or "",
            department_scope=department_scope,
            user_id=user_id,
        )
    if name == "simulate_budget_cap_change":
        return executor(
            db, request.workspace_id,
            department=args.get("department") or "",
            new_cap_usd=float(args.get("new_cap_usd") or 0),
            department_scope=department_scope,
        )
    if name == "measure_budget_cap_outcome":
        return executor(
            db, request.workspace_id,
            department=args.get("department") or "",
            department_scope=department_scope,
        )
    return {"error": f"unhandled tool: {name}"}


_ASK_AGENT_ALL_CLEAR_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"no\s+depart\w*\s+(?:is|are)\s+(?:currently\s+)?(?:over|above|exceed\w*|near|approaching|close to)",
        r"no\s+depart\w*\s+(?:is|are)\s+(?:at or )?(?:above|over)\s+(?:the\s+)?\d{1,3}\s*%",
        r"(?:all|every)\s+depart\w*\s+(?:is|are)\s+(?:well\s+)?within",
        r"nothing\s+is\s+(?:currently\s+)?(?:over|near|close to|approaching)",
        r"no\s+depart\w*\s+(?:has|have)\s+(?:hit|reached|exceeded)",
    )
]


def _ask_agent_validate_answer(tool_call_log: list, answer_text: str) -> list[str]:
    """
    Check the model's freeform final_answer text for direct contradictions
    with the budget facts it was just given — modeled on the same spirit as
    validate_ask_answer_contract() (core/ask_costpilot_contracts.py) for the
    deterministic path, which has no equivalent for the agent loop today.
    Deliberately narrow: only the failure mode that actually happened this
    session (a fluent "all clear" narrative next to tool data that says
    otherwise), not a general-purpose fact-checker.
    """
    issues: list[str] = []

    # Causal-language guardrail: previously only wired into the
    # deterministic OpenAI-narration path (_ask_grounded_narrative), never
    # into this agent tool-loop path -- meaning an agent-loop answer built
    # from query_metrics/get_account_outcomes facts could claim "AI
    # generated $600,000" with nothing catching it. Same check, same
    # pattern, now applied here too so the guardrail covers both answer
    # paths instead of just one.
    issues.extend(
        f"answer uses causal language not supported by the data: {claim!r}"
        for claim in _ask_narration_causal_claims(answer_text)
    )

    # Numeric-fidelity guardrail: previously only wired into the
    # deterministic path's OpenAI narration step (_ask_grounded_narrative),
    # never into this agent-loop path -- reproduced live: an agent-loop
    # answer stated "$87.16 in associated AI spend (27,955 tokens)" for a
    # lost opportunity whose actual tool-returned figure was $0.09 for that
    # same token count, with nothing catching the fabricated number before
    # it reached the user. Same check, same tolerance, now applied here too.
    # A list, not a dict keyed by tool name -- the same tool can legitimately
    # be called more than once in one loop (e.g. get_usage_report per
    # provider for a comparison question), and a dict would silently drop
    # every call but the last, losing real facts the fidelity check needs.
    all_facts = [result for _tool_name, _args, result in tool_call_log]
    issues.extend(
        f"answer states ${number:,.2f} which does not match any figure the tools returned"
        for number in _ask_narration_unverified_numbers(all_facts, answer_text)
    )

    budget_rows: list[dict] = []
    for tool_name, _args, result in tool_call_log:
        if tool_name == "get_budget_status":
            budget_rows.extend(result.get("departments") or [])
    if not budget_rows:
        return issues

    over_rows = [r for r in budget_rows if float(r.get("used_pct") or 0) >= 100]
    near_rows = [r for r in budget_rows if float(r.get("used_pct") or 0) >= 80]
    text_lower = answer_text.lower()

    if near_rows and any(pattern.search(answer_text) for pattern in _ASK_AGENT_ALL_CLEAR_PATTERNS):
        worst = max(near_rows, key=lambda r: float(r.get("used_pct") or 0))
        issues.append(
            f"answer claims no departments are at risk, but {worst.get('label')} "
            f"is at {worst.get('used_pct')}% of its budget"
        )

    for row in over_rows:
        label = str(row.get("label") or "").strip()
        if label and label.lower() not in text_lower:
            issues.append(
                f"{label} is over budget ({row.get('used_pct')}%) per tool data "
                f"but is never mentioned in the answer"
            )

    return issues


def _ask_normalized_question(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _ask_suggested_questions(
    category: str,
    department: Optional[str] = None,
    subject: Optional[str] = None,
    asked_question: Optional[str] = None,
) -> list[str]:
    """
    Build "you might also ask" follow-ups templated from what was actually
    asked, instead of a fixed canned list shown regardless of context (the
    prior behavior: every answer outside a few hardcoded intents got an
    empty list, and the ones that got suggestions -- e.g. any budget
    question -- always saw the same 5 generic questions with no reference
    to the department/person actually asked about). Templated rather than
    model-generated so a suggestion can never point at something that
    isn't actually answerable, matching the no-hallucination approach used
    everywhere else in Ask CostPilot. Shared by both the deterministic path
    and the agent tool loop so the feature doesn't disappear depending on
    which one answered.
    """
    if category == "no_activity":
        # Zero data anywhere in the checked period -- a department/person
        # -scoped suggestion would be equally unanswerable, so this always
        # wins regardless of what was actually filtered on.
        candidates = [
            "Who had the most AI spend yesterday?",
            "Who had the most AI spend in the last 7 days?",
            "Show the latest AI activity.",
        ]
    elif department:
        candidates = [
            f"Which model is {department}'s biggest cost driver?",
            f"How does {department} compare to other departments?",
            f"Show {department}'s AI spend by person.",
        ]
    elif subject:
        candidates = [
            f"What did {subject} spend last month?",
            f"Which accounts has {subject} worked on?",
        ]
    elif category == "budget":
        candidates = [
            "How much AI budget is left this month?",
            "Are we on track to exceed budget this month?",
            "Which departments are closest to their budget limits?",
            "What is our budget variance so far this month?",
            "How much budget does each department have?",
        ]
    elif category in {"comparison", "change_drivers"}:
        candidates = [
            "Which department contributed most to the change?",
            "Was the change within budget?",
            "Which people contributed most to the change?",
            "Which agents contributed most to the change?",
            "Which accounts contributed most to the change?",
        ]
    elif category == "pruning":
        candidates = [
            "How much have we saved from pruning this quarter?",
            "Which department benefits most from token pruning?",
        ]
    elif category == "agent_adoption":
        candidates = [
            "Which agents are most active?",
            "Which agents have never been used?",
        ]
    elif category in {"ranking", "total", "overview"}:
        candidates = [
            "Which department spent the most on AI?",
            "Who are the top users by AI spend?",
            "What models are we using the most?",
        ]
    else:
        candidates = []

    # A suggestion that just restates the question the user already asked
    # (e.g. asking "which department spent the most" and being told to
    # ask... "which department spent the most") is worse than no
    # suggestion at all -- filter any near-exact restatement out.
    asked_normalized = _ask_normalized_question(asked_question) if asked_question else None
    if asked_normalized:
        candidates = [
            candidate for candidate in candidates
            if _ask_normalized_question(candidate) != asked_normalized
        ]
    return candidates


def _ask_agent_final_payload(
    request: "AskCostPilotRequest", db: Session, final_args: dict, tool_call_log: list,
) -> Optional[dict]:
    """
    Build the response payload from the model's final_answer call. Title and
    answer text come from the model; every number in `evidence`/`calculation`
    comes from tool_call_log — the actual deterministic tool results — never
    from the model's own arguments.
    """
    title = str(final_args.get("title") or "").strip()[:180]
    answer = str(final_args.get("answer") or "").strip()[:4000]
    if not title or not answer or not tool_call_log:
        return None

    contract_issues = _ask_agent_validate_answer(tool_call_log, answer)
    if contract_issues:
        logger.warning("Ask CostPilot agent answer failed validation: %s", "; ".join(contract_issues))
        return None

    calculation = None
    period = None
    data_scope = None
    live_requests = 0
    simulator_requests = 0

    # Build a pool of every candidate evidence row across every tool call —
    # not what gets shown, just what's *available* to cite. Keyed by the
    # exact raw "id" string the model actually sees in each tool's JSON
    # result — NOT a dimension-prefixed key. An earlier version prefixed
    # pool keys with the dimension name (e.g. "people:HIST2Y:...:USER:1"),
    # but the model only ever cites the raw id it was shown
    # ("HIST2Y:...:USER:1", no prefix) — so every evidence_ids lookup
    # silently failed to match anything, and evidence always fell back to
    # the hardcoded per-tool default instead of what the model actually
    # cited. Real ids already embed a type marker (":USER:", ":ACCOUNT:",
    # etc.), so cross-dimension collisions on the raw id aren't a
    # practical risk.
    evidence_pool: dict[str, dict] = {}
    primary_breakdowns: dict[str, list[dict]] = {}  # per data-tool fallback if evidence_ids come back empty

    def _pool_display_value(value_key: str, raw_value) -> tuple[str, str]:
        # Mirrors _ask_metric_value's formatting for the deterministic path —
        # this pool used to store the raw number with no unit or label at
        # all (e.g. "1.4138" instead of "$1.4138" with an "AI spend"
        # caption), the one field on this whole page that skipped the
        # formatting every other Ask CostPilot answer applies.
        if value_key == "spend_usd":
            return _ask_metric_value("spend_usd", raw_value)
        if value_key == "used_pct":
            try:
                return f"{float(raw_value or 0):.1f}%", "of monthly budget"
            except (TypeError, ValueError):
                return str(raw_value if raw_value is not None else "—"), ""
        if value_key == "absolute_change":
            try:
                number = float(raw_value or 0)
                sign = "+" if number >= 0 else "-"
                return f"{sign}${abs(number):,.4f}", "change vs. the prior period"
            except (TypeError, ValueError):
                return str(raw_value if raw_value is not None else "—"), ""
        if value_key == "status":
            return str(raw_value or "—"), "adoption status"
        return str(raw_value if raw_value is not None else "—"), ""

    def _pool(dimension: str, rows: list, value_key: str, filter_name: Optional[str]):
        built = []
        for row in rows or []:
            if row.get("id") is None and row.get("label") is None:
                continue
            pool_key = str(row.get("id") if row.get("id") is not None else row.get("label"))
            display_value, metric_label = _pool_display_value(value_key, row.get(value_key))
            item = {
                "label": row.get("label"),
                "value": display_value,
                "metric_label": metric_label,
                "filter_name": filter_name,
                "filter_value": row.get("id"),
            }
            evidence_pool[pool_key] = item
            built.append(item)
        return built

    for tool_name, _args, result in tool_call_log:
        # Later tool calls' period/scope win, since they're closer to what the
        # final answer is actually about — but any tool result is better than
        # none, so only overwrite with a real value.
        period = result.get("period") or period
        data_scope = result.get("data_scope") or data_scope
        summary = result.get("summary") or {}
        live_requests = int(summary.get("live_count") or 0) or live_requests
        simulator_requests = int(summary.get("simulation_count") or 0) or simulator_requests

        if tool_name == "get_usage_report":
            people = _pool("people", result.get("top_people"), "spend_usd", "user_external_id")
            _pool("department", result.get("top_departments"), "spend_usd", "charged_unit")
            _pool("account", result.get("top_accounts"), "spend_usd", "account_id")
            _pool("agent", result.get("top_agents"), "spend_usd", "agent_id")
            _pool("platform", result.get("top_platforms"), "spend_usd", "source_platform")
            _pool("model", result.get("top_models"), "spend_usd", None)
            _pool("provider", result.get("top_providers"), "spend_usd", "provider")
            named_match = result.get("named_entity_match")
            primary_breakdowns[tool_name] = people  # "who" defaults to people, not every dimension at once
            if named_match:
                matched = _pool(
                    named_match["entity_type"], [named_match["row"]], "spend_usd", None
                )
                # A directly-named entity is more relevant than any generic
                # top-5 list — make it what final_answer sees by default so
                # the model doesn't fall back to citing the overall total.
                primary_breakdowns[tool_name] = matched
        elif tool_name == "get_change_drivers":
            calculation = result.get("decomposition")
            primary_breakdowns[tool_name] = _pool(
                "driver", result.get("top_contributors"), "absolute_change", None
            )
        elif tool_name == "get_budget_status":
            primary_breakdowns[tool_name] = _pool(
                "budget", result.get("departments"), "used_pct", "charged_unit"
            )
        elif tool_name == "get_agent_adoption":
            primary_breakdowns[tool_name] = _pool(
                "adoption", result.get("agents"), "status", "agent_id"
            )

    # The model already tells us which facts it actually used — trust that
    # instead of guessing which dimension is relevant. Falls back to the
    # last data tool's single primary breakdown only if the model's ids
    # don't resolve to anything (malformed call), never to dumping every
    # dimension a tool happened to return.
    requested_ids = [str(i) for i in (final_args.get("evidence_ids") or [])]
    evidence = [evidence_pool[i] for i in requested_ids if i in evidence_pool]
    if not evidence:
        for tool_name, _args, _result in reversed(tool_call_log):
            if primary_breakdowns.get(tool_name):
                evidence = primary_breakdowns[tool_name][:5]
                break
    cited_departments = {
        str(row["label"]).lower() for row in evidence
        if row.get("filter_name") == "charged_unit" and row.get("label")
    }

    # Derive the same (category, department, subject) inputs the
    # deterministic path uses for _ask_suggested_questions, but from the
    # tool call log instead of a parsed intent -- last tool call wins, same
    # "closer to what the final answer is about" rule used for period/scope
    # above, so a multi-tool turn (e.g. change drivers then budget) bases
    # suggestions on the tool that actually produced the answer.
    _TOOL_SUGGESTION_CATEGORY = {
        "get_usage_report": "ranking",
        "get_change_drivers": "change_drivers",
        "get_budget_status": "budget",
        "get_agent_adoption": "agent_adoption",
        "get_account_outcomes": "account_outcomes",
        "get_data_coverage": "coverage",
        "query_metrics": "ranking",
        "get_priority_signals": "budget",
    }
    suggestion_category = ""
    suggestion_department = None
    suggestion_subject = None
    for tool_name, call_args, result in tool_call_log:
        if tool_name not in _TOOL_SUGGESTION_CATEGORY:
            continue
        suggestion_category = _TOOL_SUGGESTION_CATEGORY[tool_name]
        suggestion_department = call_args.get("department") or None
        suggestion_subject = None
        if tool_name == "get_usage_report":
            named_match = result.get("named_entity_match")
            if named_match and named_match.get("entity_type") in {"person", "account"}:
                suggestion_subject = named_match.get("label")

    payload = {
        "title": title,
        "answer": answer,
        "intent": "agent",
        "evidence": evidence,
        "recommendations": [],
        "calculation": calculation,
        "period": period,
        "data_provenance": {
            "scope": data_scope or "no_activity",
            "live_requests": live_requests,
            "simulator_requests": simulator_requests,
        },
        "assistant_mode": "agent_tool_loop",
        "query_plan": _ask_build_query_plan(tool_call_log),
        "suggested_questions": _ask_suggested_questions(
            "no_activity" if (data_scope or "no_activity") == "no_activity" else suggestion_category,
            department=suggestion_department,
            subject=suggestion_subject,
            asked_question=request.question,
        ),
    }

    # Action Proposals slice 1: any tool result carrying a "proposal" key
    # (e.g. propose_budget_cap_change) is surfaced generically here, keyed
    # by tool name so future propose_* tools work without touching this
    # function again -- last one found wins, matching the same "closer to
    # the final answer" rule used for suggestion_category above.
    for tool_name, _call_args, result in tool_call_log:
        if isinstance(result, dict) and result.get("proposal"):
            payload["proposal"] = result["proposal"]

    try:
        for signal in workspace_attention_signals(db, request.workspace_id, limit=3):
            department = (signal.get("department") or "").lower()
            if department and department in cited_departments:
                continue  # already the subject of this answer — don't repeat it
            payload["proactive_note"] = signal
            break
    except Exception as exc:
        logger.warning("Ask CostPilot proactive signal lookup failed: %s", exc)

    return payload


def _ask_query_plan_step_summary(tool_name: str, result: dict) -> str:
    """
    One-line, generic summary of a tool call's result for the query_plan
    trace -- Ask CostPilot architecture audit Recommendation #5 (item 6,
    Traceability): the full tool_call_log already existed internally, but
    was explicitly acknowledged as never reaching the API response, only
    a debug log gated behind an env flag. This is deliberately generic
    (checks a handful of shapes common across every tool) rather than one
    branch per tool name -- a new tool doesn't need this function edited
    to get a reasonable summary instead of silently falling through to
    the "ok" default.
    """
    if not isinstance(result, dict):
        return "ok"
    if result.get("error"):
        return str(result["error"])[:200]
    if isinstance(result.get("summary"), dict):
        s = result["summary"]
        if "spend_usd" in s:
            return f"${float(s.get('spend_usd') or 0):,.2f} spend, {int(s.get('request_count') or 0)} requests"
    if result.get("found") is False:
        return "not found"
    for rows_key in ("rows", "departments", "evidence"):
        if isinstance(result.get(rows_key), list):
            return f"{len(result[rows_key])} row(s)"
    return "ok"


def _ask_build_query_plan(tool_call_log: list) -> list[dict]:
    """
    Redacted, structured trace of every tool call the agent loop made
    before its final answer -- args and a one-line result summary, not
    the full (potentially large) raw result blob. This is the "structured,
    pre-execution-validated query-plan object" the deterministic path
    already has as AskCostPilotContext, made visible for the agent-loop
    path too, closing the gap the architecture audit flagged: every tool
    call already runs through each tool's own strict-mode JSON schema
    (enum/type/required-field validation happens before the model's
    arguments ever reach this codebase) and, for query_metrics, a second
    semantic validation pass inside run_metrics_query() itself (unknown_
    metric/unknown_dimension/no_valid_metrics) -- this trace is what makes
    that validated plan auditable after the fact, not a new validation
    layer duplicating either of those.
    """
    return [
        {
            "step": i + 1,
            "tool": name,
            "args": args,
            "status": "error" if (isinstance(result, dict) and result.get("error")) else "ok",
            "summary": _ask_query_plan_step_summary(name, result),
        }
        for i, (name, args, result) in enumerate(tool_call_log)
    ]


_ASK_AGENT_LOOP_STATS = {"success": 0, "fallback": 0}


def _ask_record_agent_outcome(success: bool) -> None:
    """
    Lightweight in-memory success/fallback counter for the agent loop --
    resets on dyno restart, not persisted anywhere. Cheap enough to be
    always-on; exists so a future reliability regression (e.g. the
    guaranteed-fallback timeout math this counter was added alongside a
    fix for) shows up as a ratio in the existing logs, instead of only
    being discoverable by grepping every ask_costpilot_fallback line by
    hand after a user notices bad answers.
    """
    _ASK_AGENT_LOOP_STATS["success" if success else "fallback"] += 1
    total = _ASK_AGENT_LOOP_STATS["success"] + _ASK_AGENT_LOOP_STATS["fallback"]
    if total % 20 == 0:
        rate = _ASK_AGENT_LOOP_STATS["success"] / total * 100
        logger.warning(
            "ask_costpilot_agent_loop_stats success=%d fallback=%d success_rate=%.1f%%",
            _ASK_AGENT_LOOP_STATS["success"], _ASK_AGENT_LOOP_STATS["fallback"], rate,
        )


def _ask_record_agent_fallback(workspace_id: Optional[str], reason: str, detail: str = "") -> None:
    """
    Always-on record of why the agent tool loop was skipped in favor of the
    deterministic fallback -- unlike _ask_debug_log, this isn't gated behind
    ASK_COSTPILOT_DEBUG, so "which workspace fell back, when, and why" is
    visible in heroku logs by default, not only during a debug session.
    provider="anthropic" is included even though this loop only calls one
    provider today, so if a second provider is ever added to this path, the
    log records already carry the field needed to filter by it.
    """
    _ask_record_agent_outcome(False)
    logger.warning(
        "ask_costpilot_fallback workspace=%s provider=anthropic reason=%s%s",
        workspace_id or "default", reason, f" detail={detail}" if detail else "",
    )


def _ask_costpilot_agent(
    request: "AskCostPilotRequest", db: Session, department_scope: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Bounded tool-calling loop: the model chooses which deterministic lookups
    to run (max 4), then must call final_answer using only facts those
    lookups returned. Any failure/timeout/malformed turn returns None so
    ask_costpilot() falls straight back to the existing deterministic
    18-intent path — this path is purely additive.

    department_scope (Phase 2 slice 1): forwarded to _ask_run_agent_tool,
    which forces it onto the 3 tools that support department filtering
    today, overriding whatever department the model itself chose.
    """
    global _ASK_AGENT_DISABLED_UNTIL
    breaker_key = request.workspace_id or "default"

    if not _ask_agent_mode_enabled():
        return None
    _ask_debug_log(request, "question_received", {
        "workspace_id": request.workspace_id,
        "is_follow_up": _ask_is_follow_up(request.question),
        "context": request.context.model_dump(exclude_none=True) if request.context else None,
    })
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or api_key.startswith("YOUR"):
        _ask_debug_log(request, "abort_no_api_key", {})
        _ask_record_agent_fallback(request.workspace_id, "no_api_key")
        return None
    disabled_until = _ASK_AGENT_DISABLED_UNTIL.get(breaker_key, 0.0)
    if time.monotonic() < disabled_until:
        seconds_remaining = round(disabled_until - time.monotonic(), 1)
        _ask_debug_log(request, "abort_in_cooldown", {"seconds_remaining": seconds_remaining})
        _ask_record_agent_fallback(request.workspace_id, "in_cooldown", f"seconds_remaining={seconds_remaining}")
        return None

    from api.ask_costpilot_tools import TOOL_SCHEMAS, FINAL_ANSWER_TOOL, to_anthropic_tools

    reporting_filters = _ask_agent_reporting_filters(request)
    tool_call_log: list = []
    max_tool_calls = 4

    instructions = """You are CostPilot's usage analyst. Answer the user's question about their
attributed AI spend and usage by calling tools to retrieve real numbers — never state a figure
you did not retrieve from a tool in this conversation.
Call get_usage_report for totals, rankings, or "how much/who/what" questions. Its result has
separate top_people and top_accounts lists — "account" means a business/customer entity (e.g.
a company record in Salesforce), "people" means individual human users. These are never the
same thing; never answer an "accounts" question using top_people data, or vice versa, even if
one list is empty and the other has rows.
A bare "who"/"whose" question (e.g. "who has the highest AI spend", "whose usage grew the
most") with no department/team/account language means an individual PERSON -- answer from
top_people, never top_departments, even if a department's number happens to be larger. Confirmed
live: "Who has the highest AI spend this month?" was wrongly answered with a department's spend
("Operations has the highest AI spend") instead of naming the actual top person -- pick the
breakdown that matches what was asked, not whichever number is largest across all of them.
Always set get_usage_report's limit argument to match the question — "top 10 users" needs
limit=10, "who spent the most" needs limit=1, a general overview can use the default of 5.
Never assume more rows exist beyond what the tool actually returned.
If the question names a specific department (e.g. "what models is Sales using", "how much did
Marketing spend"), always pass that department into get_usage_report's or get_change_drivers'
department argument — the top_* lists in an unfiltered call are company-wide, not scoped to
any department, so without this argument you cannot actually answer a department-scoped
question and must never present an unfiltered total as if it were department-specific.
The same applies to provider (Anthropic/OpenAI/Google/Mistral/Meta) — this is the AI vendor
behind a model, NOT the source platform (Salesforce/ServiceNow/etc are platforms, not
providers). "Claude" or "Sonnet" means provider='Anthropic'; "GPT" means provider='OpenAI'.
For "compare OpenAI and Anthropic spend" or similar two-provider comparisons, call
get_usage_report twice, once per provider, and compare the two summaries yourself — there is
no single tool call that returns both sides of a provider comparison at once.
If the question names a specific person, account, department, agent, platform, or model (for
example "how much did BluePeak Consulting use" or "what did Maya Chen spend"), pass that exact
name in get_usage_report's entity_name argument. Its top_* lists are truncated to 5 rows by
spend, so a named entity may not appear there even though it exists — always rely on the
named_entity_match field for a named-entity question, and never answer it with the overall
total or say the entity had no usage just because it is missing from a top_* list.
Call get_change_drivers for questions about why a number changed, increased, or decreased.
Call get_budget_status for budget, cap, or "on track" questions.
Call get_agent_adoption for questions about which AGENTS (AI bots/automations, e.g. "Sensor
Summary Agent") are active, inactive, unused, never used, or recently quiet. get_usage_report
only ever returns agents that HAVE activity, so it cannot answer "which agents are inactive" —
you must call get_agent_adoption for that, never infer an answer by subtracting counts.
get_agent_adoption is ONLY about agents, never about people/users/employees -- "agent" and
"user"/"person" are different entities (an agent is a piece of software; a person is a human).
There is currently no tool that answers "which people/users have been inactive" -- if asked
that, say plainly that this isn't something you can compute yet, rather than calling
get_agent_adoption and presenting its agent-level counts as if they were about people. Confirmed
live: this produced "all 7 registered users have been active" -- a fabricated reframing of an
agent-adoption result (7 was never a count of people) as if it answered a completely different
question about people.
Call get_account_outcomes for any question about business outcomes — Opportunities won/lost/open,
pipeline value, closed-won value, resolved support cases, or AI spend tied to won vs lost deals.
get_usage_report NEVER returns outcome data, even for a named account's entity_name lookup — it
only knows spend and call counts. Pass entity_name to scope to one named account (e.g. "Acme");
leave it empty for a company-wide won/lost comparison. If get_account_outcomes returns
found: false, tell the user the account wasn't found rather than guessing or falling back to
get_usage_report's spend data as if it answered an outcome question.
Call get_cost_per_outcome for any "cost per X" or "return on AI spend" question -- cost per
closed-won opportunity, cost per resolved case, cost per hire, cost per shipped feature, or any
similar ratio of AI spend to successful outcomes. Works for any work type via its context_type
argument (e.g. 'opportunity', 'case'), not just Opportunities; leave context_type empty for
every work type combined. Its result always includes an evidence_label (early_signal / meaningful
/ executive_eligible) based on sample size -- you must state that label in your answer, and must
not present an early_signal result as a confident finding. This measures association between AI
activity and outcomes, never causation -- never say AI caused these outcomes, only that AI
activity was associated with them.
Call get_data_coverage before answering any question that names a specific source platform
(Salesforce, ServiceNow, HubSpot) -- for example "show AI activity across Salesforce, HubSpot,
and ServiceNow" -- and check whether each named platform is actually connected before answering
for it. If a named platform is not connected, say so plainly (e.g. "I can answer this for
Salesforce; HubSpot and ServiceNow are not currently connected") instead of answering only for
the connected ones without noting what's missing, and never imply a platform's data is included
when get_data_coverage shows it is not connected.
Prefer query_metrics for ordinary reporting questions -- spend/usage/outcome totals, breakdowns,
rankings, filtered questions, and comparisons -- over get_usage_report or get_change_drivers,
which remain only for backward compatibility. query_metrics can combine an activity metric
(ai_spend, ai_requests, tokens, active_agents) with an outcome metric (won_count, won_value,
pipeline_value, etc.) in one call when the question needs both, e.g. "AI spend on Acme's won
deals" -- pass filters.account and request both metrics together rather than calling two tools
and adding the results yourself. If query_metrics returns unsupported_metrics for something you
asked for, tell the user plainly that it isn't computable yet and why (using the reason given),
never substitute a different number or guess. If it returns errors (e.g. an ambiguous or
not-found account name), surface that to the user instead of guessing which account was meant.
Call get_priority_signals for open-ended attention questions -- "what should I pay attention to
today", "is anything unusual happening", "what are the top things I should know about". It
returns a pre-ranked list (budget risk first, then biggest spend swings); narrate that list, do
not reorder it by your own judgment or add priorities it didn't return. If it returns an empty
list, say plainly that nothing needs attention right now rather than inventing a concern.
Call get_decision_history for "why did we..." or "why are we using..." questions about a past
governance decision -- model selection, budget action, or collision lock -- when the user has
NOT already given you a specific decision id. Filter by agent_name, model_name, keyword (searched
against the decision rationale text), and/or event_type; leave any of those empty to not filter
on it. Narrate directly from the rationale field of each returned decision -- never invent a
justification it didn't actually state. Each returned decision also carries agent_name,
actor_name, and actor_email (the real person/system that triggered it, when known) -- state these
plainly when the user asks who or what agent was involved, e.g. a "which user/agent caused this
policy violation" follow-up. Only say the identity is unknown when these fields are actually null
on the matching row(s); never claim a specific named human approved a decision beyond what these
fields and the rationale actually state.
Call simulate_budget_cap_change for any "what if we raised/lowered X's cap to $Y", "would $Y be
enough", or "should we raise the cap" question -- it is read-only, creates nothing, and answers
the question directly with the department's real current-period spend, its run-rate projection to
month end, and whether the hypothetical cap would be exceeded and around when. Narrate those exact
numbers; never invent a projection of your own.
Call propose_budget_cap_change ONLY when the user has clearly asked to change a department's
budget cap, or has just explicitly accepted a recommendation you made to do so (e.g. they say
"do it" or "yes" right after you proposed a specific number). Never call it for a "what if"
question -- call simulate_budget_cap_change instead. Never call it speculatively to illustrate an
idea. Its result includes a real simulation of projected impact (same numbers
simulate_budget_cap_change would return) -- state those in your answer alongside the fact that
this is only a proposal awaiting confirmation; never say the change has been made, since it has
not; a human must confirm it before anything changes.
Call measure_budget_cap_outcome for "did that budget change work", "how has spend been since we
raised/lowered X's cap", or "was that a good decision" questions -- it compares real spend since
an already-executed change to what was projected before it. Narrate its exact numbers and its
trend label (accelerated/slowed/about the same); if it says too_soon, tell the user plainly that
it is too early to measure rather than offering a verdict anyway. If it reports no executed change
found, say so rather than guessing at one.
Call get_product_help for questions about how CostPilot/Ask CostPilot itself works or behaves --
including "what can you do" / "what can Ask CostPilot do" capability questions, what data it
uses, whether it guesses or invents numbers, how it handles an ambiguous name, the difference
between measured and estimated data, outcome association vs. causation, and whether it can
change a budget on your behalf. "CostPilot" or "Ask CostPilot" in a question is the product
asking about itself -- never pass it as get_usage_report's entity_name looking for a customer
entity that happens to share the name (e.g. an agent or platform named "CostPilot-API"); that
answers a completely different, unasked question with a real-looking but wrong number.
You may call more than one tool if the question needs it — for example checking change drivers
and then budget status. Once you have enough information, call final_answer. Do not call
final_answer without having called at least one data tool first, unless the question is purely
about CostPilot the product. Always call exactly one tool per turn.
The user's message includes a DEFAULT_WINDOW line giving the date range already selected in
their screen (e.g. a dashboard date picker). If the question does not name its own period
("this month", "last year", etc.), use that default window for your first data lookup instead
of guessing a period of your own — do not assume "this month" or any other arbitrary window
when the caller already told you what range they're looking at. Only widen or change the
window if the caller's default window comes back with no activity and the question doesn't
depend on that exact range mattering.
If this is a follow-up question in an ongoing conversation and it does not name its own period
either, reuse the exact period_key/days from your most recent tool call in this conversation
instead of the DEFAULT_WINDOW, "none", or any rolling-days default -- the caller is continuing to
look at the same time range until they say otherwise. Concrete example: you were just asked
"Which department has the highest AI spend for this month?" and called query_metrics with
period_key="this_month"; the caller now asks "Who has the second highest?" with no period of its
own -- call query_metrics again with period_key="this_month" (NOT period_key="none"/days=30, which
silently switches to a rolling 30-day window and answers a different question than the one still
being discussed). Only switch away from the prior period when the new question explicitly names a
different one itself (e.g. "...last month instead?").
The question may come from voice transcription and can contain misheard words (e.g. a person's
surname transcribed as an unrelated common word). Once a tool result resolves the actual entity
you matched (its real name/label as returned by the tool), always use that resolved name in your
title and answer -- never echo the user's literal misheard wording back at them just because it
appeared in their question."""

    try:
        import anthropic

        # timeout_seconds bounds a single Claude call — it does not bound
        # the loop as a whole, which can run up to max_tool_calls + 1 turns.
        # A merely-slow (not individually timed-out) sequence of turns could
        # add up to well past Heroku's 30s router limit, which kills the
        # whole request with no clean fallback — worse than just answering
        # from the deterministic path. total_budget_seconds bounds the
        # entire loop's wall-clock time instead: checked before every turn,
        # comfortably under 30s, so this code chooses to fall back on its
        # own terms rather than being cut off mid-request.
        timeout_seconds = _ask_env_seconds(
            # Raised from 10.0: reproduced live, 3/3 runs, that a turn
            # calling 2 tools (get_change_drivers + get_budget_status) has
            # its *second* model call -- the one synthesizing a longer
            # final answer from both tool results, not just picking a tool
            # -- consistently exceed a 10s per-call timeout and get killed
            # outright, separate from (and not fixed by) the total-budget
            # preflight change above. 15.0 still leaves headroom under the
            # 25.0 total budget for a turn that already took a few seconds.
            "ASK_COSTPILOT_AGENT_TIMEOUT_SECONDS", 15.0, 5.0, 45.0
        )
        total_budget_seconds = _ask_env_seconds(
            # Raised from 15.0: the preflight check below (elapsed +
            # timeout_seconds > total_budget_seconds) meant any turn taking
            # 5+ seconds already guaranteed fallback on the very next turn --
            # a single Sonnet tool-selection call commonly takes close to
            # that on its own, so any question needing 2+ tool calls was
            # essentially guaranteed to fall back (confirmed live via
            # repeated budget_preflight fallbacks). 25.0 is the clamp's own
            # pre-existing, already-vetted maximum -- the comment above
            # already explains it was chosen to stay comfortably under
            # Heroku's 30s router limit, so this is a default change inside
            # an already-shipped safety margin, not a new risk.
            "ASK_COSTPILOT_AGENT_TOTAL_BUDGET_SECONDS", 25.0, 5.0, 25.0
        )
        loop_start = time.monotonic()
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        model = os.getenv(
            "ASK_COSTPILOT_AGENT_MODEL", os.getenv("ANTHROPIC_FLAGSHIP_MODEL", "claude-sonnet-4-6")
        )
        all_tools = to_anthropic_tools(TOOL_SCHEMAS + [FINAL_ANSWER_TOOL])
        # Prompt caching: this tool list + the instructions string below are
        # identical on essentially every Ask CostPilot call, from every user,
        # every turn -- confirmed live these were being resent uncached,
        # contributing to a measured ~10-12s floor even for a single-tool-
        # call question. cache_control on the last tool caches the entire
        # tools array (Anthropic's caching order is tools -> system ->
        # messages, cumulative up to each breakpoint); marking the system
        # prompt too extends the cached prefix through both. Only the
        # per-request messages (the actual question) stay uncached, which is
        # exactly the small, unique-per-call part that should be.
        if all_tools:
            all_tools[-1] = {**all_tools[-1], "cache_control": {"type": "ephemeral"}}
        cached_system = [{"type": "text", "text": instructions, "cache_control": {"type": "ephemeral"}}]
        conversation_text = _ask_conversation_text(request)
        if request.date_from and request.date_to:
            window_line = (
                f"DEFAULT_WINDOW: {request.date_from.date()} to {request.date_to.date()} "
                "(explicit date range already selected on screen)"
            )
        else:
            window_line = f"DEFAULT_WINDOW: last {max(1, min(int(request.days or 30), 365))} days"
        messages: list = [
            {"role": "user", "content": f"{window_line}\n{conversation_text}"}
        ]

        for _turn in range(max_tool_calls + 1):
            elapsed = time.monotonic() - loop_start
            if elapsed + timeout_seconds > total_budget_seconds:
                _ask_debug_log(request, "abort_budget_preflight", {
                    "turn": _turn, "elapsed": round(elapsed, 2),
                    "timeout_seconds": timeout_seconds, "total_budget_seconds": total_budget_seconds,
                })
                _ask_record_agent_fallback(request.workspace_id, "budget_preflight", f"turn={_turn}")
                return None
            _ask_debug_log(request, "calling_model", {"turn": _turn, "model": model})
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=cached_system,
                messages=messages,
                tools=all_tools,
                tool_choice={"type": "any"},
            )
            tool_uses = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            if not tool_uses:
                _ask_debug_log(request, "abort_no_tool_use_block", {
                    "turn": _turn,
                    "stop_reason": getattr(response, "stop_reason", None),
                    "content_types": [getattr(b, "type", None) for b in response.content],
                })
                _ask_record_agent_fallback(request.workspace_id, "no_tool_use_block", f"turn={_turn}")
                return None

            messages.append({"role": "assistant", "content": response.content})

            final_call = next((block for block in tool_uses if block.name == "final_answer"), None)
            if final_call is not None:
                final_args = dict(final_call.input or {})
                _ask_debug_log(request, "final_answer", {
                    "turn": _turn, "args": final_args,
                    "tool_calls": [{"tool": n, "args": a} for n, a, _r in tool_call_log],
                })
                payload = _ask_agent_final_payload(request, db, final_args, tool_call_log)
                _ask_debug_log(request, "response", {"payload": payload})
                if payload is None:
                    _ask_record_agent_fallback(request.workspace_id, "validation_failed", f"turn={_turn}")
                else:
                    _ask_record_agent_outcome(True)
                return payload

            tool_results = []
            for call in tool_uses:
                if len(tool_call_log) >= max_tool_calls:
                    _ask_debug_log(request, "abort_max_tool_calls", {"turn": _turn})
                    _ask_record_agent_fallback(request.workspace_id, "max_tool_calls", f"turn={_turn}")
                    return None
                call_args = dict(call.input or {})
                result = _ask_run_agent_tool(call.name, call_args, db, request, reporting_filters, department_scope=department_scope, user_id=user_id)
                tool_call_log.append((call.name, call_args, result))
                _ask_debug_log(request, "tool_call", {
                    "turn": _turn, "tool": call.name, "args": call_args, "result": result,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})
        _ask_debug_log(request, "abort_loop_exhausted", {"turns": max_tool_calls + 1})
        _ask_record_agent_fallback(request.workspace_id, "loop_exhausted", f"turns={max_tool_calls + 1}")
        return None
    except Exception as exc:
        cooldown_seconds = _ask_env_seconds(
            "ASK_COSTPILOT_AGENT_COOLDOWN_SECONDS", 300.0, 15.0, 3600.0
        )
        # Scoped to this one workspace -- a transient provider timeout or a
        # genuine bug hit while answering one workspace's question no
        # longer suppresses correct answers for every other workspace for
        # the whole cooldown window (see plan: "Scope the Ask CostPilot
        # circuit breaker to workspace, not global").
        _ASK_AGENT_DISABLED_UNTIL[breaker_key] = time.monotonic() + cooldown_seconds
        logger.warning("Ask CostPilot agent loop failed: %s", exc)
        _ask_record_agent_fallback(request.workspace_id, "exception", str(exc)[:200])
        return None


def _ask_budget_flag(db: Session, workspace_id: Optional[str]) -> dict:
    """
    A deterministic budget-status badge attached to every Ask CostPilot
    answer, regardless of what was asked or which internal path produced
    the answer. Computed the same way every time from workspace_attention_signals
    — never narrated or phrased by a model — so "is anything over budget"
    never depends on an LLM correctly noticing and stating it.
    """
    try:
        signals = workspace_attention_signals(db, workspace_id, limit=20)
    except Exception as exc:
        logger.warning("Ask CostPilot budget flag lookup failed: %s", exc)
        return {"severity": "unknown", "over_budget": [], "near_cap": []}

    over_budget = [s for s in signals if s.get("type") == "budget_exceeded"]
    near_cap = [s for s in signals if s.get("type") == "budget_near_cap"]
    severity = "critical" if over_budget else "warning" if near_cap else "ok"

    # A cross-workspace "anywhere in the system" scan (any_workspace_over_budget)
    # used to run here too, but with the workspace list simplified down to
    # exactly two (Production, Simulated), it just produced a second,
    # contradictory-looking line under the workspace-scoped one above —
    # switching between two workspaces to check each is now trivial, so the
    # extra check wasn't worth the confusion. Removed; workspace_id-scoped
    # signals only.
    return {
        "severity": severity,
        "over_budget": [{"department": s.get("department"), "detail": s.get("detail")} for s in over_budget],
        "near_cap": [{"department": s.get("department"), "detail": s.get("detail")} for s in near_cap],
    }


def _ask_log_interaction(
    request: "AskCostPilotRequest",
    result,
    latency_ms: int,
    error_type: Optional[str],
    db: Session,
    user_id: Optional[int] = None,
) -> None:
    """
    Phase 1 of the governed continuous-learning plan: log one AskInteraction
    row per question, purely observational -- nothing reads this table to
    change behavior yet. Called from the single outer ask_costpilot()
    wrapper so every intent branch is covered by one insertion point.

    Field extraction is necessarily best-effort in this v1, since the
    different intent branches (help/product/decision/agent-loop/
    deterministic) return different response shapes and not all of their
    internal state is threaded up to this outer wrapper today:
      - assistant_mode/interpreted_intent/contract_issues are read directly
        off `result` when the branch that ran happens to include them
        (several already do, e.g. the contract-guardrail response).
      - tools_called is derived from the agent loop's query_plan (already
        built from tool_call_log for the response itself), not a direct
        tool_call_log capture.
      - validation_passed is inferred as True unless the response we got
        back IS a contract-guardrail response -- an agent-loop validation
        failure never surfaces as a result at all; it silently falls back
        to the deterministic path before ask_costpilot() ever sees it, so
        there is no way to distinguish "validation passed" from "the
        agent loop wasn't used for this question" purely from `result`
        alone yet.
    Wrapped in try/except and never re-raised -- a logging failure must
    never break a real answer. Several tests exercise the deterministic
    path with db=None (no real session) -- silently no-op rather than
    erroring, same as the rest of this function's fail-safe behavior.
    """
    if db is None:
        return
    try:
        parsed = result.get("interpreted_intent") if isinstance(result, dict) else None
        parsed = parsed if isinstance(parsed, dict) else {}
        assistant_mode = result.get("assistant_mode") if isinstance(result, dict) else None
        intent = (result.get("intent") if isinstance(result, dict) else None) or parsed.get("intent")
        contract_issues = result.get("contract_issues") if isinstance(result, dict) else None
        query_plan = result.get("query_plan") if isinstance(result, dict) else None
        evidence = result.get("evidence") if isinstance(result, dict) else None
        evidence_label = None
        if isinstance(evidence, list) and evidence:
            first = evidence[0]
            if isinstance(first, dict):
                evidence_label = first.get("evidence") or first.get("evidence_label")

        db.add(AskInteraction(
            workspace_id=request.workspace_id,
            user_id=user_id,
            governed_request_id=result.get("governed_request_id") if isinstance(result, dict) else None,
            question_text=(request.question or "")[:2000],
            intent=intent,
            entity=parsed.get("entity"),
            metric=parsed.get("metric"),
            filters_json=json.dumps({k: v for k, v in parsed.items() if k not in ("intent", "entity", "metric")}) if parsed else None,
            period_key=parsed.get("period_key"),
            assistant_mode=assistant_mode,
            tools_called_json=json.dumps(query_plan) if query_plan else None,
            validation_passed=False if assistant_mode == "contract_guardrail" else True,
            contract_issues_json=json.dumps(contract_issues) if contract_issues else None,
            fallback_used=bool(assistant_mode and assistant_mode != "agent_tool_loop" and intent not in ("help", "product", "decision")),
            unsupported=intent in ("help", "decision") if intent else None,
            latency_ms=latency_ms,
            error_type=error_type,
            evidence_label=evidence_label,
            modality=request.modality or "text",
            transcription_confidence=request.transcription_confidence,
            clarification_requested=(intent == "clarification_required"),
        ))
        db.commit()
    except Exception:
        # Several tests exercise the deterministic path against minimal DB
        # stubs (e.g. _BudgetDbStub) that only implement .query() -- no
        # .add()/.commit()/.rollback() at all. A logging failure must never
        # break a real answer, so this needs to survive not just the
        # primary attempt failing, but the rollback call itself failing on
        # an unusual db object.
        try:
            db.rollback()
        except Exception:
            pass


@router.post("/ask")
def ask_costpilot(
    request: AskCostPilotRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Thin wrapper: guarantees the deterministic budget_flag badge and the
    active workspace's real name are attached to every answer no matter
    which internal path (agent loop or the deterministic intent classifier,
    and any of their early-return branches) produced it. Also logs one
    AskInteraction row per question (Phase 1 of the governed continuous-
    learning plan) -- purely observational, never affects the response.

    Phase 0 identity threading (next slice of the security architecture
    assessment): resolves the caller's session via the same non-blocking
    check_membership() every other retrofitted route already uses --
    returns a real TenantContext when a valid session is presented, None
    for anything else (no header, expired session, no membership), and
    never raises since AUTH_ENFORCEMENT_ENABLED stays off. This does not
    change who can ask a question or what they're answered; it only
    means a question asked while logged in gets attributed to a real
    user_id in the audit trail instead of staying anonymous.

    Phase 2 slice 1: department_scope from that same TenantContext is
    threaded into the answer path so a department-scoped user's question
    is forcibly narrowed to their own department -- see
    _ask_run_agent_tool for where that's actually enforced. Only 3 of the
    8 agent-loop tools honor it today (get_usage_report, get_change_drivers,
    query_metrics); the rest, and the deterministic fallback path, are
    not scoped yet -- a documented gap, not an oversight."""
    ask_ctx = check_membership(db, authorization, request.workspace_id) if request.workspace_id else None
    ask_user_id = ask_ctx.user.id if ask_ctx else None
    ask_department_scope = ask_ctx.department_scope if ask_ctx else None
    start = time.monotonic()
    error_type = None
    result = None
    try:
        result = _ask_costpilot_answer(request, db, department_scope=ask_department_scope, user_id=ask_user_id)
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        _ask_log_interaction(request, result, latency_ms, error_type, db, user_id=ask_user_id)
    if isinstance(result, dict):
        result["budget_flag"] = _ask_budget_flag(db, request.workspace_id)
        result["workspace_name"] = _ask_workspace_name(db, request.workspace_id)
    return result


def _ask_workspace_name(db: Session, workspace_id: Optional[str]) -> str:
    """
    Every answer states which workspace it's scoped to — the confusion
    that caused several rounds of "the data is wrong" this session was
    never bad math, it was not knowing which workspace's data was on
    screen. Looked up from the workspaces table (Phase 0); falls back to
    the raw workspace_id if it isn't registered there for some reason.
    """
    from database.models import Workspace

    if db is None:
        return workspace_id or "Default"
    lookup_id = workspace_id or "default"
    row = db.query(Workspace).filter(Workspace.workspace_id == lookup_id).first()
    if row:
        return row.name
    return workspace_id or "Default"


def _ask_is_demo_workspace(db: Session, workspace_id: Optional[str]) -> bool:
    """
    True only for a workspace structurally flagged workspace_type="demo"
    (e.g. the historical demo dataset) -- not for a real customer
    workspace whose data simply happens to look synthetic. Used to exempt
    demo workspaces from the change_drivers/comparison traffic-scope-
    mismatch refusal (core/analytics_coverage.py's comparison_data_
    coverage) without loosening that check for anyone else. A demo
    workspace's activity is deliberately, honestly is_simulation=True end
    to end (see database/seed_historical_demo.py) specifically so it's
    never mistaken for real customer traffic elsewhere in the product --
    this is the one, narrow place that fact should stop it from being
    treated as untrustworthy instead.
    """
    from database.models import Workspace

    if db is None or not workspace_id:
        return False
    row = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).first()
    return bool(row and row.workspace_type == "demo")


def _ask_costpilot_answer(
    request: AskCostPilotRequest,
    db: Session,
    department_scope: Optional[str] = None,
    user_id: Optional[int] = None,
):
    """
    Answer executive questions using CostPilot-calculated facts.

    The natural-language layer selects a bounded reporting intent; totals,
    rankings, and evidence always come from the deterministic attribution
    report. This endpoint is read-only and cannot change routing or policy.

    department_scope (Phase 2 slice 1): forwarded to the agent loop only
    -- the deterministic path below does not honor it yet (see
    ask_costpilot's docstring for the full list of what's covered).

    user_id (Action Proposals slice 1): forwarded to the agent loop only,
    so a proposal created conversationally attributes proposed_by_user_id
    correctly instead of always logging None.
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

    # Checked before EITHER answer path -- the agent loop's query_metrics
    # tool takes a period_key from a fixed enum with nothing for an
    # arbitrary named month either, so it's just as able to guess wrong
    # and narrate the guess as fact. See _ask_named_month_unsupported's
    # docstring for the live incident this guards against.
    named_month = _ask_named_month_unsupported(question)
    if named_month:
        return _ask_unsupported_period_response(named_month)

    if _ask_agent_mode_enabled():
        agent_result = _ask_costpilot_agent(request, db, department_scope=department_scope, user_id=user_id)
        if agent_result is not None:
            return agent_result
        # Falls through to the deterministic path below unchanged.
        _ask_debug_log(request, "agent_fallback_to_deterministic", {})

    parsed, assistant_mode = _resolve_ask_intent(request)
    if parsed.get("intent") == "help":
        return _ask_help_response(request, parsed, assistant_mode)
    if parsed.get("intent") == "product":
        return _ask_product_response(request, parsed, assistant_mode)
    if parsed.get("intent") == "decision":
        return _ask_decision_response(request, parsed, db, assistant_mode)
    if parsed.get("intent") == "unsupported_period":
        return _ask_unsupported_period_response(parsed.get("named_month") or "that period")

    reporting_filters = _ask_reporting_filters(request, parsed)
    if department_scope:
        # Phase 2 slice 2: forced before the subject-filter and named-
        # department detection logic below, both of which only act when
        # charged_unit isn't already set -- so this one line also
        # correctly suppresses "the question named a different
        # department" from ever overriding a real department_scope.
        reporting_filters["charged_unit"] = department_scope
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
    if parsed.get("entity") != "department" and not reporting_filters.get("charged_unit"):
        # The question ranks something other than department (e.g. models,
        # platforms) but may still name one to scope by -- see
        # _ask_named_department for why entity alone can't carry this.
        named_department = _ask_named_department(question, request.workspace_id, db)
        if named_department:
            # "How does Sales compare to other departments?" names Sales
            # but asks for the full cross-department ranking with Sales as
            # the subject of interest -- filtering to charged_unit=Sales
            # here would leave no "other departments" left to compare
            # against, silently turning a comparison into a single-number
            # lookup (confirmed live: this exact phrasing returned "$11.99
            # for Sales" with a failed period comparison instead of where
            # Sales ranks against the rest). Only skip the filter for that
            # specific phrasing -- "what did Sales spend" or "what models
            # is Sales using" still correctly filter to Sales alone.
            asks_cross_department_comparison = bool(re.search(
                r"\b(compare|compared|vs\.?|versus)\b.{0,30}\b(other|another)\b.{0,20}\b(department|team)s?\b",
                question, re.IGNORECASE,
            ))
            if not asks_cross_department_comparison:
                reporting_filters["charged_unit"] = named_department
            if parsed.get("entity") == "overview":
                # No other ranking dimension was named (e.g. "what did
                # Sales spend" rather than "what models is Sales using") --
                # the department itself is the subject, so classify it as
                # such instead of leaving entity at the generic default.
                # _ask_intent can't know this on its own (it never touches
                # the database, so it can't check real department names);
                # this is the earliest point real department data exists.
                parsed["entity"] = "department"
    comparison_execution_plan = None
    comparison_coverage_result = None
    # Looked up once and reused for both the general date-range resolution
    # below and the comparison-intent branch further down (which used to
    # look this up separately) — a workspace's configured timezone should
    # never be read two different ways in the same request.
    stored_analytics_settings = workspace_analytics_settings(db, request.workspace_id)
    effective_timezone = (
        stored_analytics_settings.timezone_name
        if stored_analytics_settings else request.timezone_name
    )
    date_from, date_to = _ask_period_bounds(request, parsed, timezone_name=effective_timezone)
    if parsed.get("period_key") == "all_time":
        settings = workspace_analytics_settings(db, request.workspace_id)
        if settings and settings.collection_started_at:
            date_from = settings.collection_started_at
            date_to = settings.latest_complete_at or datetime.utcnow()
        else:
            profile = workspace_collection_profile(db, request.workspace_id)
            observed_start = profile.get("earliest_observed_at")
            observed_end = profile.get("latest_observed_at")
            date_from = datetime.fromisoformat(observed_start) if observed_start else None
            date_to = (
                datetime.fromisoformat(observed_end) + timedelta(microseconds=1)
                if observed_end else datetime.utcnow()
            )
    if parsed.get("intent") in {"comparison", "change_drivers"}:
        primary_period = resolve_primary_period(
            period_key=parsed.get("period_key"),
            days=parsed["days"],
            timezone_name=effective_timezone,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        comparison_execution_plan = comparison_plan(
            primary_period,
            parsed.get("comparison_key") or "previous_period",
        )
        date_from = comparison_execution_plan.primary.start
        date_to = comparison_execution_plan.primary.end
    report = project_activity_reporting(
        workspace_id=request.workspace_id,
        date_from=date_from,
        date_to=date_to,
        days=parsed["days"],
        **reporting_filters,
        activity_limit=2000,
        exclude_prune_only_rows=True,
        db=db,
    )
    summary = report.get("summary") or {}
    period = report.get("period") or {}
    context_plural = report.get("context_label_plural") or "Business Contexts"
    metric = parsed["metric"]
    metric_contract = metric_definition(metric)
    intent = parsed["intent"]
    entity = parsed["entity"]
    direction = parsed["direction"]
    result_limit = parsed["result_limit"]
    outcome_filter = parsed.get("outcome_filter")
    # Display word for outcome_filter -- "won"/"lost" for genuinely
    # Sales-Opportunity-phrased questions, "successful"/"unsuccessful" for
    # generic (claims/tickets/cases) phrasing. Falls back to "won"/"lost"
    # for any caller that set outcome_filter directly without a label
    # (e.g. a canonical-intent contract match), preserving old behavior.
    outcome_filter_label = parsed.get("outcome_filter_label") or outcome_filter
    general_outcome_question = bool(parsed.get("general_outcome_question"))
    attention_question = bool(parsed.get("attention_question"))
    decision_history_question = bool(parsed.get("decision_history_question"))
    data_coverage_question = bool(parsed.get("data_coverage_question"))
    growth_ranking_question = bool(parsed.get("growth_ranking_question"))
    per_item_cost_question = bool(parsed.get("per_item_cost_question"))
    # "Who had the highest AI spend this month and compare to last month?"
    # -- the intent classifier (both the regex fallback and the optional
    # OpenAI refinement layer) correctly sets comparison_key on a ranking
    # question, not just on intent=="comparison"/"change_drivers", but
    # nothing in the ranking answer branch below ever read it -- the
    # winning entity's prior-period figure was never fetched, so the
    # narration had no comparison data and said so, even though the
    # workspace has a full year of history and the earlier period's real
    # number was one query away. Confirmed live 2026-09-12.
    ranking_comparison_key = (
        parsed.get("comparison_key") if intent == "ranking" and not growth_ranking_question else None
    )
    evidence = []
    recommendations = []
    title = "AI usage overview"
    calculation_formula = None
    calculation_row_count = None
    driver_analysis = None
    budget_coverage = None
    context_hint = _ask_recent_context_hint(request)
    named_entity = _ask_named_entity(question, report, context_hint)
    # request.pinned_filter_name means the user already resolved this exact
    # ambiguity by clicking a specific disambiguation choice -- re-running
    # the same name-token match on the reconstructed follow-up question
    # would just tie again (e.g. "Support" the department and "Support
    # Agent" the agent both reduce to the same name tokens once generic
    # suffix words are stripped), looping back into the same prompt
    # instead of answering. The pinned reporting_filters value from
    # _ask_reporting_filters is what actually scopes this answer now.
    if named_entity is None and not request.pinned_filter_name and intent in {"total", "overview", "ranking"}:
        # Without this, an ambiguous name ("How much did Chris spend?" with
        # two Chrises) silently fell through to an unfiltered, company-wide
        # total presented as if it had answered the question about one
        # specific person -- exactly the confident-wrong-answer failure
        # mode this whole feature exists to avoid. Only intercedes when a
        # real tie exists; a name that matches nothing at all still falls
        # through to the normal (correctly labeled) company-wide answer.
        ambiguous_matches = _ask_named_entity_ambiguity(question, report, context_hint)
        if ambiguous_matches:
            labels = sorted({
                str(match["row"].get("label") or "Unknown") for match in ambiguous_matches
            })
            # Two (or more) tied candidates can share the IDENTICAL label
            # -- e.g. two separate "person" rows both named "David Kim"
            # with different emails, merged() above keys on (entity,
            # label, email) so they never combined into one row -- and
            # `labels` above dedupes by string, silently collapsing them
            # to one entry. That produced a nonsensical "I found 1 matches
            # for that name: David Kim. Which one did you mean?" (confirmed
            # live 2026-09-11): a dead end, since the user has no way to
            # tell the offered "one" apart from itself. When every tied
            # candidate is the same entity type with the same label, there
            # is nothing meaningful to disambiguate -- sum them into one
            # merged row and answer directly instead, same "merge, don't
            # fabricate" precedent department_outcome_breakdown() already
            # uses for genuine duplicate-record situations.
            if len(labels) == 1 and len({m["entity"] for m in ambiguous_matches}) == 1:
                merged_row = dict(ambiguous_matches[0]["row"])
                for other in ambiguous_matches[1:]:
                    for field in ("request_count", "input_tokens", "output_tokens", "tokens_saved",
                                  "spend_usd", "simulation_count", "total_tokens", "live_count"):
                        if field in other["row"]:
                            merged_row[field] = (merged_row.get(field) or 0) + (other["row"].get(field) or 0)
                named_entity = {**ambiguous_matches[0], "row": merged_row}
            else:
                # Picking a label re-asks a brand new question with none of
                # this turn's context -- re-asking the bare label alone lost
                # that context and got misclassified (confirmed live: "Support"
                # alone resolves to intent="help", not a department lookup,
                # since a lone noun out of context looks like a product
                # question to the OpenAI planner). Naming the entity type
                # (already known here) makes the re-ask classify correctly.
                #
                # The re-ask text alone can't disambiguate two candidates that
                # share a root word once generic suffix words are stripped for
                # matching (confirmed live: "Support" the department and
                # "Support Agent" the agent both reduce to the same name-token
                # set, so any rephrasing ties again). filter_name/filter_value
                # carry the exact already-resolved row directly -- the same
                # convention _ask_evidence()'s drill-through buttons already
                # use -- so the frontend can pin it via pinned_filter_name
                # instead of asking the matcher to guess a second time.
                match_by_name = {
                    str(match["row"].get("label") or "Unknown"): match
                    for match in ambiguous_matches
                }
                return {
                    "title": "Which one did you mean?",
                    "answer": (
                        f"I found {len(labels)} matches for that name: {', '.join(labels)}. "
                        "Which one did you mean?"
                    ),
                    "intent": "clarification_required",
                    "confidence": "CLARIFICATION_REQUIRED",
                    "evidence": [
                        {
                            "label": label, "value": None, "metric_label": None,
                            "question": f"Tell me about the {label} {match_by_name[label]['entity_label']}".strip(),
                            "filter_name": match_by_name[label]["filter_name"],
                            "filter_value": match_by_name[label]["row"].get("id"),
                        }
                        for label in labels
                    ],
                    "recommendations": [],
                    "read_only": True,
                }

    entity_config = {
        **_ASK_ENTITY_CONFIG_STATIC,
        # "context" is the one entry with a per-request dynamic label
        # (the customer's own term for a project/matter/case), so it can't
        # live in the static module-level registry with everything else.
        "context": ("project_breakdown", "project_id", context_plural),
    }

    period_from_raw = period.get("date_from")
    period_to_raw = period.get("date_to")
    if period_from_raw and period_to_raw:
        from core.analytics_periods import format_date_range as _ask_format_date_range
        from core.analytics_periods import _from_utc_naive, _workspace_zone
        try:
            # date_from/date_to are UTC-naive (that's what the DB query
            # needs) — converting straight to .date() here would show the
            # UTC calendar day, not the workspace's local one. Near a UTC
            # day boundary (late afternoon/evening in US timezones) those
            # two days can differ, which is the exact bug being fixed:
            # convert back to local time first so the displayed range
            # matches the workspace's own calendar, not the server's.
            zone = _workspace_zone(effective_timezone)
            local_from = _from_utc_naive(datetime.fromisoformat(period_from_raw), zone)
            local_to = _from_utc_naive(datetime.fromisoformat(period_to_raw), zone)
            period_label = _ask_format_date_range(local_from, local_to)
        except ValueError:
            period_label = f"{str(period_from_raw)[:10]} through {str(period_to_raw)[:10]}"
    else:
        period_label = f"the last {parsed['days']} days"
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

    latest_available_at = None
    if int(summary.get("request_count") or 0) == 0 and request.workspace_id:
        latest_available_at = db.query(func.max(TokenTransaction.timestamp)).filter(
            TokenTransaction.workspace_id == request.workspace_id,
        ).scalar()

    if intent == "change_drivers":
        prior_period = comparison_execution_plan.comparison
        prior_report = project_activity_reporting(
            workspace_id=request.workspace_id,
            date_from=prior_period.start,
            date_to=prior_period.end,
            days=parsed["days"],
            **reporting_filters,
            activity_limit=1,
            exclude_prune_only_rows=True,
            db=db,
        )
        prior_summary = prior_report.get("summary") or {}
        if db is not None and request.workspace_id:
            comparison_coverage_result = comparison_data_coverage(
                comparison_execution_plan,
                summary,
                prior_summary,
                workspace_collection_profile(db, request.workspace_id),
                is_demo_workspace=_ask_is_demo_workspace(db, request.workspace_id),
            )
        else:
            comparison_coverage_result = comparison_coverage(summary, prior_summary)
        plan_contract = comparison_execution_plan.contract()
        primary_label = plan_contract["primary"]["label"]
        comparison_label = plan_contract["comparison"]["label"]
        primary_decomposition = change_decomposition(summary, prior_summary, metric)
        token_decomposition = change_decomposition(summary, prior_summary, "total_tokens")
        spend_decomposition = change_decomposition(summary, prior_summary, "spend_usd")
        change = primary_decomposition["absolute_change"]
        pct = primary_decomposition["percent_change"]
        current_display, metric_label = _ask_metric_value(
            metric, primary_decomposition["current_value"]
        )
        prior_display, _ = _ask_metric_value(
            metric, primary_decomposition["comparison_value"]
        )
        title = f"{metric_label.title()} change drivers"
        evidence = [
            {
                "label": primary_label,
                "value": current_display,
                "metric_label": metric_label,
                "detail": f"{int(summary.get('request_count') or 0):,} governed requests",
                "filter_name": None,
                "filter_value": None,
            },
            {
                "label": comparison_label,
                "value": prior_display,
                "metric_label": metric_label,
                "detail": f"{int(prior_summary.get('request_count') or 0):,} governed requests",
                "filter_name": None,
                "filter_value": None,
            },
        ]
        if comparison_coverage_result["comparable"]:
            dimension_specs = (
                ("agent", "agent_breakdown", "agent_id"),
                ("department", "organizational_unit_breakdown", "charged_unit"),
                ("business context", "project_breakdown", "project_id"),
                ("platform", "source_platform_breakdown", "source_platform"),
                ("model", "model_breakdown", None),
            )
            contributors = []
            for dimension, report_key, filter_name in dimension_specs:
                rows = dimension_contributors(
                    report.get(report_key) or [],
                    prior_report.get(report_key) or [],
                    metric,
                    dimension,
                    change,
                    limit=5,
                )
                for row in rows:
                    row["filter_name"] = filter_name
                contributors.extend(rows)
            contributors.sort(
                key=lambda row: (-abs(row["absolute_change"]), row["dimension"], row["label"])
            )
            contributors = contributors[:5]
            for row in contributors:
                delta_display, _ = _ask_metric_value(metric, row["absolute_change"])
                evidence.append({
                    "label": row["label"],
                    "value": delta_display,
                    "metric_label": f"{metric_label} change",
                    "detail": f"Measured {row['dimension']} contribution across the two periods",
                    "filter_name": row["filter_name"],
                    "filter_value": row["id"],
                    "dimension": row["dimension"],
                    "net_change_contribution_pct": row["net_change_contribution_pct"],
                })
            volume_display, _ = _ask_metric_value(
                metric, primary_decomposition["request_volume_effect"]
            )
            intensity_display, _ = _ask_metric_value(
                metric, primary_decomposition["per_request_effect"]
            )
            direction_word = "increased" if change > 0 else "decreased" if change < 0 else "did not change"
            answer = (
                f"{metric_label.title()} {direction_word} from {prior_display} for {comparison_label} "
                f"to {current_display} for {primary_label}"
                + (f" ({abs(pct):.1f}%). " if pct is not None else ". ")
                + f"The measured request-volume effect was {volume_display}, and the per-request effect was {intensity_display}."
            )
            if contributors:
                contributor_text = ", ".join(
                    f"{row['label']} ({row['dimension']})" for row in contributors[:3]
                )
                answer += f" The largest recorded contributors were {contributor_text}."
            if metric == "total_tokens" and "spend" in question.lower():
                spend_current, _ = _ask_metric_value("spend_usd", spend_decomposition["current_value"])
                spend_prior, _ = _ask_metric_value("spend_usd", spend_decomposition["comparison_value"])
                answer += f" Recorded AI spend moved from {spend_prior} to {spend_current}."
            answer += " These are measured contributors, not inferred business causes."
            driver_analysis = {
                "primary_metric": primary_decomposition,
                "tokens": token_decomposition,
                "spend": spend_decomposition,
                "contributors": contributors,
                "causality_note": "Contribution does not establish the underlying business cause.",
            }
        else:
            answer = (
                f"CostPilot cannot determine why {metric_label} changed between {comparison_label} "
                f"and {primary_label}. {comparison_coverage_result['limitation']}"
            )
            change = None
            pct = None
        calculation_formula = (
            "Two-factor Shapley decomposition of request volume and metric per request, "
            "plus entity deltas calculated with identical filters"
        )
        calculation_row_count = (
            int(summary.get("request_count") or 0)
            + int(prior_summary.get("request_count") or 0)
        )
    elif intent == "comparison":
        current_value = _ask_row_metric(summary, metric)
        if comparison_execution_plan:
            prior_period = comparison_execution_plan.comparison
            prior_report = project_activity_reporting(
                workspace_id=request.workspace_id,
                date_from=prior_period.start,
                date_to=prior_period.end,
                days=parsed["days"],
                **reporting_filters,
                activity_limit=1,
                exclude_prune_only_rows=True,
                db=db,
            )
            prior_summary = prior_report.get("summary") or {}
            if db is not None and request.workspace_id:
                comparison_coverage_result = comparison_data_coverage(
                    comparison_execution_plan,
                    summary,
                    prior_summary,
                    workspace_collection_profile(db, request.workspace_id),
                    is_demo_workspace=_ask_is_demo_workspace(db, request.workspace_id),
                )
            else:
                comparison_coverage_result = comparison_coverage(summary, prior_summary)
            prior_value = _ask_row_metric(prior_summary, metric)
            change = current_value - prior_value
            pct = (change / prior_value * 100) if prior_value else None
            current_display, metric_label = _ask_metric_value(metric, current_value)
            prior_display, _ = _ask_metric_value(metric, prior_value)
            change_display, _ = _ask_metric_value(metric, abs(change))
            direction_word = "increased" if change > 0 else "decreased" if change < 0 else "did not change"
            plan_contract = comparison_execution_plan.contract()
            primary_label = plan_contract["primary"]["label"]
            comparison_label = plan_contract["comparison"]["label"]
            comparison_descriptions = {
                "same_period_previous_year": "the same calendar period last year",
                "previous_month": "the calendar-aligned period one month earlier",
                "previous_quarter": "the calendar-aligned period one quarter earlier",
                "previous_period": "the immediately preceding equal-length period",
            }
            comparison_description = comparison_descriptions[comparison_execution_plan.mode]
            title = f"{metric_label.title()} comparison"
            if comparison_coverage_result["comparable"]:
                answer = (
                    f"{metric_label.title()} was {current_display} for {primary_label}, "
                    f"compared with {prior_display} for {comparison_label}, {comparison_description}. "
                    f"It {direction_word} by {change_display}"
                    + (f" ({abs(pct):.1f}%)." if pct is not None else ".")
                )
            else:
                answer = (
                    f"{metric_label.title()} was {current_display} for {primary_label}. "
                    f"A valid comparison with {comparison_label} is not available. "
                    f"{comparison_coverage_result['limitation']}"
                )
                change = None
                pct = None
            evidence = [
                {"label": primary_label, "value": current_display, "metric_label": metric_label,
                 "detail": f"{int(summary.get('request_count') or 0):,} governed requests",
                 "filter_name": None, "filter_value": None},
                {"label": comparison_label, "value": prior_display, "metric_label": metric_label,
                 "detail": f"{int(prior_summary.get('request_count') or 0):,} governed requests",
                 "filter_name": None, "filter_value": None},
            ]
            calculation_formula = "Primary period metric minus comparison period metric"
            calculation_row_count = (
                int(summary.get("request_count") or 0)
                + int(prior_summary.get("request_count") or 0)
            )
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
                exclude_prune_only_rows=True,
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
                    + (f" · {row.get('audit_rationale')}" if row.get("audit_rationale") else "")
                ),
                "filter_name": "audit_event_id" if row.get("audit_event_id") else "transaction_id",
                "filter_value": row.get("audit_event_id") or row.get("transaction_id"),
                "governed_request_id": row.get("governed_request_id"),
                "decision_outcome": row.get("decision_outcome"),
                "risk_level": row.get("risk_level"),
                "live_count": 0 if row.get("is_simulation") else 1,
                "simulation_count": 1 if row.get("is_simulation") else 0,
            } for row in activity_rows[:result_limit]]
        answer = (
            f"Showing {len(evidence):,} matching result{'s' if len(evidence) != 1 else ''} "
            f"for {period_label}."
            if evidence else f"No matching AI activity was recorded for {period_label}."
        )
    elif intent in {"inactive", "agent_adoption"}:
        current_rows = report.get("agent_breakdown") or []
        current_by_id = {
            str(row.get("id")): row
            for row in current_rows
            if row.get("id") is not None
        }
        agent_query = db.query(RegisteredAgent)
        if request.workspace_id and department_scope:
            # Phase 2 slice 5: exact match on the one department, mirroring
            # the same fix already made to the agent-loop's get_agent_
            # adoption tool -- the in-period counts above (current_rows,
            # via report/reporting_filters) are already scoped by Slice 2;
            # this "universe of registered agents" query was not.
            agent_query = agent_query.filter(
                RegisteredAgent.department == f"{request.workspace_id}:{department_scope}"
            )
        elif request.workspace_id:
            agent_query = agent_query.filter(
                RegisteredAgent.department.like(f"{request.workspace_id}:%")
            )
        agent_query = agent_query.filter(
            RegisteredAgent.archived.isnot(True)
        )
        agents = agent_query.all()
        # Lifetime usage is read from TokenTransaction -- the same ledger
        # current_rows/current_by_id above already use for the in-period
        # counts -- not AuditEvent. Reproduced live: 8 agents in the
        # historical demo workspace each showed hundreds of in-period
        # requests (from current_by_id/TokenTransaction) right next to
        # "Never used / 0 lifetime requests" (from AuditEvent), because
        # that workspace's bulk-seeded historical TokenTransaction rows
        # were never matched 1:1 by AuditEvent rows for the same agent
        # ids -- AuditEvent is a decision/audit log, not a complete
        # activity ledger, and was never a safe proxy for "has this agent
        # ever been used." Scoped to just this workspace's resolved agent
        # ids rather than a separate workspace_id filter, since agent_id
        # already implies the workspace via the RegisteredAgent query above.
        agent_ids = [agent.id for agent in agents]
        lifetime_rows = {}
        if agent_ids:
            lifetime_query = db.query(
                TokenTransaction.agent_id,
                func.count(TokenTransaction.id),
                func.max(TokenTransaction.timestamp),
            ).filter(TokenTransaction.agent_id.in_(agent_ids))
            lifetime_rows = {
                str(agent_id): {
                    "request_count": int(request_count or 0),
                    "last_used_at": last_used_at,
                }
                for agent_id, request_count, last_used_at in lifetime_query.group_by(
                    TokenTransaction.agent_id
                ).all()
            }
        threshold = max(1, int(parsed.get("usage_threshold") or 10))
        requested_status = parsed.get("usage_status") or (
            "unused" if intent == "inactive" else "all"
        )
        adoption_rows = []
        status_counts = {
            "never": 0,
            "recently_inactive": 0,
            "low": 0,
            "active": 0,
        }
        for agent in agents:
            agent_key = str(agent.id)
            current = current_by_id.get(agent_key) or {}
            lifetime = lifetime_rows.get(agent_key) or {}
            current_count = int(current.get("request_count") or 0)
            lifetime_count = int(lifetime.get("request_count") or 0)
            last_used_at = lifetime.get("last_used_at") or agent.last_used_at
            if lifetime_count == 0:
                status = "never"
            elif current_count == 0:
                status = "recently_inactive"
            elif current_count < threshold:
                status = "low"
            else:
                status = "active"
            status_counts[status] += 1
            if requested_status == "unused" and status not in {
                "never", "recently_inactive"
            }:
                continue
            if requested_status not in {"all", "unused", status}:
                continue
            department = str(agent.department or "Unassigned").split(":")[-1]
            live_count_for_agent = int(current.get("live_count") or 0)
            simulation_count_for_agent = int(current.get("simulation_count") or 0)
            adoption_rows.append({
                "agent": agent,
                "status": status,
                "current_count": current_count,
                "lifetime_count": lifetime_count,
                "last_used_at": last_used_at,
                "department": department,
                "live_count": live_count_for_agent,
                "simulation_count": simulation_count_for_agent,
            })

        status_order = {
            "never": 0,
            "recently_inactive": 1,
            "low": 2,
            "active": 3,
        }
        adoption_rows.sort(key=lambda row: (
            status_order[row["status"]],
            row["current_count"] if row["status"] == "low" else -row["current_count"],
            row["agent"].name or "",
        ))
        status_labels = {
            "never": "Never used",
            "recently_inactive": "Recently inactive",
            "low": f"Low usage (1–{threshold - 1} requests)",
            "active": f"Active ({threshold}+ requests)",
        }
        evidence = []
        for row in adoption_rows[:result_limit]:
            last_used = row["last_used_at"]
            detail_parts = [
                status_labels[row["status"]],
                row["agent"].source_platform or "Unknown platform",
                row["department"],
                f"{row['lifetime_count']:,} lifetime requests",
            ]
            if last_used:
                detail_parts.append(f"last used {last_used.strftime('%b %d, %Y')}")
            evidence.append({
                "label": row["agent"].name or "Unnamed agent",
                "value": f"{row['current_count']:,}",
                "metric_label": "requests in period",
                "detail": " · ".join(detail_parts),
                "filter_name": "agent_id",
                "filter_value": row["agent"].id,
                "live_count": row["live_count"],
                "simulation_count": row["simulation_count"],
            })
        if requested_status == "all":
            title = "Agent adoption overview"
            answer = (
                f"CostPilot found {len(agents):,} registered agents: "
                f"{status_counts['active']:,} active, {status_counts['low']:,} low usage, "
                f"{status_counts['recently_inactive']:,} recently inactive, and "
                f"{status_counts['never']:,} never used. Low usage means fewer than "
                f"{threshold:,} governed requests in {period_label}."
            )
        else:
            title_by_status = {
                "unused": "Unused agents",
                "never": "Agents never used",
                "recently_inactive": "Recently inactive agents",
                "low": "Low-usage agents",
                "active": "Active agents",
            }
            definition_by_status = {
                "unused": "no requests in the selected period",
                "never": "no governed requests at any time",
                "recently_inactive": "historical usage but no requests in the selected period",
                "low": f"between 1 and {max(threshold - 1, 1):,} requests in the selected period",
                "active": f"at least {threshold:,} requests in the selected period",
            }
            title = title_by_status[requested_status]
            answer = (
                f"{len(adoption_rows):,} registered agent"
                f"{'s match' if len(adoption_rows) != 1 else ' matches'} this definition: "
                f"{definition_by_status[requested_status]}."
            )
        calculation_formula = (
            "Classify every non-archived registered agent using lifetime activity and "
            f"governed requests in the selected period; low-usage threshold is {threshold:,}"
        )
        calculation_row_count = len(agents)
        intent = "agent_adoption"
    elif intent == "tier_usage":
        title = f"{(parsed.get('model_tier') or 'Selected tier').title()} routing usage"
        value, metric_label = _ask_metric_value(metric, _ask_row_metric(summary, metric))
        answer = (
            f"CostPilot routed {int(summary.get('request_count') or 0):,} requests using "
            f"{int(summary.get('total_tokens') or 0):,} tokens and ${float(summary.get('spend_usd') or 0):,.4f} "
            f"in spend to the {(parsed.get('model_tier') or 'selected').title()} tier for {period_label}."
        )
        evidence = _ask_evidence(report.get("model_breakdown") or [], metric, None, limit=result_limit)
    elif named_entity and outcome_filter and named_entity["entity"] == "account":
        # "How many opportunities has Brightwater Marine won?" used to fall
        # into the named-entity branch below, which has no concept of won/
        # lost at all and silently answered with a generic AI-spend total
        # instead -- outcome_filter was computed correctly but discarded
        # because this branch order let the named-entity match win first.
        # Reuses the same trusted, already-correct account-outcomes lookup
        # the agent tool loop uses (run_get_account_outcomes) rather than
        # hand-rolling a second won/lost computation in this regex-driven
        # path -- this fallback path is being kept minimal, not extended,
        # per the Ask CostPilot architecture review.
        from api.ask_costpilot_tools import run_get_account_outcomes

        account_label = named_entity["row"].get("label") or "The selected account"
        outcomes = run_get_account_outcomes(db, request.workspace_id, account_label)
        entity = "account"
        # Not "account_outcomes" -- ask_interpretation_label()'s template is
        # f"{entity.title()} {intent} using {metric}", and entity is already
        # "Account", so that would render the redundant "Account account
        # outcomes using spend usd".
        intent = "outcomes"
        # A question naming both outcomes ("won/lost", "won and lost") only
        # ever set outcome_filter to one value (_ask_intent's won/lost
        # detection is an elif chain, first match wins) -- answering just
        # that one half silently ignored the other half of what was asked.
        question_lower = (request.question or "").lower()
        asks_both = bool(re.search(r"\bwon\b", question_lower)) and bool(re.search(r"\blost\b", question_lower))
        if not outcomes.get("found"):
            title = f"{account_label} outcomes"
            answer = f"CostPilot could not find a single matching account for '{account_label}'."
        elif asks_both:
            won_count = int(outcomes.get("opportunities_won") or 0)
            lost_count = int(outcomes.get("opportunities_lost") or 0)
            open_count = int(outcomes.get("opportunities_open") or 0)
            title = f"{account_label} won/lost opportunities"
            answer = (
                f"{account_label} has {won_count:,} won and {lost_count:,} lost opportunit"
                f"{'y' if (won_count + lost_count) == 1 else 'ies'}"
                + (
                    f", out of {won_count + lost_count + open_count:,} total tracked "
                    f"({open_count:,} still open)."
                    if outcomes.get("has_outcome_data") else "."
                )
            )
            if not outcomes.get("has_outcome_data"):
                answer += " No business outcome data is currently synced for this account."
        else:
            count = outcomes.get("opportunities_won") if outcome_filter == "won" else outcomes.get("opportunities_lost")
            outcome_word = "won" if outcome_filter == "won" else "lost"
            title = f"{account_label} {outcome_word} opportunities"
            answer = (
                f"{account_label} has {int(count or 0):,} {outcome_word} opportunit"
                f"{'y' if count == 1 else 'ies'}"
                + (
                    f", out of {int(outcomes.get('opportunities_won') or 0) + int(outcomes.get('opportunities_lost') or 0) + int(outcomes.get('opportunities_open') or 0):,} total tracked."
                    if outcomes.get("has_outcome_data") else "."
                )
            )
            if not outcomes.get("has_outcome_data"):
                answer += " No business outcome data is currently synced for this account."
        evidence = []
        calculation_row_count = None
        calculation_formula = (
            "Count of WorkItemOutcome rows for this account's Opportunities, "
            "grouped by outcome_success/is_closed"
        )
    elif general_outcome_question and not (named_entity and named_entity["entity"] == "account"):
        # "Is AI helping us close deals?" with no named account -- reuses
        # the exact same trusted account-outcomes lookup as the branch
        # just above and the agent loop's get_account_outcomes tool,
        # company-wide instead of scoped to one account. Without this,
        # the question fell through to a generic activity/spend summary
        # that never touched won/lost/pipeline data at all (confirmed
        # live) whenever the agent loop -- the only other path that knows
        # about outcomes -- had a transient failure and this deterministic
        # path answered instead.
        from api.ask_costpilot_tools import run_get_account_outcomes

        outcomes = run_get_account_outcomes(
            db, request.workspace_id, entity_name=None, department_scope=department_scope,
        )
        title = "Is AI helping close deals?"
        if not outcomes.get("has_outcome_data"):
            answer = (
                "No business outcome data is currently synced for this workspace, so "
                "there is no evidence either way yet -- this reflects missing data, "
                "not that AI isn't helping."
            )
        else:
            won_count = int(outcomes.get("opportunities_won") or 0)
            lost_count = int(outcomes.get("opportunities_lost") or 0)
            open_count = int(outcomes.get("opportunities_open") or 0)
            pipeline_value = float(outcomes.get("pipeline_value_usd") or 0)
            closed_won_value = float(outcomes.get("closed_won_value_usd") or 0)
            answer = (
                f"Across this workspace, {won_count:,} opportunit{'y' if won_count == 1 else 'ies'} "
                f"{'is' if won_count == 1 else 'are'} tracked as won and {lost_count:,} as lost "
                f"(${closed_won_value:,.2f} in closed-won value), with {open_count:,} still open "
                f"(${pipeline_value:,.2f} in pipeline). AI activity is tracked alongside this work, "
                "but this is association, not proof that AI caused these outcomes."
            )
        evidence = []
        calculation_row_count = None
        calculation_formula = (
            "Count of WorkItemOutcome rows across all tracked Opportunities, "
            "grouped by outcome_success/is_closed"
        )
    elif attention_question:
        # "What should I be paying attention to?" -- reuses the exact same
        # trusted, pre-ranked signal computation the agent loop's
        # get_priority_signals tool already uses (budget risk first, then
        # biggest spend swings), instead of falling through to a generic
        # company overview (or, before the help/product override fix, the
        # unhelpful capability menu) whenever the agent loop has a
        # transient failure and this deterministic path answers instead.
        from api.ask_costpilot_tools import run_get_priority_signals

        priority = run_get_priority_signals(
            db, request.workspace_id, days=7, department_scope=department_scope,
        )
        signals = priority.get("signals") or []
        title = "What needs your attention"
        if not signals:
            answer = "Nothing needs attention right now -- no department is over or near budget, and no spend has swung unusually in the last 7 days."
        else:
            lines = [
                f"{i + 1}. {s.get('detail') or s.get('label')}"
                for i, s in enumerate(signals)
            ]
            answer = "Here's what deserves attention right now:\n" + "\n".join(lines)
        evidence = [
            {
                "label": s.get("label") or "Signal",
                "value": (s.get("severity") or "").title(),
                "metric_label": s.get("type"),
                "detail": s.get("detail"),
                "filter_name": None,
                "filter_value": None,
            }
            for s in signals
        ]
        calculation_row_count = len(signals)
        calculation_formula = (
            "Departments at or over 80% of their monthly AI budget cap, plus "
            "departments whose spend changed by 15% or more vs. the prior 7 days"
        )
    elif decision_history_question:
        # "Why are we using Sonnet for the Sales agent?" -- reuses the
        # exact same trusted rationale lookup the agent loop's
        # get_decision_history tool already uses, instead of falling
        # through to a plain spend ranking that never touches
        # AuditEvent.rationale at all.
        from api.ask_costpilot_tools import run_get_decision_history

        agent_name = None
        model_name = None
        if named_entity and named_entity["entity"] == "agent":
            agent_name = named_entity["row"].get("label")
        elif named_entity and named_entity["entity"] == "model":
            model_name = named_entity["row"].get("label")
        history = run_get_decision_history(
            db, request.workspace_id, agent_name=agent_name, model_name=model_name,
            department_scope=department_scope,
        )
        decisions = history.get("decisions") or []
        subject = agent_name or model_name
        title = f"Why {subject}" if subject else "Recent governance decisions"
        if not decisions:
            answer = (
                f"No recorded decisions matched {subject}." if subject
                else "No recorded governance decisions were found."
            )
        else:
            top = decisions[0]
            answer = top.get("rationale") or "No rationale was recorded for this decision."
            if len(decisions) > 1:
                answer += f" ({len(decisions)} related decisions found; showing the most recent.)"
        evidence = [
            {
                "label": d.get("event_type") or "Decision",
                "value": d.get("timestamp"),
                "metric_label": d.get("department"),
                "detail": d.get("rationale"),
                "filter_name": None,
                "filter_value": None,
            }
            for d in decisions[:5]
        ]
        calculation_row_count = len(decisions)
        calculation_formula = "AuditEvent rows matching the named agent/model, ordered by timestamp desc"
    elif data_coverage_question:
        # "Show AI activity across Salesforce, HubSpot, and ServiceNow" --
        # states connection status per named platform before answering,
        # instead of silently presenting whichever platforms happen to be
        # connected as if they covered everything asked about.
        from api.ask_costpilot_tools import run_get_data_coverage

        coverage = run_get_data_coverage(db, request.workspace_id)
        connected = {p.lower() for p in (coverage.get("connected_platforms") or [])}
        not_connected = {p.lower() for p in (coverage.get("not_connected_platforms") or [])}
        question_lower = (request.question or "").lower()
        named = [p for p in _ASK_NAMED_PLATFORMS if p in question_lower]
        named_connected = [p for p in named if p in connected]
        named_not_connected = [p for p in named if p in not_connected or p not in connected]
        title = "Data coverage"
        if named_not_connected:
            answer = (
                (f"I can answer this for {', '.join(p.title() for p in named_connected)}. " if named_connected else "")
                + f"{', '.join(p.title() for p in named_not_connected)} "
                + ("are" if len(named_not_connected) > 1 else "is")
                + " not currently connected, so activity for "
                + ("them" if len(named_not_connected) > 1 else "it")
                + " isn't included."
            )
        else:
            answer = f"{', '.join(p.title() for p in named)} {'are' if len(named) > 1 else 'is'} connected."
        evidence = []
        calculation_row_count = None
        calculation_formula = "Connected-platform status from the workspace's registered integrations"
    elif named_entity and intent not in {
        "budget", "savings", "optimization", "pruning", "blocked", "risk_events", "ranking",
        # "When did Salesforce last sync?" named "Salesforce," which
        # matched as a platform entity here and overrode intent="lookup"
        # -- answering with Salesforce's AI SPEND figures instead of its
        # connection sync status. connection_health has its own named-
        # platform handling (see that branch) and must not be preempted.
        "connection_health", "inactive_context",
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
    elif intent == "total" and entity == "context" and outcome_filter:
        # "How much AI spend was associated with won opportunities" needs a
        # total scoped to won/lost project_breakdown rows, not the
        # company-wide summary the generic "total" branch below uses --
        # entity is otherwise ignored by that branch.
        outcome_rows = [
            row for row in (report.get("project_breakdown") or [])
            if row.get("outcome_success") == (outcome_filter == "won")
        ]
        value, metric_label = _ask_metric_value(
            metric, sum(_ask_row_metric(row, metric) for row in outcome_rows)
        )
        request_total = sum(int(row.get("request_count") or 0) for row in outcome_rows)
        outcome_label = outcome_filter_label or ("won" if outcome_filter == "won" else "lost")
        context_noun = context_plural.lower()
        title = f"Total {metric_label} on {outcome_label} {context_noun}"
        answer = (
            f"CostPilot recorded {value} in tracked {metric_label} across "
            f"{len(outcome_rows)} {outcome_label} {context_noun} for {period_label} "
            f"({request_total:,} governed requests). This is AI activity tracked "
            f"alongside those outcomes, not evidence that the AI activity caused them."
        )
        evidence = _ask_evidence(outcome_rows, metric, "project_id", limit=result_limit)
    elif intent == "total" and entity == "context" and per_item_cost_question:
        # "What's the cost per work item this quarter?" was answered with
        # the plain company-wide total (identical to the generic "total"
        # branch below), mislabeled as if it were already a per-item
        # figure -- it was never actually divided by the number of items.
        # "cost"/"spend" per item always means total dollars / item count --
        # never divide an already-per-request metric like
        # avg_cost_per_request by the item count again (confirmed live: the
        # OpenAI intent-refinement layer relabeled this question's metric
        # to avg_cost_per_request, which this per_item_cost_question guard
        # doesn't itself gate on, producing a meaningless cost-per-request-
        # per-project micro-figure instead of a real per-item dollar cost).
        metric = "spend_usd" if metric == "avg_cost_per_request" else metric
        project_count = int(summary.get("project_count") or 0)
        total_value = _ask_row_metric(summary, metric)
        per_item_value = (total_value / project_count) if project_count else 0.0
        value, metric_label = _ask_metric_value(metric, per_item_value)
        total_formatted, _ = _ask_metric_value(metric, total_value)
        context_noun = context_plural.lower()
        title = f"{metric_label} per {context_noun}"
        if project_count:
            answer = (
                f"{value} {metric_label} per {context_noun} for {period_label} "
                f"({total_formatted} total {metric_label} across {project_count:,} {context_noun})."
            )
        else:
            answer = f"No {context_noun} had recorded AI activity for {period_label}."
        evidence = [{
            "label": f"{metric_label} per {context_noun}",
            "value": value,
            "metric_label": metric_label,
            "detail": f"{total_formatted} total across {project_count:,} {context_noun}",
            "filter_name": None,
            "filter_value": None,
        }]
    elif (
        intent == "total" and entity in entity_config
        and not named_entity
        and not reporting_filters.get(entity_config[entity][1] or "")
    ):
        # "How many platforms are we governing AI activity across?" /
        # "How many active projects are we tracking?" / "How many work
        # items have AI-touched activity?" all landed on the generic
        # "total" branch below, which ignores `entity` entirely and
        # always returns the plain company spend/request total --
        # confirmed live: all three questions returned the identical
        # 191-governed-requests figure regardless of what was asked
        # about. Only fires for an UNSCOPED "how many X" question (no
        # named instance already filtered into the report, e.g. "how
        # much did Sales spend" keeps using the branch below, which
        # already answers that correctly from the pre-filtered summary).
        breakdown_key, filter_name, entity_label = entity_config[entity]
        if entity == "person":
            count = int(summary.get("people_count") or 0)
        elif entity == "agent":
            count = int(summary.get("agent_count") or 0)
        elif entity == "context":
            count = int(summary.get("project_count") or 0)
        else:
            count = len(report.get(breakdown_key) or [])
        noun = entity_label.lower()
        title = f"Distinct {noun} with AI activity"
        answer = (
            f"CostPilot recorded {count:,} distinct {noun} with AI activity for "
            f"{period_label}, across {int(summary.get('request_count') or 0):,} governed requests."
        )
        evidence = _ask_evidence(report.get(breakdown_key) or [], metric, filter_name, limit=result_limit)
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
        # DepartmentBudget.workspace_id (backfilled by database/backfill_workspaces.py)
        # is the real column now — no workspace given means the legacy
        # "default" bucket specifically, never every workspace pooled
        # together (which previously produced one evidence row per
        # workspace for any department name shared across workspaces,
        # dividing the same aggregated activity by different, often
        # stale/tiny caps — nonsensical percentages that also disagreed
        # with answers computed against a single workspace's own rows).
        budgets = db.query(DepartmentBudget).filter(
            DepartmentBudget.archived.isnot(True),
            DepartmentBudget.workspace_id == (request.workspace_id or "default"),
        ).all()
        if department_scope:
            # Phase 2 slice 5: this query is entirely independent of
            # reporting_filters/charged_unit (Slice 2's fix doesn't reach
            # it) -- mirrors the agent-loop's run_get_budget_status row
            # filter, same label convention the downstream budget_rows
            # loop below already uses.
            budgets = [
                b for b in budgets
                if (b.department or "Unassigned").split(":")[-1] == department_scope
            ]
        budget_scope = parsed.get("budget_scope") or "status"
        # "closest to going over budget" contains the literal substring
        # "over budget", so the naive over_limit check below previously
        # misread it as an already-exceeded claim (threshold=100, then
        # filtered to zero rows since nothing had actually crossed 100%)
        # instead of the proximity/ranking question it actually is --
        # "which department is nearest its cap," answerable regardless of
        # whether anything has crossed the threshold.
        is_proximity_question = any(term in question.lower() for term in (
            "closest to", "closest department", "nearest to", "which department is closest",
        ))
        over_limit = (not is_proximity_question) and any(term in question.lower() for term in (
            "over budget", "exceeded", "over cap", "above budget"
        ))
        threshold = 100 if over_limit else 70
        # Only request counts come from here now — spend is sourced from
        # the shared core.budget.recomputed_department_spend below instead
        # of being independently re-summed from this same breakdown.
        activity_by_department = {}
        for activity_row in report.get("organizational_unit_breakdown") or []:
            raw_department = str(
                activity_row.get("id") or activity_row.get("label") or ""
            ).strip()
            if request.workspace_id and raw_department.startswith(f"{request.workspace_id}:"):
                raw_department = raw_department[len(request.workspace_id) + 1:]
            elif ":" in raw_department:
                raw_department = raw_department.rsplit(":", 1)[-1]
            department_key = raw_department.casefold()
            if not department_key:
                continue
            bucket = activity_by_department.setdefault(department_key, {"requests": 0})
            bucket["requests"] += int(activity_row.get("request_count") or 0)
        from core.budget import recomputed_department_spend
        # Always recomputed from the ledger now -- current_spend_usd used
        # to be trusted as authoritative for "production" workspaces, but
        # that live-incremented counter was found to drift arbitrarily far
        # from the real transaction ledger with no self-correction
        # (confirmed live: a production workspace's counter had been
        # accumulating for 3 weeks before any matching real transaction
        # existed). Recomputing here via the same shared function
        # core.budget.get_all_budgets (Admin) and
        # ask_costpilot_tools.run_get_budget_status use means this answer
        # can't disagree with either of them, for every workspace type.
        #
        # Deliberately NOT passing this question's own date_from/date_to or
        # its already-fetched `report` through here, even though doing so
        # would save a query — "over/near budget" is a monthly-cap concept,
        # and this question's resolved period can be an arbitrary window
        # (e.g. "this week"). Scoping spend to that window while still
        # dividing by the full monthly cap produced nonsensical >100%
        # figures for a department with a burst of activity in a short
        # window, while the always-month-to-date budget_flag badge attached
        # to every answer correctly reported nothing over budget — the same
        # bug this function's docstring says was already fixed once, since
        # get_all_budgets and the badge always use true month-to-date.
        _ask_budget_spend_by_department = recomputed_department_spend(db, request.workspace_id)
        budget_rows = []
        for budget in budgets:
            cap = float(budget.monthly_cap_usd or 0)
            raw_budget_department = str(budget.department or "").strip()
            if request.workspace_id and raw_budget_department.startswith(f"{request.workspace_id}:"):
                raw_budget_department = raw_budget_department[len(request.workspace_id) + 1:]
            elif ":" in raw_budget_department:
                raw_budget_department = raw_budget_department.rsplit(":", 1)[-1]
            activity = activity_by_department.get(raw_budget_department.casefold(), {})
            spent = _ask_budget_spend_by_department.get(raw_budget_department.casefold(), 0.0)
            matched_requests = int(activity.get("requests") or 0)
            pct = spent / cap * 100 if cap > 0 else 0
            if cap <= 0:
                continue
            label = (budget.department or "Unassigned").split(":")[-1]
            budget_rows.append({
                "id": budget.department,
                "label": label,
                "pct": pct,
                "spent": spent,
                "cap": cap,
                "remaining": max(cap - spent, 0),
                "request_count": matched_requests,
                "throttled": bool(budget.throttled),
            })
        all_budget_rows = list(budget_rows)
        if budget_scope == "alerts" and not is_proximity_question:
            budget_rows = [row for row in budget_rows if row["pct"] >= threshold]
        budget_rows.sort(
            key=(
                (lambda row: row["label"].lower())
                if budget_scope == "all"
                else (lambda row: -row["pct"])
            )
        )
        total_cap = sum(row["cap"] for row in budget_rows)
        total_spent = sum(row["spent"] for row in budget_rows)
        total_remaining = max(total_cap - total_spent, 0)
        total_pct = total_spent / total_cap * 100 if total_cap else 0
        all_matched_requests = sum(row["request_count"] for row in all_budget_rows)
        all_matched_spend = sum(row["spent"] for row in all_budget_rows)
        report_requests = int(summary.get("request_count") or 0)
        report_spend = float(summary.get("spend_usd") or 0)
        unmatched_requests = max(report_requests - all_matched_requests, 0)
        unmatched_spend = max(report_spend - all_matched_spend, 0)
        attribution_available = report_requests == 0 or all_matched_requests > 0
        budget_coverage = {
            "source": "filtered_activity_ledger",
            "matched_requests": all_matched_requests,
            "unmatched_requests": unmatched_requests,
            "matched_spend_usd": round(all_matched_spend, 6),
            "unmatched_spend_usd": round(unmatched_spend, 6),
            "complete": unmatched_requests == 0,
        }
        now = datetime.utcnow()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        elapsed_fraction = min(max(now.day / days_in_month, 1 / days_in_month), 1)
        expected_to_date = total_cap * elapsed_fraction
        pace_variance = total_spent - expected_to_date
        projected_spend = total_spent / elapsed_fraction if elapsed_fraction else total_spent
        projected_variance = projected_spend - total_cap
        def budget_pct_display(value):
            return f"{value:.2f}%" if 0 < value < 0.1 else f"{value:.1f}%"

        evidence = [{
            "label": row["label"],
            "value": (
                f"${row['cap']:,.2f}"
                if budget_scope == "all"
                else f"${row['remaining']:,.2f}"
                if budget_scope == "remaining"
                else budget_pct_display(row["pct"])
            ),
            "metric_label": (
                "monthly budget"
                if budget_scope == "all"
                else "budget remaining"
                if budget_scope == "remaining"
                else "budget used"
            ),
            "detail": f"${row['spent']:,.4f} used · {budget_pct_display(row['pct'])} · " +
                      f"${max(row['cap'] - row['spent'], 0):,.2f} remaining" +
                      (" · throttled" if row["throttled"] else ""),
            "filter_name": "charged_unit",
            "filter_value": row["label"],
        } for row in budget_rows[:result_limit]]
        coverage_note = (
            f" ${unmatched_spend:,.4f} across {unmatched_requests:,} request"
            f"{'s were' if unmatched_requests != 1 else ' was'} excluded because no configured "
            "department budget matched the activity attribution."
            if unmatched_requests else ""
        )
        if budget_scope == "all":
            title = "Department AI budgets"
            answer = (
                f"{len(budget_rows):,} active departments have ${total_cap:,.2f} "
                f"in combined monthly AI budget, with ${total_spent:,.4f} in attributed "
                f"activity spend used.{coverage_note}"
                if budget_rows else
                "No active department budgets are configured."
            )
            calculation_formula = (
                "List each active department's configured monthly cap and current usage"
            )
        elif budget_scope == "remaining":
            title = "AI budget remaining"
            answer = (
                f"Budget usage cannot be calculated because none of the {report_requests:,} "
                "matching requests has a department that matches a configured budget."
                if budget_rows and not attribution_available else
                f"${total_remaining:,.2f} remains from ${total_cap:,.2f} in configured "
                f"monthly AI budgets. ${total_spent:,.4f} has been used ({budget_pct_display(total_pct)})."
                f"{coverage_note}"
                if budget_rows else
                "No active department budgets are configured, so remaining budget cannot be calculated."
            )
            calculation_formula = "Combined monthly budget minus current recorded budget spend"
        elif budget_scope == "forecast":
            title = "AI budget forecast"
            if budget_rows and not attribution_available:
                answer = (
                    f"A budget forecast cannot be calculated because none of the {report_requests:,} "
                    "matching requests has a department that matches a configured budget."
                )
            elif budget_rows:
                direction_word = "over" if projected_variance > 0 else "under"
                answer = (
                    f"At the current pace, month-end AI spend is projected at "
                    f"${projected_spend:,.2f}, which is ${abs(projected_variance):,.2f} "
                    f"{direction_word} the ${total_cap:,.2f} configured budget.{coverage_note}"
                )
            else:
                answer = "No active department budgets are configured, so a budget forecast cannot be calculated."
            calculation_formula = (
                f"Current monthly spend divided by {elapsed_fraction:.1%} of the calendar month elapsed"
            )
        elif budget_scope == "variance":
            title = "AI budget pace variance"
            if budget_rows and not attribution_available:
                answer = (
                    f"Budget variance cannot be calculated because none of the {report_requests:,} "
                    "matching requests has a department that matches a configured budget."
                )
            elif budget_rows:
                direction_word = "above" if pace_variance > 0 else "below"
                answer = (
                    f"AI spend is ${abs(pace_variance):,.2f} {direction_word} the "
                    f"time-phased budget pace. ${total_spent:,.4f} has been used versus "
                    f"${expected_to_date:,.2f} expected by this point in the month.{coverage_note}"
                )
            else:
                answer = "No active department budgets are configured, so budget variance cannot be calculated."
            calculation_formula = "Current spend minus the monthly budget multiplied by calendar time elapsed"
        elif budget_scope == "status":
            title = "AI budget status"
            if budget_rows and not attribution_available:
                answer = (
                    f"Budget status cannot be calculated because none of the {report_requests:,} "
                    "matching requests has a department that matches a configured budget."
                )
            elif budget_rows:
                status = "within" if total_spent <= total_cap else "over"
                answer = (
                    f"AI spend is {status} the configured monthly budget: "
                    f"${total_spent:,.4f} of ${total_cap:,.2f} used ({budget_pct_display(total_pct)}), "
                    f"with ${total_remaining:,.2f} remaining.{coverage_note}"
                )
            else:
                answer = "No active department budgets are configured, so budget status cannot be calculated."
            calculation_formula = "Combined current department spend divided by combined configured monthly budget"
        elif is_proximity_question:
            title = "Budget watch"
            # budget_rows is sorted by -pct here (the non-"all" sort key
            # above), so the first row is already the closest to its cap --
            # answer with it directly rather than a threshold count, since
            # "closest to going over" is a ranking question, not a yes/no
            # over-threshold check.
            if budget_rows:
                top = budget_rows[0]
                status_word = "over" if top["pct"] >= 100 else "closest to"
                answer = (
                    f"{top['label']} is {status_word} its monthly AI budget: "
                    f"${top['spent']:,.4f} of ${top['cap']:,.2f} used "
                    f"({budget_pct_display(top['pct'])})."
                )
            else:
                answer = "No active department budgets are configured, so budget proximity cannot be calculated."
            calculation_formula = "Department with the highest current spend as a percentage of its monthly cap"
        else:
            title = "Budget watch"
            answer = (
                f"{len(budget_rows)} department{'s are' if len(budget_rows) != 1 else ' is'} "
                f"at or above {threshold}% of its monthly AI budget."
                if budget_rows else
                f"No active department is at or above {threshold}% of its monthly AI budget."
            )
            calculation_formula = (
                f"Current department spend divided by monthly cap; include departments at or above {threshold}%"
            )
        calculation_row_count = len(budget_rows)
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
    elif intent == "connection_health":
        # "Which platform connections are healthy?" / "When did
        # Salesforce last sync?" / "Are there any connection health
        # issues?" -- previously fell through to a generic company spend
        # overview with zero mention of connection status, or (worse)
        # answered "when did Salesforce last sync" with Salesforce's AI
        # SPEND figures. Reuses core.data_coverage.get_data_coverage(),
        # the same real, already-populated IntegrationConnection-backed
        # function the data_coverage_question branch above already uses
        # for "is Salesforce connected" -- not a new data source, just a
        # health-focused framing of the same trusted status.
        from core.data_coverage import get_data_coverage

        coverage = get_data_coverage(db, request.workspace_id)
        platforms = coverage.platforms
        title = "Connection health"
        calculation_formula = (
            "Read integration_connections.status/last_outcome_sync_at directly -- "
            "not derived from AI activity volume"
        )
        calculation_row_count = len(platforms)
        evidence = [
            {
                "label": p["platform"].title(),
                "value": p.get("status") or ("connected" if p["connected"] else "not connected"),
                "metric_label": "connection status",
                "detail": (
                    (f"last outcome sync: {p['last_outcome_sync_at']}" if p.get("last_outcome_sync_at")
                     else "never synced")
                    + (" · sync is stale" if p.get("stale") else "")
                ),
                "filter_name": None,
                "filter_value": None,
            }
            for p in platforms
        ]
        named = [p for p in platforms if p["platform"] in question.lower()]
        if named:
            target = named[0]
            if target.get("last_outcome_sync_at"):
                try:
                    sync_display = datetime.fromisoformat(
                        target["last_outcome_sync_at"]
                    ).strftime("%b %-d, %Y at %H:%M UTC")
                except ValueError:
                    sync_display = target["last_outcome_sync_at"]
                answer = (
                    f"{target['platform'].title()} last completed an outcome sync at "
                    f"{sync_display}"
                    + (", but that sync data now looks stale." if target.get("stale") else ".")
                )
            elif target["connected"]:
                answer = f"{target['platform'].title()} is connected but has never completed an outcome sync."
            else:
                answer = f"{target['platform'].title()} is not currently connected."
        else:
            unhealthy = [
                p for p in platforms
                if p["connected"] and (p.get("status") not in (None, "active") or p.get("stale"))
            ]
            healthy = [
                p for p in platforms
                if p["connected"] and not p.get("stale") and p.get("status") in (None, "active")
            ]
            if unhealthy:
                names = ", ".join(p["platform"].title() for p in unhealthy)
                answer = (
                    f"{len(unhealthy)} of {len(platforms)} connection"
                    f"{'s' if len(platforms) != 1 else ''} need attention: {names}."
                )
            elif not healthy:
                answer = "No platforms are currently connected."
            else:
                names = ", ".join(p["platform"].title() for p in healthy)
                answer = f"All {len(healthy)} connected platform{'s' if len(healthy) != 1 else ''} are healthy: {names}."
    elif intent == "inactive_context":
        # "Which projects have no AI activity at all?" -- mirrors the
        # agent-adoption block above (full catalog minus entities with
        # real activity) for work items/projects instead of agents.
        # Confirmed live without this: the question fell through to a
        # generic overview whose evidence rows were the projects WITH
        # activity, the opposite of what was asked.
        workitem_query = db.query(WorkItem).filter(WorkItem.merged_into_work_item_id.is_(None))
        if request.workspace_id:
            workitem_query = workitem_query.filter(WorkItem.workspace_id == request.workspace_id)
        work_items = workitem_query.all()
        work_item_ids = [wi.id for wi in work_items]
        ids_with_activity = set()
        if work_item_ids:
            ids_with_activity = {
                row[0] for row in db.query(TokenTransaction.work_item_id)
                .filter(TokenTransaction.work_item_id.in_(work_item_ids))
                .distinct()
                .all()
                if row[0] is not None
            }
        dormant = [wi for wi in work_items if wi.id not in ids_with_activity]
        context_noun = context_plural.lower()
        title = f"{context_plural} with no AI activity"
        calculation_formula = (
            f"{context_plural} in the catalog with zero TokenTransaction rows ever recorded"
        )
        calculation_row_count = len(dormant)
        evidence = [
            {
                "label": wi.name or wi.external_id,
                "value": "0",
                "metric_label": "lifetime AI requests",
                "detail": (
                    f"status: {wi.status or 'unknown'}"
                    + (f" · owner: {wi.owner}" if wi.owner else "")
                ),
                "filter_name": "project_id",
                "filter_value": wi.external_id,
            }
            for wi in dormant[:result_limit]
        ]
        if not work_items:
            answer = f"No {context_noun} are tracked yet."
        elif dormant:
            names = ", ".join((wi.name or wi.external_id) for wi in dormant[:5])
            more = f" and {len(dormant) - 5} more" if len(dormant) > 5 else ""
            answer = (
                f"{len(dormant):,} of {len(work_items):,} {context_noun} have never had any "
                f"recorded AI activity: {names}{more}."
            )
        else:
            answer = f"All {len(work_items):,} {context_noun} have at least some recorded AI activity."
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
    elif intent == "ranking" and entity in entity_config and growth_ranking_question:
        # "Which project's AI spend grew the most this month?" -- the
        # generic ranking branch below ranks by ABSOLUTE value this
        # period, silently dropping the "grew" comparison the question
        # actually asked for. Confirmed live: it returned whichever
        # project had the highest raw spend this month, not the one
        # with the biggest period-over-period increase. Computes the
        # prior period of equal length via the same comparison_plan()
        # the company-wide comparison/change_drivers intents already
        # use, re-runs project_activity_reporting() for it with the
        # same reporting_filters, and ranks by the delta instead.
        breakdown_key, filter_name, entity_label = entity_config[entity]
        growth_primary_period = resolve_primary_period(
            period_key=parsed.get("period_key"), days=parsed["days"],
            timezone_name=effective_timezone,
            date_from=request.date_from, date_to=request.date_to,
        )
        growth_comparison_plan = comparison_plan(growth_primary_period, "previous_period")
        prior_report = project_activity_reporting(
            workspace_id=request.workspace_id,
            date_from=growth_comparison_plan.comparison.start,
            date_to=growth_comparison_plan.comparison.end,
            days=parsed["days"], **reporting_filters,
            activity_limit=2000, exclude_prune_only_rows=True, db=db,
        )
        current_rows = {
            row["id"]: row for row in (report.get(breakdown_key) or [])
            if row.get("id") not in ("__unknown__", "__simulator__")
        }
        prior_rows = {
            row["id"]: row for row in (prior_report.get(breakdown_key) or [])
            if row.get("id") not in ("__unknown__", "__simulator__")
        }
        deltas = []
        for entity_id, row in current_rows.items():
            current_value = _ask_row_metric(row, metric)
            prior_value = _ask_row_metric(prior_rows.get(entity_id, {}), metric)
            deltas.append({
                "id": entity_id,
                "label": row.get("label"),
                "current": current_value,
                "prior": prior_value,
                "delta": current_value - prior_value,
            })
        wants_increase = not any(
            term in question.lower() for term in ("fell", "dropped", "decreased", "declined")
        )
        deltas.sort(key=lambda d: d["delta"], reverse=wants_increase)
        calculation_row_count = len(deltas)
        calculation_formula = (
            f"{metric} for {period_label} minus {metric} for the immediately preceding "
            "period of equal length, ranked by that difference"
        )
        top_deltas = deltas[:max(1, result_limit)]
        _, metric_label = _ask_metric_value(metric, 0)
        title = f"Biggest {'increase' if wants_increase else 'decrease'} in {metric_label.lower()} by {entity_label.lower()}"
        evidence = [
            {
                "label": d["label"] or "Unknown",
                "value": _ask_metric_value(metric, d["delta"])[0],
                "metric_label": f"change in {metric_label}",
                "detail": (
                    f"{_ask_metric_value(metric, d['prior'])[0]} → "
                    f"{_ask_metric_value(metric, d['current'])[0]}"
                ),
                "filter_name": filter_name,
                "filter_value": d["id"],
            }
            for d in top_deltas
        ]
        if top_deltas and top_deltas[0]["delta"] != 0:
            leader = top_deltas[0]
            direction_word = "increase" if leader["delta"] >= 0 else "decrease"
            answer = (
                f"{leader['label'] or 'Unknown'} had the biggest {direction_word} in "
                f"{metric_label.lower()} for {period_label} vs. the prior period: "
                f"{_ask_metric_value(metric, leader['prior'])[0]} → "
                f"{_ask_metric_value(metric, leader['current'])[0]} "
                f"({_ask_metric_value(metric, leader['delta'])[0]})."
            )
        else:
            answer = (
                f"No {entity_label.lower()} showed a change in {metric_label.lower()} between "
                f"{period_label} and the prior period."
            )
    elif intent == "ranking" and entity in entity_config:
        breakdown_key, filter_name, entity_label = entity_config[entity]
        ranking_rows = report.get(breakdown_key) or []
        # people_breakdown/project_breakdown carry a "__unknown__"/
        # "__simulator__" placeholder bucket (routes_work_items.py's
        # project_activity_reporting()) for activity with no real
        # attributed person/work item -- e.g. "Simulator User" is that
        # bucket's label, not an actual person. real_person_count/
        # real_project_count (routes_work_items.py:2425) already treat
        # these ids as non-real; a ranking answer must too, or "who had
        # the highest spend" can crown a synthetic catch-all bucket
        # instead of an actual named individual (confirmed live
        # 2026-09-11: "Simulator User" answered as the top person).
        ranking_rows = [row for row in ranking_rows if row.get("id") not in ("__unknown__", "__simulator__")]
        if entity == "context" and outcome_filter:
            # "Which won opportunities had the highest AI investment" --
            # narrow to rows whose synced outcome matches, before ranking.
            ranking_rows = [
                row for row in ranking_rows
                if row.get("outcome_success") == (outcome_filter == "won")
            ]
            outcome_adjective = outcome_filter_label or ("won" if outcome_filter == "won" else "lost")
            entity_label = f"{outcome_adjective.capitalize()} {entity_label.lower()}"
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
        # "Who had the highest AI spend this month and compare to last
        # month?" -- comparison_key was already correctly detected (by
        # both the regex fallback and the OpenAI refinement layer) but
        # nothing here ever read it, so the winning entity's prior-period
        # figure was never fetched and the narration said no comparison
        # data existed, even on a workspace with a full year of history.
        # Confirmed live 2026-09-12. Only looks up the already-ranked
        # winner(s) in a second, prior-period report -- same
        # comparison_plan()-driven pattern the growth-ranking branch above
        # uses, just applied to an existing ranking instead of re-ranking
        # by delta.
        ranking_comparison_sentence = ""
        if ranking_comparison_key and ranked:
            comparison_ranking_period = resolve_primary_period(
                period_key=parsed.get("period_key"), days=parsed["days"],
                timezone_name=effective_timezone,
                date_from=request.date_from, date_to=request.date_to,
            )
            ranking_comparison_plan = comparison_plan(comparison_ranking_period, ranking_comparison_key)
            ranking_comparison_report = project_activity_reporting(
                workspace_id=request.workspace_id,
                date_from=ranking_comparison_plan.comparison.start,
                date_to=ranking_comparison_plan.comparison.end,
                days=parsed["days"], **reporting_filters,
                activity_limit=2000, exclude_prune_only_rows=True, db=db,
            )
            comparison_rows_by_id = {
                row["id"]: row for row in (ranking_comparison_report.get(breakdown_key) or [])
                if row.get("id") not in ("__unknown__", "__simulator__")
            }
            winner_comparison_row = comparison_rows_by_id.get(ranked[0].get("id"))
            comparison_period_label = ranking_comparison_plan.comparison.contract()["label"]
            if winner_comparison_row:
                comparison_value, _ = _ask_metric_value(
                    metric, _ask_row_metric(winner_comparison_row, metric)
                )
                ranking_comparison_sentence = (
                    f" For {comparison_period_label}, {ranked[0].get('label') or 'Unknown'} "
                    f"had {comparison_value}."
                )
            else:
                ranking_comparison_sentence = (
                    f" {ranked[0].get('label') or 'Unknown'} had no attributed AI activity "
                    f"for {comparison_period_label}."
                )
        evidence = _ask_evidence(
            ranked,
            metric,
            filter_name,
            direction=direction,
            limit=result_limit,
        )
        rank_label = "Lowest" if direction == "asc" else "Top"
        title = f"{rank_label} {entity_label.lower()} by {_ask_metric_value(metric, 0)[1]}"
        # When the query was scoped to a department (either via a carried-
        # over subject filter or _ask_named_department detecting one named
        # in this question) but the ranked dimension is something else
        # (models, platforms, agents...), say so explicitly -- the numbers
        # were already correctly scoped, but without this the sentence read
        # identically to an unfiltered, company-wide answer, which is
        # exactly the ambiguity that made the department-scoping bug look
        # unfixed even after the underlying query was corrected.
        department_scope = reporting_filters.get("charged_unit") if entity != "department" else None
        scope_suffix = f" for {department_scope}" if department_scope else ""
        if ranked:
            value, metric_label = _ask_metric_value(
                metric, _ask_row_metric(ranked[0], metric)
            )
            if result_limit > 1:
                available = min(result_limit, len(ranked))
                qualifier = (
                    f"the {available} matching {entity_label.lower()}{scope_suffix}"
                    if available == result_limit
                    else f"all {available} matching {entity_label.lower()}{scope_suffix} available"
                )
                answer = (
                    f"Showing {qualifier}, ordered from "
                    f"{'lowest to highest' if direction == 'asc' else 'highest to lowest'} "
                    f"{metric_label} for {period_label}. "
                    f"{ranked[0].get('label') or 'Unknown'} is first at {value}."
                    f"{ranking_comparison_sentence}"
                )
            else:
                answer = (
                    f"{ranked[0].get('label') or 'Unknown'} had the "
                    f"{'lowest' if direction == 'asc' else 'highest'} {metric_label}{scope_suffix} "
                    f"for {period_label}: {value}. "
                    f"That includes {int(ranked[0].get('request_count') or 0):,} governed requests."
                    f"{ranking_comparison_sentence}"
                )
        else:
            if latest_available_at:
                latest_date = latest_available_at.strftime("%B %-d, %Y")
                answer = (
                    f"No {entity_label.lower()} had attributed AI activity for {period_label}. "
                    f"The latest available activity in this workspace is from {latest_date}. "
                    "Expand the date range to include that activity."
                )
            else:
                answer = f"No {entity_label.lower()} had attributed AI activity in this period."
    else:
        context_rows = report.get("project_breakdown") or []
        if entity == "context" and outcome_filter:
            context_rows = [
                row for row in context_rows
                if row.get("outcome_success") == (outcome_filter == "won")
            ]
            outcome_label = outcome_filter_label or ("won" if outcome_filter == "won" else "lost")
            context_noun = context_plural.lower()
            outcome_spend = sum(float(row.get("spend_usd") or 0) for row in context_rows)
            outcome_requests = sum(int(row.get("request_count") or 0) for row in context_rows)
            title = f"AI spend on {outcome_label} {context_noun}"
            answer = (
                f"For {period_label}, CostPilot tracked ${outcome_spend:,.4f} of AI spend "
                f"across {outcome_requests:,} requests on {len(context_rows)} {outcome_label} "
                f"{context_noun}. This is AI activity tracked alongside those outcomes, not "
                f"evidence that the AI activity caused them."
            )
        else:
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
        "metric_contract": metric_contract.public_contract(),
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
    if comparison_execution_plan:
        calculation["comparison_plan"] = comparison_execution_plan.contract()
        calculation["absolute_change"] = change
        calculation["percent_change"] = pct
    if driver_analysis:
        calculation["driver_analysis"] = driver_analysis
    conversation_context = {
        "intent": intent,
        "entity": entity,
        "metric": metric,
        "direction": direction,
        "days": int(parsed["days"]),
        "result_limit": int(result_limit),
        "period_key": parsed.get("period_key"),
        "comparison_key": parsed.get("comparison_key"),
        "source_platform": parsed.get("source_platform"),
        "model_tier": parsed.get("model_tier"),
    }
    for field in ("usage_status", "budget_scope"):
        if parsed.get(field) not in (None, ""):
            conversation_context[field] = parsed[field]
    if parsed.get("usage_threshold") not in (None, ""):
        conversation_context["usage_threshold"] = int(parsed["usage_threshold"])
    for field in ("subject_entity", "subject_filter_name", "subject_filter_value"):
        if parsed.get(field) not in (None, ""):
            conversation_context[field] = parsed[field]
    if named_entity and named_entity.get("filter_name") in reporting_filters:
        conversation_context.update({
            "subject_entity": named_entity.get("entity"),
            "subject_filter_name": named_entity.get("filter_name"),
            "subject_filter_value": named_entity.get("row", {}).get("id"),
        })

    payload = {
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
            "coverage": comparison_coverage_result,
            "budget_coverage": budget_coverage,
            "latest_available_at": (
                latest_available_at.isoformat() if latest_available_at else None
            ),
        },
        "assistant_mode": assistant_mode,
        # entity/intent are reassigned inside several branches above (e.g.
        # the named-entity and account-outcomes branches) to reflect what
        # actually answered the question -- parsed still held whatever the
        # initial classifier guessed before those branches ran, so
        # ask_interpretation_label() rendered a stale, sometimes-misleading
        # label ("Context total using spend usd" for an answer that was
        # really an account-scoped outcomes lookup). Synced here so the
        # label always matches the branch that actually produced the answer.
        "interpreted_as": ask_interpretation_label({**parsed, "entity": entity, "intent": intent}),
        "contract_status": "passed",
        "interpreted_intent": {**parsed, "entity": entity, "intent": intent},
        "conversation_context": conversation_context,
        "suggested_questions": _ask_suggested_questions(
            "no_activity" if data_scope == "no_activity" and latest_available_at else intent,
            department=reporting_filters.get("charged_unit"),
            subject=(
                named_entity["row"].get("label")
                if named_entity and named_entity.get("entity") in {"person", "account"}
                else None
            ),
            asked_question=question,
        ),
        "read_only": True,
    }
    contract_issues = validate_ask_answer_contract(parsed, payload)
    if contract_issues:
        logger.warning(
            "Ask CostPilot answer contract rejected intent=%s issues=%s",
            parsed.get("canonical_intent"),
            contract_issues,
        )
        return _ask_contract_failure_response(request, parsed, contract_issues)
    if assistant_mode == "deterministic_period_contract":
        narrated_title, narrated_answer, narrated = (
            payload["title"], payload["answer"], False
        )
    else:
        narrated_title, narrated_answer, narrated = _ask_grounded_narrative(
            request, payload
        )
    payload["title"] = narrated_title
    payload["answer"] = narrated_answer
    if narrated:
        payload["assistant_mode"] = f"{assistant_mode}+grounded_narrator"
    return payload


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


# ── AI-powered report builder (Milestone 5) ────────────────────────────────

class NLQueryRequest(BaseModel):
    question: str
    workspace_id: Optional[str] = None
    # A previously returned report_state (see NLQueryResult below), passed
    # back in for conversational refinement -- "only show Salesforce"
    # means "add a platform filter to THIS state," not "start over."
    current_report_state: Optional[dict] = None


def _nl_query_translate(question: str, current_report_state: Optional[dict]) -> dict:
    """
    Single forced tool call: the model's only job is to emit a valid
    query_metrics request representing the question (or the refinement of
    current_report_state). It never sees data and never answers anything
    -- translation only, same tool_choice={"type": "any"} pattern already
    used by the agent loop above, just one turn instead of a loop.
    """
    import anthropic
    from api.ask_costpilot_tools import TOOL_SCHEMAS, to_anthropic_tools

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Report builder is not configured (no ANTHROPIC_API_KEY).")

    query_metrics_schema = next(s for s in TOOL_SCHEMAS if s["name"] == "query_metrics")
    tools = to_anthropic_tools([query_metrics_schema])

    system = (
        "You translate a business reporting question, or a refinement instruction, into a "
        "single query_metrics tool call. You do not answer the question, compute any number, "
        "or narrate a result -- you only decide the metrics, dimensions, filters, timeframe, "
        "comparison, sort, and limit the report should use. If CURRENT REPORT STATE is not "
        "empty, treat the request as a refinement of it: 'only show Salesforce' means set "
        "filters.platform on top of the existing state, not start a new report from scratch, "
        "unless the request clearly describes a different report entirely. Keep every field "
        "from the current state that the request doesn't mention."
    )
    user_content = (
        f"CURRENT REPORT STATE: {json.dumps(current_report_state or {}, default=str)}\n\n"
        f"REQUEST: {question}"
    )

    model = os.getenv("ASK_COSTPILOT_AGENT_MODEL", os.getenv("ANTHROPIC_FLAGSHIP_MODEL", "claude-sonnet-4-6"))
    client = anthropic.Anthropic(api_key=api_key, timeout=15.0, max_retries=0)
    try:
        response = client.messages.create(
            model=model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user_content}],
            tools=tools, tool_choice={"type": "any"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Report builder translation failed: {exc}")

    tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
    if not tool_uses:
        raise HTTPException(status_code=502, detail="Could not translate that into a report.")
    return dict(tool_uses[0].input or {})


@router.post("/nl-query")
def nl_query(request: NLQueryRequest, db: Session = Depends(get_db)):
    """
    Natural-language -> report translation for the AI report builder.
    Returns BOTH the structured report_state (what the visual filter UI
    should render/sync to) and the executed result (query_metrics's real,
    SQL-computed numbers) -- the LLM only produced report_state; every
    number in `result` comes from run_query_metrics, same as every other
    Ask CostPilot answer path.
    """
    from api.ask_costpilot_tools import run_query_metrics

    report_state = _nl_query_translate(request.question, request.current_report_state)

    result = run_query_metrics(
        db, request.workspace_id,
        metrics=report_state.get("metrics") or [],
        dimensions=report_state.get("dimensions") or [],
        filters=report_state.get("filters") or {},
        days=int(report_state.get("days") or 30),
        period_key=report_state.get("period_key") or "none",
        compare_to=report_state.get("compare_to") or None,
        sort=report_state.get("sort") or None,
        limit=int(report_state.get("limit") or 20),
    )

    return {"report_state": report_state, "result": result}
