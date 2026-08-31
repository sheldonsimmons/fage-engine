"""
core/metrics_catalog.py — the single definition of every CostPilot metric
and dimension, used by core/metrics_query.py (query_metrics) and every
future caller (dashboard, reports, Ask CostPilot).

Why this file exists: as of this session, SUM(TokenTransaction.cost_usd)
alone was independently written in 6 files / 28 call sites
(routes_dashboard.py, routes_trial.py, routes_work_items.py,
routes_timeseries.py, routes_models.py, routes_router.py). This catalog is
not a new computation -- it is a name for computations that already exist,
so "ai_spend" means the same thing regardless of which screen or tool asks
for it. Definitions here should match the most-recently-established
behavior in the codebase, not invent a new one.

Two metric sources exist and are never mixed inside one SQL query (see
metrics_query.py for why):

- "transaction": aggregated directly from TokenTransaction, one row per
  AI call. Requires no join to answer on its own.
- "outcome": aggregated from WorkItemOutcome (one row per WorkItem, current
  state only), requires a join through WorkItem. Counting these by
  summing over TokenTransaction rows would double-count a WorkItem with
  more than one linked call -- this is why they're a separate source.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    source: str  # "transaction" | "outcome"
    unit: str  # "usd" | "count" | "tokens"
    definition: str
    provenance: str
    # -- Ask CostPilot architecture audit Recommendation #3: generalizing
    # the sample-size-awareness that used to live only inside
    # compute_cost_per_outcome() as one hand-rolled special case, so ANY
    # sample-size-sensitive metric can get the same "don't state this as
    # confident fact off 2 data points" treatment, not just that one. --
    #
    # sample_size_metric: the key of the paired COUNT metric that measures
    # how many underlying records this metric was computed over -- e.g.
    # won_value's reliability depends on won_count (a $500K "average" won
    # deal size off 1 closed deal isn't a stable number). None for metrics
    # that are already their own sample (a count) or aren't an average/
    # sum-over-a-small-population type (ai_spend is real regardless of how
    # many calls made it up -- there's no "small sample" failure mode for
    # a plain total).
    sample_size_metric: Optional[str] = None
    # min_meaningful_sample / min_executive_sample: per-metric overrides
    # for the evidence thresholds core.metrics_query.MIN_MEANINGFUL_SAMPLE/
    # MIN_EXECUTIVE_SAMPLE apply by default -- None means "use the module
    # default," not "no threshold." No metric needs an override today;
    # the field exists so one can be added without changing the contract
    # shape again.
    min_meaningful_sample: Optional[int] = None
    min_executive_sample: Optional[int] = None
    # comparable: whether a period-over-period comparison (run_metrics_
    # query's compare_to) is meaningful for this metric as reported. True
    # for every metric today (all are sums/counts, where a delta is a real
    # answer); reserved for a future ratio/average metric (e.g. a
    # registry-level cost_per_outcome) where a raw before/after difference
    # would need its own sample-aware treatment rather than a plain
    # subtraction.
    comparable: bool = True


@dataclass(frozen=True)
class DimensionDef:
    key: str
    label: str
    # Which metric source(s) this dimension can be grouped by. Some
    # dimensions (e.g. outcome_status) only make sense for outcome metrics.
    sources: tuple
    definition: str


METRICS: dict[str, MetricDef] = {
    "ai_spend": MetricDef(
        key="ai_spend", label="AI Spend", source="transaction", unit="usd",
        definition=(
            "Sum of TokenTransaction.cost_usd for AI calls, excluding "
            "VOICE_GUARD_PRUNE rows (calls pruned before they became a "
            "real AI request). Matches api/routes_dashboard.py's "
            "IS_AI_CALL convention (get_dashboard_changes, get_top_models, "
            "get_business_impact). project_activity_reporting()'s SUM is "
            "still unfiltered by default (unchanged for its other callers "
            "-- core/budget.py's live spend recompute, trial reporting, "
            "insights), but every Ask CostPilot call site now opts in via "
            "its exclude_prune_only_rows=True parameter, so the two "
            "reporting paths agree for Ask CostPilot specifically. VOICE_"
            "GUARD_PRUNE rows are always cost_usd=0.0 (routes_voice.py), "
            "so this was never a spend-dollar discrepancy in practice --"
            "request counts and token sums were the actual mismatch."
        ),
        provenance="TokenTransaction.cost_usd",
    ),
    "ai_requests": MetricDef(
        key="ai_requests", label="AI Requests", source="transaction", unit="count",
        definition="Count of TokenTransaction rows, same IS_AI_CALL exclusion as ai_spend.",
        provenance="COUNT(TokenTransaction.id)",
    ),
    "input_tokens": MetricDef(
        key="input_tokens", label="Input Tokens", source="transaction", unit="tokens",
        definition="Sum of TokenTransaction.input_tokens, same IS_AI_CALL exclusion as ai_spend.",
        provenance="TokenTransaction.input_tokens",
    ),
    "output_tokens": MetricDef(
        key="output_tokens", label="Output Tokens", source="transaction", unit="tokens",
        definition="Sum of TokenTransaction.output_tokens, same IS_AI_CALL exclusion as ai_spend.",
        provenance="TokenTransaction.output_tokens",
    ),
    "total_tokens": MetricDef(
        key="total_tokens", label="Total Tokens", source="transaction", unit="tokens",
        definition="input_tokens + output_tokens for the same matched rows.",
        provenance="TokenTransaction.input_tokens + TokenTransaction.output_tokens",
    ),
    "work_items_touched": MetricDef(
        key="work_items_touched", label="Work Items Touched", source="transaction", unit="count",
        definition="Count of distinct WorkItem ids with at least one matched AI call.",
        provenance="COUNT(DISTINCT TokenTransaction.work_item_id)",
    ),
    "accounts_touched": MetricDef(
        key="accounts_touched", label="Accounts Touched", source="transaction", unit="count",
        definition="Count of distinct WorkAccount ids reached via a matched AI call's WorkItem.",
        provenance="COUNT(DISTINCT WorkItem.account_id)",
    ),
    "active_agents": MetricDef(
        key="active_agents", label="Active Agents", source="transaction", unit="count",
        definition="Count of distinct RegisteredAgent ids with at least one matched AI call in the timeframe.",
        provenance="COUNT(DISTINCT TokenTransaction.agent_id)",
    ),
    "people_touched": MetricDef(
        key="people_touched", label="People", source="transaction", unit="count",
        definition=(
            "Count of distinct identities from the 'person' dimension "
            "(WorkUser, else actor fields), excluding rows with no "
            "identity at all. Matches project_activity_reporting()'s "
            "summary.people_count (real_person_count), modulo that "
            "function's separate '__simulator__' bucket, which the "
            "'person' dimension doesn't have -- same documented "
            "simplification as that dimension already carries."
        ),
        provenance="COUNT(DISTINCT person dimension key), excluding '__unknown__'",
    ),
    "tokens_saved_count": MetricDef(
        key="tokens_saved_count", label="Tokens Saved", source="transaction", unit="tokens",
        definition=(
            "Sum of TokenTransaction.tokens_saved (raw token count, not a "
            "dollar value -- see 'savings' for that). Matches "
            "project_activity_reporting()'s per-row tokens_saved field."
        ),
        provenance="TokenTransaction.tokens_saved",
    ),
    "simulation_count": MetricDef(
        key="simulation_count", label="Simulated Requests", source="transaction", unit="count",
        definition=(
            "Count of matched calls flagged as simulator traffic: "
            "TokenTransaction.is_simulation = true, OR (no WorkItem/"
            "WorkUser/actor identity at all but a RegisteredAgent is set) "
            "-- the same is_simulator_traffic fallback heuristic "
            "project_activity_reporting() uses."
        ),
        provenance="TokenTransaction.is_simulation, with identity-based fallback",
    ),
    "live_count": MetricDef(
        key="live_count", label="Live Requests", source="transaction", unit="count",
        definition="ai_requests minus simulation_count for the same matched rows.",
        provenance="COUNT(TokenTransaction.id) - simulation_count",
    ),
    "won_count": MetricDef(
        key="won_count", label="Opportunities Won", source="outcome", unit="count",
        definition=(
            "Count of WorkItems with context_type='opportunity' and "
            "WorkItemOutcome.outcome_success = true. Matches "
            "api/routes_dashboard.py's get_business_impact()."
        ),
        provenance="WorkItemOutcome.outcome_success via WorkItem.context_type='opportunity'",
    ),
    "lost_count": MetricDef(
        key="lost_count", label="Opportunities Lost", source="outcome", unit="count",
        definition="Count of opportunity WorkItems with outcome_success = false and is_closed = true.",
        provenance="WorkItemOutcome.outcome_success, is_closed via WorkItem.context_type='opportunity'",
    ),
    "open_count": MetricDef(
        key="open_count", label="Opportunities Open", source="outcome", unit="count",
        definition="Count of opportunity WorkItems with is_closed = false.",
        provenance="WorkItemOutcome.is_closed via WorkItem.context_type='opportunity'",
    ),
    "won_value": MetricDef(
        key="won_value", label="Closed Won Value", source="outcome", unit="usd",
        definition="Sum of WorkItemOutcome.outcome_value for won opportunity WorkItems.",
        provenance="WorkItemOutcome.outcome_value where outcome_success = true",
        sample_size_metric="won_count",
    ),
    "pipeline_value": MetricDef(
        key="pipeline_value", label="Pipeline Value", source="outcome", unit="usd",
        definition="Sum of WorkItemOutcome.outcome_value for open opportunity WorkItems.",
        provenance="WorkItemOutcome.outcome_value where is_closed = false",
        sample_size_metric="open_count",
    ),
    "support_cases_total": MetricDef(
        key="support_cases_total", label="Support Cases", source="outcome", unit="count",
        definition="Count of WorkItems with context_type in (case, ticket, incident).",
        provenance="WorkItem.context_type IN (case, ticket, incident)",
    ),
    "support_cases_resolved": MetricDef(
        key="support_cases_resolved", label="Support Cases Resolved", source="outcome", unit="count",
        definition="Count of support WorkItems with WorkItemOutcome.is_closed = true.",
        provenance="WorkItemOutcome.is_closed where WorkItem.context_type IN (case, ticket, incident)",
    ),
    "successful_outcomes": MetricDef(
        key="successful_outcomes", label="Successful Outcomes", source="outcome", unit="count",
        definition=(
            "Count of WorkItems with WorkItemOutcome.outcome_success = true, "
            "for ANY context_type -- not restricted to opportunities. The "
            "generic form of won_count, for work types with no won/lost "
            "language (a resolved case, a shipped feature, a processed "
            "invoice, a hired candidate)."
        ),
        provenance="WorkItemOutcome.outcome_success = true, any WorkItem.context_type",
    ),
    "unsuccessful_outcomes": MetricDef(
        key="unsuccessful_outcomes", label="Unsuccessful Outcomes", source="outcome", unit="count",
        definition="Count of closed WorkItems with WorkItemOutcome.outcome_success = false, for ANY context_type. The generic form of lost_count.",
        provenance="WorkItemOutcome.outcome_success = false, is_closed = true, any WorkItem.context_type",
    ),
    "open_outcomes": MetricDef(
        key="open_outcomes", label="Open Work Items", source="outcome", unit="count",
        definition="Count of WorkItems with WorkItemOutcome.is_closed = false, for ANY context_type. The generic form of open_count.",
        provenance="WorkItemOutcome.is_closed = false, any WorkItem.context_type",
    ),
    "successful_outcome_value": MetricDef(
        key="successful_outcome_value", label="Successful Outcome Value", source="outcome", unit="usd",
        definition="Sum of WorkItemOutcome.outcome_value for successful WorkItems, for ANY context_type. The generic form of won_value.",
        provenance="WorkItemOutcome.outcome_value where outcome_success = true, any WorkItem.context_type",
        sample_size_metric="successful_outcomes",
    ),
    "outcomes_with_data": MetricDef(
        key="outcomes_with_data", label="Work Items With Known Outcome", source="outcome", unit="count",
        definition="Count of WorkItems that have any WorkItemOutcome row at all (synced from a source system), regardless of status -- the denominator for outcome coverage.",
        provenance="COUNT(WorkItemOutcome.work_item_id)",
    ),
    "savings": MetricDef(
        key="savings", label="Total Savings", source="transaction", unit="usd",
        definition=(
            "pruning_savings + downgrade_savings. Same rates and method as "
            "api/routes_reports.py's savings_report() (GET /api/reports/savings), "
            "reimplemented as a SQL aggregate here instead of that endpoint's "
            "Python-loop sum over every matching row. An estimate against a "
            "counterfactual (what the same tokens would have cost at flagship "
            "rates), not a stored ledger value."
        ),
        provenance="TokenTransaction.tokens_saved, input_tokens, output_tokens, model_tier, was_pruned",
    ),
    "pruning_savings": MetricDef(
        key="pruning_savings", label="Pruning Savings", source="transaction", unit="usd",
        definition="Tokens removed by pruning, priced at the flagship-tier input rate.",
        provenance="TokenTransaction.tokens_saved where was_pruned = true",
    ),
    "downgrade_savings": MetricDef(
        key="downgrade_savings", label="Model Downgrade Savings", source="transaction", unit="usd",
        definition="For economy-tier (Scout/Analyst/micro) calls, the delta between actual cost and what the same tokens would have cost at flagship rates.",
        provenance="TokenTransaction.input_tokens, output_tokens where model_tier is economy-tier",
    ),
}

# Requested by the spec but not backed by any real column yet. Listed
# explicitly (not silently omitted) so a caller asking for one gets an
# honest "not yet computable" instead of a KeyError or a guess.
NOT_YET_COMPUTABLE: dict[str, str] = {
    "average_resolution_time": "WorkItemOutcome has no case-open timestamp distinct from outcome_date; can't compute a duration yet.",
    "sla_met_rate": "No SLA target field exists on WorkItem or WorkItemOutcome yet.",
}

DIMENSIONS: dict[str, DimensionDef] = {
    "account": DimensionDef(
        key="account", label="Account", sources=("transaction", "outcome"),
        definition="WorkAccount.name (via WorkItem.account_id), coalesced to 'Unassigned account' when absent.",
    ),
    "department": DimensionDef(
        key="department", label="Department", sources=("transaction",),
        definition=(
            "TokenTransaction.charged_org_unit_name when set (non-blank), "
            "else TokenTransaction.department with its workspace prefix "
            "stripped (segment after the last ':') -- matches "
            "project_activity_reporting()'s organizational_unit_breakdown "
            "fallback exactly, including its merge of prefixed and "
            "unprefixed rows for the same department name."
        ),
    ),
    "agent": DimensionDef(
        key="agent", label="Agent", sources=("transaction",),
        definition="RegisteredAgent.name via TokenTransaction.agent_id.",
    ),
    "platform": DimensionDef(
        key="platform", label="Source Platform", sources=("transaction",),
        definition="TokenTransaction.source_platform (Salesforce, ServiceNow, etc).",
    ),
    "model": DimensionDef(
        key="model", label="Model", sources=("transaction",),
        definition="TokenTransaction.model_name, falling back to model_tier when model_name is unset.",
    ),
    "person": DimensionDef(
        key="person", label="Person", sources=("transaction",),
        definition=(
            "WorkUser.external_id/.name (via TokenTransaction.work_user_id), "
            "falling back to TokenTransaction.actor_external_id/.actor_name "
            "when there's no linked WorkUser row. Simpler than "
            "project_activity_reporting()'s people_breakdown, which also "
            "has a simulator-traffic fallback bucket -- reconciling that "
            "is a Milestone 4 follow-up, same precedent as the "
            "'department' dimension above."
        ),
    ),
    "provider": DimensionDef(
        key="provider", label="Provider", sources=("transaction",),
        definition=(
            "The AI vendor (Anthropic/OpenAI/Google/...) resolved from "
            "TokenTransaction.model_name via core.model_provider -- not a "
            "stored column, so this is computed by first grouping by the "
            "'model' dimension, then re-aggregating those buckets by "
            "provider (core.metrics_query._provider_breakdown_rows). Only "
            "supported as the sole requested dimension for a query, same "
            "as project_activity_reporting()'s provider_breakdown."
        ),
    ),
    "outcome_status": DimensionDef(
        key="outcome_status", label="Outcome Status", sources=("outcome",),
        definition="Derived: 'won' | 'lost' | 'open' from WorkItemOutcome.outcome_success/is_closed.",
    ),
}


def metric_or_none(key: str) -> Optional[MetricDef]:
    return METRICS.get(key)


def dimension_or_none(key: str) -> Optional[DimensionDef]:
    return DIMENSIONS.get(key)
