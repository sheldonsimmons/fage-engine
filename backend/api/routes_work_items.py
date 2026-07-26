"""Work Attribution API — accounts and projects/matters/engagements."""

import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    AuditEvent,
    RegisteredAgent,
    TokenTransaction,
    WorkAccount,
    WorkItem,
    WorkItemAgent,
    WorkItemSourceLink,
    WorkItemUser,
    WorkUser,
)
from core.agentlake import display_agent_name, display_department, infer_platform
from core.business_context import (
    BUSINESS_CONTEXT_TEMPLATES,
    business_context_json,
    normalize_context_type,
)


router = APIRouter()

VALID_STATUSES = {"active", "on_hold", "completed", "cancelled", "archived"}
VALID_COST_TREATMENTS = {
    "unspecified",
    "overhead",
    "internal_allocation",
    "fixed_fee",
    "recoverable",
    "nonbillable",
    "review_required",
}
VALID_BUDGET_ACTIONS = {"warn", "throttle", "block"}


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_id: Optional[str] = Field(default=None, max_length=120)
    department: Optional[str] = Field(default=None, max_length=120)
    status: str = "active"
    workspace_id: Optional[str] = Field(default=None, max_length=120)


class WorkItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_id: Optional[str] = Field(default=None, max_length=120)
    account_id: Optional[int] = None
    owner: Optional[str] = Field(default=None, max_length=200)
    department: Optional[str] = Field(default=None, max_length=120)
    status: str = "active"
    monthly_ai_budget: Optional[float] = Field(default=None, ge=0)
    budget_warning_pct: float = Field(default=80, ge=1, le=100)
    budget_action: str = "warn"
    cost_treatment: str = "unspecified"
    source_platform: Optional[str] = Field(default="CostPilot", max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    context_type: str = Field(default="project", max_length=40)
    context_template: Optional[str] = Field(default=None, max_length=120)
    source_record_type: Optional[str] = Field(default=None, max_length=120)
    source_record_id: Optional[str] = Field(default=None, max_length=120)


class WorkItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    account_id: Optional[int] = None
    owner: Optional[str] = Field(default=None, max_length=200)
    department: Optional[str] = Field(default=None, max_length=120)
    status: Optional[str] = None
    monthly_ai_budget: Optional[float] = Field(default=None, ge=0)
    budget_warning_pct: Optional[float] = Field(default=None, ge=1, le=100)
    budget_action: Optional[str] = None
    cost_treatment: Optional[str] = None
    source_platform: Optional[str] = Field(default=None, max_length=120)
    context_type: Optional[str] = Field(default=None, max_length=40)
    context_template: Optional[str] = Field(default=None, max_length=120)
    source_record_type: Optional[str] = Field(default=None, max_length=120)
    source_record_id: Optional[str] = Field(default=None, max_length=120)


class AgentAssignmentIn(BaseModel):
    agent_id: int
    role: str = Field(default="Contributor", min_length=1, max_length=120)


class AgentAssignmentsIn(BaseModel):
    assignments: list[AgentAssignmentIn]
    assigned_by: Optional[str] = Field(default=None, max_length=200)


class ProjectAgentCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=120)
    source_platform: Optional[str] = Field(default=None, max_length=120)
    role: str = Field(default="Contributor", min_length=1, max_length=120)
    permissions: str = Field(default="read,write", max_length=120)
    target_table: str = Field(default="records", max_length=120)
    collision_policy: str = "lock"
    assigned_by: Optional[str] = Field(default=None, max_length=200)


class ProjectUserUpsertIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=320)
    source_platform: str = Field(default="CostPilot", min_length=1, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    role: str = Field(default="Member", min_length=1, max_length=120)
    status: str = Field(default="active", max_length=40)
    can_use_ai: bool = True
    assigned_by: Optional[str] = Field(default=None, max_length=200)


class SourceLinkIn(BaseModel):
    source_platform: str = Field(min_length=1, max_length=120)
    source_record_id: str = Field(min_length=1, max_length=120)
    source_record_type: Optional[str] = Field(default=None, max_length=120)
    source_record_name: Optional[str] = Field(default=None, max_length=200)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    account_external_id: Optional[str] = Field(default=None, max_length=120)
    is_primary: bool = False


class MergeWorkItemsIn(BaseModel):
    target_identifier: str = Field(min_length=1, max_length=240)


def _clean_external_id(value: Optional[str], prefix: str) -> str:
    if value:
        cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "-", value.strip()).strip("-")
        if cleaned:
            return cleaned
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _validate_status(value: str):
    if value not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )


