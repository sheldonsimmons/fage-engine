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
from sqlalchemy import and_, case, func, or_
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    TokenTransaction, RegisteredAgent,
    AuditEvent, WorkItem, WorkItemOutcome,
)
from core.workspace_scope import workspace_filter as _workspace_filter

router = APIRouter()


def _keyword_stats(db: Session, days: int = 30, top_n: int = 10, workspace_id: str | None = None) -> list:
    """Count keyword frequency from matched_keywords_json on recent AuditEvents."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    filters = [
        AuditEvent.timestamp >= cutoff,
        AuditEvent.matched_keywords_json.isnot(None),
        AuditEvent.matched_keywords_json != "[]",
        AuditEvent.matched_keywords_json != "",
    ]
    workspace_clause = _workspace_filter(AuditEvent, workspace_id)
    if workspace_clause is not None:
        filters.append(workspace_clause)
    events = db.query(AuditEvent.matched_keywords_json).filter(*filters).all()
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
def get_dashboard(
    workspace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Single endpoint that powers the entire executive dashboard."""

    now         = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── Spend ──────────────────────────────────────────────────────────────────
    # Both spend figures query token_transactions directly so they always agree.
    tx_scope = _workspace_filter(TokenTransaction, workspace_id)
    audit_scope = _workspace_filter(AuditEvent, workspace_id)
    agent_scope = _workspace_filter(RegisteredAgent, workspace_id)

    def _filters(*items):
        return [x for x in items if x is not None]

    spend_today = db.query(func.sum(TokenTransaction.cost_usd)).filter(
        *_filters(tx_scope),
        TokenTransaction.timestamp >= today_start
    ).scalar() or 0.0

    spend_month = db.query(func.sum(TokenTransaction.cost_usd)).filter(
        *_filters(tx_scope),
        TokenTransaction.timestamp >= month_start
    ).scalar() or 0.0

    # ── Token savings from pruning ─────────────────────────────────────────────
    tokens_saved_today = db.query(func.sum(TokenTransaction.tokens_saved)).filter(
        *_filters(tx_scope),
        TokenTransaction.timestamp >= today_start,
        TokenTransaction.was_pruned == True,
    ).scalar() or 0

    tokens_saved_total = db.query(func.sum(TokenTransaction.tokens_saved)).filter(
        *_filters(tx_scope),
        TokenTransaction.was_pruned == True,
    ).scalar() or 0

    # Estimated dollar value of all pruning savings (blended micro/flagship rate)
    # Using micro rate as conservative floor estimate
    # Savings = tokens pruned × what they would have cost at Advisor/Sonnet rate
    # (not Scout rate — pruning saves against whatever model the call was using)
    ADVISOR_INPUT_PER_TOKEN = 3.00 / 1_000_000
    pruning_savings_usd = round((tokens_saved_total or 0) * ADVISOR_INPUT_PER_TOKEN, 6)

    # ── Call counts — exclude Voice Guard prune-only records (cost=$0, no AI call) ──
    # VOICE_GUARD_PRUNE rows exist only to record token savings; they are not AI calls.
    IS_AI_CALL = TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE"

    total_calls = db.query(func.count(TokenTransaction.id)).filter(*_filters(tx_scope, IS_AI_CALL)).scalar() or 0
    simulation_routed_calls = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.is_simulation.is_(True))
    ).scalar() or 0

    # Economy tiers: Scout (tier 1), Analyst (tier 2), and legacy "micro"
    ECONOMY_TIERS  = ("Scout", "Analyst", "micro")
    # Premium tiers: Advisor (tier 3), Strategist (tier 4), and legacy "flagship"
    PREMIUM_TIERS  = ("Advisor", "Strategist", "flagship")

    micro_calls    = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier.in_(ECONOMY_TIERS))
    ).scalar() or 0
    flagship_calls = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier.in_(PREMIUM_TIERS))
    ).scalar() or 0

    micro_pct    = round((micro_calls    / total_calls) * 100, 1) if total_calls else 0
    flagship_pct = round((flagship_calls / total_calls) * 100, 1) if total_calls else 0

    # Per-tier call counts
    scout_calls      = db.query(func.count(TokenTransaction.id)).filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier.in_(("Scout", "micro")))).scalar() or 0
    analyst_calls    = db.query(func.count(TokenTransaction.id)).filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier == "Analyst")).scalar() or 0
    advisor_calls    = db.query(func.count(TokenTransaction.id)).filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier.in_(("Advisor", "flagship")))).scalar() or 0
    strategist_calls = db.query(func.count(TokenTransaction.id)).filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.model_tier == "Strategist")).scalar() or 0

    def _pct(n): return round((n / total_calls) * 100, 1) if total_calls else 0

    calls_today = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope),
        IS_AI_CALL, TokenTransaction.timestamp >= today_start
    ).scalar() or 0

    scout_calls_today = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= today_start, TokenTransaction.model_tier.in_(("Scout", "micro")))
    ).scalar() or 0
    analyst_calls_today = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= today_start, TokenTransaction.model_tier == "Analyst")
    ).scalar() or 0
    advisor_calls_today = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= today_start, TokenTransaction.model_tier.in_(("Advisor", "flagship")))
    ).scalar() or 0
    strategist_calls_today = db.query(func.count(TokenTransaction.id)).filter(
        *_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= today_start, TokenTransaction.model_tier == "Strategist")
    ).scalar() or 0
    tier_split_today_total = scout_calls_today + analyst_calls_today + advisor_calls_today + strategist_calls_today

    def _today_pct(n): return round((n / tier_split_today_total) * 100, 1) if tier_split_today_total else 0

    # ── Agent counts ───────────────────────────────────────────────────────────
    agents_total  = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope)).scalar() or 0
    agents_active = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope, RegisteredAgent.status == "active")).scalar()  or 0
    agents_locked = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope, RegisteredAgent.status == "locked")).scalar()  or 0
    agents_idle   = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope, RegisteredAgent.status == "idle")).scalar()    or 0

    # ── Budget summaries ───────────────────────────────────────────────────────
    # current_spend_usd is the live-tracked counter real throttling enforcement
    # acts on — the source of truth for a production workspace, but it never
    # gets touched by backfilled/simulated data, so it reads $0.00 forever for
    # demo/simulation workspaces even with real recorded activity. Reuse
    # core.budget.get_all_budgets (the same function Admin > Budgets and Ask
    # CostPilot use) so this dashboard can't disagree with either of them.
    from core.budget import get_all_budgets
    enriched_budgets = get_all_budgets(db, workspace_id)
    throttled_count = sum(1 for b in enriched_budgets if b["throttled"])

    total_cap   = sum(b["monthly_cap_usd"]   for b in enriched_budgets)
    total_spend = sum(b["current_spend_usd"] for b in enriched_budgets)
    overall_pct = round((total_spend / total_cap) * 100, 1) if total_cap else 0

    budget_summaries = [
        {
            "department":        b["department"],
            "monthly_cap_usd":   b["monthly_cap_usd"],
            "current_spend_usd": b["current_spend_usd"],
            "used_pct":          b["used_pct"],
            "throttled":         b["throttled"],
            "override_granted":  b["override_granted"],
        }
        for b in enriched_budgets
    ]

    # ── Governance & Compliance stats ─────────────────────────────────────────
    blocked_count      = db.query(func.count(AuditEvent.id)).filter(*_filters(audit_scope, AuditEvent.decision_outcome.ilike("%blocked%"))).scalar() or 0
    escalated_count    = db.query(func.count(AuditEvent.id)).filter(*_filters(audit_scope, AuditEvent.event_type == "ESCALATED")).scalar() or 0
    flagged_count      = db.query(func.count(AuditEvent.id)).filter(*_filters(audit_scope)).scalar() or 0
    pii_count          = db.query(func.count(AuditEvent.id)).filter(*_filters(audit_scope, AuditEvent.event_type.ilike("%PII%"))).scalar()  or 0
    throttle_prevented = db.query(func.count(AuditEvent.id)).filter(
        *_filters(
            audit_scope,
            or_(
                AuditEvent.event_type == "THROTTLE",
                AuditEvent.rationale.ilike("%BUDGET CAP ENFORCED%"),
                AuditEvent.rationale.ilike("%capped at%"),
                AuditEvent.rationale.ilike("%downgraded to the micro-model tier%"),
            ),
        )
    ).scalar() or 0
    collision_lock_count = db.query(func.count(AuditEvent.id)).filter(
        *_filters(audit_scope, AuditEvent.event_type.in_(("LOCK", "COLLISION_LOCK")))
    ).scalar() or 0
    collision_queue_count = db.query(func.count(AuditEvent.id)).filter(
        *_filters(audit_scope, AuditEvent.event_type == "COLLISION_QUEUE")
    ).scalar() or 0
    collision_skip_count = db.query(func.count(AuditEvent.id)).filter(
        *_filters(audit_scope, AuditEvent.event_type == "COLLISION_SKIP")
    ).scalar() or 0
    collision_count = collision_lock_count + collision_queue_count + collision_skip_count

    # ── Executive Summary ROI ─────────────────────────────────────────────────
    FLAGSHIP_AVG = 0.030   # avg cost per flagship call ($0.03 at Opus 4 rates)
    requests_routed = scout_calls + analyst_calls + advisor_calls + strategist_calls
    requests_blocked = blocked_count
    requests_governed = requests_routed + requests_blocked
    simulation_blocked_calls = db.query(func.count(AuditEvent.id)).filter(
        *_filters(
            audit_scope,
            AuditEvent.decision_outcome.ilike("%blocked%"),
            AuditEvent.is_simulation.is_(True),
        )
    ).scalar() or 0
    simulation_calls = simulation_routed_calls + simulation_blocked_calls
    full_flagship_cost = requests_routed * FLAGSHIP_AVG
    routing_savings_usd = max(0.0, full_flagship_cost - (spend_month or 0.0))
    blocked_savings_usd = round(requests_blocked * 0.018, 6)
    throttle_savings_usd = round(throttle_prevented * FLAGSHIP_AVG, 6)
    total_savings_usd   = routing_savings_usd + pruning_savings_usd + blocked_savings_usd + throttle_savings_usd
    projected_annual_savings = round(total_savings_usd * 12, 2)

    if full_flagship_cost > 0:
        cost_reduction_pct = round((routing_savings_usd / full_flagship_cost) * 100, 1)
        cost_reduction_pct = max(0, min(99, cost_reduction_pct))
    else:
        cost_reduction_pct = 0

    economy_calls = scout_calls + analyst_calls
    routing_efficiency_pct = round((economy_calls / requests_routed) * 100, 1) if requests_routed > 0 else 0

    # ── Recent audit events (last 5 for the KPI strip) ─────────────────────────
    recent_query = db.query(AuditEvent)
    if audit_scope is not None:
        recent_query = recent_query.filter(audit_scope)
    recent_audits = recent_query.order_by(
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
        .filter(*_filters(tx_scope))
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
        .filter(*_filters(tx_scope))
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
        "simulation_calls":      simulation_calls,
        "live_calls":            max(0, requests_governed - simulation_calls),
        "requests_governed":     requests_governed,
        "requests_routed":       requests_routed,
        "requests_blocked":      requests_blocked,
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
        "tier_split_today": {
            "total":             tier_split_today_total,
            "scout_calls":       scout_calls_today,
            "analyst_calls":     analyst_calls_today,
            "advisor_calls":     advisor_calls_today,
            "strategist_calls":  strategist_calls_today,
            "scout_pct":         _today_pct(scout_calls_today),
            "analyst_pct":       _today_pct(analyst_calls_today),
            "advisor_pct":       _today_pct(advisor_calls_today),
            "strategist_pct":    _today_pct(strategist_calls_today),
        },

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
        "collision_breakdown": {
            "lock":  collision_lock_count,
            "queue": collision_queue_count,
            "skip":  collision_skip_count,
        },

        # ── Executive Summary ROI ──────────────────────────────────────────────
        "projected_annual_savings": projected_annual_savings,
        "routing_savings_usd":   round(routing_savings_usd, 6),
        "blocked_savings_usd":   blocked_savings_usd,
        "throttle_savings_usd":  throttle_savings_usd,
        "total_savings_usd":     round(total_savings_usd, 6),
        "routing_efficiency_pct": routing_efficiency_pct,
        "cost_reduction_pct":    cost_reduction_pct,
        "compliance_events_total": flagged_count,

        # ── Top Keywords ──────────────────────────────────────────────────────
        "keyword_stats":         _keyword_stats(db, workspace_id=workspace_id),

        # ── Meta ──────────────────────────────────────────────────────────────
        "generated_at":          datetime.utcnow().isoformat(),
    }


