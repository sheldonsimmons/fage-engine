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
from core.metrics_query import run_metrics_query

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

    # Routed through the canonical metrics registry (core/metrics_query.py)
    # instead of an independent func.sum() -- this was the headline-KPI
    # instance of "AI spend" computed a separate way from get_dashboard_
    # changes/get_top_models/get_business_impact in this same file, which
    # already use run_metrics_query(). Same ai_spend metric definition,
    # same workspace_filter() semantics (workspace_id=None -> no filter,
    # identical to the old tx_scope behavior), real datetime objects in
    # timeframe (not .isoformat() strings -- see this metric's own
    # SQLite-vs-Postgres timeframe bug fixed earlier this session).
    def _ai_spend_for(start: datetime, end: datetime) -> float:
        result = run_metrics_query(
            db, workspace_id, metrics=["ai_spend"],
            timeframe={"start": start, "end": end},
        )
        if result.rows:
            return result.rows[0].get("ai_spend") or 0.0
        return 0.0

    spend_today = _ai_spend_for(today_start, now)
    spend_month = _ai_spend_for(month_start, now)

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
    IS_AI_CALL = or_(TokenTransaction.routing_reason.is_(None), TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE")

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
    # Active/idle used to be judged by RegisteredAgent.status -- an
    # administrative field nothing in the codebase ever sets to "active"
    # based on real traffic (confirmed via search: it only ever changes via
    # direct edit, e.g. admin.html, and defaults to "idle" forever
    # otherwise). Reproduced live: a workspace with two agents that had
    # both just sent real AI activity ("What Changed" correctly detected
    # this from the transaction ledger) still showed Active Agents = 0,
    # 2 idle, because status had simply never been touched. last_used_at
    # is a real column, updated by every live call (see routes_router.py/
    # routes_enrich.py/routes_proxy.py) -- recency of real activity is
    # what "active" should mean here, not a manually-set label.
    agents_total  = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope)).scalar() or 0
    agents_locked = db.query(func.count(RegisteredAgent.id)).filter(*_filters(agent_scope, RegisteredAgent.status == "locked")).scalar()  or 0
    active_cutoff = datetime.utcnow() - timedelta(days=7)
    agents_active = db.query(func.count(RegisteredAgent.id)).filter(
        *_filters(agent_scope, RegisteredAgent.status != "locked", RegisteredAgent.last_used_at >= active_cutoff)
    ).scalar() or 0
    agents_idle = max(0, agents_total - agents_active - agents_locked)

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

    total_cap = sum(b["monthly_cap_usd"] for b in enriched_budgets)
    # budgeted_spend (sum of only the departments with a configured budget
    # row) drives overall_pct -- cap utilization is only meaningful against
    # spend a cap actually covers. total_spend_usd itself now reuses
    # spend_month (the same all-department MTD sum "AI Investment" shows)
    # instead of a second independent sum-of-budgets total: confirmed live
    # on SIM-HISTORICAL-2Y these two disagreed by $0.0165/mo because a
    # "Marketing" department had real spend but no DepartmentBudget row,
    # so the old sum-of-budgets total silently dropped it. Both KPI cards
    # now share one variable and can't drift apart again.
    budgeted_spend = sum(b["current_spend_usd"] for b in enriched_budgets)
    unbudgeted_spend = max(0.0, round(spend_month - budgeted_spend, 6))
    overall_pct = round((budgeted_spend / total_cap) * 100, 1) if total_cap else 0

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
        "total_spend_usd":       round(spend_month, 4),
        "unbudgeted_spend_usd":  unbudgeted_spend,
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