def _validate_cost_treatment(value: str):
    if value not in VALID_COST_TREATMENTS:
        raise HTTPException(
            status_code=400,
            detail=f"cost_treatment must be one of: {', '.join(sorted(VALID_COST_TREATMENTS))}",
        )


def _validate_budget_action(value: str):
    if value not in VALID_BUDGET_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"budget_action must be one of: {', '.join(sorted(VALID_BUDGET_ACTIONS))}",
        )


def _source_link_json(link: WorkItemSourceLink) -> dict:
    return {
        "id": link.id,
        "source_platform": link.source_platform,
        "source_record_type": link.source_record_type,
        "source_record_id": link.source_record_id,
        "source_record_name": link.source_record_name,
        "workspace_id": link.workspace_id,
        "account_external_id": link.account_external_id,
        "is_primary": bool(link.is_primary),
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


def _account_json(account: WorkAccount) -> dict:
    return {
        "id": account.id,
        "external_id": account.external_id,
        "name": account.name,
        "department": account.department,
        "status": account.status,
        "workspace_id": account.workspace_id,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


def _work_item_json(item: WorkItem, db: Session, include_stats: bool = True) -> dict:
    spend_usd = 0.0
    spend_month_usd = 0.0
    request_count = 0
    input_tokens = 0
    output_tokens = 0
    tokens_saved = 0
    last_activity_at = None
    agent_rows = []
    risk_event_count = 0
    model_tiers = []
    activity_platforms = []
    agent_team = []
    user_team = []
    if include_stats:
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        request_count, input_tokens, output_tokens, tokens_saved, spend_usd, last_activity_at = (
            db.query(
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.max(TokenTransaction.timestamp),
            )
            .filter(TokenTransaction.work_item_id == item.id)
            .one()
        )
        spend_month_usd = (
            db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .filter(
                TokenTransaction.work_item_id == item.id,
                TokenTransaction.timestamp >= month_start,
            )
            .scalar()
            or 0.0
        )
        agent_rows = (
            db.query(RegisteredAgent.id, RegisteredAgent.name)
            .join(TokenTransaction, TokenTransaction.agent_id == RegisteredAgent.id)
            .filter(TokenTransaction.work_item_id == item.id)
            .distinct()
            .order_by(RegisteredAgent.name)
            .all()
        )
        risk_event_count = (
            db.query(func.count(AuditEvent.id))
            .filter(
                AuditEvent.work_item_id == item.id,
                func.lower(func.coalesce(AuditEvent.risk_level, "low")).in_(
                    ("medium", "high", "critical")
                ),
            )
            .scalar()
            or 0
        )
        model_tiers = [
            row[0]
            for row in (
                db.query(TokenTransaction.model_tier)
                .filter(
                    TokenTransaction.work_item_id == item.id,
                    TokenTransaction.model_tier.isnot(None),
                )
                .distinct()
                .order_by(TokenTransaction.model_tier)
                .all()
            )
        ]
        activity_platforms = [
            row[0]
            for row in (
                db.query(TokenTransaction.source_platform)
                .filter(
                    TokenTransaction.work_item_id == item.id,
                    TokenTransaction.source_platform.isnot(None),
                    TokenTransaction.source_platform != "",
                )
                .distinct()
                .order_by(TokenTransaction.source_platform)
                .all()
            )
        ]
        agent_team = _project_agent_rows(item, db)
        user_team = _project_user_rows(item, db)
    budget = item.monthly_ai_budget
    source_links = sorted(
        item.source_links,
        key=lambda link: (not bool(link.is_primary), link.source_platform, link.source_record_type or "", link.source_record_name or link.source_record_id),
    )
    return {
        "id": item.id,
        "external_id": item.external_id,
        "name": item.name,
        "account_id": item.account_id,
        "account_name": item.account.name if item.account else None,
        "owner": item.owner,
        "department": item.department,
        "status": item.status,
        "monthly_ai_budget": budget,
        "budget_warning_pct": float(item.budget_warning_pct or 80),
        "budget_action": item.budget_action or "warn",
        "cost_treatment": item.cost_treatment,
        "source_platform": item.source_platform,
        "workspace_id": item.workspace_id,
        "context_type": item.context_type or "project",
        "context_template": item.context_template,
        "source_record_type": item.source_record_type,
        "source_record_id": item.source_record_id,
        "source_links": [_source_link_json(link) for link in source_links],
        "merged_into_work_item_id": item.merged_into_work_item_id,
        "business_context": business_context_json(item),
        "request_count": int(request_count or 0),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "tokens_saved": int(tokens_saved or 0),
        "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
        "agent_count": len(agent_rows),
        "agents": [{"id": row.id, "name": row.name} for row in agent_rows],
        "risk_event_count": int(risk_event_count or 0),
        "model_tiers": model_tiers,
        "activity_platforms": activity_platforms,
        "assigned_agent_count": sum(1 for row in agent_team if row["assignment_status"] == "assigned"),
        "agent_team": agent_team,
        "user_count": sum(1 for row in user_team if row["usage_status"] == "used"),
        "assigned_user_count": sum(1 for row in user_team if row["assignment_status"] == "assigned"),
        "user_team": user_team,
        "spend_usd": round(float(spend_usd or 0.0), 6),
        "spend_month_usd": round(float(spend_month_usd or 0.0), 6),
        "budget_remaining_usd": (
            round(float(budget) - float(spend_month_usd or 0.0), 6)
            if budget is not None
            else None
        ),
        "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _project_agent_rows(item: WorkItem, db: Session) -> list[dict]:
    assignments = (
        db.query(WorkItemAgent)
        .filter(WorkItemAgent.work_item_id == item.id)
        .order_by(WorkItemAgent.assigned_at, WorkItemAgent.id)
        .all()
    )
    assigned_by_agent = {assignment.agent_id: assignment for assignment in assignments}
    observed_ids = {
        row[0]
        for row in db.query(TokenTransaction.agent_id)
        .filter(
            TokenTransaction.work_item_id == item.id,
            TokenTransaction.agent_id.isnot(None),
        )
        .distinct()
        .all()
    }
    agent_ids = set(assigned_by_agent) | observed_ids
    if not agent_ids:
        return []

    agents = {
        agent.id: agent
        for agent in db.query(RegisteredAgent).filter(RegisteredAgent.id.in_(agent_ids)).all()
    }
    rows = []
    for agent_id in sorted(agent_ids, key=lambda value: (agents.get(value).name if agents.get(value) else "")):
        agent = agents.get(agent_id)
        if not agent:
            continue
        call_count, tokens_saved, spend_usd, last_activity_at = (
            db.query(
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.max(TokenTransaction.timestamp),
            )
            .filter(
                TokenTransaction.work_item_id == item.id,
                TokenTransaction.agent_id == agent_id,
            )
            .one()
        )
        risk_count = (
            db.query(func.count(AuditEvent.id))
            .filter(
                AuditEvent.work_item_id == item.id,
                AuditEvent.agent_id == agent_id,
                func.lower(func.coalesce(AuditEvent.risk_level, "low")).in_(
                    ("medium", "high", "critical")
                ),
            )
            .scalar()
            or 0
        )
        tiers = [
            row[0]
            for row in db.query(TokenTransaction.model_tier)
            .filter(
                TokenTransaction.work_item_id == item.id,
                TokenTransaction.agent_id == agent_id,
                TokenTransaction.model_tier.isnot(None),
            )
            .distinct()
            .order_by(TokenTransaction.model_tier)
            .all()
        ]
        assignment = assigned_by_agent.get(agent_id)
        rows.append({
            "agent_id": agent.id,
            "name": agent.name,
            "display_name": display_agent_name(agent.name, agent.department, agent.source_platform),
            "department": display_department(agent.department),
            "source_platform": agent.source_platform,
            "role": assignment.role if assignment else None,
            "assignment_status": "assigned" if assignment else "unexpected",
            "usage_status": "used" if int(call_count or 0) > 0 else "never_used",
            "call_count": int(call_count or 0),
            "tokens_saved": int(tokens_saved or 0),
            "spend_usd": round(float(spend_usd or 0.0), 6),
            "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
            "risk_event_count": int(risk_count),
            "model_tiers": tiers,
            "assigned_at": assignment.assigned_at.isoformat() if assignment and assignment.assigned_at else None,
        })
    return rows


def _project_user_rows(item: WorkItem, db: Session) -> list[dict]:
    assignments = (
        db.query(WorkItemUser)
        .filter(WorkItemUser.work_item_id == item.id)
        .order_by(WorkItemUser.assigned_at, WorkItemUser.id)
        .all()
    )
    assigned_by_user = {assignment.work_user_id: assignment for assignment in assignments}
    observed_ids = {
        row[0]
        for row in db.query(TokenTransaction.work_user_id)
        .filter(
            TokenTransaction.work_item_id == item.id,
            TokenTransaction.work_user_id.isnot(None),
        )
        .distinct()
        .all()
    }
    user_ids = set(assigned_by_user) | observed_ids
    if not user_ids:
        return []

    users = {
        user.id: user
        for user in db.query(WorkUser).filter(WorkUser.id.in_(user_ids)).all()
    }
    rows = []
    for user_id in sorted(user_ids, key=lambda value: (users.get(value).name if users.get(value) else "")):
        user = users.get(user_id)
        if not user:
            continue
        call_count, input_tokens, output_tokens, tokens_saved, spend_usd, last_activity_at = (
            db.query(
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.max(TokenTransaction.timestamp),
            )
            .filter(
                TokenTransaction.work_item_id == item.id,
                TokenTransaction.work_user_id == user_id,
            )
            .one()
        )
        agent_count = (
            db.query(func.count(func.distinct(TokenTransaction.agent_id)))
            .filter(
                TokenTransaction.work_item_id == item.id,
                TokenTransaction.work_user_id == user_id,
                TokenTransaction.agent_id.isnot(None),
            )
            .scalar()
            or 0
        )
        assignment = assigned_by_user.get(user_id)
        rows.append({
            "user_id": user.id,
            "external_id": user.external_id,
            "name": user.name,
            "email": user.email,
            "source_platform": user.source_platform,
            "role": assignment.role if assignment else None,
            "membership_status": assignment.status if assignment else None,
            "can_use_ai": assignment.can_use_ai if assignment else None,
            "assignment_status": "assigned" if assignment else "unexpected",
            "usage_status": "used" if int(call_count or 0) > 0 else "never_used",
            "call_count": int(call_count or 0),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
            "tokens_saved": int(tokens_saved or 0),
            "spend_usd": round(float(spend_usd or 0.0), 6),
            "agent_count": int(agent_count),
            "last_activity_at": last_activity_at.isoformat() if last_activity_at else None,
            "assigned_at": assignment.assigned_at.isoformat() if assignment and assignment.assigned_at else None,
        })
    return rows


def resolve_work_item(db: Session, identifier: str) -> Optional[WorkItem]:
    """Resolve either the public external ID or the internal integer ID."""
    value = str(identifier or "").strip()
    if not value:
        return None
    item = db.query(WorkItem).filter(WorkItem.external_id == value).first()
    if not item and value.isdigit():
        item = db.query(WorkItem).filter(WorkItem.id == int(value)).first()
    return item


@router.get("/accounts")
def list_accounts(
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WorkAccount)
    if workspace_id:
        query = query.filter(WorkAccount.workspace_id == workspace_id)
    return [_account_json(account) for account in query.order_by(WorkAccount.name).all()]


@router.get("/context-templates")
def list_context_templates():
    """Templates translate platform records into the universal context contract."""
    return [template.as_dict() for template in BUSINESS_CONTEXT_TEMPLATES.values()]


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, db: Session = Depends(get_db)):
    _validate_status(body.status)
    external_id = _clean_external_id(body.external_id, "ACCOUNT")
    if db.query(WorkAccount).filter(WorkAccount.external_id == external_id).first():
        raise HTTPException(status_code=409, detail="An account with that external_id already exists")
    account = WorkAccount(
        **body.model_dump(exclude={"external_id"}),
        external_id=external_id,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return _account_json(account)


@router.get("")
def list_work_items(
    status: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WorkItem)
    if status:
        _validate_status(status)
        query = query.filter(WorkItem.status == status)
    if workspace_id:
        query = query.filter(WorkItem.workspace_id == workspace_id)
    items = query.order_by(WorkItem.status, WorkItem.name).all()
    return [_work_item_json(item, db) for item in items]


@router.get("/summary")
def work_item_summary(
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    item_query = db.query(WorkItem)
    transaction_query = db.query(TokenTransaction)
    if workspace_id:
        item_query = item_query.filter(WorkItem.workspace_id == workspace_id)
        transaction_query = transaction_query.filter(
            TokenTransaction.department.like(f"{workspace_id}:%")
        )

    items = item_query.all()
    item_ids = [item.id for item in items]
    total_spend = float(
        transaction_query.with_entities(
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0)
        ).scalar()
        or 0.0
    )
    attributed_spend = 0.0
    if item_ids:
        attributed_spend = float(
            db.query(func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0))
            .filter(TokenTransaction.work_item_id.in_(item_ids))
            .scalar()
            or 0.0
        )

    item_rows = [_work_item_json(item, db) for item in items]
    attention = []
    for item in item_rows:
        budget = item.get("monthly_ai_budget")
        spend_month = float(item.get("spend_month_usd") or 0.0)
        budget_pct = (spend_month / float(budget) * 100) if budget else 0.0
        unexpected_agents = sum(
            1 for member in item.get("agent_team", [])
            if member.get("assignment_status") == "unexpected"
        )
        risk_events = int(item.get("risk_event_count") or 0)
        signals = []
        if item["status"] == "on_hold":
            signals.append("on_hold")
        if budget_pct >= float(item.get("budget_warning_pct") or 80):
            signals.append("budget")
        if risk_events:
            signals.append("risk")
        if unexpected_agents:
            signals.append("unexpected_agents")
        if signals:
            attention.append({
                "external_id": item["external_id"],
                "name": item["name"],
                "status": item["status"],
                "budget_used_pct": round(budget_pct, 1),
                "spend_month_usd": spend_month,
                "monthly_ai_budget": budget,
                "risk_event_count": risk_events,
                "unexpected_agent_count": unexpected_agents,
                "signals": signals,
            })

    return {
        "project_count": len(items),
        "active_project_count": sum(1 for item in items if item.status == "active"),
        "attributed_spend_usd": round(attributed_spend, 6),
        "unattributed_spend_usd": round(max(0.0, total_spend - attributed_spend), 6),
        "attributed_spend_pct": round(
            (attributed_spend / total_spend * 100) if total_spend else 0.0,
            1,
        ),
        "tokens_saved": sum(int(item.get("tokens_saved") or 0) for item in item_rows),
        "projects_needing_attention": sorted(
            attention,
            key=lambda item: (item["status"] != "on_hold", -item["budget_used_pct"]),
        ),
    }


@router.post("", status_code=201)
def create_work_item(body: WorkItemIn, db: Session = Depends(get_db)):
    _validate_status(body.status)
    _validate_cost_treatment(body.cost_treatment)
    _validate_budget_action(body.budget_action)
    try:
        context_type = normalize_context_type(
            body.context_type,
            template_key=body.context_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    external_id = _clean_external_id(body.external_id, "PROJECT")
    if db.query(WorkItem).filter(WorkItem.external_id == external_id).first():
        raise HTTPException(status_code=409, detail="A work item with that external_id already exists")
    if body.account_id and not db.query(WorkAccount).filter(WorkAccount.id == body.account_id).first():
        raise HTTPException(status_code=404, detail="Account not found")
    item = WorkItem(
        **body.model_dump(exclude={"external_id", "context_type"}),
        external_id=external_id,
        context_type=context_type,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _work_item_json(item, db)


@router.get("/{identifier}")
def get_work_item(identifier: str, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    return _work_item_json(item, db)


@router.patch("/{identifier}")
def update_work_item(identifier: str, body: WorkItemUpdate, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes:
        _validate_status(changes["status"])
    if "cost_treatment" in changes:
        _validate_cost_treatment(changes["cost_treatment"])
    if "budget_action" in changes:
        _validate_budget_action(changes["budget_action"])
    if "context_type" in changes or "context_template" in changes:
        try:
            changes["context_type"] = normalize_context_type(
                changes.get("context_type", item.context_type),
                template_key=changes.get("context_template", item.context_template),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "external_id" in changes:
        changes["external_id"] = _clean_external_id(changes["external_id"], "PROJECT")
        duplicate = (
            db.query(WorkItem)
            .filter(WorkItem.external_id == changes["external_id"], WorkItem.id != item.id)
            .first()
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="A work item with that external_id already exists")
    if changes.get("account_id") and not db.query(WorkAccount).filter(
        WorkAccount.id == changes["account_id"]
    ).first():
        raise HTTPException(status_code=404, detail="Account not found")
    for field, value in changes.items():
        setattr(item, field, value)
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _work_item_json(item, db)


@router.post("/{identifier}/source-links", status_code=201)
def link_source_record(
    identifier: str,
    body: SourceLinkIn,
    db: Session = Depends(get_db),
):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    workspace_id = (body.workspace_id or item.workspace_id or "default").strip() or "default"
    platform = body.source_platform.strip()
    record_id = body.source_record_id.strip()
    existing = (
        db.query(WorkItemSourceLink)
        .filter(
            WorkItemSourceLink.workspace_id == workspace_id,
            func.lower(WorkItemSourceLink.source_platform) == platform.lower(),
            WorkItemSourceLink.source_record_id == record_id,
        )
        .first()
    )
    if existing and existing.work_item_id != item.id:
        other = db.query(WorkItem).filter(WorkItem.id == existing.work_item_id).first()
        raise HTTPException(
            status_code=409,
            detail=f"That source record is already linked to {other.name if other else 'another project'}. Merge the projects to move it safely.",
        )
    link = existing or WorkItemSourceLink(
        work_item_id=item.id,
        workspace_id=workspace_id,
        source_platform=platform,
        source_record_id=record_id,
    )
    link.source_record_type = body.source_record_type
    link.source_record_name = body.source_record_name
    link.account_external_id = body.account_external_id
    link.is_primary = body.is_primary
    if not existing:
        db.add(link)
    db.commit()
    db.refresh(link)
    return _source_link_json(link)


@router.post("/{identifier}/merge")
def merge_work_items(
    identifier: str,
    body: MergeWorkItemsIn,
    db: Session = Depends(get_db),
):
    source = resolve_work_item(db, identifier)
    target = resolve_work_item(db, body.target_identifier)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or destination project was not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Select two different projects")
    if source.workspace_id and target.workspace_id and source.workspace_id != target.workspace_id:
        raise HTTPException(status_code=409, detail="Projects from different workspaces cannot be merged")

    db.query(TokenTransaction).filter(TokenTransaction.work_item_id == source.id).update(
        {TokenTransaction.work_item_id: target.id}, synchronize_session=False
    )
    db.query(AuditEvent).filter(AuditEvent.work_item_id == source.id).update(
        {AuditEvent.work_item_id: target.id}, synchronize_session=False
    )

    target_agent_ids = {
        row[0] for row in db.query(WorkItemAgent.agent_id)
        .filter(WorkItemAgent.work_item_id == target.id).all()
    }
    for assignment in list(source.agent_assignments):
        if assignment.agent_id in target_agent_ids:
            db.delete(assignment)
        else:
            assignment.work_item_id = target.id
            target_agent_ids.add(assignment.agent_id)

    target_user_ids = {
        row[0] for row in db.query(WorkItemUser.work_user_id)
        .filter(WorkItemUser.work_item_id == target.id).all()
    }
    for assignment in list(source.user_assignments):
        if assignment.work_user_id in target_user_ids:
            db.delete(assignment)
        else:
            assignment.work_item_id = target.id
            target_user_ids.add(assignment.work_user_id)

    for link in list(source.source_links):
        duplicate = (
            db.query(WorkItemSourceLink)
            .filter(
                WorkItemSourceLink.work_item_id == target.id,
                WorkItemSourceLink.workspace_id == link.workspace_id,
                func.lower(WorkItemSourceLink.source_platform) == link.source_platform.lower(),
                WorkItemSourceLink.source_record_id == link.source_record_id,
            )
            .first()
        )
        if duplicate:
            db.delete(link)
        else:
            link.work_item_id = target.id

    source.status = "archived"
    source.merged_into_work_item_id = target.id
    source.updated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(target)
    return {
        "merged": source.external_id,
        "into": target.external_id,
        "project": _work_item_json(target, db),
    }


@router.post("/{identifier}/archive")
def archive_work_item(identifier: str, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    item.status = "archived"
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _work_item_json(item, db)


@router.get("/{identifier}/agents")
def list_project_agents(identifier: str, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    return _project_agent_rows(item, db)


@router.post("/{identifier}/agents")
def assign_project_agents(
    identifier: str,
    body: AgentAssignmentsIn,
    db: Session = Depends(get_db),
):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    requested_ids = [assignment.agent_id for assignment in body.assignments]
    agents = db.query(RegisteredAgent).filter(RegisteredAgent.id.in_(requested_ids)).all() if requested_ids else []
    found_ids = {agent.id for agent in agents}
    missing = sorted(set(requested_ids) - found_ids)
    if missing:
        raise HTTPException(status_code=404, detail=f"Agents not found: {', '.join(map(str, missing))}")

    existing = {
        assignment.agent_id: assignment
        for assignment in db.query(WorkItemAgent)
        .filter(
            WorkItemAgent.work_item_id == item.id,
            WorkItemAgent.agent_id.in_(requested_ids),
        )
        .all()
    } if requested_ids else {}
    for requested in body.assignments:
        assignment = existing.get(requested.agent_id)
        if assignment:
            assignment.role = requested.role.strip() or "Contributor"
            assignment.status = "assigned"
        else:
            db.add(WorkItemAgent(
                work_item_id=item.id,
                agent_id=requested.agent_id,
                role=requested.role.strip() or "Contributor",
                status="assigned",
                assigned_by=body.assigned_by,
            ))
    db.commit()
    return _project_agent_rows(item, db)


@router.post("/{identifier}/agents/create", status_code=201)
def create_project_agent(
    identifier: str,
    body: ProjectAgentCreateIn,
    db: Session = Depends(get_db),
):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    if body.collision_policy not in {"lock", "queue", "skip"}:
        raise HTTPException(status_code=400, detail="collision_policy must be lock, queue, or skip")
    agent = RegisteredAgent(
        name=body.name.strip(),
        department=body.department.strip(),
        source_platform=infer_platform(body.name, body.source_platform),
        permissions=body.permissions,
        target_table=body.target_table,
        collision_policy=body.collision_policy,
        status="idle",
    )
    db.add(agent)
    try:
        db.flush()
        db.add(WorkItemAgent(
            work_item_id=item.id,
            agent_id=agent.id,
            role=body.role.strip() or "Contributor",
            status="assigned",
            assigned_by=body.assigned_by,
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"An agent named '{body.name}' is already registered")
    return _project_agent_rows(item, db)


@router.delete("/{identifier}/agents/{agent_id}")
def unassign_project_agent(identifier: str, agent_id: int, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    assignment = (
        db.query(WorkItemAgent)
        .filter(WorkItemAgent.work_item_id == item.id, WorkItemAgent.agent_id == agent_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Agent is not assigned to this project")
    db.delete(assignment)
    db.commit()
    return {"unassigned": True, "agent_id": agent_id, "project_id": item.external_id}


@router.get("/{identifier}/users")
def list_project_users(identifier: str, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    return _project_user_rows(item, db)


@router.post("/{identifier}/users", status_code=201)
def upsert_project_user(
    identifier: str,
    body: ProjectUserUpsertIn,
    db: Session = Depends(get_db),
):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    status = body.status.strip().lower()
    if status not in {"active", "inactive"}:
        raise HTTPException(status_code=400, detail="status must be active or inactive")
    workspace_id = (body.workspace_id or item.workspace_id or "default").strip()
    platform = body.source_platform.strip()
    user = (
        db.query(WorkUser)
        .filter(
            WorkUser.workspace_id == workspace_id,
            WorkUser.source_platform == platform,
            WorkUser.external_id == body.external_id.strip(),
        )
        .first()
    )
    if not user:
        user = WorkUser(
            workspace_id=workspace_id,
            source_platform=platform,
            external_id=body.external_id.strip(),
            name=body.name.strip(),
            email=(body.email or "").strip() or None,
            status="active",
        )
        db.add(user)
        db.flush()
    else:
        user.name = body.name.strip()
        user.email = (body.email or "").strip() or user.email

    membership = (
        db.query(WorkItemUser)
        .filter(
            WorkItemUser.work_item_id == item.id,
            WorkItemUser.work_user_id == user.id,
        )
        .first()
    )
    if not membership:
        membership = WorkItemUser(
            work_item_id=item.id,
            work_user_id=user.id,
            role=body.role.strip(),
            status=status,
            can_use_ai=body.can_use_ai,
            assigned_by=body.assigned_by,
        )
        db.add(membership)
    else:
        membership.role = body.role.strip()
        membership.status = status
        membership.can_use_ai = body.can_use_ai
        membership.assigned_by = body.assigned_by or membership.assigned_by
    db.commit()
    return _project_user_rows(item, db)


@router.delete("/{identifier}/users/{user_id}")
def unassign_project_user(identifier: str, user_id: int, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    membership = (
        db.query(WorkItemUser)
        .filter(
            WorkItemUser.work_item_id == item.id,
            WorkItemUser.work_user_id == user_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=404, detail="User is not assigned to this project")
    db.delete(membership)
    db.commit()
    return {"unassigned": True, "user_id": user_id, "project_id": item.external_id}
