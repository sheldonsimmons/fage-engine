"""
core/metrics_query.py — query_metrics: the general analytical query engine
backing the semantic metrics layer (core/metrics_catalog.py).

Two metric sources, two SQL queries, merged in Python:

- "transaction" metrics aggregate TokenTransaction directly (one row per
  AI call) -- ai_spend, ai_requests, tokens, etc.
- "outcome" metrics aggregate WorkItemOutcome (one row per WorkItem's
  current state) -- won_count, pipeline_value, etc.

These cannot share one SQL query when both are requested: a WorkItem with
5 linked TokenTransactions would make a naive SUM(CASE WHEN won ...) over
the joined transaction rows count that single "won" outcome 5 times. So
each source is aggregated in its own GROUP BY query (both still fully
SQL-side -- no raw row is loaded into Python, only the small number of
already-grouped result rows), and the two grouped result sets are merged
by dimension key in Python. This mirrors the existing precedent in
api/routes_work_items.py's provider_breakdown, which is itself derived by
re-summing model_breakdown's small grouped buckets rather than raw rows.

v1 scope, documented rather than silently assumed:
- Only metrics/dimensions actually used by an existing caller are
  implemented (see core/metrics_catalog.py). Unknown keys are reported in
  MetricsResult.errors, never silently dropped or guessed.
- The transaction-source query only sees WorkItems reachable via a
  TokenTransaction (matches project_activity_reporting()'s existing base
  query). An account with WorkItems but zero linked AI activity will not
  appear when only transaction-source metrics are requested. Purely
  outcome-only, company-wide questions with no activity metric are still
  best served by api/routes_dashboard.py's get_business_impact() /
  ask_costpilot_tools.run_get_account_outcomes() in this milestone.
- outcome metrics are NOT time-windowed by `timeframe` -- they reflect
  current WorkItemOutcome state, matching get_business_impact()'s existing
  behavior (there is no per-outcome event timestamp comparable to
  TokenTransaction.timestamp; WorkItemOutcomeEvent has history but this
  milestone doesn't query it).
- Grouped result cardinality is capped at MAX_GROUPS per source query
  before the Python merge (not a raw-row cap) -- real dimension
  cardinality (accounts, departments, models) is bounded in practice.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from core.analytics_periods import AnalyticalPeriod, comparison_plan, resolve_primary_period
from core.metrics_catalog import DIMENSIONS, METRICS, NOT_YET_COMPUTABLE
from core.workspace_scope import workspace_filter
from database.models import RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome, WorkUser

MAX_GROUPS = 500

# Rows pruned by the budget guard before ever becoming a real AI call --
# excluded from every activity metric. Matches api/routes_dashboard.py's
# IS_AI_CALL convention (see core/metrics_catalog.py's ai_spend definition
# for why this differs from project_activity_reporting()'s older SUM).
#
# NULL-safe: plain `!= "VOICE_GUARD_PRUNE"` evaluates to SQL NULL (not
# true) for any row where routing_reason was never set, silently excluding
# it from every metric built on this filter -- caught when Cost per
# Outcome (Phase B) returned $0 despite real spend existing, traced to
# 99.6% of a real workspace's TokenTransaction rows having a NULL
# routing_reason. or_(...is_(None), ...) treats "never set" the same as
# "not pruned", which is what every caller actually wants.
IS_AI_CALL = or_(TokenTransaction.routing_reason.is_(None), TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE")

# Matches project_activity_reporting()'s is_sim_condition exactly: an
# explicit is_simulation flag, OR a RegisteredAgent-attributed call with no
# real identity at all (no WorkItem, no WorkUser, no actor_external_id, no
# origin_record_id) -- the fallback heuristic for older simulated traffic
# that predates the is_simulation column. Requires WorkItem/RegisteredAgent/
# WorkUser already outer-joined by the caller (_run_activity_query).
_IS_SIMULATOR_TRAFFIC = or_(
    TokenTransaction.is_simulation.is_(True),
    and_(
        RegisteredAgent.id.isnot(None),
        WorkItem.id.is_(None),
        WorkUser.id.is_(None),
        TokenTransaction.actor_external_id.is_(None),
        TokenTransaction.origin_record_id.is_(None),
    ),
)


@dataclass
class MetricsResult:
    rows: list
    metrics: list
    dimensions: list
    metric_definitions: dict
    scope: dict
    filters_applied: dict
    timeframe: Optional[dict]
    comparison: Optional[dict] = None
    errors: list = field(default_factory=list)
    unsupported_metrics: dict = field(default_factory=dict)
    freshness: Optional[dict] = None


def _resolve_account(db: Session, workspace_id: Optional[str], name: str):
    """Fuzzy-match an account name to a single WorkAccount, scoped to the
    workspace. Returns (account_or_None, error_or_None)."""
    q = db.query(WorkAccount).filter(WorkAccount.name.ilike(f"%{name}%"))
    scope = workspace_filter(WorkAccount, workspace_id)
    if scope is not None:
        q = q.filter(scope)
    matches = q.limit(6).all()
    if not matches:
        return None, {"code": "account_not_found", "message": f"No account matching '{name}' was found."}
    if len(matches) > 1:
        return None, {
            "code": "account_ambiguous",
            "message": f"More than one account matches '{name}': {', '.join(a.name for a in matches)}.",
            "candidates": [a.name for a in matches],
        }
    return matches[0], None


def _department_clause(value: str):
    return or_(TokenTransaction.department.ilike(f"%:{value}"), TokenTransaction.department.ilike(value))


def _outcome_status_clause(value: str):
    v = (value or "").lower()
    if v == "won":
        return WorkItemOutcome.outcome_success.is_(True)
    if v == "lost":
        return and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    if v == "open":
        return WorkItemOutcome.is_closed.is_(False)
    # Generic (any context_type) forms -- the successful_outcomes/
    # unsuccessful_outcomes analog of won/lost, for work types with no
    # won/lost language.
    if v == "successful":
        return WorkItemOutcome.outcome_success.is_(True)
    if v == "unsuccessful":
        return and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    if v == "any":
        return WorkItemOutcome.work_item_id.isnot(None)
    return None


def _dimension_expr(dim_key: str):
    """Returns (key_expr, label_expr) for GROUP BY / SELECT."""
    if dim_key == "account":
        return (
            func.coalesce(WorkAccount.external_id, "__unassigned__"),
            func.coalesce(WorkAccount.name, "Unassigned account"),
        )
    if dim_key == "department":
        # Matches project_activity_reporting()'s organizational_unit_breakdown:
        # prefer charged_org_unit_name when a non-blank value was recorded
        # (e.g. certain ServiceNow-sourced traffic), falling back to
        # department otherwise. Confirmed via production data that this
        # affects ~1% of TokenTransaction rows -- not blank-safe to skip.
        # NOTE: this raw form does NOT strip a workspace prefix from the
        # department-fallback branch (see _department_breakdown_rows for
        # that split); it's used as-is only when "department" is combined
        # with another dimension in one query (no current caller does
        # this -- _run_activity_query special-cases the sole-department
        # case to the split-aware path).
        charged = func.nullif(func.trim(func.coalesce(TokenTransaction.charged_org_unit_name, "")), "")
        expr = func.coalesce(charged, TokenTransaction.department, "Unassigned")
        return expr, expr
    if dim_key == "_charged_org_unit_raw":
        expr = TokenTransaction.charged_org_unit_name
        return expr, expr
    if dim_key == "_department_col_raw":
        expr = TokenTransaction.department
        return expr, expr
    if dim_key == "agent":
        return (
            func.coalesce(RegisteredAgent.id, -1),
            func.coalesce(RegisteredAgent.name, "Unknown agent"),
        )
    if dim_key == "platform":
        expr = func.coalesce(TokenTransaction.source_platform, "Unknown platform")
        return expr, expr
    if dim_key == "model":
        expr = func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model")
        return expr, expr
    if dim_key == "person":
        # Simpler than project_activity_reporting()'s people_breakdown,
        # which also has a "__simulator__"/"Simulator User" fallback
        # bucket for simulated traffic with no real user identity --
        # omitted here (same documented-simplification precedent as the
        # "department" dimension above); reconciling that is a Milestone
        # 4 follow-up, not a blocker for the metrics this dimension
        # already supports correctly.
        return (
            func.coalesce(WorkUser.external_id, TokenTransaction.actor_external_id, "__unknown__"),
            func.coalesce(WorkUser.name, TokenTransaction.actor_name, "Unknown user"),
        )
    if dim_key == "outcome_status":
        expr = case(
            (WorkItemOutcome.outcome_success.is_(True), "won"),
            (and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True)), "lost"),
            (WorkItemOutcome.is_closed.is_(False), "open"),
            else_="unknown",
        )
        return expr, expr
    raise ValueError(f"unknown dimension: {dim_key}")


def _activity_metric_expr(metric_key: str):
    if metric_key == "ai_spend":
        return func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0)
    if metric_key == "ai_requests":
        return func.count(TokenTransaction.id)
    if metric_key == "input_tokens":
        return func.coalesce(func.sum(TokenTransaction.input_tokens), 0)
    if metric_key == "output_tokens":
        return func.coalesce(func.sum(TokenTransaction.output_tokens), 0)
    if metric_key == "total_tokens":
        return func.coalesce(func.sum(TokenTransaction.input_tokens + TokenTransaction.output_tokens), 0)
    if metric_key == "work_items_touched":
        return func.count(func.distinct(TokenTransaction.work_item_id))
    if metric_key == "accounts_touched":
        return func.count(func.distinct(WorkItem.account_id))
    if metric_key == "active_agents":
        return func.count(func.distinct(TokenTransaction.agent_id))
    if metric_key == "people_touched":
        person_key, _ = _dimension_expr("person")
        return func.count(func.distinct(case((person_key != "__unknown__", person_key), else_=None)))
    if metric_key in ("savings", "pruning_savings", "downgrade_savings"):
        return _savings_expr(metric_key)
    if metric_key == "tokens_saved_count":
        return func.coalesce(func.sum(TokenTransaction.tokens_saved), 0)
    if metric_key == "simulation_count":
        return func.coalesce(func.sum(case((_IS_SIMULATOR_TRAFFIC, 1), else_=0)), 0)
    if metric_key == "live_count":
        return func.count(TokenTransaction.id) - func.coalesce(
            func.sum(case((_IS_SIMULATOR_TRAFFIC, 1), else_=0)), 0
        )
    raise ValueError(f"unknown activity metric: {metric_key}")


# Same per-token rates api/routes_reports.py's savings_report() already
# uses (Haiku 4.5 for micro/economy tiers, Sonnet 4.6 for flagship) --
# duplicated here rather than imported because routes_reports.py's
# constants aren't currently in a shared module; if that endpoint is ever
# migrated onto this metric (see catalog docstring), these become the one
# copy instead of two.
_MICRO_INPUT_COST = 0.80 / 1_000_000
_MICRO_OUTPUT_COST = 4.00 / 1_000_000
_FLAGSHIP_INPUT_COST = 3.00 / 1_000_000
_FLAGSHIP_OUTPUT_COST = 15.00 / 1_000_000
_ECONOMY_TIERS = ("Scout", "Analyst", "micro")


def _savings_expr(metric_key: str):
    """
    SQL-side version of api/routes_reports.py's savings_report(), which
    still loads every matching TokenTransaction into Python and sums in a
    loop -- the exact O(n) pattern this session already moved every other
    reporting hotspot off of. Same two components, same rates:
    pruning_savings = tokens pruned x the flagship input rate (what those
    tokens would have cost if not stripped); downgrade_savings = for each
    economy-tier call, the delta between what it actually cost and what
    the same tokens would have cost at flagship rates.
    """
    is_micro = TokenTransaction.model_tier.in_(_ECONOMY_TIERS)
    pruning = func.coalesce(
        func.sum(case((TokenTransaction.was_pruned.is_(True), TokenTransaction.tokens_saved), else_=0)), 0
    ) * _FLAGSHIP_INPUT_COST
    downgrade = func.coalesce(
        func.sum(case(
            (is_micro, (
                (TokenTransaction.input_tokens + TokenTransaction.tokens_saved) * (_FLAGSHIP_INPUT_COST - _MICRO_INPUT_COST)
                + TokenTransaction.output_tokens * (_FLAGSHIP_OUTPUT_COST - _MICRO_OUTPUT_COST)
            )), else_=0.0,
        )), 0.0,
    )
    if metric_key == "pruning_savings":
        return pruning
    if metric_key == "downgrade_savings":
        return downgrade
    return pruning + downgrade


def _outcome_metric_expr(metric_key: str):
    is_opp = WorkItem.context_type == "opportunity"
    is_support = WorkItem.context_type.in_(("case", "ticket", "incident"))
    is_won = and_(is_opp, WorkItemOutcome.outcome_success.is_(True))
    is_lost = and_(is_opp, WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    is_open_opp = and_(is_opp, WorkItemOutcome.is_closed.is_(False))
    value = func.coalesce(WorkItemOutcome.outcome_value, 0.0)

    # Generic (any context_type) forms, added for non-Salesforce-Opportunity
    # work types -- recruiting, engineering, finance, etc. have no won/lost
    # language, but outcome_success/is_closed already mean the same thing
    # regardless of context_type, so these simply drop the is_opp gate.
    is_successful = WorkItemOutcome.outcome_success.is_(True)
    is_unsuccessful = and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    is_open_any = WorkItemOutcome.is_closed.is_(False)

    if metric_key == "won_count":
        return func.coalesce(func.sum(case((is_won, 1), else_=0)), 0)
    if metric_key == "lost_count":
        return func.coalesce(func.sum(case((is_lost, 1), else_=0)), 0)
    if metric_key == "open_count":
        return func.coalesce(func.sum(case((is_open_opp, 1), else_=0)), 0)
    if metric_key == "won_value":
        return func.coalesce(func.sum(case((is_won, value), else_=0.0)), 0.0)
    if metric_key == "pipeline_value":
        return func.coalesce(func.sum(case((is_open_opp, value), else_=0.0)), 0.0)
    if metric_key == "support_cases_total":
        return func.coalesce(func.sum(case((is_support, 1), else_=0)), 0)
    if metric_key == "support_cases_resolved":
        return func.coalesce(
            func.sum(case((and_(is_support, WorkItemOutcome.is_closed.is_(True)), 1), else_=0)), 0
        )
    if metric_key == "successful_outcomes":
        return func.coalesce(func.sum(case((is_successful, 1), else_=0)), 0)
    if metric_key == "unsuccessful_outcomes":
        return func.coalesce(func.sum(case((is_unsuccessful, 1), else_=0)), 0)
    if metric_key == "open_outcomes":
        return func.coalesce(func.sum(case((is_open_any, 1), else_=0)), 0)
    if metric_key == "successful_outcome_value":
        return func.coalesce(func.sum(case((is_successful, value), else_=0.0)), 0.0)
    if metric_key == "outcomes_with_data":
        return func.coalesce(func.count(WorkItemOutcome.work_item_id), 0)
    raise ValueError(f"unknown outcome metric: {metric_key}")


def _department_breakdown_rows(
    db: Session, workspace_id: Optional[str], metric_keys: list,
    filters: dict, start: Optional[datetime], end: Optional[datetime], account,
) -> list:
    """
    Exactly replicates project_activity_reporting()'s label derivation:
    `charged_org_unit_name.strip() or department.split(":")[-1].strip() or
    "Unassigned"`. Critically, the colon-split applies ONLY to the
    department-fallback branch -- a charged_org_unit_name value is used
    verbatim even if it happens to contain a colon (confirmed via a real
    production row whose charged_org_unit_name was itself workspace-
    prefixed and must NOT be split). Since that decision depends on which
    of the two raw columns a given row's label actually came from, this
    groups by the (charged_org_unit_name, department) RAW PAIR in SQL
    first (so provenance survives aggregation), derives each bucket's
    final label in Python using the same precedence, then merges buckets
    that land on the same final label (e.g. "WS-1:Support" and "Support").
    """
    raw_rows = _run_activity_query(
        db, workspace_id, metric_keys, ["_charged_org_unit_raw", "_department_col_raw"],
        filters, start, end, account,
    )
    grouped: dict[str, dict] = {}
    for row in raw_rows:
        charged_raw, department_raw = (row["dim_labels"] + [None, None])[:2]
        charged = (charged_raw or "").strip()
        label = charged or (department_raw or "").split(":")[-1].strip() or "Unassigned"
        bucket = grouped.setdefault(label, {"dim_key": (label,), "dim_labels": [label], "values": {}})
        for metric_key, value in row["values"].items():
            bucket["values"][metric_key] = bucket["values"].get(metric_key, 0) + (value or 0)
    return list(grouped.values())


def _provider_breakdown_rows(
    db: Session, workspace_id: Optional[str], metric_keys: list,
    filters: dict, start: Optional[datetime], end: Optional[datetime], account,
) -> list:
    """
    "provider" (Anthropic/OpenAI/...) isn't a stored column -- only
    model_name is -- so it can't be a SQL GROUP BY key the way the other
    dimensions are. Same approach project_activity_reporting() already
    uses: resolve each DISTINCT model name to a provider once (via
    core.model_provider, a handful of calls, not one per transaction),
    then re-sum the already-aggregated per-model buckets by provider.
    Kept as a dedicated pre-aggregation step rather than forcing
    _dimension_expr() into a fake SQL expression for a value that isn't
    actually a column.
    """
    from core.model_provider import load_provider_registry, resolve_provider

    model_rows = _run_activity_query(db, workspace_id, metric_keys, ["model"], filters, start, end, account)
    registry = load_provider_registry(db)
    grouped: dict[str, dict] = {}
    for row in model_rows:
        model_name = row["dim_labels"][0] if row["dim_labels"] else None
        provider_name = resolve_provider(model_name, registry=registry)
        bucket = grouped.setdefault(provider_name, {"dim_key": (provider_name,), "dim_labels": [provider_name], "values": {}})
        for metric_key, value in row["values"].items():
            bucket["values"][metric_key] = bucket["values"].get(metric_key, 0) + (value or 0)
    return list(grouped.values())


def _run_activity_query(
    db: Session, workspace_id: Optional[str], metric_keys: list, dim_keys: list,
    filters: dict, start: Optional[datetime], end: Optional[datetime], account,
) -> list:
    if dim_keys == ["provider"]:
        return _provider_breakdown_rows(db, workspace_id, metric_keys, filters, start, end, account)
    if dim_keys == ["department"]:
        return _department_breakdown_rows(db, workspace_id, metric_keys, filters, start, end, account)
    q = (
        db.query(TokenTransaction.id)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(WorkAccount, WorkItem.account_id == WorkAccount.id)
        .outerjoin(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .filter(IS_AI_CALL)
    )
    if start is not None:
        q = q.filter(TokenTransaction.timestamp >= start)
    if end is not None:
        q = q.filter(TokenTransaction.timestamp < end)
    if workspace_id:
        q = q.filter(or_(
            TokenTransaction.workspace_id == workspace_id,
            and_(TokenTransaction.workspace_id.is_(None), WorkItem.workspace_id == workspace_id),
        ))
    if account is not None:
        q = q.filter(WorkItem.account_id == account.id)
    if filters.get("department"):
        q = q.filter(_department_clause(filters["department"]))
    if filters.get("charged_unit"):
        # Exact match against the same fallback chain the "department"
        # dimension groups by (charged_org_unit_name, else department) --
        # matches project_activity_reporting()'s charged_unit filter,
        # including its documented multi-colon department-string caveat
        # (see that function's own comment for why an exact single SQL
        # expression can't safely reproduce every edge of the Python
        # original; real data never has more than one colon in
        # `department`, so this is the same accepted tradeoff).
        charged_unit = filters["charged_unit"]
        q = q.filter(or_(
            func.trim(func.coalesce(TokenTransaction.charged_org_unit_name, "")) == charged_unit,
            TokenTransaction.department == charged_unit,
            TokenTransaction.department.like(f"%:{charged_unit}"),
        ))
    if filters.get("provider"):
        # Not a stored column -- resolve to the set of model names that
        # map to this provider (same core.model_provider registry the
        # "provider" dimension re-aggregates through), then filter on
        # model_name membership. Small, cached-per-call lookup, not one
        # resolve_provider() call per transaction.
        from core.model_provider import load_provider_registry, resolve_provider

        registry = load_provider_registry(db)
        target = filters["provider"].strip().lower()
        distinct_models = [
            m for (m,) in db.query(TokenTransaction.model_name).filter(
                TokenTransaction.model_name.isnot(None)
            ).distinct()
        ]
        matching_models = [
            m for m in distinct_models if resolve_provider(m, registry=registry).lower() == target
        ]
        q = q.filter(TokenTransaction.model_name.in_(matching_models)) if matching_models else q.filter(False)
    if filters.get("agent"):
        q = q.filter(RegisteredAgent.name.ilike(f"%{filters['agent']}%"))
    if filters.get("agent_id") is not None:
        q = q.filter(TokenTransaction.agent_id == filters["agent_id"])
    if filters.get("platform"):
        q = q.filter(TokenTransaction.source_platform.ilike(filters["platform"]))
    if filters.get("model"):
        q = q.filter(or_(
            TokenTransaction.model_name.ilike(filters["model"]),
            TokenTransaction.model_tier.ilike(filters["model"]),
        ))
    # Legacy values (won/lost/open) keep the opportunity-only gate for
    # backward compatibility with existing callers; the generic values
    # (successful/unsuccessful/any) apply to whatever WorkItem the caller's
    # own context_type filter (or none) already scopes to -- no implicit
    # opportunity restriction, since the whole point of adding them was to
    # answer this for non-opportunity work types too.
    if filters.get("outcome_status"):
        q = q.outerjoin(WorkItemOutcome, WorkItem.id == WorkItemOutcome.work_item_id)
        clause = _outcome_status_clause(filters["outcome_status"])
        if clause is not None:
            if filters["outcome_status"].lower() in ("won", "lost", "open"):
                q = q.filter(WorkItem.context_type == "opportunity", clause)
            else:
                q = q.filter(clause)
    if filters.get("context_type"):
        q = q.filter(WorkItem.context_type == filters["context_type"])

    dim_exprs = [_dimension_expr(d) for d in dim_keys]
    key_exprs = [e[0] for e in dim_exprs]
    label_exprs = [e[1] for e in dim_exprs]
    metric_exprs = [_activity_metric_expr(m) for m in metric_keys]

    select_cols = key_exprs + label_exprs + metric_exprs
    q = q.with_entities(*select_cols)
    if key_exprs:
        q = q.group_by(*key_exprs, *label_exprs)
        primary_metric_idx = len(key_exprs) + len(label_exprs)
        q = q.order_by(select_cols[primary_metric_idx].desc()).limit(MAX_GROUPS)

    rows = q.all()
    n_dims = len(dim_keys)
    out = []
    for row in rows:
        row = tuple(row)
        keys = row[:n_dims]
        labels = row[n_dims:2 * n_dims]
        values = row[2 * n_dims:]
        out.append({
            "dim_key": tuple(str(k) for k in keys),
            "dim_labels": list(labels),
            "values": dict(zip(metric_keys, values)),
        })
    return out


def _run_outcome_query(
    db: Session, workspace_id: Optional[str], metric_keys: list, dim_keys: list,
    filters: dict, account,
) -> list:
    q = (
        db.query(WorkItemOutcome.id)
        .join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
        .outerjoin(WorkAccount, WorkItem.account_id == WorkAccount.id)
    )
    scope = workspace_filter(WorkItem, workspace_id)
    if scope is not None:
        q = q.filter(scope)
    if account is not None:
        q = q.filter(WorkItem.account_id == account.id)
    if filters.get("outcome_status"):
        clause = _outcome_status_clause(filters["outcome_status"])
        if clause is not None:
            q = q.filter(clause)
    if filters.get("context_type"):
        # Scopes the generic (any-context-type) outcome metrics --
        # successful_outcomes, unsuccessful_outcomes, open_outcomes,
        # successful_outcome_value, outcomes_with_data -- to one work type,
        # e.g. "case" for support or a custom context_type for recruiting/
        # engineering/finance, without needing a dedicated metric per type
        # the way won_count/support_cases_total are.
        q = q.filter(WorkItem.context_type == filters["context_type"])
    if filters.get("work_item_ids") is not None:
        # WorkItemOutcome has no agent_id of its own -- a caller scoping to
        # one agent (e.g. compute_cost_per_outcome's agent_id param) passes
        # the distinct WorkItem ids that agent's TokenTransactions touched,
        # pre-computed once, rather than joining TokenTransaction in here
        # (which would duplicate WorkItemOutcome rows for any WorkItem with
        # more than one transaction and inflate every count/sum below).
        q = q.filter(WorkItem.id.in_(filters["work_item_ids"]))

    dim_exprs = [_dimension_expr(d) for d in dim_keys]
    key_exprs = [e[0] for e in dim_exprs]
    label_exprs = [e[1] for e in dim_exprs]
    metric_exprs = [_outcome_metric_expr(m) for m in metric_keys]

    select_cols = key_exprs + label_exprs + metric_exprs
    q = q.with_entities(*select_cols)
    if key_exprs:
        q = q.group_by(*key_exprs, *label_exprs)
        primary_metric_idx = len(key_exprs) + len(label_exprs)
        q = q.order_by(select_cols[primary_metric_idx].desc()).limit(MAX_GROUPS)

    rows = q.all()
    n_dims = len(dim_keys)
    out = []
    for row in rows:
        row = tuple(row)
        keys = row[:n_dims]
        labels = row[n_dims:2 * n_dims]
        values = row[2 * n_dims:]
        out.append({
            "dim_key": tuple(str(k) for k in keys),
            "dim_labels": list(labels),
            "values": dict(zip(metric_keys, values)),
        })
    return out


def _outcome_freshness(db: Session, workspace_id: Optional[str], account) -> Optional[dict]:
    """
    Oldest last_synced_at among the WorkItemOutcome rows a request could
    have drawn from -- reported as the freshness of the whole response
    (conservative: if any matched outcome is stale, the response is
    flagged stale), not per-row. Mirrors core.data_coverage's
    STALE_SYNC_THRESHOLD for what "stale" means, applied to outcome data
    instead of connector sync recency.
    """
    from core.data_coverage import STALE_SYNC_THRESHOLD

    q = db.query(func.min(WorkItemOutcome.last_synced_at)).select_from(WorkItemOutcome).join(
        WorkItem, WorkItemOutcome.work_item_id == WorkItem.id
    )
    scope = workspace_filter(WorkItem, workspace_id)
    if scope is not None:
        q = q.filter(scope)
    if account is not None:
        q = q.filter(WorkItem.account_id == account.id)
    oldest = q.scalar()
    if oldest is None:
        return None
    return {
        "last_synced_at": oldest.isoformat(),
        "stale": (datetime.utcnow() - oldest) > STALE_SYNC_THRESHOLD,
    }


def _totals_for_period(
    db: Session, workspace_id: Optional[str], activity_metrics: list, outcome_metrics: list,
    dim_keys: list, filters: dict, start: Optional[datetime], end: Optional[datetime], account,
) -> dict:
    """Runs both source queries for one time window and merges them by
    dimension key. Returns {dim_key_tuple: {"labels": [...], "values": {...}}}."""
    merged: dict = {}

    def _merge(source_rows, metric_keys):
        for r in source_rows:
            bucket = merged.setdefault(r["dim_key"], {"labels": r["dim_labels"], "values": {}})
            if not bucket["labels"] and r["dim_labels"]:
                bucket["labels"] = r["dim_labels"]
            bucket["values"].update(r["values"])

    if activity_metrics:
        _merge(_run_activity_query(db, workspace_id, activity_metrics, dim_keys, filters, start, end, account), activity_metrics)
    if outcome_metrics:
        _merge(_run_outcome_query(db, workspace_id, outcome_metrics, dim_keys, filters, account), outcome_metrics)

    # Fill zeros for any metric a dimension key didn't appear in for one
    # source but did for the other (e.g. an account with AI spend but no
    # outcome data yet) -- never leave a metric silently missing.
    all_metrics = activity_metrics + outcome_metrics
    for bucket in merged.values():
        for m in all_metrics:
            bucket["values"].setdefault(m, 0)
    return merged


def run_metrics_query(
    db: Session,
    workspace_id: Optional[str],
    metrics: list,
    dimensions: Optional[list] = None,
    filters: Optional[dict] = None,
    timeframe: Optional[dict] = None,
    compare_to: Optional[str] = None,
    sort: Optional[str] = None,
    limit: int = 20,
) -> MetricsResult:
    dimensions = dimensions or []
    filters = filters or {}
    errors = []
    unsupported = {}

    valid_metrics = []
    for m in metrics:
        if m in NOT_YET_COMPUTABLE:
            unsupported[m] = NOT_YET_COMPUTABLE[m]
        elif m not in METRICS:
            errors.append({"code": "unknown_metric", "message": f"'{m}' is not a known metric."})
        else:
            valid_metrics.append(m)

    valid_dims = []
    for d in dimensions:
        if d not in DIMENSIONS:
            errors.append({"code": "unknown_dimension", "message": f"'{d}' is not a known dimension."})
        else:
            valid_dims.append(d)

    if not valid_metrics:
        return MetricsResult(
            rows=[], metrics=metrics, dimensions=dimensions, metric_definitions={},
            scope={"workspace_id": workspace_id}, filters_applied=filters, timeframe=timeframe,
            errors=errors or [{"code": "no_valid_metrics", "message": "No requested metric could be computed."}],
            unsupported_metrics=unsupported,
        )

    account = None
    if filters.get("account"):
        account, acct_error = _resolve_account(db, workspace_id, filters["account"])
        if acct_error:
            errors.append(acct_error)
            return MetricsResult(
                rows=[], metrics=metrics, dimensions=dimensions, metric_definitions={},
                scope={"workspace_id": workspace_id}, filters_applied=filters, timeframe=timeframe,
                errors=errors, unsupported_metrics=unsupported,
            )

    activity_metrics = [m for m in valid_metrics if METRICS[m].source == "transaction"]
    outcome_metrics = [m for m in valid_metrics if METRICS[m].source == "outcome"]

    # Both source queries must GROUP BY the exact same dimension set for
    # the Python merge's dim_key tuples to line up -- a dimension that
    # doesn't exist in one source's query (e.g. "department" has no column
    # in the WorkItemOutcome-rooted query) would either crash that query or
    # silently misalign the merge, so when both metric types are
    # requested, only dimensions valid for BOTH sources are honored; the
    # rest are reported as an error rather than dropped silently.
    if activity_metrics and outcome_metrics:
        dims_for_merge = [d for d in valid_dims if set(DIMENSIONS[d].sources) >= {"transaction", "outcome"}]
        dropped = [d for d in valid_dims if d not in dims_for_merge]
        if dropped:
            errors.append({
                "code": "dimension_unsupported_for_mixed_metrics",
                "message": (
                    f"{dropped} can't be used to group both activity and outcome "
                    "metrics together in one request; only dimensions valid for "
                    "both sources (currently just 'account') are supported when "
                    "mixing metric sources. Request the metrics separately to use "
                    "that dimension."
                ),
            })
    elif activity_metrics:
        dims_for_merge = [d for d in valid_dims if "transaction" in DIMENSIONS[d].sources]
    else:
        dims_for_merge = [d for d in valid_dims if "outcome" in DIMENSIONS[d].sources]

    start = end = None
    if timeframe and timeframe.get("start") and timeframe.get("end"):
        start, end = timeframe["start"], timeframe["end"]
    elif timeframe is None:
        period = resolve_primary_period(period_key="none", days=30)
        start, end = period.start, period.end
        timeframe = {"start": start.isoformat(), "end": end.isoformat()}

    def _merge_for(s, e):
        # Both source queries are called with the SAME dims_for_merge keys
        # so dim_key tuples line up in the Python merge.
        return _totals_for_period(db, workspace_id, activity_metrics, outcome_metrics, dims_for_merge, filters, s, e, account)

    primary = _merge_for(start, end)
    sort_metric = sort if sort in valid_metrics else (valid_metrics[0] if valid_metrics else None)
    row_limit = max(1, min(int(limit or 20), 100))

    comparison_block = None
    if compare_to and start is not None and end is not None:
        try:
            primary_period = AnalyticalPeriod("custom", start, end, "UTC")
            plan = comparison_plan(primary_period, compare_to)
            prior = _merge_for(plan.comparison.start, plan.comparison.end)
        except Exception as exc:
            errors.append({"code": "comparison_unavailable", "message": str(exc)})
            prior = {}

        comparison_block = {"mode": compare_to, "rows": []}
        all_keys = set(primary.keys()) | set(prior.keys())
        for key in all_keys:
            a = primary.get(key, {"labels": [], "values": {m: 0 for m in valid_metrics}})
            b = prior.get(key, {"labels": [], "values": {m: 0 for m in valid_metrics}})
            row = {"dimensions": dict(zip(dims_for_merge, a["labels"] or b["labels"]))}
            for m in valid_metrics:
                va, vb = float(a["values"].get(m, 0) or 0), float(b["values"].get(m, 0) or 0)
                diff = va - vb
                pct = round((diff / vb) * 100, 1) if vb else None
                row[m] = {"current": va, "previous": vb, "difference": diff, "pct_difference": pct}
            comparison_block["rows"].append(row)

        # Ranked by magnitude of change on the sort metric, same convention
        # api/routes_dashboard.py's get_dashboard_changes() already uses for
        # its `changes` list -- "which department drove the increase"
        # implies an answer ordered by how much each row moved, not
        # whatever order the dimension merge happened to produce.
        if sort_metric:
            comparison_block["rows"].sort(
                key=lambda r: abs(r[sort_metric]["difference"]), reverse=True
            )
        comparison_block["rows"] = comparison_block["rows"][:row_limit]

    rows = []
    for key, bucket in primary.items():
        row = {"dimensions": dict(zip(dims_for_merge, bucket["labels"]))}
        row.update(bucket["values"])
        rows.append(row)

    if sort_metric:
        rows.sort(key=lambda r: float(r.get(sort_metric, 0) or 0), reverse=True)
    rows = rows[:row_limit]

    freshness = _outcome_freshness(db, workspace_id, account) if outcome_metrics else None

    return MetricsResult(
        rows=rows,
        metrics=valid_metrics,
        dimensions=dims_for_merge,
        metric_definitions={m: METRICS[m].__dict__ for m in valid_metrics},
        scope={"workspace_id": workspace_id, "account": account.name if account else None},
        filters_applied=filters,
        timeframe=timeframe,
        comparison=comparison_block,
        errors=errors,
        unsupported_metrics=unsupported,
        freshness=freshness,
    )


# Sample-size thresholds settled for the ROI/Business Impact investigation
# (see the approved plan): below MIN_MEANINGFUL_SAMPLE, a comparison is
# labeled "Early Signal / Insufficient Sample" rather than presented as a
# meaningful result; below MIN_EXECUTIVE_SAMPLE, it should not feed an
# executive-level claim even if shown descriptively. Configuration values,
# not permanent constants -- Phase F's Baseline model is where these become
# overridable per workspace/context_type instead of a shared default.
MIN_MEANINGFUL_SAMPLE = 30
MIN_EXECUTIVE_SAMPLE = 50


def compute_cost_per_outcome(
    db: Session,
    workspace_id: Optional[str],
    context_type: Optional[str] = None,
    account_name: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> dict:
    """
    AI investment associated with successful outcomes, divided by the count
    of successful outcomes -- "Cost per Outcome" from the ROI/Business
    Impact investigation (Phase B). Association, not causation: this is how
    much AI activity touched the work that turned out successful, not proof
    AI caused that success -- same guardrail as get_business_impact() and
    run_get_account_outcomes().

    Built entirely from existing catalog metrics (ai_spend, successful_
    outcomes, outcomes_with_data) via two run_metrics_query() calls -- one
    per metric source, per the catalog's own "never mix sources in one
    query" rule -- and a Python division, not new SQL.

    agent_id (added for the Agent Intelligence Profile) scopes both sides
    to one agent's activity: the spend side via a direct
    TokenTransaction.agent_id filter, the outcome side via the distinct
    WorkItems that agent's transactions touched (WorkItemOutcome carries
    no agent_id of its own) -- same "AI activity associated with an
    outcome" definition as the unscoped case, just narrowed to one agent's
    transactions instead of the whole workspace's.
    """
    filters: dict = {}
    if context_type:
        filters["context_type"] = context_type
    if account_name:
        filters["account"] = account_name

    touched_work_item_ids: Optional[list] = None
    if agent_id is not None:
        scope = workspace_filter(TokenTransaction, workspace_id)
        touched_query = db.query(TokenTransaction.work_item_id).filter(
            TokenTransaction.agent_id == agent_id, TokenTransaction.work_item_id.isnot(None),
        ).distinct()
        if scope is not None:
            touched_query = touched_query.filter(scope)
        touched_work_item_ids = [row[0] for row in touched_query.all()]

    # Explicit empty timeframe -- run_metrics_query() defaults to a 30-day
    # window when timeframe is None (see its "elif timeframe is None"
    # branch), but successful_outcomes/outcomes_with_data below are
    # unbounded (WorkItemOutcome is current-state, not time-windowed).
    # Dividing a 30-day spend number by an all-time outcome count silently
    # understated cost-per-outcome -- caught via a live run before this
    # shipped further (see commit history). Passing {} (falsy, but not
    # None) skips both the explicit-range and the 30-day-default branches,
    # leaving activity unbounded to match the outcome side.
    spend_result = run_metrics_query(
        db, workspace_id, metrics=["ai_spend"],
        filters={**filters, "outcome_status": "successful", **({"agent_id": agent_id} if agent_id is not None else {})},
        timeframe={},
    )
    outcome_filters = dict(filters)
    if agent_id is not None:
        outcome_filters["work_item_ids"] = touched_work_item_ids
    outcome_result = run_metrics_query(
        db, workspace_id, metrics=["successful_outcomes", "outcomes_with_data"],
        filters=outcome_filters,
    )

    ai_spend = float(spend_result.rows[0].get("ai_spend", 0.0)) if spend_result.rows else 0.0
    successful_outcomes = int(outcome_result.rows[0].get("successful_outcomes", 0)) if outcome_result.rows else 0
    outcomes_with_data = int(outcome_result.rows[0].get("outcomes_with_data", 0)) if outcome_result.rows else 0

    # 6, not 2, decimal places -- a real cost-per-outcome in the
    # fractional-cent range (typical for low-volume/early-stage accounts)
    # rounded to $0.00 at 2 decimals, the same precision bug already found
    # and fixed for the equivalent per-account and per-WorkItem ratios
    # this session.
    cost_per_outcome = round(ai_spend / successful_outcomes, 6) if successful_outcomes else None

    if successful_outcomes >= MIN_EXECUTIVE_SAMPLE:
        evidence_label = "executive_eligible"
    elif successful_outcomes >= MIN_MEANINGFUL_SAMPLE:
        evidence_label = "meaningful"
    else:
        evidence_label = "early_signal"

    return {
        "context_type": context_type,
        "account": account_name,
        "agent_id": agent_id,
        "ai_spend_on_successful_outcomes_usd": round(ai_spend, 6),
        "successful_outcomes": successful_outcomes,
        "outcomes_with_known_data": outcomes_with_data,
        "cost_per_successful_outcome_usd": cost_per_outcome,
        "sample_size": successful_outcomes,
        "evidence_label": evidence_label,
        "evidence_note": (
            "Early Signal / Insufficient Sample: fewer than 30 successful outcomes -- descriptive only."
            if evidence_label == "early_signal" else
            "Meaningful sample (30+), but below the 50+ preferred for executive-level claims."
            if evidence_label == "meaningful" else
            "Sample size supports an executive-level claim (50+ successful outcomes)."
        ),
        "association_note": (
            "This is AI activity associated with successful outcomes, not evidence AI caused them."
        ),
    }


def compute_outcome_coverage(
    db: Session,
    workspace_id: Optional[str],
    context_type: Optional[str] = None,
    account_name: Optional[str] = None,
    agent_id: Optional[int] = None,
) -> dict:
    """
    What share of AI-supported WorkItems actually have a known outcome --
    "Outcome Coverage" from the Business Impact upgrade plan. This is a
    prerequisite trust signal for every other Business Impact number: a low
    coverage percentage means Associated Business Value / Cost per Outcome
    are being computed over a small, possibly unrepresentative slice of the
    account's real work, not "no impact happened."

    Deliberately NOT two independent run_metrics_query() calls (an earlier
    version of this function was): work_items_touched (has a
    TokenTransaction) and outcomes_with_data (has a WorkItemOutcome) are
    not nested sets -- a WorkItem can have an outcome from bulk import
    without ever having any tracked AI activity. Reproduced live: one
    workspace had 190 WorkItems with outcome data but only 11 with any AI
    transaction at all, so dividing the two independent totals produced a
    physically impossible 1727% "coverage." Coverage has to be the actual
    intersection -- of the WorkItems AI has touched, how many also have a
    known outcome -- which needs one query, not two merged after the
    fact.
    """
    touched_query = (
        db.query(WorkItem.id)
        .join(TokenTransaction, TokenTransaction.work_item_id == WorkItem.id)
        .distinct()
    )
    scope = workspace_filter(WorkItem, workspace_id)
    if scope is not None:
        touched_query = touched_query.filter(scope)
    if context_type:
        touched_query = touched_query.filter(WorkItem.context_type == context_type)
    if account_name:
        account, error = _resolve_account(db, workspace_id, account_name)
        if error:
            return {
                "context_type": context_type, "account": account_name,
                "work_items_touched": 0, "outcomes_with_known_data": 0,
                "outcome_coverage_pct": None, "coverage_note": error["message"],
            }
        touched_query = touched_query.filter(WorkItem.account_id == account.id)
    if agent_id is not None:
        # The TokenTransaction join already exists above -- scoping "touched"
        # to one agent is just one more filter on it, same definition of
        # coverage narrowed to one agent's transactions.
        touched_query = touched_query.filter(TokenTransaction.agent_id == agent_id)

    touched_ids = [row[0] for row in touched_query.all()]
    work_items_touched = len(touched_ids)
    outcomes_with_data = (
        db.query(func.count(WorkItemOutcome.id))
        .filter(WorkItemOutcome.work_item_id.in_(touched_ids))
        .scalar()
        if touched_ids else 0
    )

    coverage_pct = (
        round(100.0 * outcomes_with_data / work_items_touched, 1) if work_items_touched else None
    )

    return {
        "context_type": context_type,
        "account": account_name,
        "agent_id": agent_id,
        "work_items_touched": work_items_touched,
        "outcomes_with_known_data": outcomes_with_data,
        "outcome_coverage_pct": coverage_pct,
        "coverage_note": (
            "Share of AI-supported WorkItems that have a synced outcome from the connected system. "
            "Below this coverage level, Associated Business Value and Cost per Successful Outcome "
            "reflect only the WorkItems with known outcomes, not the whole account."
        ),
    }


# Legacy two-tier labels ("micro"/"flagship", pre-dating the four-tier
# Scout/Analyst/Advisor/Strategist naming used by KnownModel/core.budget/
# core.router) alongside the newer names -- both are live in the real
# transaction ledger simultaneously (confirmed: model_tier is 100%
# populated across every row, unlike the sparser model_name field, so
# this is the reliable column to key off of). "flagship" is treated as
# tier 3 (Advisor-equivalent), matching the ~$0.03/call FLAGSHIP_AVG
# estimate used elsewhere in the codebase (routes_dashboard.py) -- a
# documented approximation, not an exact mapping.
_TIER_RANK = {
    "Scout": 1, "micro": 1,
    "Analyst": 2,
    "Advisor": 3, "flagship": 3,
    "Strategist": 4,
}


def compute_potential_savings(
    db: Session,
    workspace_id: Optional[str],
    *,
    top_agent_limit: int = 5,
    agent_id: Optional[int] = None,
) -> dict:
    """
    "Potential Savings" (Business Impact upgrade plan, Part B) -- v1:
    model right-sizing. Estimated, never mixed with Realized Savings: this
    quantifies what a ROUTINE call *could have cost* at the cheapest
    active tier, not money actually saved.

    Real data only, no invented heuristic: TokenTransaction.routing_reason
    already distinguishes ROUTINE calls (CostPilot's own router judged
    them simple) from COMPLEX/THROTTLED ones, and KnownModel already
    stores real admin-maintained $/1M-token rates per tier. A ROUTINE call
    that ran above tier 1 is exactly the doc's own example ("this agent
    could downgrade to a cheaper model"), quantified from a real pricing
    table instead of a guess.
    """
    from database.models import KnownModel

    cheapest_tier1 = (
        db.query(KnownModel)
        .filter(KnownModel.tier == 1, KnownModel.is_active.is_(True))
        .order_by((KnownModel.cost_input_per_1m + KnownModel.cost_output_per_1m).asc())
        .first()
    )
    if not cheapest_tier1:
        return {
            "potential_savings_usd": None,
            "candidate_request_count": 0,
            "top_agents": [],
            "evidence": "insufficient_data",
            "note": "No active tier-1 (Scout) model is configured to compare against.",
        }
    scout_input_rate = cheapest_tier1.cost_input_per_1m / 1_000_000
    scout_output_rate = cheapest_tier1.cost_output_per_1m / 1_000_000

    scope = workspace_filter(TokenTransaction, workspace_id)
    query = db.query(
        TokenTransaction.agent_id,
        TokenTransaction.model_tier,
        TokenTransaction.input_tokens,
        TokenTransaction.output_tokens,
        TokenTransaction.cost_usd,
    ).filter(TokenTransaction.routing_reason == "ROUTINE")
    if scope is not None:
        query = query.filter(scope)
    if agent_id is not None:
        query = query.filter(TokenTransaction.agent_id == agent_id)

    savings_by_agent: dict[Optional[int], float] = {}
    total_savings = 0.0
    candidate_count = 0
    for agent_id, model_tier, input_tokens, output_tokens, cost_usd in query.all():
        rank = _TIER_RANK.get(model_tier)
        if not rank or rank <= 1:
            continue
        hypothetical_cost = (input_tokens or 0) * scout_input_rate + (output_tokens or 0) * scout_output_rate
        delta = float(cost_usd or 0.0) - hypothetical_cost
        if delta <= 0:
            continue
        total_savings += delta
        candidate_count += 1
        savings_by_agent[agent_id] = savings_by_agent.get(agent_id, 0.0) + delta

    top_agent_ids = sorted(savings_by_agent, key=lambda k: -savings_by_agent[k])[:top_agent_limit]
    agent_names = {
        row.id: row.name
        for row in db.query(RegisteredAgent).filter(RegisteredAgent.id.in_(
            [a for a in top_agent_ids if a is not None]
        )).all()
    } if top_agent_ids else {}
    top_agents = [
        {
            "agent_id": agent_id,
            "agent_name": agent_names.get(agent_id, "Unassigned" if agent_id is None else f"Agent {agent_id}"),
            "potential_savings_usd": round(savings_by_agent[agent_id], 6),
        }
        for agent_id in top_agent_ids
    ]

    if candidate_count >= MIN_MEANINGFUL_SAMPLE:
        evidence = "estimated"
    else:
        evidence = "early_signal"

    return {
        "potential_savings_usd": round(total_savings, 6),
        "candidate_request_count": candidate_count,
        "top_agents": top_agents,
        "evidence": evidence,
        "note": (
            "Estimated: what these ROUTINE-classified requests would have cost at the cheapest "
            f"active tier-1 model ({cheapest_tier1.display_name}) versus what they actually cost. "
            "This is a potential optimization, not money already saved."
        ),
    }
