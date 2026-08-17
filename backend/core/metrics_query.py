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
from database.models import RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome

MAX_GROUPS = 500

# Rows pruned by the budget guard before ever becoming a real AI call --
# excluded from every activity metric. Matches api/routes_dashboard.py's
# IS_AI_CALL convention (see core/metrics_catalog.py's ai_spend definition
# for why this differs from project_activity_reporting()'s older SUM).
IS_AI_CALL = TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE"


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
    return None


def _dimension_expr(dim_key: str):
    """Returns (key_expr, label_expr) for GROUP BY / SELECT."""
    if dim_key == "account":
        return (
            func.coalesce(WorkAccount.external_id, "__unassigned__"),
            func.coalesce(WorkAccount.name, "Unassigned account"),
        )
    if dim_key == "department":
        expr = func.coalesce(TokenTransaction.department, "Unassigned")
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
    raise ValueError(f"unknown activity metric: {metric_key}")


def _outcome_metric_expr(metric_key: str):
    is_opp = WorkItem.context_type == "opportunity"
    is_support = WorkItem.context_type.in_(("case", "ticket", "incident"))
    is_won = and_(is_opp, WorkItemOutcome.outcome_success.is_(True))
    is_lost = and_(is_opp, WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    is_open_opp = and_(is_opp, WorkItemOutcome.is_closed.is_(False))
    value = func.coalesce(WorkItemOutcome.outcome_value, 0.0)

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
    raise ValueError(f"unknown outcome metric: {metric_key}")


def _run_activity_query(
    db: Session, workspace_id: Optional[str], metric_keys: list, dim_keys: list,
    filters: dict, start: Optional[datetime], end: Optional[datetime], account,
) -> list:
    q = (
        db.query(TokenTransaction.id)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(WorkAccount, WorkItem.account_id == WorkAccount.id)
        .outerjoin(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
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
    if filters.get("agent"):
        q = q.filter(RegisteredAgent.name.ilike(f"%{filters['agent']}%"))
    if filters.get("platform"):
        q = q.filter(TokenTransaction.source_platform.ilike(filters["platform"]))
    if filters.get("model"):
        q = q.filter(or_(
            TokenTransaction.model_name.ilike(filters["model"]),
            TokenTransaction.model_tier.ilike(filters["model"]),
        ))
    if filters.get("outcome_status"):
        q = q.outerjoin(WorkItemOutcome, WorkItem.id == WorkItemOutcome.work_item_id)
        clause = _outcome_status_clause(filters["outcome_status"])
        if clause is not None:
            q = q.filter(WorkItem.context_type == "opportunity", clause)

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

    rows = []
    for key, bucket in primary.items():
        row = {"dimensions": dict(zip(dims_for_merge, bucket["labels"]))}
        row.update(bucket["values"])
        rows.append(row)

    sort_metric = sort if sort in valid_metrics else (valid_metrics[0] if valid_metrics else None)
    if sort_metric:
        rows.sort(key=lambda r: float(r.get(sort_metric, 0) or 0), reverse=True)
    rows = rows[: max(1, min(int(limit or 20), 100))]

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
    )
