"""
core/recommendations.py — CostPilot Recommendations engine v1.

Deterministic first: every recommendation here is produced by a plain SQL/
Python condition over real data (TokenTransaction, DepartmentBudget,
WorkItemOutcome, KnownModel, RegisteredAgent) -- nothing is invented or
LLM-generated. An LLM can narrate a recommendation returned here later
(Ask CostPilot style: "reporting layer calculates, LLM narrates"), but it
never gets to invent the recommendation's basis, matching the same
discipline already enforced everywhere else in this codebase.

Every recommendation returned by every detector shares one contract (see
_recommendation() below) so the frontend renders one shape regardless of
which detector produced it, and so a future detector slots in without a
new frontend case.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from core.workspace_scope import workspace_filter
from database.models import (
    DepartmentBudget, KnownModel, RegisteredAgent, TokenTransaction,
    WorkItem, WorkItemOutcome, WorkItemOutcomeEvent,
)

MIN_SAMPLE = 5  # below this, a comparison is noise, not a pattern -- flagged as low-confidence, not omitted.


def _recommendation(
    *,
    recommendation_type: str,
    title: str,
    why_it_matters: str,
    evidence: str,
    current_state: str,
    recommended_action: str,
    estimated_impact: Optional[float],
    impact_type: str,  # "savings_usd" | "risk_usd" | "none"
    confidence: str,  # "measured" | "estimated" | "associated" | "early_signal"
    affected_agent: Optional[str] = None,
    affected_department: Optional[str] = None,
    affected_work_item: Optional[str] = None,
    source_metrics: Optional[dict] = None,
) -> dict:
    return {
        "recommendation_type": recommendation_type,
        "title": title,
        "why_it_matters": why_it_matters,
        "evidence": evidence,
        "current_state": current_state,
        "recommended_action": recommended_action,
        "estimated_impact": round(estimated_impact, 2) if estimated_impact is not None else None,
        "impact_type": impact_type,
        "confidence": confidence,
        "affected_agent": affected_agent,
        "affected_department": affected_department,
        "affected_work_item": affected_work_item,
        "source_metrics": source_metrics or {},
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── 1. Budget risk ───────────────────────────────────────────────────────

def detect_budget_risk(db: Session, workspace_id: Optional[str]) -> list[dict]:
    """Reuses core.budget.get_all_budgets() -- the same always-recomputed-
    from-the-ledger numbers Admin/cockpit/Ask CostPilot already agree on
    (see core.budget.sync_current_spend_from_ledger's docstring)."""
    from core.budget import get_all_budgets

    out = []
    for b in get_all_budgets(db, workspace_id):
        if b.get("archived"):
            continue
        pct = float(b.get("used_pct") or 0)
        if pct < 80:
            continue
        dept = str(b.get("department") or "").split(":")[-1]
        over = pct >= 100 or b.get("throttled")
        out.append(_recommendation(
            recommendation_type="budget_risk",
            title=f"{dept} is {'over' if over else 'approaching'} its AI budget",
            why_it_matters="A department at or near its monthly cap risks either overspending or having requests throttled.",
            evidence=f"Measured · {pct}% of ${b.get('monthly_cap_usd'):,.0f}/mo cap",
            current_state=f"${b.get('budget_spent_usd', b.get('current_spend_usd', 0)):,.2f} spent of ${b.get('monthly_cap_usd'):,.0f} cap",
            recommended_action=f"Review {dept}'s AI usage before month-end" if over else f"Monitor {dept}'s AI usage for the rest of the period",
            estimated_impact=None,
            impact_type="none",
            confidence="measured",
            affected_department=dept,
            source_metrics={"used_pct": pct, "throttled": bool(b.get("throttled"))},
        ))
    return out


# ── 2. Model right-sizing ────────────────────────────────────────────────

def detect_model_right_sizing(db: Session, workspace_id: Optional[str], *, min_requests: int = 20) -> list[dict]:
    """
    Reuses core.metrics_query.compute_potential_savings()'s exact
    detection logic (ROUTINE calls above tier 1, real KnownModel rates) --
    extended here with a per-agent eligibility rate (what share of an
    agent's ROUTINE calls ran above Scout), matching the flagship example
    from the approved recommendation spec ("Support Summarizer used Sonnet
    for 81% of requests that met the current Haiku eligibility rules").
    """
    from core.metrics_query import compute_potential_savings, _TIER_RANK

    result = compute_potential_savings(db, workspace_id)
    if not result["top_agents"]:
        return []

    scope = workspace_filter(TokenTransaction, workspace_id)
    query = db.query(
        TokenTransaction.agent_id, TokenTransaction.model_tier, func.count(TokenTransaction.id),
    ).filter(TokenTransaction.routing_reason == "ROUTINE")
    if scope is not None:
        query = query.filter(scope)
    query = query.group_by(TokenTransaction.agent_id, TokenTransaction.model_tier)

    totals: dict[Optional[int], int] = {}
    above_scout: dict[Optional[int], int] = {}
    for agent_id, model_tier, count in query.all():
        totals[agent_id] = totals.get(agent_id, 0) + count
        rank = _TIER_RANK.get(model_tier)
        if rank and rank > 1:
            above_scout[agent_id] = above_scout.get(agent_id, 0) + count

    out = []
    for agent in result["top_agents"]:
        agent_id = agent["agent_id"]
        total = totals.get(agent_id, 0)
        if total < min_requests:
            continue
        pct = round(100.0 * above_scout.get(agent_id, 0) / total, 0) if total else 0
        out.append(_recommendation(
            recommendation_type="model_right_sizing",
            title="Model right-sizing opportunity",
            why_it_matters="Requests CostPilot's own router classified as routine don't need a premium-tier model -- running them there costs more with no quality benefit for that request.",
            evidence=(
                f"Estimated from observed request mix and current model pricing "
                f"({cheapest_tier_note(db)})."
            ),
            current_state=f"{agent['agent_name']} used an above-Scout-tier model for {pct:.0f}% of its {total} routine requests",
            recommended_action=f"Review model routing for {agent['agent_name']}",
            estimated_impact=agent["potential_savings_usd"] * _month_multiplier(db, workspace_id),
            impact_type="savings_usd",
            confidence="estimated",
            affected_agent=agent["agent_name"],
            source_metrics={
                "eligible_pct": pct, "candidate_requests": total,
                "period_savings_usd": agent["potential_savings_usd"],
            },
        ))
    return out


def cheapest_tier_note(db: Session) -> str:
    cheapest = (
        db.query(KnownModel)
        .filter(KnownModel.tier == 1, KnownModel.is_active.is_(True))
        .order_by((KnownModel.cost_input_per_1m + KnownModel.cost_output_per_1m).asc())
        .first()
    )
    return f"vs. {cheapest.display_name}" if cheapest else "vs. the cheapest active tier-1 model"


def _month_multiplier(db: Session, workspace_id: Optional[str]) -> float:
    """
    compute_potential_savings() has no fixed window (it scans the whole
    ledger) -- projecting that total as a flat "/month" figure the way the
    approved spec's example does would overstate it for a workspace with
    months of history. Scales by (days in a 30-day month / days actually
    observed in the ledger) so a short-lived workspace's number isn't
    silently inflated into a false monthly rate; capped at 1.0 so a very
    new workspace's month projection is never a *reduction* of what it
    already spent.
    """
    scope = workspace_filter(TokenTransaction, workspace_id)
    query = db.query(func.min(TokenTransaction.timestamp), func.max(TokenTransaction.timestamp))
    if scope is not None:
        query = query.filter(scope)
    earliest, latest = query.first()
    if not earliest or not latest:
        return 1.0
    days = max((latest - earliest).total_seconds() / 86400.0, 1.0)
    return min(1.0, 30.0 / days) if days < 30 else 30.0 / days


# ── 3 & 5. Outcome-vs-spend patterns (weak outcome / strong outcome) ─────

def _stage_at_time(events: list[tuple], timestamp: datetime, no_stage_label: str) -> str:
    """Same point-in-time cursor logic as
    routes_work_items.py's get_work_item_business_impact() -- which stage/
    status was active when a given transaction happened, not the WorkItem's
    eventual/current status."""
    cursor = 0
    while cursor < len(events) and events[cursor][0] <= timestamp:
        cursor += 1
    if cursor > 0:
        return events[cursor - 1][1] or no_stage_label
    if events:
        return events[0][1] or no_stage_label
    return no_stage_label


def detect_outcome_spend_patterns(db: Session, workspace_id: Optional[str]) -> list[dict]:
    """
    Per-stage AI spend, split by whether the Opportunity eventually won or
    lost -- flags a stage where Lost Opportunities used meaningfully more
    AI than Won ones (high spend + weak outcome, the flagship "2.1x more
    AI during Qualification" example) and, separately, a stage with
    strong outcomes at low relative spend (low-cost + strong outcome).

    Bounded to closed Opportunities only (open ones have no "weak/strong"
    outcome yet -- see detect_stalled_high_spend for open work). Walks
    each WorkItem's own transactions against its own WorkItemOutcomeEvent
    history in Python, same shape as core.stage_attribution's per-account
    cursor walk, just aggregated by stage x outcome instead of by stage
    alone.
    """
    scope = workspace_filter(WorkItem, workspace_id)
    query = (
        db.query(WorkItem.id, WorkItemOutcome.outcome_success)
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type == "opportunity", WorkItemOutcome.is_closed.is_(True))
    )
    if scope is not None:
        query = query.filter(scope)
    closed_items = query.all()
    if len(closed_items) < MIN_SAMPLE:
        return []

    NO_STAGE = "Before tracking began"
    stage_totals: dict[str, dict[str, float]] = {}
    for work_item_id, outcome_success in closed_items:
        events = [
            (e.recorded_at, e.outcome_status)
            for e in db.query(WorkItemOutcomeEvent)
            .filter(WorkItemOutcomeEvent.work_item_id == work_item_id)
            .order_by(WorkItemOutcomeEvent.recorded_at).all()
        ]
        txs = (
            db.query(TokenTransaction.timestamp, TokenTransaction.cost_usd)
            .filter(TokenTransaction.work_item_id == work_item_id).all()
        )
        for timestamp, cost_usd in txs:
            stage = _stage_at_time(events, timestamp, NO_STAGE)
            bucket = stage_totals.setdefault(stage, {"won_spend": 0.0, "won_count": 0, "lost_spend": 0.0, "lost_count": 0})
            if outcome_success:
                bucket["won_spend"] += float(cost_usd or 0)
                bucket["won_count"] += 1
            else:
                bucket["lost_spend"] += float(cost_usd or 0)
                bucket["lost_count"] += 1

    won_total = sum(1 for _, s in closed_items if s)
    lost_total = sum(1 for _, s in closed_items if s is False)

    out = []
    for stage, b in stage_totals.items():
        if stage == NO_STAGE or b["won_count"] < MIN_SAMPLE or b["lost_count"] < MIN_SAMPLE:
            continue
        won_avg = b["won_spend"] / b["won_count"]
        lost_avg = b["lost_spend"] / b["lost_count"]
        if won_avg > 0 and lost_avg >= won_avg * 1.5:
            ratio = round(lost_avg / won_avg, 1)
            out.append(_recommendation(
                recommendation_type="high_spend_weak_outcome",
                title="High AI investment on unsuccessful work",
                why_it_matters="AI spend concentrated in a stage that correlates with losses, not wins, may be reinforcing a workflow that isn't working rather than improving it.",
                evidence=f"Associated · {won_total} Won / {lost_total} Lost Opportunities",
                current_state=f"Closed Lost Opportunities used {ratio}x more AI during {stage} than Closed Won Opportunities",
                recommended_action=f"Review {stage}-stage AI workflows before increasing spend",
                estimated_impact=None,
                impact_type="none",
                confidence="associated",
                source_metrics={
                    "stage": stage, "won_avg_usd": round(won_avg, 6), "lost_avg_usd": round(lost_avg, 6),
                    "ratio": ratio,
                },
            ))
        elif lost_avg >= 0 and won_avg >= lost_avg * 1.5 and lost_avg > 0:
            # Inverse pattern -- efficient, not wasteful: Won Opportunities
            # actually used less AI than Lost ones in this stage. Distinct
            # message, not just "not flagged".
            ratio = round(won_avg / lost_avg, 1) if lost_avg else None
            if ratio:
                out.append(_recommendation(
                    recommendation_type="low_cost_strong_outcome",
                    title="Efficient AI usage pattern",
                    why_it_matters="A stage where wins cost less AI than losses is a pattern worth preserving and replicating elsewhere.",
                    evidence=f"Associated · {won_total} Won / {lost_total} Lost Opportunities",
                    current_state=f"Closed Won Opportunities used {ratio}x less AI during {stage} than Closed Lost Opportunities",
                    recommended_action=f"Document what's working in {stage} and consider applying it to other stages",
                    estimated_impact=None,
                    impact_type="none",
                    confidence="associated",
                    source_metrics={
                        "stage": stage, "won_avg_usd": round(won_avg, 6), "lost_avg_usd": round(lost_avg, 6),
                        "ratio": ratio,
                    },
                ))
    return out


