"""
api/routes_reports.py — CostPilot Reporting Engine

GET /api/reports/savings     — Savings report: pruning + model downgrade savings over time
GET /api/reports/risk        — Risk report: sensitive term hits, audit events, blocks
GET /api/reports/departments — Department scorecard: spend, savings, budget health by dept
GET /api/reports/timeline    — Daily spend + call volume bucketed by day (for charts)
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction, AuditEvent, DepartmentBudget, SensitiveTerm
from core.agentlake import display_department

router = APIRouter()

MICRO_INPUT_COST     = 0.80  / 1_000_000   # Haiku 4.5 input
MICRO_OUTPUT_COST    = 4.00  / 1_000_000   # Haiku 4.5 output
FLAGSHIP_INPUT_COST  = 3.00  / 1_000_000   # Sonnet 4.6 (Advisor) input
FLAGSHIP_OUTPUT_COST = 15.00 / 1_000_000   # Sonnet 4.6 (Advisor) output

ECONOMY_TIERS = {"Scout", "Analyst", "micro"}
PREMIUM_TIERS = {"Advisor", "Strategist", "flagship"}

def _tier_bucket(tier: str) -> str:
    """Normalize Scout/Analyst/micro → 'micro', Advisor/Strategist/flagship → 'flagship'."""
    return "micro" if tier in ECONOMY_TIERS else "flagship"


def _parse_range(days: int):
    end   = datetime.utcnow()
    start = end - timedelta(days=days)
    return start, end


def _timeline_dates(start: datetime, end: datetime):
    """Return calendar dates covered by the report window, including today."""
    current = start.date()
    last = end.date()
    while current <= last:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


# ── Savings Report ─────────────────────────────────────────────────────────────

@router.get("/savings")
def savings_report(days: int = Query(30, ge=1, le=365),
                   workspace_id: str = Query(None),
                   db: Session = Depends(get_db)):
    start, end = _parse_range(days)

    q = db.query(TokenTransaction).filter(
        TokenTransaction.timestamp >= start,
        TokenTransaction.timestamp <= end,
    )
    if workspace_id:
        q = q.filter(TokenTransaction.department.like(f"{workspace_id}:%"))
    txns = q.all()

    total_cost       = sum(t.cost_usd for t in txns)
    total_calls      = len(txns)
    micro_calls      = sum(1 for t in txns if _tier_bucket(t.model_tier) == "micro")
    flagship_calls   = sum(1 for t in txns if _tier_bucket(t.model_tier) == "flagship")
    tokens_pruned    = sum(t.tokens_saved for t in txns if t.was_pruned)
    pruning_saved    = round(tokens_pruned * FLAGSHIP_INPUT_COST, 6)

    # Downgrade savings: for each Scout call, what it would have cost at Advisor (flagship) rates
    # This is always positive — Scout is always cheaper than Advisor
    downgrade_saved = round(sum(
        (t.input_tokens + t.tokens_saved) * (FLAGSHIP_INPUT_COST - MICRO_INPUT_COST) +
        t.output_tokens * (FLAGSHIP_OUTPUT_COST - MICRO_OUTPUT_COST)
        for t in txns
        if _tier_bucket(t.model_tier) == "micro"
    ), 6)

    # Hypothetical cost with no CostPilot routing (all calls at flagship rates, no pruning savings)
    cost_if_all_flagship = round(total_cost + downgrade_saved, 6)
    total_saved          = round(pruning_saved + downgrade_saved, 6)

    # Daily buckets for chart
    daily = {}
    for t in txns:
        day = t.timestamp.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"cost": 0.0, "tokens_saved": 0, "calls": 0, "flagship": 0, "micro": 0}
        daily[day]["cost"]         = round(daily[day]["cost"] + t.cost_usd, 6)
        daily[day]["tokens_saved"] += t.tokens_saved if t.was_pruned else 0
        daily[day]["calls"]        += 1
        daily[day][_tier_bucket(t.model_tier)] += 1

    # Fill missing days with zeros
    timeline = []
    for day in _timeline_dates(start, end):
        d   = daily.get(day, {"cost": 0.0, "tokens_saved": 0, "calls": 0, "flagship": 0, "micro": 0})
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
    }


# ── Risk Report ────────────────────────────────────────────────────────────────

@router.get("/risk")
def risk_report(days: int = Query(30, ge=1, le=365),
                workspace_id: str = Query(None),
                db: Session = Depends(get_db)):
    start, end = _parse_range(days)

    q = db.query(AuditEvent).filter(
        AuditEvent.timestamp >= start,
        AuditEvent.timestamp <= end,
    )
    if workspace_id:
        q = q.filter(AuditEvent.department.like(f"{workspace_id}:%"))
    events = q.order_by(AuditEvent.timestamp.desc()).all()

    total_events  = len(events)
    critical      = sum(1 for e in events if e.risk_level == "critical")
    high          = sum(1 for e in events if e.risk_level == "high")
    medium        = sum(1 for e in events if e.risk_level == "medium")
    low           = sum(1 for e in events if e.risk_level == "low")
    blocked       = sum(1 for e in events if (
        "blocked" in (e.decision_outcome or "").lower() or
        "REQUEST BLOCKED" in (e.rationale or "")
    ))
    locks         = sum(1 for e in events if e.event_type in ("LOCK", "COLLISION_LOCK"))
    collision_queues = sum(1 for e in events if e.event_type == "COLLISION_QUEUE")
    collision_skips  = sum(1 for e in events if e.event_type == "COLLISION_SKIP")
    throttled     = sum(1 for e in events if "throttled" in (e.rationale or "").lower())

    # Daily risk buckets
    daily = {}
    for e in events:
        day = e.timestamp.strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        daily[day][e.risk_level] += 1
        daily[day]["total"]      += 1

    timeline = []
    for day in _timeline_dates(start, end):
        d   = daily.get(day, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0})
        timeline.append({"date": day, **d})

    # By department
    dept_risk = {}
    for e in events:
        dept = display_department(e.department)
        if dept not in dept_risk:
            dept_risk[dept] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        dept_risk[dept][e.risk_level] += 1
        dept_risk[dept]["total"]      += 1

    # Recent high-stakes events for table and report drill-downs
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
        for e in events[:500]
    ]

    # Term library stats
    term_count = db.query(func.count(SensitiveTerm.id)).scalar() or 0
    block_terms = db.query(func.count(SensitiveTerm.id)).filter_by(action="block").scalar() or 0
    escalate_terms = db.query(func.count(SensitiveTerm.id)).filter_by(action="escalate").scalar() or 0

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
    }


# ── Department Scorecard ───────────────────────────────────────────────────────

@router.get("/departments")
def dept_scorecard(days: int = Query(30, ge=1, le=365),
                   workspace_id: str = Query(None),
                   db: Session = Depends(get_db)):
    start, end = _parse_range(days)

    q = db.query(TokenTransaction).filter(
        TokenTransaction.timestamp >= start,
        TokenTransaction.timestamp <= end,
    )
    if workspace_id:
        q = q.filter(TokenTransaction.department.like(f"{workspace_id}:%"))
    txns = q.all()

    budgets = {b.department: b for b in db.query(DepartmentBudget).all()}

    # Aggregate per department
    dept_data = {}
    for t in txns:
        d = t.department
        if d not in dept_data:
            dept_data[d] = {
                "department":       d,
                "total_calls":      0,
                "micro_calls":      0,
                "flagship_calls":   0,
                "total_cost_usd":   0.0,
                "tokens_pruned":    0,
                "pruning_saved_usd": 0.0,
            }
        dept_data[d]["total_calls"]    += 1
        dept_data[d]["total_cost_usd"] = round(dept_data[d]["total_cost_usd"] + t.cost_usd, 6)
        if _tier_bucket(t.model_tier) == "micro":
            dept_data[d]["micro_calls"]    += 1
        else:
            dept_data[d]["flagship_calls"] += 1
        if t.was_pruned:
            dept_data[d]["tokens_pruned"]     += t.tokens_saved
            dept_data[d]["pruning_saved_usd"]  = round(
                dept_data[d]["pruning_saved_usd"] + t.tokens_saved * FLAGSHIP_INPUT_COST, 6
            )

    # Merge budget data
    scorecards = []
    all_depts = set(list(dept_data.keys()) + list(budgets.keys()))
    for d in sorted(all_depts):
        data   = dept_data.get(d, {"total_calls": 0, "micro_calls": 0, "flagship_calls": 0,
                                    "total_cost_usd": 0.0, "tokens_pruned": 0, "pruning_saved_usd": 0.0})
        budget = budgets.get(d)
        calls  = data["total_calls"]
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
            "budget_used_pct":   round((budget.current_spend_usd / budget.monthly_cap_usd * 100), 1)
                                  if budget and budget.monthly_cap_usd > 0 else 0,
            "throttled":         budget.throttled         if budget else False,
            "override_granted":  budget.override_granted  if budget else False,
        })

    # Daily spend per dept for stacked chart
    daily_dept = {}
    for t in txns:
        day = t.timestamp.strftime("%Y-%m-%d")
        if day not in daily_dept:
            daily_dept[day] = {}
        dept = t.department
        daily_dept[day][dept] = round(daily_dept[day].get(dept, 0) + t.cost_usd, 6)

    timeline = []
    for day in _timeline_dates(start, end):
        timeline.append({"date": day, **(daily_dept.get(day, {}))})

    return {
        "period_days": days,
        "scorecards":  scorecards,
        "timeline":    timeline,
        "departments": sorted(all_depts),
    }
