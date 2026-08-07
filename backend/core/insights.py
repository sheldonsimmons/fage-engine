"""
core/insights.py — deterministic "what stands out" signal library.

Each signal is a small, explainable check computed from real data via the
same shared functions used elsewhere in the app (core.budget,
project_activity_reporting) — never an LLM-invented number, consistent
with how every other Ask CostPilot/dashboard figure in this app works.
Signals only appear when they cross a real threshold; the caller ranks by
severity and returns the top few, instead of a fixed rotation of the same
cards regardless of whether anything is actually notable this week.

This is also the first slice of the roadmap's "V3 — Predictive
Intelligence" phase: budget_pace_signals projects month-end spend from the
current daily rate and states a real exhaustion date when a department is
on pace to run out — "at your current usage, this department will exhaust
its AI budget by September 18" — computed with a plain linear projection,
not a guess.
"""

import calendar
from datetime import datetime, timedelta


def budget_pace_signals(db, workspace_id: str | None) -> list[dict]:
    """
    Project month-end spend per department from the current daily pace.
    Flags departments on track to exceed their monthly cap before the
    month ends, with the projected exhaustion date.
    """
    from core.budget import get_all_budgets

    now = datetime.utcnow()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month = max(1, now.day)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    signals = []
    for budget in get_all_budgets(db, workspace_id):
        if budget.get("archived"):
            continue
        cap = float(budget["monthly_cap_usd"] or 0)
        spend = float(budget["current_spend_usd"] or 0)
        if cap <= 0 or spend <= 0:
            continue

        daily_rate = spend / day_of_month
        projected_month_end = daily_rate * days_in_month
        if projected_month_end <= cap:
            continue  # on track — nothing to flag

        days_to_exhaustion = cap / daily_rate if daily_rate > 0 else None
        exhaustion_date = None
        if days_to_exhaustion is not None and days_to_exhaustion <= days_in_month:
            exhaustion_date = month_start + timedelta(days=days_to_exhaustion)

        overage_pct = round((projected_month_end / cap - 1) * 100, 1)
        department = str(budget["department"] or "Unassigned").split(":")[-1]
        exhaustion_text = (
            f" — projected to exhaust its budget around {exhaustion_date.strftime('%b %-d')}."
            if exhaustion_date else "."
        )
        signals.append({
            "type": "budget_pace",
            "severity": min(150.0, overage_pct),
            "department": department,
            "spend_to_date_usd": round(spend, 4),
            "monthly_cap_usd": cap,
            "daily_rate_usd": round(daily_rate, 4),
            "projected_month_end_usd": round(projected_month_end, 2),
            "overage_pct": overage_pct,
            "exhaustion_date": exhaustion_date.date().isoformat() if exhaustion_date else None,
            "title": f"{department} is on pace to exceed its AI budget",
            "detail": (
                f"At the current daily rate (${daily_rate:,.2f}/day), projected month-end spend is "
                f"${projected_month_end:,.2f} against a ${cap:,.2f} cap{exhaustion_text}"
            ),
        })
    signals.sort(key=lambda s: -s["severity"])
    return signals


def spend_anomaly_signals(db, workspace_id: str | None, lookback_days: int = 14) -> list[dict]:
    """
    Compare each department's spend in the most recent half of the lookback
    window against the earlier half. Flags departments whose recent spend
    is meaningfully higher than their own recent baseline.
    """
    from api.routes_work_items import project_activity_reporting

    half = max(1, lookback_days // 2)
    now = datetime.utcnow()
    mid = now - timedelta(days=half)
    start = now - timedelta(days=lookback_days)

    def _spend_by_department(date_from, date_to):
        report = project_activity_reporting(
            workspace_id=workspace_id, date_from=date_from, date_to=date_to, days=half,
            project_id=None, user_external_id=None, agent_id=None, account_id=None,
            source_platform=None, record_type=None, model_tier=None, charged_unit=None,
            business_purpose=None, activity_limit=1, db=db,
        )
        return {
            row.get("label"): float(row.get("spend_usd") or 0)
            for row in report.get("organizational_unit_breakdown") or []
            if row.get("label")
        }

    recent_by_dept = _spend_by_department(mid, now)
    prior_by_dept = _spend_by_department(start, mid)

    signals = []
    for department, recent_spend in recent_by_dept.items():
        if recent_spend < 0.01:
            continue
        prior_spend = prior_by_dept.get(department, 0.0)
        if prior_spend <= 0:
            if recent_spend < 1.0:
                continue  # a small new department shouldn't read as a 100% spike
            pct_change = 100.0
        else:
            pct_change = round((recent_spend - prior_spend) / prior_spend * 100, 1)
        if pct_change < 40:
            continue  # only meaningful increases are worth surfacing

        signals.append({
            "type": "spend_anomaly",
            "severity": min(150.0, pct_change),
            "department": department,
            "recent_spend_usd": round(recent_spend, 4),
            "prior_spend_usd": round(prior_spend, 4),
            "pct_change": pct_change,
            "title": f"{department}'s AI spend jumped {pct_change:.0f}% vs. its recent baseline",
            "detail": (
                f"${recent_spend:,.4f} in the last {half} days vs. ${prior_spend:,.4f} "
                f"in the {half} days before that."
            ),
        })
    signals.sort(key=lambda s: -s["severity"])
    return signals


def top_signals(db, workspace_id: str | None, limit: int = 5) -> list[dict]:
    """Combined, ranked signal feed — the actual 'what stands out right now.'"""
    signals = budget_pace_signals(db, workspace_id) + spend_anomaly_signals(db, workspace_id)
    signals.sort(key=lambda s: -s["severity"])
    return signals[:limit]
