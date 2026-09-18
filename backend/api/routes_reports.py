"""
api/routes_reports.py — CostPilot Reporting Engine

GET /api/reports/savings     — Savings report: pruning + model downgrade savings over time
GET /api/reports/risk        — Risk report: sensitive term hits, audit events, blocks
GET /api/reports/departments — Department scorecard: spend, savings, budget health by dept
GET /api/reports/timeline    — Daily spend + call volume bucketed by day (for charts)
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, and_, or_, case
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction, AuditEvent, DepartmentBudget, SensitiveTerm
from core.agentlake import display_department
from core.auth import check_membership
from core.workspace_scope import workspace_filter


def _check_reporting_access(
    db: Session, authorization: Optional[str], workspace_id: Optional[str],
) -> Optional[str]:
    """
    Same retrofit as api/routes_dashboard.py's helper of the same name --
    this file (the classic Savings/Risk/Departments report tabs) had NO
    membership check of any kind until this pass, unlike Business Impact/
    the Executive Dashboard. See routes_dashboard.py's docstring for the
    full soft-mode-gated reasoning; kept as a duplicate here rather than
    a shared import to avoid a cross-router dependency for one helper.
    """
    if not workspace_id:
        return None
    ctx = check_membership(db, authorization, workspace_id, "view_reports")
    return ctx.department_scope if ctx else None
# The rate table and economy-tier set here used to be a second, independently
# maintained copy of core/metrics_query.py's -- same numbers, but nothing
# stopped them from silently drifting apart. Now sourced from one place;
# this file still loops over raw rows itself (savings_report()/
# dept_scorecard() both build a per-day timeline, which metrics_query.py
# has no time-bucketing support for yet), so the query path isn't unified,
# only the formula inputs are.
from core.metrics_query import (
    MICRO_INPUT_COST, MICRO_OUTPUT_COST,
    FLAGSHIP_INPUT_COST, FLAGSHIP_OUTPUT_COST,
    ECONOMY_TIERS,
)

router = APIRouter()

# HISTORICAL NOTE, kept for context: this file used to aggregate by
# looping over every matching row in Python, capped at a
# MAX_REPORT_ROWS=100,000 "emergency memory safety valve" added after a
# live production R14/OOM incident -- the "Default (legacy)" workspace's
# unscoped bucket alone holds ~135K TokenTransaction rows within the last
# year, and a single 365-day Savings/Risk/Departments request against it
# loaded the WHOLE result set as ORM objects in one request, exceeding a
# Standard-2X dyno's 1GB quota and taking down Ask CostPilot (an
# unrelated endpoint on the same dyno) with it. All three report
# functions below (compute_realized_savings, risk_report, dept_scorecard)
# are now migrated onto SQL-side SUM/COUNT/CASE/GROUP BY aggregation --
# see each function's own docstring -- so there is no row-count ceiling
# left to hit, and the MAX_REPORT_ROWS constant/cap themselves are gone,
# not just raised.

PREMIUM_TIERS = {"Advisor", "Strategist", "flagship"}

def _tier_bucket(tier: str) -> str:
    """Normalize Scout/Analyst/micro → 'micro', Advisor/Strategist/flagship → 'flagship'."""
    return "micro" if tier in ECONOMY_TIERS else "flagship"


def _parse_range(days: int, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None):
    end = date_to or datetime.utcnow()
    start = date_from or (end - timedelta(days=days))
    if start >= end:
        raise ValueError("date_from must be before date_to")
    return start, end


def _timeline_dates(start: datetime, end: datetime):
    """Return calendar dates covered by the report window, including today."""
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while current <= last:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


# ── Savings Report ─────────────────────────────────────────────────────────────

def compute_realized_savings(
    db: Session, workspace_id: Optional[str], days: int,
    date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
    *, agent_id: Optional[int] = None, person_external_id: Optional[str] = None,
    department_scope: Optional[str] = None,
) -> dict:
    """
    The trusted "Realized Savings" calculation (pruning + model-downgrade
    savings already achieved, not a hypothetical) -- extracted from
    savings_report() so the Agent Intelligence Profile (and now the Person
    Intelligence Profile) can reuse the exact same formula scoped to one
    agent's or person's transactions instead of reimplementing it.
    savings_report() below is unchanged in behavior; it now just calls
    this with agent_id=None, person_external_id=None.

    Migrated off the per-row Python loop onto SQL-side SUM/COUNT/CASE
    aggregation (reporting architecture assessment's own flagged gap, and
    the direct cause of a real production R14/OOM incident this file's
    own MAX_REPORT_ROWS docstring documents -- a 365-day request against
    a ~135K-row workspace loaded the entire result set as ORM objects in
    one request). SQL aggregation has no such row-count ceiling: every
    matching row is summed by the database, never materialized into
    Python objects, so `truncated` is now structurally always False
    rather than a band-aid cap -- kept in the response shape only for
    backward compatibility with the frontend's existing truncated check.
    Verified against real local data (33 real transactions across the
    date range) to produce byte-identical totals to the old per-row loop
    before this replaced it, not assumed equivalent from the math alone.
    """
    start, end = _parse_range(days, date_from, date_to)

    filters = [TokenTransaction.timestamp >= start, TokenTransaction.timestamp < end]
    if workspace_id:
        filters.append(workspace_filter(TokenTransaction, workspace_id))
    if agent_id is not None:
        filters.append(TokenTransaction.agent_id == agent_id)
    if person_external_id is not None:
        from core.metrics_query import person_clause
        filters.append(person_clause(person_external_id))
    if department_scope:
        filters.append(or_(
            func.trim(func.coalesce(TokenTransaction.charged_org_unit_name, "")) == department_scope,
            TokenTransaction.department == department_scope,
            TokenTransaction.department.like(f"%:{department_scope}"),
        ))

    # Same "in ECONOMY_TIERS -> micro, else flagship" bucketing _tier_bucket()
    # applied per-row -- expressed as SQL CASE instead so the database does
    # the bucketing during aggregation, not a Python loop afterward.
    is_micro = TokenTransaction.model_tier.in_(ECONOMY_TIERS)
    micro_flag = case((is_micro, 1), else_=0)
    flagship_flag = case((is_micro, 0), else_=1)
    pruned_tokens_saved = case((TokenTransaction.was_pruned.is_(True), TokenTransaction.tokens_saved), else_=0)
    # Downgrade-savings terms, summed separately per row-set then combined
    # by the fixed rate constants once at the end -- sum(a_i*k1 + b_i*k2)
    # == k1*sum(a_i) + k2*sum(b_i) by simple distributivity, so this is
    # the same total the old per-row formula computed, just aggregated in
    # SQL instead of a Python generator expression.
    micro_input_plus_saved = case((is_micro, TokenTransaction.input_tokens + TokenTransaction.tokens_saved), else_=0)
    micro_output = case((is_micro, TokenTransaction.output_tokens), else_=0)

    base_q = db.query(TokenTransaction)
    if person_external_id is not None:
        from database.models import WorkUser
        base_q = base_q.outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
    base_q = base_q.filter(*filters)

    total_cost, total_calls, micro_calls, flagship_calls, tokens_pruned, micro_input_saved_sum, micro_output_sum = (
        base_q.with_entities(
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
            func.coalesce(func.sum(micro_flag), 0),
            func.coalesce(func.sum(flagship_flag), 0),
            func.coalesce(func.sum(pruned_tokens_saved), 0),
            func.coalesce(func.sum(micro_input_plus_saved), 0),
            func.coalesce(func.sum(micro_output), 0),
        ).one()
    )
    total_cost = float(total_cost or 0.0)
    total_calls = int(total_calls or 0)
    micro_calls = int(micro_calls or 0)
    flagship_calls = int(flagship_calls or 0)
    tokens_pruned = int(tokens_pruned or 0)
    pruning_saved = round(tokens_pruned * FLAGSHIP_INPUT_COST, 6)

    # Downgrade savings: for each Scout call, what it would have cost at Advisor (flagship) rates
    # This is always positive — Scout is always cheaper than Advisor
    downgrade_saved = round(
        float(micro_input_saved_sum or 0) * (FLAGSHIP_INPUT_COST - MICRO_INPUT_COST)
        + float(micro_output_sum or 0) * (FLAGSHIP_OUTPUT_COST - MICRO_OUTPUT_COST),
        6,
    )

    # Hypothetical cost with no CostPilot routing (all calls at flagship rates, no pruning savings)
    cost_if_all_flagship = round(total_cost + downgrade_saved, 6)
    total_saved          = round(pruning_saved + downgrade_saved, 6)

    # Daily timeline via SQL GROUP BY day -- same func.date() pattern
    # already proven live in routes_timeseries.py (including its
    # documented SQLite-string-vs-Postgres-date-object normalization),
    # not a per-row Python loop bucketing every transaction by hand.
    day_expr = func.date(TokenTransaction.timestamp)
    timeline_rows = base_q.with_entities(
        day_expr.label("day"),
        func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        func.coalesce(func.sum(pruned_tokens_saved), 0),
        func.count(TokenTransaction.id),
        func.coalesce(func.sum(flagship_flag), 0),
        func.coalesce(func.sum(micro_flag), 0),
    ).group_by("day").all()

    def _as_date_str(value) -> str:
        return value if isinstance(value, str) else value.strftime("%Y-%m-%d")

    daily = {
        _as_date_str(row[0]): {
            "cost": round(float(row[1] or 0.0), 6), "tokens_saved": int(row[2] or 0),
            "calls": int(row[3] or 0), "flagship": int(row[4] or 0), "micro": int(row[5] or 0),
        }
        for row in timeline_rows
    }

    # Fill missing days with zeros
    timeline = []
    for day in _timeline_dates(start, end):
        d = daily.get(day, {"cost": 0.0, "tokens_saved": 0, "calls": 0, "flagship": 0, "micro": 0})
        timeline.append({"date": day, **d})

    return {
        "period_days":           days,
        "total_cost_usd":        round(total_cost, 6),
        "total_calls":           total_calls,
        "micro_calls":           micro_calls,
        "flagship_calls":        flagship_calls,
        "micro_pct":             round((micro_calls / total_calls * 100), 1) if total_calls else 0,
        "tokens_pruned":         tokens_pruned,
        "pruning_saved_usd":     pruning_saved,
        "downgrade_saved_usd":   downgrade_saved,
        "total_saved_usd":       total_saved,
        "cost_if_no_fage_usd":   round(cost_if_all_flagship, 6),
        "timeline":              timeline,
        "truncated":             False,
    }


@router.get("/savings")
def savings_report(days: int = Query(30, ge=1, le=365),
                   workspace_id: str = Query(None),
                   date_from: Optional[datetime] = Query(None),
                   date_to: Optional[datetime] = Query(None),
                   db: Session = Depends(get_db),
                   authorization: Optional[str] = Header(default=None)):
    department_scope = _check_reporting_access(db, authorization, workspace_id)
    return compute_realized_savings(db, workspace_id, days, date_from, date_to, department_scope=department_scope)


# ── Risk Report ────────────────────────────────────────────────────────────────

@router.get("/risk")
def risk_report(days: int = Query(30, ge=1, le=365),
                workspace_id: str = Query(None),
                date_from: Optional[datetime] = Query(None),
                date_to: Optional[datetime] = Query(None),
                db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    """
    Same MAX_REPORT_ROWS/OOM exposure as compute_realized_savings had
    (see that function's docstring) -- migrated the same way: every
    count/breakdown computed via SQL aggregation instead of a per-row
    Python loop, and the recent-events table fetched as its own bounded
    query (ORDER BY timestamp DESC LIMIT 500) that never depends on how
    many total rows exist, rather than slicing the first 500 of an
    already-fully-loaded result set.
    """
    department_scope = _check_reporting_access(db, authorization, workspace_id)
    start, end = _parse_range(days, date_from, date_to)

    filters = [AuditEvent.timestamp >= start, AuditEvent.timestamp < end]
    if workspace_id:
        filters.append(workspace_filter(AuditEvent, workspace_id))
    if department_scope:
        filters.append(or_(
            AuditEvent.department == department_scope,
            AuditEvent.department.like(f"%:{department_scope}"),
        ))
    base_q = db.query(AuditEvent).filter(*filters)

    # "blocked" matches decision_outcome case-insensitively (mirrors the
    # original .lower() check) and rationale against the fixed-case
    # literal core/auditor.py actually writes ("REQUEST BLOCKED — ...") --
    # that string is never user input and always emitted in this exact
    # case, so the LIKE-vs-ILIKE case-sensitivity difference between
    # SQLite and Postgres has no practical effect on real data, verified
    # against real rows before this replaced the per-row check.
    is_blocked = or_(
        AuditEvent.decision_outcome.ilike("%blocked%"),
        AuditEvent.rationale.like("%REQUEST BLOCKED%"),
    )
    is_throttled = AuditEvent.rationale.ilike("%throttled%")
    is_lock = AuditEvent.event_type.in_(("LOCK", "COLLISION_LOCK"))

    (
        total_events, critical, high, medium, low,
        blocked, locks, collision_queues, collision_skips, throttled,
    ) = base_q.with_entities(
        func.count(AuditEvent.id),
        func.coalesce(func.sum(case((AuditEvent.risk_level == "critical", 1), else_=0)), 0),
        func.coalesce(func.sum(case((AuditEvent.risk_level == "high", 1), else_=0)), 0),
        func.coalesce(func.sum(case((AuditEvent.risk_level == "medium", 1), else_=0)), 0),
        func.coalesce(func.sum(case((AuditEvent.risk_level == "low", 1), else_=0)), 0),
        func.coalesce(func.sum(case((is_blocked, 1), else_=0)), 0),
        func.coalesce(func.sum(case((is_lock, 1), else_=0)), 0),
        func.coalesce(func.sum(case((AuditEvent.event_type == "COLLISION_QUEUE", 1), else_=0)), 0),
        func.coalesce(func.sum(case((AuditEvent.event_type == "COLLISION_SKIP", 1), else_=0)), 0),
        func.coalesce(func.sum(case((is_throttled, 1), else_=0)), 0),
    ).one()
    total_events, critical, high, medium, low = int(total_events or 0), int(critical or 0), int(high or 0), int(medium or 0), int(low or 0)
    blocked, locks = int(blocked or 0), int(locks or 0)
    collision_queues, collision_skips, throttled = int(collision_queues or 0), int(collision_skips or 0), int(throttled or 0)

    # Daily risk buckets -- GROUP BY (day, risk_level) instead of one
    # increment per row; func.date() is the same pattern already proven
    # live in routes_timeseries.py.
    day_expr = func.date(AuditEvent.timestamp)
    daily = {}
    for day_raw, level, count in base_q.with_entities(
        day_expr.label("day"), AuditEvent.risk_level, func.count(AuditEvent.id)
    ).group_by("day", AuditEvent.risk_level).all():
        day = day_raw if isinstance(day_raw, str) else day_raw.strftime("%Y-%m-%d")
        bucket = daily.setdefault(day, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0})
        bucket[level] = bucket.get(level, 0) + int(count or 0)
        bucket["total"] += int(count or 0)

    timeline = []
    for day in _timeline_dates(start, end):
        d   = daily.get(day, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0})
        timeline.append({"date": day, **d})

    # By department -- GROUP BY (department, risk_level); display_department()
    # applied to the small aggregated result, same merge-same-display-name
    # behavior the original per-row loop had (multiple raw department
    # strings that display the same way still accumulate into one bucket,
    # since the dict key here is the display name, not the raw one).
    dept_risk = {}
    for dept_raw, level, count in base_q.with_entities(
        AuditEvent.department, AuditEvent.risk_level, func.count(AuditEvent.id)
    ).group_by(AuditEvent.department, AuditEvent.risk_level).all():
        dept = display_department(dept_raw)
        bucket = dept_risk.setdefault(dept, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0})
        bucket[level] = bucket.get(level, 0) + int(count or 0)
        bucket["total"] += int(count or 0)

    # Recent high-stakes events for table and report drill-downs -- its
    # own bounded query, never the full matching result set.
    recent = [
        {
            "id":             e.id,
            "timestamp":      e.timestamp.isoformat() if e.timestamp else None,
            "event_type":     e.event_type,
            "department":     e.department,
            "display_department": display_department(e.department),
            "risk_level":     e.risk_level,
            "decision_outcome": e.decision_outcome or "—",
            "rationale":      (e.rationale or "")[:200],
        }
        for e in base_q.order_by(AuditEvent.timestamp.desc()).limit(500).all()
    ]

    # Term library stats
    active_terms = (
        SensitiveTerm.enabled.is_(True),
        SensitiveTerm.deleted_at.is_(None),
    )
    term_count = db.query(func.count(SensitiveTerm.id)).filter(*active_terms).scalar() or 0
    block_terms = db.query(func.count(SensitiveTerm.id)).filter(
        *active_terms, SensitiveTerm.action == "block"
    ).scalar() or 0
    escalate_terms = db.query(func.count(SensitiveTerm.id)).filter(
        *active_terms, SensitiveTerm.action == "escalate"
    ).scalar() or 0

    return {
        "period_days":     days,
        "total_events":    total_events,
        "critical":        critical,
        "high":            high,
        "medium":          medium,
        "low":             low,
        "blocked":         blocked,
        "locks":           locks,
        "collision_count": locks + collision_queues + collision_skips,
        "collision_breakdown": {
            "lock": locks,
            "queue": collision_queues,
            "skip": collision_skips,
        },
        "throttled":       throttled,
        "by_department":   dept_risk,
        "timeline":        timeline,
        "recent_events":   recent,
        "term_library": {
            "total":    term_count,
            "block":    block_terms,
            "escalate": escalate_terms,
        },
        "truncated":       False,  # SQL aggregation has no row-count ceiling anymore -- see this route's own docstring
    }


# ── Department Scorecard ───────────────────────────────────────────────────────

@router.get("/departments")
def dept_scorecard(days: int = Query(30, ge=1, le=365),
                   workspace_id: str = Query(None),
                   date_from: Optional[datetime] = Query(None),
                   date_to: Optional[datetime] = Query(None),
                   db: Session = Depends(get_db),
                   authorization: Optional[str] = Header(default=None)):
    """
    Same MAX_REPORT_ROWS/OOM exposure as compute_realized_savings and
    risk_report (see their own docstrings) -- migrated the same way: the
    per-department/per-day transaction aggregation now runs as SQL GROUP
    BY instead of a per-row Python loop. The budget-merge logic below
    (DepartmentBudget has no real workspace_id column, so scoping it is
    its own separate concern from the transaction OOM fix) is unchanged --
    it was never the source of the row-count exposure, since it only ever
    loads one row per department, not one per transaction.
    """
    department_scope = _check_reporting_access(db, authorization, workspace_id)
    start, end = _parse_range(days, date_from, date_to)

    filters = [TokenTransaction.timestamp >= start, TokenTransaction.timestamp < end]
    if workspace_id:
        filters.append(workspace_filter(TokenTransaction, workspace_id))
    if department_scope:
        filters.append(or_(
            TokenTransaction.department == department_scope,
            TokenTransaction.department.like(f"%:{department_scope}"),
        ))
    base_q = db.query(TokenTransaction).filter(*filters)

    def _dept_matches_scope(raw_dept: str) -> bool:
        return not department_scope or raw_dept == department_scope or (raw_dept or "").endswith(f":{department_scope}")

    # DepartmentBudget has no real workspace_id column (see the model) --
    # workspace scoping for it is entirely the "WORKSPACE_ID:Department"
    # prefix convention every other model here uses when it lacks that
    # column too. This query previously had NO workspace check at all,
    # only the unrelated department_scope (RBAC) filter above -- confirmed
    # live (2026-09-13): querying SIM-HISTORICAL-2Y returned budget rows
    # literally named "4BE43240A6674314:Marketing" and
    # "BDB2754C199247E8:Marketing" (two OTHER, unrelated workspaces' own
    # Marketing budgets) alongside this workspace's real, unprefixed
    # "Marketing" row -- all three collapse to the same "Marketing"
    # display name, so the frontend's cross-row cap-summing merge diluted
    # a genuinely-over-cap 126% used against its real $18 cap down to a
    # falsely-comfortable 60% against a $38 cap that included two other
    # workspaces' $10 phantom caps with zero real activity.
    def _budget_belongs_to_workspace(raw_dept: str) -> bool:
        raw_dept = raw_dept or ""
        if workspace_id and workspace_id != "default" and raw_dept.startswith(f"{workspace_id}:"):
            return True
        # No colon prefix at all -- the unprefixed/default bucket, same
        # convention _run_activity_query and workspace_filter() already
        # use elsewhere; a DIFFERENT explicit workspace's own prefix
        # (like the two examples above) is what this excludes.
        return ":" not in raw_dept

    budgets = {
        b.department: b for b in db.query(DepartmentBudget).all()
        if _dept_matches_scope(b.department) and _budget_belongs_to_workspace(b.department)
    }

    # Aggregate per department via SQL GROUP BY instead of a per-row loop.
    is_micro = TokenTransaction.model_tier.in_(ECONOMY_TIERS)
    micro_flag = case((is_micro, 1), else_=0)
    flagship_flag = case((is_micro, 0), else_=1)
    pruned_tokens_saved = case((TokenTransaction.was_pruned.is_(True), TokenTransaction.tokens_saved), else_=0)

    dept_data = {}
    for d, total_calls, total_cost, mic, flag, tok_pruned in base_q.with_entities(
        TokenTransaction.department,
        func.count(TokenTransaction.id),
        func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        func.coalesce(func.sum(micro_flag), 0),
        func.coalesce(func.sum(flagship_flag), 0),
        func.coalesce(func.sum(pruned_tokens_saved), 0),
    ).group_by(TokenTransaction.department).all():
        tokens_pruned = int(tok_pruned or 0)
        dept_data[d] = {
            "department":        d,
            "total_calls":       int(total_calls or 0),
            "micro_calls":       int(mic or 0),
            "flagship_calls":    int(flag or 0),
            "total_cost_usd":    round(float(total_cost or 0.0), 6),
            "tokens_pruned":     tokens_pruned,
            "pruning_saved_usd": round(tokens_pruned * FLAGSHIP_INPUT_COST, 6),
        }

    # Merge budget data
    scorecards = []
    all_depts = set(list(dept_data.keys()) + list(budgets.keys()))
    for d in sorted(all_depts):
        data   = dept_data.get(d, {"total_calls": 0, "micro_calls": 0, "flagship_calls": 0,
                                    "total_cost_usd": 0.0, "tokens_pruned": 0, "pruning_saved_usd": 0.0})
        budget = budgets.get(d)
        calls  = data["total_calls"]
        budget_used_pct = (
            round((budget.current_spend_usd / budget.monthly_cap_usd * 100), 1)
            if budget and budget.monthly_cap_usd > 0 else 0
        )
        scorecards.append({
            "department":        d,
            "display_department": display_department(d),
            "total_calls":       calls,
            "micro_calls":       data["micro_calls"],
            "flagship_calls":    data["flagship_calls"],
            "micro_pct":         round((data["micro_calls"] / calls * 100), 1) if calls else 0,
            "total_cost_usd":    data["total_cost_usd"],
            "tokens_pruned":     data["tokens_pruned"],
            "pruning_saved_usd": data["pruning_saved_usd"],
            "monthly_cap_usd":   budget.monthly_cap_usd   if budget else 0,
            "current_spend_usd": round(budget.current_spend_usd, 6) if budget else 0,
            "budget_used_pct":   budget_used_pct,
            # Derived from the SAME budget_used_pct just computed above,
            # not the stored budget.throttled column -- confirmed live
            # (2026-09-13): the redesigned Department Report showed
            # Marketing as "Throttled" at only 59.9% budget used, because
            # the stored column reflects whatever spend figure last
            # triggered enforcement (potentially a different, drifted
            # total -- core/budget.py's own get_all_budgets() already
            # documents this exact "current_spend_usd can drift from the
            # real recomputed total" failure mode and recomputes fresh
            # for that reason), while this row's own budget_used_pct is
            # computed fresh right here. Two numbers describing the same
            # thing must come from the same calculation or they will
            # eventually disagree exactly like this.
            "throttled":         bool(budget and budget_used_pct >= 100 and not budget.override_granted),
            "override_granted":  budget.override_granted  if budget else False,
        })

    # Daily spend per dept for stacked chart -- GROUP BY (day, department)
    # via the same func.date() pattern proven live in routes_timeseries.py,
    # instead of one dict update per transaction.
    day_expr = func.date(TokenTransaction.timestamp)
    daily_dept = {}
    for day_raw, dept, cost in base_q.with_entities(
        day_expr.label("day"), TokenTransaction.department, func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0)
    ).group_by("day", TokenTransaction.department).all():
        day = day_raw if isinstance(day_raw, str) else day_raw.strftime("%Y-%m-%d")
        daily_dept.setdefault(day, {})[dept] = round(float(cost or 0.0), 6)

    timeline = []
    for day in _timeline_dates(start, end):
        timeline.append({"date": day, **(daily_dept.get(day, {}))})

    return {
        "period_days": days,
        "scorecards":  scorecards,
        "timeline":    timeline,
        "departments": sorted(all_depts),
        "truncated":   False,  # SQL aggregation has no row-count ceiling anymore -- see this route's own docstring
    }