# ── 4. High spend + stalled/open work ────────────────────────────────────

def detect_stalled_high_spend(db: Session, workspace_id: Optional[str], *, stall_days: int = 30) -> list[dict]:
    """
    An open Opportunity with real AI spend but no outcome-stage change in
    stall_days -- heavy AI usage on work that isn't visibly progressing.
    Uses WorkItemOutcome.last_synced_at/outcome_date as the "still open"
    signal and the most recent WorkItemOutcomeEvent as the last-known
    progression point.
    """
    cutoff = datetime.utcnow() - timedelta(days=stall_days)
    scope = workspace_filter(WorkItem, workspace_id)
    query = (
        db.query(WorkItem, WorkItemOutcome, func.sum(TokenTransaction.cost_usd), func.count(TokenTransaction.id))
        .join(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .join(TokenTransaction, TokenTransaction.work_item_id == WorkItem.id)
        .filter(WorkItem.context_type == "opportunity", WorkItemOutcome.is_closed.is_(False))
        .group_by(WorkItem.id, WorkItemOutcome.id)
    )
    if scope is not None:
        query = query.filter(scope)

    rows = [(wi, wo, float(spend or 0), int(count or 0)) for wi, wo, spend, count in query.all() if spend]
    if not rows:
        return []
    avg_spend = sum(r[2] for r in rows) / len(rows)

    out = []
    for wi, wo, spend, count in rows:
        if spend < avg_spend * 1.5:
            continue
        last_event = (
            db.query(func.max(WorkItemOutcomeEvent.recorded_at))
            .filter(WorkItemOutcomeEvent.work_item_id == wi.id).scalar()
        )
        if not last_event or last_event > cutoff:
            continue
        stalled_days = (datetime.utcnow() - last_event).days
        out.append(_recommendation(
            recommendation_type="high_spend_stalled_work",
            title="High AI spend on stalled work",
            why_it_matters="Continued AI investment on work that hasn't progressed in a while may not be moving the deal forward.",
            evidence=f"Measured · no stage change in {stalled_days} days",
            current_state=f"{wi.name} has ${spend:,.2f} in AI spend ({count} requests) and no outcome change in {stalled_days} days",
            recommended_action=f"Review {wi.name} before continuing AI investment",
            estimated_impact=None,
            impact_type="none",
            confidence="measured",
            affected_work_item=wi.name,
            source_metrics={"spend_usd": round(spend, 6), "request_count": count, "stalled_days": stalled_days},
        ))
    return out[:5]


# ── 6. Outcome coverage gaps ──────────────────────────────────────────────

def detect_outcome_coverage_gaps(db: Session, workspace_id: Optional[str], *, threshold_pct: float = 60.0) -> list[dict]:
    from core.metrics_query import compute_outcome_coverage

    coverage = compute_outcome_coverage(db, workspace_id)
    pct = coverage["outcome_coverage_pct"]
    if pct is None or pct >= threshold_pct:
        return []
    return [_recommendation(
        recommendation_type="outcome_coverage_gap",
        title="Limited outcome coverage",
        why_it_matters="Business Impact numbers (Associated Business Value, Cost per Outcome) only reflect the WorkItems with a known outcome -- low coverage means those numbers describe a small, possibly unrepresentative slice of real AI-supported work.",
        evidence=f"Measured · {coverage['outcomes_with_known_data']} of {coverage['work_items_touched']} AI-supported WorkItems have a known outcome",
        current_state=f"{pct}% outcome coverage",
        recommended_action="Check outcome sync connectivity for the WorkItems missing outcome data",
        estimated_impact=None,
        impact_type="none",
        confidence="measured",
        source_metrics=coverage,
    )]


# ── 7. Inactive/idle agent review ────────────────────────────────────────

def detect_inactive_agents(db: Session, workspace_id: Optional[str], *, idle_days: int = 60) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=idle_days)
    scope = workspace_filter(RegisteredAgent, workspace_id)
    query = db.query(RegisteredAgent).filter(
        RegisteredAgent.archived.isnot(True),
        (RegisteredAgent.last_used_at.is_(None)) | (RegisteredAgent.last_used_at < cutoff),
    )
    if scope is not None:
        query = query.filter(scope)

    out = []
    for agent in query.limit(5).all():
        last_used = f"{(datetime.utcnow() - agent.last_used_at).days} days ago" if agent.last_used_at else "never"
        out.append(_recommendation(
            recommendation_type="inactive_agent",
            title="Inactive agent may need review",
            why_it_matters="A registered agent with no recent activity may be retired, misconfigured, or simply unused -- worth confirming rather than leaving it in the registry indefinitely.",
            evidence=f"Measured · last used {last_used}",
            current_state=f"{agent.name} last used {last_used}",
            recommended_action=f"Review {agent.name} for retirement or reactivation",
            estimated_impact=None,
            impact_type="none",
            confidence="measured",
            affected_agent=agent.name,
            affected_department=agent.department,
            source_metrics={"last_used_at": agent.last_used_at.isoformat() if agent.last_used_at else None},
        ))
    return out


