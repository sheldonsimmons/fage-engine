"""
api/routes_timeseries.py — 30-day daily spend and call-volume time-series

GET /api/timeseries   — daily spend (by department) + call counts (by model tier)
                        for the last 30 days, with zeros filled for empty days.
"""

from datetime import datetime, timedelta, date as date_type
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction
from core.agentlake import display_department
from core.workspace_scope import workspace_filter

router = APIRouter()

TIER_ALIASES = {
    "micro":    "Scout",
    "flagship": "Advisor",
    "Scout":    "Scout",
    "Analyst":  "Analyst",
    "Advisor":  "Advisor",
    "Strategist": "Strategist",
}
ALL_TIERS = ["Scout", "Analyst", "Advisor", "Strategist"]


@router.get("")
def get_timeseries(
    days: int = 30,
    workspace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Returns the last `days` days (default 30) of:
      - daily_spend:  [{date, total_usd, by_dept}]
      - daily_calls:  [{date, total_calls, by_tier}]
      - departments:  sorted list of all active departments
      - labels:       ISO date strings for chart x-axis
    """
    start = datetime.utcnow() - timedelta(days=days)

    def _scoped(query):
        # TokenTransaction.workspace_id is the real column — activity written
        # through /api/route (including the traffic simulator) sets it but
        # leaves department unprefixed, so the old department-prefix-only
        # filter silently excluded most/all new simulated traffic from this
        # chart while every other page (already fixed) kept up to date. Same
        # bug as the dashboard undercount found earlier, just in a file that
        # hadn't been touched yet.
        workspace_clause = workspace_filter(TokenTransaction, workspace_id)
        if workspace_clause is not None:
            query = query.filter(workspace_clause)
        return query.filter(TokenTransaction.timestamp >= start)

    # GROUP BY in SQL instead of pulling every transaction row into Python --
    # the result set here is bounded by (days x distinct departments) and
    # (days x 4 tiers), typically a few hundred rows, regardless of whether
    # the underlying table has thousands or millions of transactions. Same
    # fix as project_activity_reporting() got earlier this week, applied
    # here since this chart never got the same treatment at the time.
    spend_rows = _scoped(
        db.query(
            func.date(TokenTransaction.timestamp).label("day"),
            TokenTransaction.department,
            func.sum(TokenTransaction.cost_usd),
        )
    ).group_by("day", TokenTransaction.department).all()

    call_rows = _scoped(
        db.query(
            func.date(TokenTransaction.timestamp).label("day"),
            TokenTransaction.model_tier,
            func.count(TokenTransaction.id),
        )
    ).group_by("day", TokenTransaction.model_tier).all()

    # Build ordered date range (oldest → newest)
    today = datetime.utcnow().date()
    date_range = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    date_range_set = set(date_range)

    def _as_date(value) -> date_type:
        # SQLite's func.date() returns an ISO string ("2026-08-11");
        # Postgres' returns a real date object -- normalize both the same
        # way display_department()/parity testing already expects a plain
        # date, not a driver-specific type.
        return value if isinstance(value, date_type) else datetime.strptime(value, "%Y-%m-%d").date()

    # display_department() strips internal workspace prefixes
    # ("WORKSPACE_ID:Sales" -> "Sales") -- applying it AFTER the SQL
    # GROUP BY, on the small aggregated result set, rather than trying to
    # replicate that Python-side string logic in SQL, keeps this an exact
    # behavioral match for the previous per-row implementation (including
    # its existing quirk of merging same-named departments across
    # workspaces when workspace_id is unset -- preserved here, not
    # something this change should silently alter).
    spend_by_day_dept: dict[date_type, dict] = {d: {} for d in date_range}
    for raw_day, raw_dept, total in spend_rows:
        d = _as_date(raw_day)
        if d not in date_range_set:
            continue
        dept = display_department(raw_dept)
        if dept:
            spend_by_day_dept[d][dept] = spend_by_day_dept[d].get(dept, 0.0) + float(total or 0.0)

    calls_by_day_tier: dict[date_type, dict] = {d: {} for d in date_range}
    for raw_day, raw_tier, count in call_rows:
        d = _as_date(raw_day)
        if d not in date_range_set:
            continue
        canonical_tier = TIER_ALIASES.get(raw_tier, raw_tier)
        if canonical_tier:
            calls_by_day_tier[d][canonical_tier] = calls_by_day_tier[d].get(canonical_tier, 0) + int(count or 0)

    all_depts = sorted({dept for day in spend_by_day_dept.values() for dept in day})

    daily_spend = [
        {
            "date":      d.isoformat(),
            "total_usd": round(sum(spend_by_day_dept[d].values()), 4),
            "by_dept":   {dept: round(spend_by_day_dept[d].get(dept, 0.0), 4) for dept in all_depts},
        }
        for d in date_range
    ]

    daily_calls = [
        {
            "date":        d.isoformat(),
            "total_calls": sum(calls_by_day_tier[d].values()),
            "by_tier":     {tier: calls_by_day_tier[d].get(tier, 0) for tier in ALL_TIERS},
        }
        for d in date_range
    ]

    return {
        "labels":       [d.isoformat() for d in date_range],
        "daily_spend":  daily_spend,
        "daily_calls":  daily_calls,
        "departments":  all_depts,
    }