@router.get("/changes")
def get_dashboard_changes(
    workspace_id: str | None = Query(None),
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
):
    """
    Real period-over-period comparison -- "what changed" vs the prior period
    of equal length, entirely from SQL aggregates against real tables. No
    invented percentages: every number here is `current` and `previous`
    computed the same way, from the same columns, so the diff is honest.

    This intentionally does NOT try to explain business outcomes (pipeline,
    cases resolved, etc.) -- that needs a real link between AI activity and
    outcome data that doesn't exist yet. This only covers what CostPilot's
    own tables can already answer: spend, call volume, tier mix, new agents.
    """
    now = datetime.utcnow()
    current_start = now - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    tx_scope = _workspace_filter(TokenTransaction, workspace_id)
    agent_scope = _workspace_filter(RegisteredAgent, workspace_id)
    IS_AI_CALL = TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE"
    ECONOMY_TIERS = ("Scout", "Analyst", "micro")

    def _filters(*items):
        return [x for x in items if x is not None]

    def _period_totals(period_start, period_end):
        base = _filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= period_start, TokenTransaction.timestamp < period_end)
        spend = db.query(func.sum(TokenTransaction.cost_usd)).filter(*base).scalar() or 0.0
        calls = db.query(func.count(TokenTransaction.id)).filter(*base).scalar() or 0
        economy_calls = db.query(func.count(TokenTransaction.id)).filter(
            *base, TokenTransaction.model_tier.in_(ECONOMY_TIERS)
        ).scalar() or 0
        economy_pct = round((economy_calls / calls) * 100, 1) if calls else 0.0
        return {"spend": round(float(spend), 6), "calls": calls, "economy_pct": economy_pct}

    current = _period_totals(current_start, now)
    previous = _period_totals(prior_start, current_start)

    new_agents = db.query(func.count(RegisteredAgent.id)).filter(
        *_filters(agent_scope, RegisteredAgent.created_at >= current_start, RegisteredAgent.created_at < now)
    ).scalar() or 0

    def _pct_change(curr: float, prev: float) -> float | None:
        if prev == 0:
            return None  # undefined -- can't express "% change" from a zero baseline honestly
        return round(((curr - prev) / prev) * 100, 1)

    changes = []

    spend_pct = _pct_change(current["spend"], previous["spend"])
    if spend_pct is not None:
        direction = "increased" if spend_pct >= 0 else "decreased"
        changes.append({
            "metric": "spend",
            "label": "AI spend",
            "current": current["spend"],
            "previous": previous["spend"],
            "pct_change": spend_pct,
            "summary": f"AI spend {direction} {abs(spend_pct):.1f}% (${abs(current['spend'] - previous['spend']):,.2f}) vs the prior {days} days.",
        })

    calls_pct = _pct_change(current["calls"], previous["calls"])
    if calls_pct is not None:
        direction = "increased" if calls_pct >= 0 else "decreased"
        changes.append({
            "metric": "calls",
            "label": "Call volume",
            "current": current["calls"],
            "previous": previous["calls"],
            "pct_change": calls_pct,
            "summary": f"Call volume {direction} {abs(calls_pct):.1f}% ({current['calls']} vs {previous['calls']}) vs the prior {days} days.",
        })

    mix_shift = round(current["economy_pct"] - previous["economy_pct"], 1)
    if previous["calls"] and current["calls"] and abs(mix_shift) >= 1:
        direction = "toward" if mix_shift > 0 else "away from"
        changes.append({
            "metric": "model_mix",
            "label": "Model mix",
            "current": current["economy_pct"],
            "previous": previous["economy_pct"],
            "pct_change": mix_shift,
            "summary": f"Routing shifted {direction} economy-tier models ({previous['economy_pct']}% → {current['economy_pct']}% of calls).",
        })

    if new_agents:
        changes.append({
            "metric": "new_agents",
            "label": "New agents",
            "current": new_agents,
            "previous": 0,
            "pct_change": None,
            "summary": f"{new_agents} new agent{'s' if new_agents != 1 else ''} started sending AI activity this period.",
        })

    changes.sort(key=lambda c: abs(c["pct_change"]) if c["pct_change"] is not None else 0, reverse=True)

    return {
        "workspace_id": workspace_id,
        "period_days": days,
        "current_period": {"start": current_start.isoformat(), "end": now.isoformat()},
        "prior_period": {"start": prior_start.isoformat(), "end": current_start.isoformat()},
        "changes": changes,
    }


