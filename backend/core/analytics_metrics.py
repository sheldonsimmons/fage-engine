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
}


def metric_definition(metric_id: str) -> MetricDefinition:
    """Return the approved definition or fail closed for unknown metrics."""
    try:
        return METRIC_REGISTRY[metric_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported executive analytics metric: {metric_id}") from exc
