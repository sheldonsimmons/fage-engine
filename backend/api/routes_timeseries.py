"""
api/routes_timeseries.py — 30-day daily spend and call-volume time-series

GET /api/timeseries   — daily spend (by department) + call counts (by model tier)
                        for the last 30 days, with zeros filled for empty days.
"""

from datetime import datetime, timedelta, date as date_type
from fastapi import APIRouter, Depends
from sqlalchemy import func, cast, Date as SADate
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction

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
def get_timeseries(days: int = 30, db: Session = Depends(get_db)):
    """
    Returns the last `days` days (default 30) of:
      - daily_spend:  [{date, total_usd, by_dept}]
      - daily_calls:  [{date, total_calls, by_tier}]
      - departments:  sorted list of all active departments
      - labels:       ISO date strings for chart x-axis
    """
    start = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(
            cast(TokenTransaction.timestamp, SADate).label("day"),
            TokenTransaction.department,
            TokenTransaction.model_tier,
            func.sum(TokenTransaction.cost_usd).label("total_usd"),
            func.count(TokenTransaction.id).label("total_calls"),
        )
        .filter(TokenTransaction.timestamp >= start)
        .group_by(
            cast(TokenTransaction.timestamp, SADate),
            TokenTransaction.department,
            TokenTransaction.model_tier,
        )
        .order_by(cast(TokenTransaction.timestamp, SADate))
        .all()
    )

    # Build ordered date range (oldest → newest)
    today = datetime.utcnow().date()
    date_range = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    # Collect all departments seen in data
    all_depts = sorted({r.department for r in rows})

    # Initialize per-day buckets
    spend_by_day_dept: dict[date_type, dict] = {d: {} for d in date_range}
    calls_by_day_tier: dict[date_type, dict] = {d: {} for d in date_range}

    for r in rows:
        d = r.day
        if d not in spend_by_day_dept:
            continue
        canonical_tier = TIER_ALIASES.get(r.model_tier, r.model_tier)
        dept = r.department
        spend_by_day_dept[d][dept] = spend_by_day_dept[d].get(dept, 0.0) + (r.total_usd or 0.0)
        calls_by_day_tier[d][canonical_tier] = calls_by_day_tier[d].get(canonical_tier, 0) + (r.total_calls or 0)

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