@router.get("/top-models")
def get_top_models(
    workspace_id: str | None = Query(None),
    days: int = Query(30, ge=1, le=180),
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Real per-model spend breakdown, SQL GROUP BY on model_name -- the exact
    provider/registry model string set at write time (e.g.
    "claude-3-5-sonnet"), not the coarser Scout/Analyst/Advisor/Strategist
    tier. Rows where model_name was never populated (older data, or a call
    path that only recorded tier) are grouped under their model_tier
    instead of silently dropped, labeled so that's clear rather than
    implied to be a real model name.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    tx_scope = _workspace_filter(TokenTransaction, workspace_id)
    IS_AI_CALL = TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE"

    def _filters(*items):
        return [x for x in items if x is not None]

    # COALESCE to model_tier (with a "(tier only)" suffix applied in
    # Python) rather than a SQL literal, so the label logic stays in one
    # place and is easy to change without touching the query.
    model_key = func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier)

    rows = (
        db.query(model_key, func.sum(TokenTransaction.cost_usd), func.count(TokenTransaction.id))
        .filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= cutoff))
        .group_by(model_key)
        .all()
    )

    # Tier names that appear ONLY because model_name was never set for that
    # row (fallback), so those rows can be labeled honestly as tier-only
    # rather than implied to be a specific model.
    tier_only_names = {
        tier for (tier,) in db.query(TokenTransaction.model_tier)
        .filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= cutoff, TokenTransaction.model_name.is_(None)))
        .distinct()
    }

    total_spend = sum(float(spend or 0.0) for _, spend, _ in rows)
    results = [
        {
            "model": name or "Unknown",
            "is_tier_only": name in tier_only_names,
            "spend_usd": round(float(spend or 0.0), 6),
            "calls": count,
            "pct_of_total": round((float(spend or 0.0) / total_spend) * 100, 1) if total_spend else 0.0,
        }
        for name, spend, count in rows
    ]
    results.sort(key=lambda r: r["spend_usd"], reverse=True)

    return {
        "workspace_id": workspace_id,
        "period_days": days,
        "total_spend_usd": round(total_spend, 6),
        "models": results[:limit],
    }


