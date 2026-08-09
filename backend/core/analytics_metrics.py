"""Versioned metric contracts shared by executive analytics agents."""

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    definition: str
    formula: str
    source: str
    time_field: str
    grain: str
    classification: str
    version: str = "1.0"

    def public_contract(self) -> dict:
        return asdict(self)


METRIC_REGISTRY: Mapping[str, MetricDefinition] = {
    "spend_usd": MetricDefinition(
        id="spend_usd",
        label="AI spend",
        definition="Recorded model-call cost for matching governed requests.",
        formula="SUM(cost_usd)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="recorded_fact",
    ),
    "total_tokens": MetricDefinition(
        id="total_tokens",
        label="tokens",
        definition="Recorded input and output tokens for matching model calls.",
        formula="SUM(input_tokens + output_tokens)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="recorded_fact",
    ),
    "request_count": MetricDefinition(
        id="request_count",
        label="governed requests",
        definition="Count of matching governed model-call records.",
        formula="COUNT(token_transactions.id)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="recorded_fact",
    ),
    "tokens_saved": MetricDefinition(
        id="tokens_saved",
        label="tokens pruned",
        definition="Tokens measured as removed before matching model calls.",
        formula="SUM(tokens_saved)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="derived_fact",
    ),
    "risk_event_count": MetricDefinition(
        id="risk_event_count",
        label="risk events",
        definition="Matching governed requests classified as risk events.",
        formula="COUNT(matching risk events)",
        source="audit_events",
        time_field="timestamp",
        grain="audit_event",
        classification="recorded_fact",
    ),
    "avg_cost_per_request": MetricDefinition(
        id="avg_cost_per_request",
        label="average cost per request",
        definition="Recorded model-call spend divided by matching request count.",
        formula="SUM(cost_usd) / COUNT(token_transactions.id)",
        source="token_transactions",
        time_field="timestamp",
        grain="aggregate",
        classification="derived_fact",
    ),
    "input_tokens": MetricDefinition(
        id="input_tokens",
        label="input tokens",
        definition="Recorded input (prompt) tokens for matching model calls.",
        formula="SUM(input_tokens)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="recorded_fact",
    ),
    "output_tokens": MetricDefinition(
        id="output_tokens",
        label="output tokens",
        definition="Recorded output (completion) tokens for matching model calls.",
        formula="SUM(output_tokens)",
        source="token_transactions",
        time_field="timestamp",
        grain="model_call",
        classification="recorded_fact",
    ),
    "budget_used_pct": MetricDefinition(
        id="budget_used_pct",
        label="percent of budget used",
        definition="Current month-to-date department spend divided by its monthly cap.",
        formula="current_spend_usd / monthly_cap_usd",
        source="department_budgets",
        time_field="period_start",
        grain="department_month",
        classification="derived_fact",
    ),
    "budget_remaining_usd": MetricDefinition(
        id="budget_remaining_usd",
        label="budget remaining",
        definition="Monthly cap minus current month-to-date department spend, floored at zero.",
        formula="MAX(monthly_cap_usd - current_spend_usd, 0)",
        source="department_budgets",
        time_field="period_start",
        grain="department_month",
        classification="derived_fact",
    ),
}


def metric_definition(metric_id: str) -> MetricDefinition:
    """Return the approved definition or fail closed for unknown metrics."""
    try:
        return METRIC_REGISTRY[metric_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported executive analytics metric: {metric_id}") from exc


# Natural-language phrases that identify each metric, ordered most-specific
# first. This is the single place new metrics become answerable by keyword
# question — the deterministic intent classifier (api/routes_efficiency.py
# _ask_intent) walks this list instead of keeping its own separate keyword
# tuples, so a metric added here without an alias entry is registered but
# silently unreachable from natural language, and a metric with aliases
# here needs no matching change in the classifier to become reachable.
METRIC_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tokens_saved", (
        "prun", "tokens removed", "removed before model", "saved", "savings",
        "save us", "save money", "avoided cost", "reduction",
        "would our spend have been", "without pruning",
    )),
    ("risk_event_count", ("risk event", "risk events")),
    ("avg_cost_per_request", (
        "average cost", "avg cost", "cost per request", "average request cost",
    )),
    ("input_tokens", ("input token", "prompt token")),
    ("output_tokens", ("output token", "completion token")),
    ("total_tokens", (
        "token", "burn", "burning", "consumption", "consumed",
        "token bill",
    )),
    ("spend_usd", (
        "expensive", "costliest", "highest cost", "most cost", "spend", "spent",
        "cost", "bill", "ai bill", "money", "budget",
    )),
    ("request_count", (
        "request", "call", "volume", "used most", "usage", "activity", "traffic",
    )),
)


def metric_for_keywords(text: str, default: str = "spend_usd") -> str:
    """Classify free text into a canonical metric id using METRIC_KEYWORD_ALIASES."""
    for metric_id, phrases in METRIC_KEYWORD_ALIASES:
        if any(phrase in text for phrase in phrases):
            return metric_id
    return default
