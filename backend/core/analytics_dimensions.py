"""
core/analytics_dimensions.py — the canonical registry of dimensions Ask
CostPilot can group, rank, or filter by, and which metrics each one
actually supports.

This is Ask CostPilot's dimension half of the semantic layer (see
analytics_metrics.py for the metric half). Its purpose is the same as
that file's: give query planning ONE place to check "is this combination
real" instead of letting a query plan silently reference a
dimension/metric pairing nothing in the codebase actually computes.

Kept independent from the breakdown wiring in
api/routes_efficiency.py (`entity_config`) and api/ask_costpilot_tools.py
rather than replacing them, to avoid restructuring already-working,
heavily-tested query code for a documentation-grade registry -- but
tests/test_analytics_registry_consistency.py asserts the two can't drift
apart silently.
"""
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class DimensionDefinition:
    id: str
    label: str
    definition: str
    source: str
    breakdown_key: str  # key in project_activity_reporting()'s result dict
    filter_name: Optional[str]  # kwarg name on project_activity_reporting(), or None if not filterable
    supported_metrics: Tuple[str, ...]


DIMENSION_REGISTRY: Mapping[str, DimensionDefinition] = {
    "USER": DimensionDefinition(
        id="USER",
        label="User",
        definition="An individual human identity attributed to a model call (by actor or linked WorkUser).",
        source="token_transactions",
        breakdown_key="people_breakdown",
        filter_name="user_external_id",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "ACCOUNT": DimensionDefinition(
        id="ACCOUNT",
        label="Account",
        definition="A customer/business-unit parent for attributed work (WorkAccount) -- distinct from USER.",
        source="work_accounts",
        breakdown_key="account_breakdown",
        filter_name="account_id",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "DEPARTMENT": DimensionDefinition(
        id="DEPARTMENT",
        label="Department / team",
        definition="The org unit a request is charged against (TokenTransaction.department, prefix-stripped).",
        source="token_transactions",
        breakdown_key="organizational_unit_breakdown",
        filter_name="charged_unit",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
            "budget_used_pct", "budget_remaining_usd",
        ),
    ),
    "PROJECT": DimensionDefinition(
        id="PROJECT",
        label="Project / matter / case",
        definition="A WorkItem: a project, matter, engagement, case, or claim.",
        source="work_items",
        breakdown_key="project_breakdown",
        filter_name="project_id",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "AGENT": DimensionDefinition(
        id="AGENT",
        label="Agent",
        definition="A registered AI digital worker (RegisteredAgent) that made the call.",
        source="registered_agents",
        breakdown_key="agent_breakdown",
        filter_name="agent_id",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "MODEL": DimensionDefinition(
        id="MODEL",
        label="Model",
        definition="The exact model name/tier used for the call (TokenTransaction.model_name/model_tier).",
        source="token_transactions",
        breakdown_key="model_breakdown",
        # No model filter exists on project_activity_reporting today -- a
        # model-level ranking can be shown, but not used to scope another
        # question ("spend for GPT-4o" filters by provider, not this exact
        # model id, unless model_tier happens to match).
        filter_name=None,
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "PROVIDER": DimensionDefinition(
        id="PROVIDER",
        label="Provider",
        definition=(
            "The AI vendor (Anthropic/OpenAI/Google/...) derived from model_name via "
            "core/model_provider.py -- distinct from PLATFORM (the source system a "
            "request came from, e.g. Salesforce)."
        ),
        source="token_transactions (derived)",
        breakdown_key="provider_breakdown",
        filter_name="provider",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "PLATFORM": DimensionDefinition(
        id="PLATFORM",
        label="Source platform",
        definition="The system a request originated from (Salesforce, ServiceNow, HubSpot, ...).",
        source="token_transactions",
        breakdown_key="source_platform_breakdown",
        filter_name="source_platform",
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
    "BUSINESS_PURPOSE": DimensionDefinition(
        id="BUSINESS_PURPOSE",
        label="Business purpose",
        definition="A classified reason for the request (see core/business_context.py).",
        source="token_transactions (derived)",
        breakdown_key="business_purpose_breakdown",
        filter_name="business_purpose",
        supported_metrics=("spend_usd", "total_tokens", "request_count"),
    ),
    "DATE": DimensionDefinition(
        id="DATE",
        label="Date / period",
        definition="The calendar period a request falls in -- see core/analytics_periods.py.",
        source="token_transactions",
        breakdown_key="period",  # not a ranked breakdown; used for TREND/time-series questions
        filter_name=None,  # handled via date_from/date_to/days, not a simple equality filter
        supported_metrics=(
            "spend_usd", "total_tokens", "input_tokens", "output_tokens",
            "tokens_saved", "request_count", "avg_cost_per_request",
        ),
    ),
}


# Dimensions that exist conceptually but cannot be answered from the
# current schema -- see docs/ASK_COSTPILOT_QUESTION_CATALOG.md for the
# specific unsupported questions this drives.
UNSUPPORTED_DIMENSIONS = {
    "REVENUE": "No revenue/billing-to-customer data is tracked anywhere in CostPilot's schema.",
    "BUSINESS_OUTCOME": "No outcome/ROI data (deals won, tickets resolved, etc.) is linked to AI activity.",
    "SENSITIVE_DATA_FLAG": (
        "sensitive_terms exists for policy configuration, but no column on TokenTransaction "
        "records whether a specific call's content matched one -- cannot report per-request."
    ),
}


def dimension_definition(dimension_id: str) -> DimensionDefinition:
    try:
        return DIMENSION_REGISTRY[dimension_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported executive analytics dimension: {dimension_id}") from exc


def supports(metric_id: str, dimension_id: str) -> bool:
    """Whether a (metric, dimension) combination is real -- use before executing a query plan."""
    dimension = DIMENSION_REGISTRY.get(dimension_id)
    return bool(dimension and metric_id in dimension.supported_metrics)
