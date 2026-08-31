"""
core/person_intelligence.py — Person Intelligence Profile data layer.

The Person-level companion to core/agent_intelligence.py's Agent
Intelligence Profile and api/routes_work_items.py's account_profile() --
built for the AI Activity Explorer's Person drill-down (Phase 1 of the
approved reporting plan). No such profile existed before this: "person"
was previously only a run_metrics_query grouping dimension (totals), with
no per-person breakdown by agent/account/work item/model.

Every number here comes from CostPilot's existing trusted calculations
(core/metrics_query.py, api/routes_reports.py), each already extended
with an optional person_external_id parameter the same way they were
already extended with agent_id for the Agent Intelligence Profile -- no
second source of truth for AI Investment, Realized Savings, Potential
Savings, Cost per Successful Outcome, or Outcome Coverage.

Strictly factual and attribution-focused, by explicit design decision
(see the approved plan): spend, requests, tokens, savings, WorkItems
touched, and business value *associated* with that work. No productivity
score, no ranking of people against each other, no derived judgment of
any kind -- if it isn't a real, deterministic count or sum, it does not
belong in this module. Association, not causation, same discipline as
agent_intelligence.py.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkUser
from core.workspace_scope import workspace_filter
from core.metrics_query import (
    compute_cost_per_outcome, compute_outcome_coverage, compute_potential_savings, person_clause,
)


def _check_person_visibility(request_user, target_person_external_id: str) -> None:
    """
    Swappable access-control seam for person-level reporting -- a no-op
    today because no role/permission model exists anywhere in this
    codebase yet (auth is workspace-scoped only). Kept as a single call
    site here (called once, at the top of get_person_intelligence_profile)
    rather than scattered checks through the query/rendering code below,
    so a real permission system can be dropped in later by editing this
    one function, not restructuring this module. Raise here (or return a
    redacted profile) once such a system exists; do not build the system
    itself until there's a user/role model to hang it on.
    """
    return None


def _resolve_person(db: Session, workspace_id: Optional[str], external_id: str):
    """
    Returns (kind, work_user_or_none, display_name, email, source_platform)
    or None if no activity matches this identifier at all. A person may
    have a linked WorkUser row (synced identity from a source system) or
    only ever appear via TokenTransaction.actor_external_id (older/
    legacy/simulated traffic with no synced WorkUser) -- both are real,
    valid identities; the profile just draws its display fields from
    whichever one actually has data, same precedence as the "person"
    dimension itself.
    """
    query = db.query(WorkUser).filter(WorkUser.external_id == external_id)
    if workspace_id:
        query = query.filter(WorkUser.workspace_id == workspace_id)
    work_user = query.first()
    if work_user:
        return "work_user", work_user, work_user.name, work_user.email, work_user.source_platform

    scope = workspace_filter(TokenTransaction, workspace_id)
    tx_query = db.query(TokenTransaction.actor_name, TokenTransaction.source_platform).filter(
        TokenTransaction.actor_external_id == external_id
    )
    if scope is not None:
        tx_query = tx_query.filter(scope)
    row = tx_query.order_by(TokenTransaction.timestamp.desc()).first()
    if row:
        actor_name, source_platform = row
        return "actor_only", None, actor_name or external_id, None, source_platform
    return None


def _economics(db: Session, workspace_id: Optional[str], external_id: str) -> dict:
    scope = workspace_filter(TokenTransaction, workspace_id)
    q = db.query(
        func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        func.count(TokenTransaction.id),
        func.coalesce(func.sum(TokenTransaction.input_tokens + TokenTransaction.output_tokens), 0),
    ).outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id).filter(person_clause(external_id))
    if scope is not None:
        q = q.filter(scope)
    total_spend, total_requests, total_tokens = q.first()

    realized_savings = compute_realized_savings_for_person(db, workspace_id, external_id)
    potential_savings = compute_potential_savings(db, workspace_id, person_external_id=external_id)
    cost_per_outcome = compute_cost_per_outcome(db, workspace_id, person_external_id=external_id)
    coverage = compute_outcome_coverage(db, workspace_id, person_external_id=external_id)

    # Associated Business Value -- same "spend on WorkItems this identity
    # touched that also have a successful WorkItemOutcome" definition as
    # the agent-level equivalent, just filtered to one person.
    from database.models import WorkItemOutcome
    associated_value_q = db.query(
        func.coalesce(func.sum(WorkItemOutcome.outcome_value), 0.0)
    ).select_from(TokenTransaction).outerjoin(
        WorkUser, TokenTransaction.work_user_id == WorkUser.id
    ).join(
        WorkItem, TokenTransaction.work_item_id == WorkItem.id
    ).join(
        WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id
    ).filter(person_clause(external_id), WorkItemOutcome.outcome_success.is_(True))
    associated_value = float(associated_value_q.scalar() or 0.0)

    return {
        "ai_investment_usd": round(float(total_spend or 0.0), 6),
        "ai_requests": int(total_requests or 0),
        "total_tokens": int(total_tokens or 0),
        "realized_savings_usd": realized_savings["total_saved_usd"],
        "potential_savings_usd": potential_savings["potential_savings_usd"],
        "potential_savings_evidence": potential_savings["evidence"],
        "potential_savings_note": potential_savings["note"],
        "associated_business_value_usd": round(associated_value, 6),
        "cost_per_successful_outcome_usd": cost_per_outcome["cost_per_successful_outcome_usd"],
        "cost_per_outcome_evidence": cost_per_outcome["evidence_label"],
        "successful_outcomes": cost_per_outcome["successful_outcomes"],
        "outcome_coverage_pct": coverage["outcome_coverage_pct"],
        "work_items_touched": coverage["work_items_touched"],
        "outcomes_with_known_data": coverage["outcomes_with_known_data"],
        "association_note": (
            "This reports AI activity and its association with business work -- "
            "spend, requests, tokens, and business value associated with WorkItems "
            "this person's activity touched. It is not a productivity or performance "
            "score, and does not imply this person's activity caused any outcome."
        ),
    }


def compute_realized_savings_for_person(db: Session, workspace_id: Optional[str], external_id: str) -> dict:
    """
    Thin wrapper so _economics() doesn't need to know
    api/routes_reports.py's import path -- same lazy-import pattern
    core/agent_intelligence.py's _economics() already uses for the same
    function, avoiding core importing from api at module load time.
    """
    from api.routes_reports import compute_realized_savings

    return compute_realized_savings(db, workspace_id, days=30, person_external_id=external_id)


def _agents_used(db: Session, workspace_id: Optional[str], external_id: str, limit: int = 25) -> list[dict]:
    scope = workspace_filter(TokenTransaction, workspace_id)
    q = (
        db.query(
            RegisteredAgent.id, RegisteredAgent.name,
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
        )
        .select_from(TokenTransaction)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .join(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
        .filter(person_clause(external_id))
    )
    if scope is not None:
        q = q.filter(scope)
    rows = (
        q.group_by(RegisteredAgent.id, RegisteredAgent.name)
        .order_by(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0).desc())
        .limit(limit)
        .all()
    )
    return [
        {"agent_id": aid, "agent_name": name, "ai_spend_usd": round(float(spend or 0.0), 6), "request_count": int(count or 0)}
        for aid, name, spend, count in rows
    ]


def _accounts(db: Session, workspace_id: Optional[str], external_id: str, limit: int = 25) -> list[dict]:
    scope = workspace_filter(TokenTransaction, workspace_id)
    q = (
        db.query(
            WorkAccount.external_id, WorkAccount.name,
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
        )
        .select_from(TokenTransaction)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .join(WorkAccount, WorkItem.account_id == WorkAccount.id)
        .filter(person_clause(external_id))
    )
    if scope is not None:
        q = q.filter(scope)
    rows = (
        q.group_by(WorkAccount.external_id, WorkAccount.name)
        .order_by(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0).desc())
        .limit(limit)
        .all()
    )
    return [
        {"account_external_id": eid, "account_name": name, "ai_spend_usd": round(float(spend or 0.0), 6), "request_count": int(count or 0)}
        for eid, name, spend, count in rows
    ]


def _work_items(db: Session, workspace_id: Optional[str], external_id: str, limit: int = 25) -> list[dict]:
    from database.models import WorkItemOutcome

    scope = workspace_filter(TokenTransaction, workspace_id)
    q = (
        db.query(
            WorkItem.id, WorkItem.name, WorkItem.context_type,
            WorkItemOutcome.outcome_status, WorkItemOutcome.outcome_success, WorkItemOutcome.is_closed,
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
        )
        .select_from(TokenTransaction)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(person_clause(external_id))
    )
    if scope is not None:
        q = q.filter(scope)
    rows = (
        q.group_by(WorkItem.id, WorkItem.name, WorkItem.context_type,
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


def _model_usage(db: Session, workspace_id: Optional[str], external_id: str, limit: int = 10) -> list[dict]:
    scope = workspace_filter(TokenTransaction, workspace_id)
    model_expr = func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, "Unknown model")
    q = (
        db.query(model_expr, func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0), func.count(TokenTransaction.id))
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .filter(person_clause(external_id))
    )
    if scope is not None:
        q = q.filter(scope)
    rows = (
        q.group_by(model_expr)
        .order_by(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0).desc())
        .limit(limit)
        .all()
    )
    return [
        {"model": model, "spend_usd": round(float(spend or 0.0), 6), "call_count": int(count or 0)}
        for model, spend, count in rows
    ]


def _recent_activity(db: Session, workspace_id: Optional[str], external_id: str, limit: int = 50) -> list[dict]:
    scope = workspace_filter(TokenTransaction, workspace_id)
    q = (
        db.query(TokenTransaction.timestamp, TokenTransaction.cost_usd, TokenTransaction.model_name,
                  TokenTransaction.model_tier, WorkItem.name)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .filter(person_clause(external_id))
    )
    if scope is not None:
        q = q.filter(scope)
    rows = q.order_by(TokenTransaction.timestamp.desc()).limit(limit).all()
    return [
        {
            "timestamp": ts.isoformat() if ts else None,
            "cost_usd": round(float(cost or 0.0), 6),
            "model": model_name or model_tier,
            "work_item_name": wi_name,
        }
        for ts, cost, model_name, model_tier, wi_name in rows
    ]


def get_person_intelligence_profile(
    db: Session, workspace_id: Optional[str], external_id: str, request_user=None,
) -> Optional[dict]:
    _check_person_visibility(request_user, external_id)

    resolved = _resolve_person(db, workspace_id, external_id)
    if resolved is None:
        return None
    kind, work_user, display_name, email, source_platform = resolved

    return {
        "person": {
            "external_id": external_id,
            "name": display_name,
            "email": email,
            "source_platform": source_platform,
            "workspace_id": workspace_id,
            "identity_kind": kind,  # "work_user" (synced identity) | "actor_only" (legacy/simulated, no synced record)
        },
        "economics": _economics(db, workspace_id, external_id),
        "agents_used": _agents_used(db, workspace_id, external_id),
        "accounts": _accounts(db, workspace_id, external_id),
        "work_items": _work_items(db, workspace_id, external_id),
        "model_usage": _model_usage(db, workspace_id, external_id),
        "activity_timeline": _recent_activity(db, workspace_id, external_id),
        "generated_at": datetime.utcnow().isoformat(),
    }