@router.get("/business-impact")
def get_business_impact(
    workspace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Workspace-wide version of api/routes_work_items.py's account_profile()
    outcome totals -- same real WorkItemOutcome aggregation (opportunity
    won/lost/open counts, pipeline value, closed-won value, resolved
    support cases), just scoped to every work item in the workspace
    instead of one account's. Nothing new computed here; this widens an
    already-proven query's WHERE clause.

    Real data only for workspaces with an outcome-sync-connected platform
    (Salesforce today) -- has_outcome_data distinguishes "genuinely zero"
    from "no outcome data exists yet" so the frontend doesn't have to
    guess which one a set of zeros means.
    """
    work_item_scope = _workspace_filter(WorkItem, workspace_id)

    def _scoped(query):
        return query.filter(work_item_scope) if work_item_scope is not None else query

    is_lost = and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    is_open = WorkItemOutcome.is_closed.is_(False)
    is_won = WorkItemOutcome.outcome_success.is_(True)
    outcome_value = func.coalesce(WorkItemOutcome.outcome_value, 0.0)

    # Scoped to context_type == "opportunity" specifically -- without this,
    # a Case that's still open would count toward "opportunities open" too,
    # since WorkItemOutcome itself doesn't distinguish deal type. Found by
    # a failing test, not by inspection: a workspace mixing Opportunities
    # and Cases makes this ambiguity far more visible than it is on
    # account_profile()'s per-account version, which has the same
    # characteristic but wasn't in scope to fix here.
    won_count, lost_count, open_count, pipeline_value, closed_won_value = _scoped(
        db.query(
            func.coalesce(func.sum(case((is_won, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_lost, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_open, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_open, outcome_value), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((is_won, outcome_value), else_=0.0)), 0.0),
        )
        .join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type == "opportunity")
    ).first()

    SUPPORT_CONTEXT_TYPES = ("case", "ticket", "incident")
    support_total, support_resolved = _scoped(
        db.query(
            func.count(WorkItem.id),
            func.coalesce(func.sum(case((WorkItemOutcome.is_closed.is_(True), 1), else_=0)), 0),
        )
        .outerjoin(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type.in_(SUPPORT_CONTEXT_TYPES))
    ).first()

    won_count, lost_count, open_count = int(won_count or 0), int(lost_count or 0), int(open_count or 0)
    support_resolved = int(support_resolved or 0)
    has_outcome_data = bool(won_count + lost_count + open_count + support_resolved)

    return {
        "workspace_id": workspace_id,
        "has_outcome_data": has_outcome_data,
        "opportunities_won": won_count,
        "opportunities_lost": lost_count,
        "opportunities_open": open_count,
        "pipeline_value_usd": round(float(pipeline_value or 0.0), 2),
        "closed_won_value_usd": round(float(closed_won_value or 0.0), 2),
        "support_cases_total": int(support_total or 0),
        "support_cases_resolved": support_resolved,
    }
