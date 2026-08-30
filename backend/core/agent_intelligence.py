"""
core/agent_intelligence.py — Agent Intelligence Profile data layer.

This is the read-only "financial and business-impact story" companion to
core/agentlake.py's operational registry. Deliberately a separate module,
never imported by or importing from core/agentlake.py's collision/claim
logic -- that surface is frozen (see backend/tests/test_agentlake.py).

Every number here comes from CostPilot's existing trusted calculations
(core/metrics_query.py, api/routes_reports.py), each extended with an
optional agent_id parameter rather than reimplemented -- there is no
second source of truth for AI Investment, Realized Savings, Potential
Savings, Cost per Successful Outcome, or Outcome Coverage; this module
just calls the same functions with one more filter.

Attention signals are deterministic (plain conditions over the numbers
already computed below), never LLM-generated -- same discipline as
core/recommendations.py, kept in this separate module instead of that
file's contract.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import RegisteredAgent, TokenTransaction, WorkItem, WorkItemOutcome
from core.workspace_scope import workspace_filter
from core.metrics_query import (
    compute_cost_per_outcome, compute_outcome_coverage, compute_potential_savings,
    MIN_EXECUTIVE_SAMPLE, MIN_MEANINGFUL_SAMPLE,
)

MIN_SAMPLE = 5  # same "below this, a comparison is noise" floor as core/recommendations.py
INACTIVE_IDLE_DAYS = 60  # matches core/recommendations.py's detect_inactive_agents default
OUTCOME_COVERAGE_GAP_THRESHOLD_PCT = 60.0  # matches detect_outcome_coverage_gaps' default


def _agent_active_recently_relaxed(agent: RegisteredAgent) -> bool:
    """
    A looser "has this agent done anything lately" check than
    core.agentlake.agent_active_recently's 5-second window (which answers
    "is it mid-request right now", not "is it in regular use") -- used only
    for attention signals here, not for anything AgentLake itself relies on.
    """
    if not agent.last_used_at:
        return False
    return datetime.utcnow() - agent.last_used_at <= timedelta(days=7)


def _economics(db: Session, agent: RegisteredAgent) -> dict:
    # Lazy import, same pattern as core/recommendations.py's
    # detect_connection_health_issues -- avoids core importing from api at
    # module load time.
    from api.routes_reports import compute_realized_savings

    workspace_id = agent.workspace_id  # may be None for legacy unscoped agents -- see models.py's comment

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    scope = workspace_filter(TokenTransaction, workspace_id)
    ai_investment_month_usd = db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0)).filter(
        TokenTransaction.agent_id == agent.id,
        TokenTransaction.timestamp >= month_start,
        *([scope] if scope is not None else []),
    ).scalar() or 0.0

    realized_savings = compute_realized_savings(db, workspace_id, days=30, agent_id=agent.id)
    potential_savings = compute_potential_savings(db, workspace_id, agent_id=agent.id)
    cost_per_outcome = compute_cost_per_outcome(db, workspace_id, agent_id=agent.id)
    coverage = compute_outcome_coverage(db, workspace_id, agent_id=agent.id)

    # Associated Business Value for this agent -- same "spend on WorkItems
    # this agent touched that also have a WorkItemOutcome" definition as
    # the workspace-wide Business Impact figure, just filtered to one
    # agent's transactions.
    associated_value = db.query(
        func.coalesce(func.sum(WorkItemOutcome.outcome_value), 0.0)
    ).select_from(TokenTransaction).join(
        WorkItem, TokenTransaction.work_item_id == WorkItem.id
    ).join(
        WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id
    ).filter(
        TokenTransaction.agent_id == agent.id, WorkItemOutcome.outcome_success.is_(True),
    ).scalar() or 0.0
    associated_value = float(associated_value)

    # Associated Value-to-AI-Investment Ratio -- architected now, shown
    # only when the underlying sample is large enough to be more than
    # noise (same MIN_MEANINGFUL_SAMPLE gate compute_cost_per_outcome
    # already uses for its own evidence label). Never called "ROI":
    # ratio of two numbers that happen to co-occur, not a causal claim.
    successful_outcomes = cost_per_outcome["successful_outcomes"]
    total_agent_spend = float(
        db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
        .filter(TokenTransaction.agent_id == agent.id).scalar() or 0.0
    )
    if successful_outcomes >= MIN_MEANINGFUL_SAMPLE and total_agent_spend > 0:
        ratio = round(associated_value / total_agent_spend, 2)
        ratio_evidence = "executive_eligible" if successful_outcomes >= MIN_EXECUTIVE_SAMPLE else "meaningful"
    else:
        ratio = None
        ratio_evidence = "insufficient_data"

    return {
        "ai_investment_month_to_date_usd": round(float(ai_investment_month_usd), 6),
        "realized_savings_30d_usd": realized_savings["total_saved_usd"],
        "potential_savings_usd": potential_savings["potential_savings_usd"],
        "potential_savings_evidence": potential_savings["evidence"],
        "potential_savings_note": potential_savings["note"],
        "associated_business_value_usd": round(associated_value, 6),
        "cost_per_successful_outcome_usd": cost_per_outcome["cost_per_successful_outcome_usd"],
        "cost_per_outcome_evidence": cost_per_outcome["evidence_label"],
        "outcome_coverage_pct": coverage["outcome_coverage_pct"],
        "work_items_touched": coverage["work_items_touched"],
        "outcomes_with_known_data": coverage["outcomes_with_known_data"],
        "successful_outcomes": successful_outcomes,
        "associated_value_to_ai_investment_ratio": ratio,
        "associated_value_to_ai_investment_evidence": ratio_evidence,
        "associated_value_note": (
            "Associated Value-to-AI-Investment Ratio compares this agent's associated "
            "business value to its total AI spend. It is not ROI and does not imply this "
            "agent's activity caused the associated outcomes -- association, not causation."
        ),
        "total_ai_spend_usd": round(total_agent_spend, 6),
    }


def _model_usage(db: Session, agent: RegisteredAgent, limit: int = 10) -> list[dict]:
    rows = (
        db.query(
            func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model"),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
        )
        .filter(TokenTransaction.agent_id == agent.id)
        .group_by(func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model"))
        .order_by(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0).desc())
        .limit(limit)
        .all()
    )
    return [
        {"model": model, "spend_usd": round(float(spend or 0.0), 6), "call_count": int(count or 0)}
        for model, spend, count in rows
    ]


def _work_item_attribution(db: Session, agent: RegisteredAgent, limit: int = 25) -> list[dict]:
    """
    WorkItems this agent's transactions touched, with current outcome
    state -- current-state, not a per-item point-in-time stage timeline
    (that pattern, from routes_work_items.py's cursor-walk, is built for
    one WorkItem's own history; an agent typically touches many WorkItems
    at once, so this lists them ranked by this agent's spend on each
    instead).
    """
    rows = (
        db.query(
            WorkItem.id, WorkItem.name, WorkItem.context_type,
            WorkItemOutcome.outcome_status, WorkItemOutcome.outcome_success, WorkItemOutcome.is_closed,
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
        )
        .select_from(TokenTransaction)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(TokenTransaction.agent_id == agent.id)
        .group_by(WorkItem.id, WorkItem.name, WorkItem.context_type,
                  WorkItemOutcome.outcome_status, WorkItemOutcome.outcome_success, WorkItemOutcome.is_closed)
        .order_by(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "work_item_id": wi_id, "name": name, "context_type": context_type,
            "outcome_status": outcome_status, "outcome_success": outcome_success, "is_closed": is_closed,
            "ai_spend_usd": round(float(spend or 0.0), 6), "call_count": int(count or 0),
        }
        for wi_id, name, context_type, outcome_status, outcome_success, is_closed, spend, count in rows
    ]


def _recent_activity(db: Session, agent: RegisteredAgent, limit: int = 50) -> list[dict]:
    rows = (
        db.query(TokenTransaction.timestamp, TokenTransaction.cost_usd, TokenTransaction.model_name,
                  TokenTransaction.model_tier, WorkItem.name)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .filter(TokenTransaction.agent_id == agent.id)
        .order_by(TokenTransaction.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "timestamp": ts.isoformat() if ts else None,
            "cost_usd": round(float(cost or 0.0), 6),
            "model": model_name or model_tier,
            "work_item_name": wi_name,
        }
        for ts, cost, model_name, model_tier, wi_name in rows
    ]


def _attention_signals(agent: RegisteredAgent, economics: dict) -> list[dict]:
    """
    Deterministic signals only -- every condition below is a plain
    comparison against numbers already computed in `economics` or plain
    RegisteredAgent columns, no LLM involved. Multiple signals can fire
    at once (e.g. an agent can be both Active + Unreviewed and have an
    Outcome Coverage Gap).
    """
    signals = []
    active_recently = _agent_active_recently_relaxed(agent)
    approval_status = agent.approval_status or "unreviewed"

    if active_recently and approval_status == "unreviewed":
        signals.append({
            "signal": "active_unreviewed",
            "label": "Active but unreviewed",
            "detail": "This agent has real recent activity but has never been reviewed for approval.",
        })

    idle_cutoff = datetime.utcnow() - timedelta(days=INACTIVE_IDLE_DAYS)
    if not agent.archived and (not agent.last_used_at or agent.last_used_at < idle_cutoff):
        signals.append({
            "signal": "inactive_agent",
            "label": "Inactive agent",
            "detail": f"No activity in the last {INACTIVE_IDLE_DAYS} days.",
        })

    if economics["potential_savings_usd"] and economics["potential_savings_usd"] > 0:
        signals.append({
            "signal": "optimization_opportunity",
            "label": "Optimization opportunity available",
            "detail": "Some of this agent's routine requests ran on a more expensive tier than needed.",
        })

    if (economics["work_items_touched"] >= MIN_SAMPLE and economics["successful_outcomes"] == 0
            and economics["total_ai_spend_usd"] > 0):
        signals.append({
            "signal": "high_spend_weak_outcome",
            "label": "High spend / weak outcomes",
            "detail": (
                f"Touched {economics['work_items_touched']} WorkItems with real AI spend, "
                "but none have a known successful outcome yet."
            ),
        })

    if economics["outcome_coverage_pct"] is not None and economics["outcome_coverage_pct"] < OUTCOME_COVERAGE_GAP_THRESHOLD_PCT:
        signals.append({
            "signal": "outcome_coverage_gap",
            "label": "Outcome coverage gap",
            "detail": f"Only {economics['outcome_coverage_pct']}% of this agent's WorkItems have a known outcome.",
        })

    if approval_status in ("deprecated", "retired") and active_recently:
        signals.append({
            "signal": "governance_concern",
            "label": "Governance concern",
            "detail": f"Marked '{approval_status}' but still has recent activity.",
        })

    return signals


def get_agent_intelligence_profile(db: Session, agent_id: int) -> Optional[dict]:
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        return None

    economics = _economics(db, agent)
    signals = _attention_signals(agent, economics)

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "department": agent.department,
            "workspace_id": agent.workspace_id,
            "source_platform": agent.source_platform,
            # Runtime (AgentLake-owned) and lifecycle/governance are kept
            # as two separate top-level blocks, never merged into one
            # status enum.
            "runtime_status": agent.status,
            "lifecycle": {
                "approval_status": agent.approval_status or "unreviewed",
                "business_purpose": agent.business_purpose,
                "owner": agent.owner,
            },
            "governance_config": {
                "min_tier": agent.min_tier if agent.min_tier is not None else 1,
                "max_tier": agent.max_tier if agent.max_tier is not None else 4,
                "pruning_enabled": agent.pruning_enabled if agent.pruning_enabled is not None else True,
                "mode": agent.mode or "observe",
                "collision_policy": agent.collision_policy or "lock",
            },
            "last_used_at": agent.last_used_at.isoformat() if agent.last_used_at else None,
            "archived": bool(agent.archived),
        },
        "economics": economics,
        # Deliberately not a computed score yet -- these six dimensions
        # (cost efficiency, business outcomes, usage, governance,
        # optimization, outcome coverage) are the future Agent Health
        # Score's inputs, already present here as real numbers so a
        # future score is a pure combination step with no new queries:
        # cost efficiency <- cost_per_successful_outcome_usd / potential_savings_usd
        # business outcomes <- associated_business_value_usd / associated_value_to_ai_investment_ratio
        # usage <- total_ai_spend_usd / ai_investment_month_to_date_usd
        # governance <- lifecycle.approval_status / business_purpose / owner presence
        # optimization <- potential_savings_usd
        # outcome coverage <- outcome_coverage_pct
        "attention_signals": signals,
        "model_usage": _model_usage(db, agent),
        "work_item_attribution": _work_item_attribution(db, agent),
        "activity_timeline": _recent_activity(db, agent),
        "generated_at": datetime.utcnow().isoformat(),
    }