def _spend_driver_department(
    db: Session, workspace_id: str | None,
    current_start: datetime, current_end: datetime, prior_start: datetime, prior_end: datetime,
    *, total_delta: float,
) -> dict | None:
    """
    Which department contributed most to an overall spend swing --
    "driver attribution" for the "AI spend" change entry. Per-department
    spend for the same current/prior windows already computed for the
    overall total, just grouped one more dimension. Returns None when the
    total delta is negligible (avoids a meaningless "100% of a $0.01
    swing" driver) or no single department explains a meaningful share.
    """
    if abs(total_delta) < 0.01:
        return None
    tx_scope = _workspace_filter(TokenTransaction, workspace_id)

    def _spend_by_department(start, end):
        base = [tx_scope] if tx_scope is not None else []
        rows = (
            db.query(TokenTransaction.department, func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .filter(*base, TokenTransaction.timestamp >= start, TokenTransaction.timestamp < end)
            .group_by(TokenTransaction.department)
            .all()
        )
        return {(dept or "Unassigned").split(":")[-1]: float(spend or 0.0) for dept, spend in rows}

    current_by_dept = _spend_by_department(current_start, current_end)
    prior_by_dept = _spend_by_department(prior_start, prior_end)
    all_depts = set(current_by_dept) | set(prior_by_dept)
    if not all_depts:
        return None

    best_dept, best_delta = None, 0.0
    for dept in all_depts:
        delta = current_by_dept.get(dept, 0.0) - prior_by_dept.get(dept, 0.0)
        # Same sign as the overall change -- a department moving opposite
        # the overall trend isn't "driving" it.
        if abs(delta) > abs(best_delta) and (delta >= 0) == (total_delta >= 0):
            best_dept, best_delta = dept, delta
    if not best_dept or abs(total_delta) < 0.01:
        return None
    contribution_pct = round(min(100.0, abs(best_delta / total_delta) * 100), 1)
    if contribution_pct < 20:
        return None  # no single department meaningfully explains this swing
    return {"department": best_dept, "delta_usd": round(best_delta, 6), "contribution_pct": contribution_pct}


def _spend_driver_agent(
    db: Session, workspace_id: str | None,
    current_start: datetime, current_end: datetime, prior_start: datetime, prior_end: datetime,
    *, total_delta: float,
) -> dict | None:
    """
    Same shape as _spend_driver_department, one dimension down -- which
    single agent contributed most to the overall spend swing, so the
    summary sentence can name both the department and the agent behind it
    ("Sales accounted for 32% of the increase, primarily driven by Agent
    X") instead of stopping at department level.
    """
    if abs(total_delta) < 0.01:
        return None
    tx_scope = _workspace_filter(TokenTransaction, workspace_id)

    def _spend_by_agent(start, end):
        base = [tx_scope] if tx_scope is not None else []
        rows = (
            db.query(RegisteredAgent.name, func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .select_from(TokenTransaction)
            .join(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
            .filter(*base, TokenTransaction.timestamp >= start, TokenTransaction.timestamp < end)
            .group_by(RegisteredAgent.name)
            .all()
        )
        return {name: float(spend or 0.0) for name, spend in rows}

    current_by_agent = _spend_by_agent(current_start, current_end)
    prior_by_agent = _spend_by_agent(prior_start, prior_end)
    all_agents = set(current_by_agent) | set(prior_by_agent)
    if not all_agents:
        return None

    best_agent, best_delta = None, 0.0
    for agent in all_agents:
        delta = current_by_agent.get(agent, 0.0) - prior_by_agent.get(agent, 0.0)
        if abs(delta) > abs(best_delta) and (delta >= 0) == (total_delta >= 0):
            best_agent, best_delta = agent, delta
    if not best_agent:
        return None
    contribution_pct = round(min(100.0, abs(best_delta / total_delta) * 100), 1)
    if contribution_pct < 20:
        return None
    return {"agent": best_agent, "delta_usd": round(best_delta, 6), "contribution_pct": contribution_pct}


def _biggest_model_spend_shift(
    db: Session, workspace_id: str | None,
    current_start: datetime, current_end: datetime, prior_start: datetime, prior_end: datetime,
    days: int,
) -> dict | None:
    """
    Which specific model (not just tier) had the largest spend increase --
    "Claude Sonnet usage increased 43%. Estimated additional monthly cost:
    $4,800" from the approved recommendation spec. Estimated monthly cost
    scales the observed window's delta to a 30-day-equivalent rate,
    regardless of the actual `days` window requested, so this number means
    the same thing whether the caller asked for a 7-day or 90-day change.
    """
    tx_scope = _workspace_filter(TokenTransaction, workspace_id)

    def _spend_by_model(start, end):
        base = [tx_scope] if tx_scope is not None else []
        rows = (
            db.query(
                func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model"),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.count(TokenTransaction.id),
            )
            .filter(*base, TokenTransaction.timestamp >= start, TokenTransaction.timestamp < end)
            .group_by(func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model"))
            .all()
        )
        return {model: (float(spend or 0.0), int(count or 0)) for model, spend, count in rows}

    current_by_model = _spend_by_model(current_start, current_end)
    prior_by_model = _spend_by_model(prior_start, prior_end)
    all_models = set(current_by_model) | set(prior_by_model)

    best_model, best_delta, best_pct = None, 0.0, None
    for model in all_models:
        curr_spend, _ = current_by_model.get(model, (0.0, 0))
        prev_spend, _ = prior_by_model.get(model, (0.0, 0))
        if prev_spend <= 0 or curr_spend <= 0:
            continue  # a brand-new or discontinued model isn't a "usage shift" -- new_agents/other entries cover new activity
        pct = ((curr_spend - prev_spend) / prev_spend) * 100
        delta = curr_spend - prev_spend
        if abs(delta) > abs(best_delta):
            best_model, best_delta, best_pct = model, delta, pct

    if not best_model or abs(best_pct or 0) < 20 or abs(best_delta) < 0.01:
        return None  # not a meaningful shift

    monthly_delta = best_delta * (30.0 / max(days, 1))
    direction = "increased" if best_delta >= 0 else "decreased"
    cost_word = "additional" if best_delta >= 0 else "reduced"
    return {
        "metric": "model_shift",
        "label": f"{best_model} usage",
        "current": round(current_by_model.get(best_model, (0.0, 0))[0], 6),
        "previous": round(prior_by_model.get(best_model, (0.0, 0))[0], 6),
        "pct_change": round(best_pct, 1),
        "summary": (
            f"{best_model} usage {direction} {abs(best_pct):.0f}%. "
            f"Estimated {cost_word} monthly cost: ${abs(monthly_delta):,.0f}."
        ),
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
    from core.metrics_query import run_metrics_query

    now = datetime.utcnow()
    current_start = now - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    tx_scope = _workspace_filter(TokenTransaction, workspace_id)
    agent_scope = _workspace_filter(RegisteredAgent, workspace_id)
    IS_AI_CALL = or_(TokenTransaction.routing_reason.is_(None), TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE")
    ECONOMY_TIERS = ("Scout", "Analyst", "micro")

    def _filters(*items):
        return [x for x in items if x is not None]

    # spend/calls current-vs-prior comes from the shared metrics layer's
    # own comparison support (Milestone 4) instead of a locally re-run
    # pair of SUM/COUNT queries -- same ai_spend/ai_requests definitions
    # every other caller uses. economy_pct (a derived ratio, not a
    # catalog metric) still needs its own query below.
    spend_calls = run_metrics_query(
        db, workspace_id, metrics=["ai_spend", "ai_requests"],
        timeframe={"start": current_start, "end": now}, compare_to="previous_period",
    )
    cmp_row = spend_calls.comparison["rows"][0] if spend_calls.comparison and spend_calls.comparison["rows"] else {
        "ai_spend": {"current": 0.0, "previous": 0.0, "pct_difference": None},
        "ai_requests": {"current": 0, "previous": 0, "pct_difference": None},
    }

    def _period_economy_pct(period_start, period_end):
        base = _filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= period_start, TokenTransaction.timestamp < period_end)
        calls = db.query(func.count(TokenTransaction.id)).filter(*base).scalar() or 0
        economy_calls = db.query(func.count(TokenTransaction.id)).filter(
            *base, TokenTransaction.model_tier.in_(ECONOMY_TIERS)
        ).scalar() or 0
        return round((economy_calls / calls) * 100, 1) if calls else 0.0

    current = {
        "spend": round(cmp_row["ai_spend"]["current"], 6),
        "calls": int(cmp_row["ai_requests"]["current"]),
        "economy_pct": _period_economy_pct(current_start, now),
    }
    previous = {
        "spend": round(cmp_row["ai_spend"]["previous"], 6),
        "calls": int(cmp_row["ai_requests"]["previous"]),
        "economy_pct": _period_economy_pct(prior_start, current_start),
    }

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
        change_noun = "increase" if spend_pct >= 0 else "decrease"
        summary = f"AI spend {direction} {abs(spend_pct):.1f}% (${abs(current['spend'] - previous['spend']):,.2f}) vs the prior {days} days."
        driver = _spend_driver_department(
            db, workspace_id, current_start, now, prior_start, current_start,
            total_delta=current["spend"] - previous["spend"],
        )
        agent_driver = _spend_driver_agent(
            db, workspace_id, current_start, now, prior_start, current_start,
            total_delta=current["spend"] - previous["spend"],
        )
        if driver:
            summary += f" Primary driver: {driver['department']} ({driver['contribution_pct']:.0f}% of the {change_noun})"
            summary += f", primarily driven by {agent_driver['agent']}." if agent_driver else "."
        changes.append({
            "metric": "spend",
            "label": "AI spend",
            "current": current["spend"],
            "previous": previous["spend"],
            "pct_change": spend_pct,
            "summary": summary,
            "driver": driver,
            "agent_driver": agent_driver,
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

    model_shift = _biggest_model_spend_shift(db, workspace_id, current_start, now, prior_start, current_start, days)
    if model_shift:
        changes.append(model_shift)

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
    Real per-model spend breakdown, grouped via the shared metrics layer
    (core.metrics_query -- Milestone 4 of the reporting-intelligence
    upgrade) instead of a locally-written SUM/COUNT/GROUP BY, so "ai_spend"
    means exactly the same thing here as everywhere else that calls
    query_metrics. Model rows where model_name was never populated (older
    data, or a call path that only recorded tier) are grouped under their
    model_tier instead of silently dropped -- that tier-only labeling is
    bespoke to this endpoint (the shared "model" dimension doesn't carry
    it), so it's computed here as a thin second query, same pattern
    already used by this file's own provider_breakdown-style derivations.
    """
    from core.metrics_query import run_metrics_query

    tx_scope = _workspace_filter(TokenTransaction, workspace_id)
    IS_AI_CALL = or_(TokenTransaction.routing_reason.is_(None), TokenTransaction.routing_reason != "VOICE_GUARD_PRUNE")
    cutoff = datetime.utcnow() - timedelta(days=days)

    def _filters(*items):
        return [x for x in items if x is not None]

    tier_only_names = {
        tier for (tier,) in db.query(TokenTransaction.model_tier)
        .filter(*_filters(tx_scope, IS_AI_CALL, TokenTransaction.timestamp >= cutoff, TokenTransaction.model_name.is_(None)))
        .distinct()
    }

    # total_spend_usd must reflect every model, not just the top `limit`
    # (the old unlimited-GROUP-BY-then-slice behavior) -- request
    # run_metrics_query's own row cap (100) rather than `limit` itself, so
    # pct_of_total/total_spend_usd stay correct unless a single workspace
    # has more than 100 distinct models, which no real workspace has hit
    # so far.
    result = run_metrics_query(
        db, workspace_id, metrics=["ai_spend", "ai_requests"], dimensions=["model"],
        timeframe={"start": cutoff, "end": datetime.utcnow()}, sort="ai_spend", limit=100,
    )
    total_spend = sum(r["ai_spend"] for r in result.rows)
    results = [
        {
            "model": r["dimensions"]["model"],
            "is_tier_only": r["dimensions"]["model"] in tier_only_names,
            "spend_usd": round(r["ai_spend"], 6),
            "calls": r["ai_requests"],
            "pct_of_total": round((r["ai_spend"] / total_spend) * 100, 1) if total_spend else 0.0,
        }
        for r in result.rows
    ]

    return {
        "workspace_id": workspace_id,
        "period_days": days,
        "total_spend_usd": round(total_spend, 6),
        "models": results[:limit],
    }


# Same "which context_types count as support work" scope
# support_cases_total/resolved already use (core/metrics_query.py:383) --
# support_cost_per_resolution_usd used to be silently narrower ("case"
# only), which meant it and support_cases_resolved were measuring
# different populations despite being shown side by side. Fixed here so
# every support-scoped figure in this endpoint agrees.
_SUPPORT_CONTEXT_TYPES = ("case", "ticket", "incident")


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

    Real data for any workspace with outcome rows in WorkItemOutcome,
    regardless of source -- pull-synced (Salesforce/ServiceNow, via
    core/outcome_adapters/*.py) or push-ingested via Universal Outcome
    Ingestion (core/outcome_ingestion.py, any custom platform). This query
    has no source_system filter and never has; has_outcome_data
    distinguishes "genuinely zero" from "no outcome data exists yet" so
    the frontend doesn't have to guess which one a set of zeros means.

    Every ratio KPI now carries its OWN evidence label (sized off that
    KPI's actual sample count), not one workspace-wide label sized off
    won_count alone applied to everything -- found live: this endpoint's
    single evidence_label was being shown next to
    support_cost_per_resolution_usd, a completely different sample size
    than the won-opportunity count that label was actually measuring.
    """
    from core.metrics_query import (
        run_metrics_query, compute_outcome_coverage, compute_cost_per_outcome,
        compute_potential_savings, evidence_for_sample,
    )

    work_item_scope = _workspace_filter(WorkItem, workspace_id)

    def _scoped(query):
        return query.filter(work_item_scope) if work_item_scope is not None else query

    # won/lost/open/pipeline/closed-won/support-case counts now come from
    # the shared metrics layer's outcome-sourced metrics (Milestone 4) --
    # exact same context_type='opportunity' and support-type scoping this
    # endpoint already established, just no longer duplicated here.
    outcome_result = run_metrics_query(
        db, workspace_id,
        metrics=["won_count", "lost_count", "open_count", "won_value", "pipeline_value",
                 "support_cases_total", "support_cases_resolved"],
    )
    o = outcome_result.rows[0] if outcome_result.rows else {
        "won_count": 0, "lost_count": 0, "open_count": 0, "won_value": 0.0,
        "pipeline_value": 0.0, "support_cases_total": 0, "support_cases_resolved": 0,
    }
    won_count, lost_count, open_count = int(o["won_count"]), int(o["lost_count"]), int(o["open_count"])
    pipeline_value, closed_won_value = float(o["pipeline_value"]), float(o["won_value"])
    support_total, support_resolved = int(o["support_cases_total"]), int(o["support_cases_resolved"])
    support_unresolved = max(support_total - support_resolved, 0)
    has_outcome_data = bool(won_count + lost_count + open_count + support_resolved)

    # AI investment specifically tied to the work items that have real
    # outcome data -- association, not causation: this is "how much AI
    # activity touched the deals/cases in this picture," never framed as
    # "AI caused this result" (same guardrail already enforced in Ask
    # CostPilot's narration). Only counts transactions actually linked to
    # an outcome-bearing work item, so it's a subset of total workspace
    # spend, not a duplicate of the Total AI Spend KPI.
    ai_spend, ai_tokens = _scoped(
        db.query(
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.coalesce(func.sum(TokenTransaction.input_tokens + TokenTransaction.output_tokens), 0),
        )
        .select_from(TokenTransaction)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
    ).first()

    # Outcome Coverage + cost-per-outcome -- reuses the exact functions
    # built for the account-level Business Impact summary
    # (routes_work_items.py's account_profile()), widened to the whole
    # workspace via workspace_id alone (no account_name filter), which
    # sidesteps the name-collision bug already found once this session in
    # an account_name-based lookup (two WorkAccount rows sharing one
    # name) -- there's no equivalent risk at workspace scope. Both already
    # carry their own real sample-size-based evidence label -- reused
    # directly below instead of recomputed.
    coverage = compute_outcome_coverage(db, workspace_id)
    cost_per_outcome = compute_cost_per_outcome(db, workspace_id)
    potential_savings = compute_potential_savings(db, workspace_id)

    # Business Impact's deeper economics layer -- same TokenTransaction ->
    # WorkItem -> WorkItemOutcome join the ai_spend query above already
    # uses, split by outcome instead of pooled, and scoped to
    # context_type == "opportunity" specifically (the ai_spend query above
    # pools every context_type with an outcome -- Opportunity, Case,
    # etc. -- these are Opportunity-specific by design, matching "Cost per
    # Won Opportunity" rather than the generic workspace-wide "Cost per
    # Successful Outcome" KPI already shipped).
    is_won = WorkItemOutcome.outcome_success.is_(True)
    is_lost = and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    opp_won_spend, opp_lost_spend, opp_total_spend = _scoped(
        db.query(
            func.coalesce(func.sum(case((is_won, TokenTransaction.cost_usd), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((is_lost, TokenTransaction.cost_usd), else_=0.0)), 0.0),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        )
        .select_from(TokenTransaction)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type == "opportunity")
    ).first()
    opp_won_spend = float(opp_won_spend or 0.0)
    opp_lost_spend = float(opp_lost_spend or 0.0)
    opp_total_spend = float(opp_total_spend or 0.0)
    opp_count = won_count + lost_count + open_count

    cost_per_won_opportunity_usd = round(opp_won_spend / won_count, 6) if won_count else None
    ai_investment_on_lost_opportunities_usd = round(opp_lost_spend, 6) if lost_count else None
    avg_ai_investment_per_opportunity_usd = round(opp_total_spend / opp_count, 6) if opp_count else None

    # Support Cost per Resolution -- same shape as the opportunity split
    # above, scoped to every support context_type (case/ticket/incident),
    # divided by the resolved count already computed above. Also exposes
    # resolved/unresolved spend as raw numbers (not just the ratio) for
    # the Support Impact comparison section.
    support_resolved_spend, support_total_spend = _scoped(
        db.query(
            func.coalesce(func.sum(case((WorkItemOutcome.is_closed.is_(True), TokenTransaction.cost_usd), else_=0.0)), 0.0),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        )
        .select_from(TokenTransaction)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type.in_(_SUPPORT_CONTEXT_TYPES))
    ).first()
    support_resolved_spend = float(support_resolved_spend or 0.0)
    support_total_spend = float(support_total_spend or 0.0)
    support_unresolved_spend = round(max(support_total_spend - support_resolved_spend, 0.0), 6)
    support_cost_per_resolution_usd = (
        round(support_resolved_spend / support_resolved, 6) if support_resolved else None
    )

    # Period-over-period trend for the cost-per-outcome ratios above, PLUS
    # the workspace-wide cost_per_successful_outcome_usd KPI, which used
    # to have NO trend computed for it at all -- the frontend was reading
    # cost_per_won_opportunity_usd's trend for both cards, showing
    # identical numbers under two different KPIs. These ratios are
    # all-time by design (a small workspace needs its full history to
    # clear MIN_SAMPLE-style noise floors), so there's no natural "prior
    # period" for the ratio itself -- this instead recomputes the same
    # ratio restricted to two adjacent 30-day windows (by
    # WorkItemOutcome.outcome_date for the count side, TokenTransaction.
    # timestamp for the spend side, same join shape as above) and diffs
    # those. Reuses the same current-vs-prior-period-of-equal-length shape
    # as get_dashboard_changes rather than inventing a second comparison
    # method. Null (not zero) whenever a window's denominator is zero --
    # never fabricates a trend from an empty period.
    def _period_ratios(period_start, period_end):
        base_outcome_filter = [
            WorkItemOutcome.outcome_date >= period_start,
            WorkItemOutcome.outcome_date < period_end,
        ]
        won_n = _scoped(
            db.query(func.count(WorkItemOutcome.id))
            .select_from(WorkItemOutcome).join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItem.context_type == "opportunity", is_won, *base_outcome_filter)
        ).scalar() or 0
        lost_n = _scoped(
            db.query(func.count(WorkItemOutcome.id))
            .select_from(WorkItemOutcome).join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItem.context_type == "opportunity", is_lost, *base_outcome_filter)
        ).scalar() or 0
        resolved_n = _scoped(
            db.query(func.count(WorkItemOutcome.id))
            .select_from(WorkItemOutcome).join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItem.context_type.in_(_SUPPORT_CONTEXT_TYPES), WorkItemOutcome.is_closed.is_(True), *base_outcome_filter)
        ).scalar() or 0
        # Any context_type -- matches compute_cost_per_outcome()'s own
        # "successful_outcomes" definition, not scoped to opportunities.
        succ_n = _scoped(
            db.query(func.count(WorkItemOutcome.id))
            .select_from(WorkItemOutcome).join(WorkItem, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(is_won, *base_outcome_filter)
        ).scalar() or 0

        tx_filter = [TokenTransaction.timestamp >= period_start, TokenTransaction.timestamp < period_end]
        won_spend, lost_spend, opp_spend = _scoped(
            db.query(
                func.coalesce(func.sum(case((is_won, TokenTransaction.cost_usd), else_=0.0)), 0.0),
                func.coalesce(func.sum(case((is_lost, TokenTransaction.cost_usd), else_=0.0)), 0.0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            )
            .select_from(TokenTransaction)
            .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
            .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItem.context_type == "opportunity", *tx_filter)
        ).first()
        support_spend = _scoped(
            db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .select_from(TokenTransaction)
            .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
            .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(WorkItem.context_type.in_(_SUPPORT_CONTEXT_TYPES), WorkItemOutcome.is_closed.is_(True), *tx_filter)
        ).scalar() or 0.0
        succ_spend = _scoped(
            db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .select_from(TokenTransaction)
            .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
            .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
            .filter(is_won, *tx_filter)
        ).scalar() or 0.0
        # Closed opportunities only (won + lost) -- unlike the all-time
        # avg_ai_investment_per_opportunity_usd figure above, this window
        # can't see currently-open opportunities that haven't closed yet,
        # so it's a "closed-opportunity" trend, not a literal window
        # version of that all-time metric.
        closed_n = won_n + lost_n

        return {
            "cost_per_won_opportunity_usd": (float(won_spend) / won_n) if won_n else None,
            "ai_investment_on_lost_opportunities_usd": float(lost_spend) if lost_n else None,
            "avg_ai_investment_per_opportunity_usd": (float(opp_spend) / closed_n) if closed_n else None,
            "support_cost_per_resolution_usd": (float(support_spend) / resolved_n) if resolved_n else None,
            "cost_per_successful_outcome_usd": (float(succ_spend) / succ_n) if succ_n else None,
        }

    def _trend_pct(curr: float | None, prev: float | None) -> float | None:
        if curr is None or prev is None or prev == 0:
            return None
        return round(((curr - prev) / prev) * 100, 1)

    _now = datetime.utcnow()
    _current_period = _period_ratios(_now - timedelta(days=30), _now)
    _prior_period = _period_ratios(_now - timedelta(days=60), _now - timedelta(days=30))
    trend_pct_change = {
        key: _trend_pct(_current_period[key], _prior_period[key])
        for key in _current_period
    }

    return {
        "workspace_id": workspace_id,
        "has_outcome_data": has_outcome_data,
        "opportunities_won": won_count,
        "opportunities_lost": lost_count,
        "opportunities_open": open_count,
        "pipeline_value_usd": round(float(pipeline_value or 0.0), 2),
        "closed_won_value_usd": round(float(closed_won_value or 0.0), 2),
        "won_ai_investment_usd": round(opp_won_spend, 6),
        "lost_ai_investment_usd": round(opp_lost_spend, 6),
        "support_cases_total": int(support_total or 0),
        "support_cases_resolved": support_resolved,
        "support_cases_unresolved": support_unresolved,
        "support_resolved_ai_investment_usd": round(support_resolved_spend, 6),
        "support_unresolved_ai_investment_usd": support_unresolved_spend,
        "ai_spend_usd": round(float(ai_spend or 0.0), 6),
        "ai_tokens_total": int(ai_tokens or 0),
        "outcome_coverage_pct": coverage["outcome_coverage_pct"],
        "successful_outcomes": won_count,
        # Deprecated: use the per-KPI evidence_by_kpi block below. Kept
        # (won_count-based, unchanged value) so any existing caller reading
        # this top-level field doesn't silently break.
        "evidence_label": evidence_for_sample(won_count, noun="won opportunities")["evidence_label"],
        "cost_per_successful_outcome_usd": cost_per_outcome["cost_per_successful_outcome_usd"],
        "cost_per_won_opportunity_usd": cost_per_won_opportunity_usd,
        "ai_investment_on_lost_opportunities_usd": ai_investment_on_lost_opportunities_usd,
        "avg_ai_investment_per_opportunity_usd": avg_ai_investment_per_opportunity_usd,
        "support_cost_per_resolution_usd": support_cost_per_resolution_usd,
        # Per-KPI evidence -- each sized off the sample this SPECIFIC
        # ratio's denominator actually is, not one label borrowed from a
        # different metric's sample size.
        "evidence_by_kpi": {
            "cost_per_successful_outcome_usd": cost_per_outcome["evidence_label"],
            "cost_per_won_opportunity_usd": evidence_for_sample(won_count, noun="won opportunities")["evidence_label"],
            "ai_investment_on_lost_opportunities_usd": evidence_for_sample(lost_count, noun="lost opportunities")["evidence_label"],
            "avg_ai_investment_per_opportunity_usd": evidence_for_sample(opp_count, noun="tracked opportunities")["evidence_label"],
            "support_cost_per_resolution_usd": evidence_for_sample(support_resolved, noun="resolved support items")["evidence_label"],
            "outcome_coverage_pct": evidence_for_sample(coverage["work_items_touched"], noun="AI-touched work items")["evidence_label"],
        },
        "trend_pct_change": trend_pct_change,
        "potential_savings_usd": potential_savings["potential_savings_usd"],
        "potential_savings_evidence": potential_savings["evidence"],
        "potential_savings_candidate_count": potential_savings["candidate_request_count"],
        "potential_savings_top_agents": potential_savings["top_agents"],
        "potential_savings_note": potential_savings["note"],
    }


@router.get("/business-impact/top-work-items")
def get_business_impact_top_work_items(
    workspace_id: str | None = Query(None),
    outcome_status: str | None = Query(None, description="won|lost|successful|unsuccessful|open|None"),
    rank_by: str = Query("spend", description="spend|cost_ratio"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Top WorkItems by AI investment, optionally narrowed to one outcome
    bucket -- "highest AI investment on unsuccessful work," "highest
    spend on won opportunities," etc.

    rank_by="spend" (default): raw AI spend. Zero new aggregation --
    run_metrics_query()'s existing "work_item" dimension + the existing
    outcome_status filter (core/metrics_query.py's _outcome_status_clause).

    rank_by="cost_ratio": AI spend as a fraction of the outcome's own
    value (spend / outcome_value) -- surfaces WorkItems where investment
    looks disproportionate to what came of it, not just where investment
    was largest. "work_item" is a transaction-only dimension so this
    can't run through run_metrics_query() in one call (same constraint
    documented on core.metrics_query.department_outcome_breakdown);
    backed by the standalone core.metrics_query.work_items_by_cost_ratio().

    Each row carries the real WorkItem external_id, so the frontend can
    link straight to work-item-profile.html the same way the AI Activity
    Explorer's "View Profile ->" links already do.
    """
    if rank_by == "cost_ratio":
        from core.metrics_query import work_items_by_cost_ratio

        rows = work_items_by_cost_ratio(db, workspace_id, outcome_status, limit)
        return {"workspace_id": workspace_id, "outcome_status": outcome_status, "rank_by": rank_by, "rows": rows, "errors": []}

    from core.metrics_query import run_metrics_query

    filters = {"outcome_status": outcome_status} if outcome_status else {}
    result = run_metrics_query(
        db, workspace_id,
        metrics=["ai_spend", "ai_requests"],
        dimensions=["work_item"],
        filters=filters,
        sort="ai_spend",
        limit=limit,
    )
    return {
        "workspace_id": workspace_id,
        "outcome_status": outcome_status,
        "rank_by": rank_by,
        "rows": [
            {
                "work_item_id": row["dimension_ids"]["work_item"],
                "label": row["dimensions"]["work_item"],
                "ai_spend_usd": round(float(row.get("ai_spend") or 0.0), 6),
                "ai_requests": int(row.get("ai_requests") or 0),
            }
            for row in result.rows
        ],
        "errors": result.errors,
    }


@router.get("/business-impact/by-department")
def get_business_impact_by_department(
    workspace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Business Impact ranked by department -- won/lost/open opportunity
    counts, closed-won value, AI investment, and cost per won opportunity,
    one row per department, ranked by AI investment.

    See core.metrics_query.department_outcome_breakdown()'s docstring for
    why this is a standalone query rather than a new mixable dimension on
    the shared metrics registry: WorkItemOutcome-rooted queries have no
    TokenTransaction join to read the authoritative
    charged_org_unit_name-based department label from, so this uses
    WorkItem.department (the closest available per-WorkItem hint) and
    merges outcome + spend queries in Python -- deliberately not touching
    the registry path every other metric/dimension combination depends on.
    """
    from core.metrics_query import department_outcome_breakdown

    rows = department_outcome_breakdown(db, workspace_id)
    return {"workspace_id": workspace_id, "rows": rows}


@router.get("/recommendations")
def get_recommendations(
    workspace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Recommendations engine v1 -- see core/recommendations.py for the full
    detector list and the shared contract every recommendation returns.
    Deterministic: every entry here comes from a plain SQL/Python
    condition over real data, nothing LLM-generated.
    """
    from core.recommendations import run_recommendations

    recommendations = run_recommendations(db, workspace_id)
    return {"workspace_id": workspace_id, "recommendations": recommendations}
