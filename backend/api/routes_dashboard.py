"""
api/routes_dashboard.py — Aggregated dashboard KPI endpoint  [Step 7]

GET /api/dashboard
  Returns all data needed to render the executive dashboard in one call:
    - Total spend today and this month across all departments
    - Token savings from pruning (total and today)
    - Active / idle / locked agent counts
    - Routing split: micro vs flagship call percentages
    - Per-department budget summaries
    - Recent audit events
    - Top-level throttle count
"""

import json
from datetime import datetime, date, timedelta
from sqlalchemy import func
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    TokenTransaction, RegisteredAgent,
    DepartmentBudget, AuditEvent,
)

router = APIRouter()


def _keyword_stats(db: Session, days: int = 30, top_n: int = 10) -> list:
    """Count keyword frequency from matched_keywords_json on recent AuditEvents."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    events = db.query(AuditEvent.matched_keywords_json).filter(
        AuditEvent.timestamp >= cutoff,
        AuditEvent.matched_keywords_json.isnot(None),
        AuditEvent.matched_keywords_json != "[]",
        AuditEvent.matched_keywords_json != "",
    ).all()
    counts: dict = {}
    for (kw_json,) in events:
        try:
            for kw in json.loads(kw_json or "[]"):
                counts[kw] = counts.get(kw, 0) + 1
        except Exception:
            pass
    sorted_kws = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{"kw": kw, "count": cnt} for kw, cnt in sorted_kws]


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    """Single endpoint that powers the entire executive dashboard."""

    now         = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Spend ──────────────────────────────────────────────────────────────────
    # Both spend figures query token_transactions directly so they always agree.
    spend_today = db.query(func.sum(TokenTransaction.cost_usd)).filter(
        TokenTransaction.timestamp >= today_start
    ).scalar() or 0.0

    spend_month = db.query(func.sum(TokenTransaction.cost_usd)).filter(
        TokenTransaction.timestamp >= month_start
    ).scalar() or 0.0

    # Budget table still used for cap / throttle display — load once here
    budgets_for_spend = db.query(DepartmentBudget).all()

    # ── Token savings from pruning ─────────────────────────────────────────────
    tokens_saved_today = db.query(func.sum(TokenTransaction.tokens_saved)).filter(
        TokenTransaction.timestamp >= today_start,
        TokenTransaction.was_pruned == True,
    ).scalar() or 0

    tokens_saved_total = db.query(func.sum(TokenTransaction.tokens_saved)).filter(
        TokenTransaction.was_pruned == True,
    ).scalar() or 0

    # Estimated dollar value of all pruning savings (blended micro/flagship rate)
    # Using micro rate as conservative floor estimate
    MICRO_COST_PER_TOKEN = 0.15 / 1_000_000
    pruning_savings_usd = round(tokens_saved_total * MICRO_COST_PER_TOKEN, 6)

    # ── Call counts — exclude Voice Guard prune-only records (cost=$0, no AI call) ──
    # VOICE_GUARD_PRUNE rows exist only to record token savings; they are not AI calls.
    IS_AI_CALL = TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE"

    total_calls = db.query(func.count(TokenTransaction.id)).filter(IS_AI_CALL).scalar() or 0

    # Economy tiers: Scout (tier 1), Analyst (tier 2), and legacy "micro"
    ECONOMY_TIERS  = ("Scout", "Analyst", "micro")
    # Premium tiers: Advisor (tier 3), Strategist (tier 4), and legacy "flagship"
    PREMIUM_TIERS  = ("Advisor", "Strategist", "flagship")

    micro_calls    = db.query(func.count(TokenTransaction.id)).filter(
        IS_AI_CALL, TokenTransaction.model_tier.in_(ECONOMY_TIERS)
    ).scalar() or 0
    flagship_calls = db.query(func.count(TokenTransaction.id)).filter(
        IS_AI_CALL, TokenTransaction.model_tier.in_(PREMIUM_TIERS)
    ).scalar() or 0

    micro_pct    = round((micro_calls    / total_calls) * 100, 1) if total_calls else 0
    flagship_pct = round((flagship_calls / total_calls) * 100, 1) if total_calls else 0

    # Per-tier call counts
    scout_calls      = db.query(func.count(TokenTransaction.id)).filter(IS_AI_CALL, TokenTransaction.model_tier.in_(("Scout",     "micro"))).scalar() or 0
    analyst_calls    = db.query(func.count(TokenTransaction.id)).filter(IS_AI_CALL, TokenTransaction.model_tier == "Analyst").scalar() or 0
    advisor_calls    = db.query(func.count(TokenTransaction.id)).filter(IS_AI_CALL, TokenTransaction.model_tier.in_(("Advisor",   "flagship"))).scalar() or 0
    strategist_calls = db.query(func.count(TokenTransaction.id)).filter(IS_AI_CALL, TokenTransaction.model_tier == "Strategist").scalar() or 0

    def _pct(n): return round((n / total_calls) * 100, 1) if total_calls else 0

    calls_today = db.query(func.count(TokenTransaction.id)).filter(
        IS_AI_CALL, TokenTransaction.timestamp >= today_start
    ).scalar() or 0

    # ── Agent counts ───────────────────────────────────────────────────────────
    agents_total  = db.query(func.count(RegisteredAgent.id)).scalar() or 0
    agents_active = db.query(func.count(RegisteredAgent.id)).filter_by(status="active").scalar()  or 0
    agents_locked = db.query(func.count(RegisteredAgent.id)).filter_by(status="locked").scalar()  or 0
    agents_idle   = db.query(func.count(RegisteredAgent.id)).filter_by(status="idle").scalar()    or 0

    # ── Budget summaries (reuse budgets_for_spend already loaded above) ──────────
    budgets = budgets_for_spend
    throttled_count = sum(1 for b in budgets if b.throttled)

    total_cap   = sum(b.monthly_cap_usd   for b in budgets)
    total_spend = sum(b.current_spend_usd for b in budgets)
    overall_pct = round((total_spend / total_cap) * 100, 1) if total_cap else 0

    budget_summaries = [
        {
            "department":        b.department,
            "monthly_cap_usd":   b.monthly_cap_usd,
            "current_spend_usd": round(b.current_spend_usd, 4),
            "used_pct":          round((b.current_spend_usd / b.monthly_cap_usd) * 100, 1),
            "throttled":         b.throttled,
            "override_granted":  b.override_granted,
        }
        for b in budgets
    ]

    # ── Governance & Compliance stats ─────────────────────────────────────────
    blocked_count      = db.query(func.count(AuditEvent.id)).filter(AuditEvent.decision_outcome.ilike("%blocked%")).scalar() or 0
    escalated_count    = db.query(func.count(AuditEvent.id)).filter(AuditEvent.event_type == "ESCALATED").scalar() or 0
    flagged_count      = db.query(func.count(AuditEvent.id)).scalar() or 0
    pii_count          = db.query(func.count(AuditEvent.id)).filter(AuditEvent.event_type.ilike("%PII%")).scalar()  or 0
    throttle_prevented = db.query(func.count(AuditEvent.id)).filter(AuditEvent.event_type == "THROTTLE").scalar()  or 0
    collision_count    = db.query(func.count(AuditEvent.id)).filter(AuditEvent.event_type == "LOCK").scalar()       or 0

    # ── Executive Summary ROI ─────────────────────────────────────────────────
    FLAGSHIP_AVG = 0.030   # avg cost per flagship call ($0.03 at Opus 4 rates)
    full_flagship_cost = total_calls * FLAGSHIP_AVG
    routing_savings_usd = max(0.0, full_flagship_cost - (spend_month or 0.0))
    total_savings_usd   = routing_savings_usd + pruning_savings_usd
    projected_annual_savings = round(total_savings_usd * 12, 2)

    if full_flagship_cost > 0:
        cost_reduction_pct = round((routing_savings_usd / full_flagship_cost) * 100, 1)
        cost_reduction_pct = max(0, min(99, cost_reduction_pct))
    else:
        cost_reduction_pct = 0

    economy_calls = scout_calls + analyst_calls
    routing_efficiency_pct = round((economy_calls / total_calls) * 100, 1) if total_calls > 0 else 0

    # ── Recent audit events (last 5 for the KPI strip) ─────────────────────────
    recent_audits = db.query(AuditEvent).order_by(
        AuditEvent.timestamp.desc()
    ).limit(5).all()

    audit_strip = [
        {
            "id":          e.id,
            "event_type":  e.event_type,
            "department":  e.department,
            "risk_level":  e.risk_level,
            "timestamp":   e.timestamp.isoformat() if e.timestamp else None,
        }
        for e in recent_audits
    ]

    # ── Spend by department (for chart) ───────────────────────────────────────
    dept_spend = (
        db.query(TokenTransaction.department, func.sum(TokenTransaction.cost_usd))
        .group_by(TokenTransaction.department)
        .all()
    )
    spend_by_dept = {dept: round(amt, 4) for dept, amt in dept_spend}

    # ── Routing breakdown by department ────────────────────────────────────────
    routing_breakdown = (
        db.query(
            TokenTransaction.department,
            TokenTransaction.model_tier,
            func.count(TokenTransaction.id),
        )
        .group_by(TokenTransaction.department, TokenTransaction.model_tier)
        .all()
    )
    routing_by_dept = {}
    for dept, tier, cnt in routing_breakdown:
        if dept not in routing_by_dept:
            routing_by_dept[dept] = {"micro": 0, "flagship": 0}
        # Normalize tier names: Scout/Analyst → micro bucket, Advisor/Strategist → flagship bucket
        if tier in ("Scout", "Analyst", "micro"):
            routing_by_dept[dept]["micro"] = routing_by_dept[dept].get("micro", 0) + cnt
        elif tier in ("Advisor", "Strategist", "flagship"):
            routing_by_dept[dept]["flagship"] = routing_by_dept[dept].get("flagship", 0) + cnt
        else:
            routing_by_dept[dept][tier] = cnt

    return {
        # ── Top-line KPIs ──────────────────────────────────────────────────────
        "spend_today_usd":       round(spend_today, 4),
        "spend_month_usd":       round(spend_month, 4),
        "tokens_saved_today":    tokens_saved_today,
        "tokens_saved_total":    tokens_saved_total,
        "pruning_savings_usd":   pruning_savings_usd,
        "calls_today":           calls_today,
        "total_calls":           total_calls,
        "micro_calls":           micro_calls,
        "flagship_calls":        flagship_calls,
        "micro_pct":             micro_pct,
        "flagship_pct":          flagship_pct,
        "scout_calls":           scout_calls,
        "analyst_calls":         analyst_calls,
        "advisor_calls":         advisor_calls,
        "strategist_calls":      strategist_calls,
        "scout_pct":             _pct(scout_calls),
        "analyst_pct":           _pct(analyst_calls),
        "advisor_pct":           _pct(advisor_calls),
        "strategist_pct":        _pct(strategist_calls),

        # ── Agents ────────────────────────────────────────────────────────────
        "agents_total":          agents_total,
        "agents_active":         agents_active,
        "agents_locked":         agents_locked,
        "agents_idle":           agents_idle,

        # ── Budgets ───────────────────────────────────────────────────────────
        "throttled_count":       throttled_count,
        "total_cap_usd":         round(total_cap, 2),
        "total_spend_usd":       round(total_spend, 4),
        "overall_budget_pct":    overall_pct,
        "budget_summaries":      budget_summaries,

        # ── Spend chart data ──────────────────────────────────────────────────
        "spend_by_dept":         spend_by_dept,
        "routing_by_dept":       routing_by_dept,

        # ── Audit strip ───────────────────────────────────────────────────────
        "recent_audits":         audit_strip,

        # ── Governance & Compliance ────────────────────────────────────────────
        "blocked_count":         blocked_count,
        "escalated_count":       escalated_count,
        "flagged_count":         flagged_count,
        "pii_count":             pii_count,
        "throttle_prevented":    throttle_prevented,
        "collision_count":       collision_count,

        # ── Executive Summary ROI ──────────────────────────────────────────────
        "projected_annual_savings": projected_annual_savings,
        "routing_savings_usd":   round(routing_savings_usd, 6),
        "total_savings_usd":     round(total_savings_usd, 6),
        "routing_efficiency_pct": routing_efficiency_pct,
        "cost_reduction_pct":    cost_reduction_pct,
        "compliance_events_total": flagged_count,

        # ── Top Keywords ──────────────────────────────────────────────────────
        "keyword_stats":         _keyword_stats(db),

        # ── Meta ──────────────────────────────────────────────────────────────
        "generated_at":          datetime.utcnow().isoformat(),
    }