# ── 8. Connection/data-health issues ─────────────────────────────────────

def detect_connection_health_issues(db: Session, workspace_id: Optional[str]) -> list[dict]:
    # Reuses the exact same rule-based health check /cockpit/'s
    # KpiRow/Recommendations already display via
    # GET /api/integrations/connections/health, rather than a second,
    # independent health computation that could drift from it.
    from api.routes_connections import workspace_connection_health

    health = workspace_connection_health(workspace_id=workspace_id or "default", db=db)
    out = []
    for rec in health.get("recommendations") or []:
        out.append(_recommendation(
            recommendation_type="connection_health",
            title="Data connection needs attention",
            why_it_matters="Business Impact and outcome-based recommendations depend on connected systems staying in sync -- a stale or broken connection silently degrades every downstream number.",
            evidence="Measured · connection health check",
            current_state=rec,
            recommended_action="Review Connected Systems status",
            estimated_impact=None,
            impact_type="none",
            confidence="measured",
        ))
    return out


# ── Orchestration ─────────────────────────────────────────────────────────

_SEVERITY_ORDER = {
    "budget_risk": 0,
    "high_spend_stalled_work": 1,
    "high_spend_weak_outcome": 2,
    "model_right_sizing": 3,
    "outcome_coverage_gap": 4,
    "connection_health": 5,
    "inactive_agent": 6,
    "low_cost_strong_outcome": 7,
}


def run_recommendations(db: Session, workspace_id: Optional[str]) -> list[dict]:
    """
    Runs every detector and returns one ranked list. Each detector is
    independent and best-effort -- one raising an exception (e.g. a
    workspace with no KnownModel rows yet) doesn't take down the others.
    """
    detectors = [
        detect_budget_risk,
        detect_model_right_sizing,
        detect_outcome_spend_patterns,
        detect_stalled_high_spend,
        detect_outcome_coverage_gaps,
        detect_inactive_agents,
        detect_connection_health_issues,
    ]
    results: list[dict] = []
    for detector in detectors:
        try:
            results.extend(detector(db, workspace_id))
        except Exception:
            continue
    results.sort(key=lambda r: _SEVERITY_ORDER.get(r["recommendation_type"], 99))
    return results
