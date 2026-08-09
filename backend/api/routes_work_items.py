"""Work Attribution API — accounts and projects/matters/engagements."""

import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
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
    WorkItemOutcome,
    WorkItemSourceLink,
    WorkItemUser,
    WorkUser,
)
from core.agentlake import display_agent_name, display_department, infer_platform
from core.model_provider import load_provider_registry, resolve_provider
from core.workspace_scope import workspace_filter
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

BUSINESS_PURPOSE_RULES = (
    (
        "Customer & Employee Support",
        {
            "case", "incident", "problem", "support", "ticket", "customer",
            "contact", "service", "help", "triage", "resolution",
        },
    ),
    (
        "Sales & Revenue",
        {
            "account", "opportunity", "quote", "lead", "sales", "renewal",
            "pipeline", "campaign", "promotion", "loyalty",
        },
    ),
    (
        "IT Service Management",
        {
            "change_request", "change request", "cmdb", "it service",
            "maintenance", "infrastructure", "configuration",
        },
    ),
    (
        "Document Processing",
        {
            "document", "contract", "invoice", "content", "email", "brief",
            "summarization", "summarize",
        },
    ),
    (
        "Research & Analysis",
        {
            "research", "analysis", "analyst", "forecast", "review", "quality",
            "risk", "audit",
        },
    ),
    (
        "Software Development",
        {
            "code", "software", "developer", "engineering", "bug", "test",
            "release", "deployment",
        },
    ),
    (
        "Business Operations",
        {
            "operations", "workflow", "fulfillment", "inventory", "schedule",
            "billing", "finance", "capex", "refund", "chargeback",
        },
    ),
)


def classify_business_purpose_fields(
    origin_record_type=None, origin_record_name=None,
    context_type=None, source_record_type=None, agent_name=None,
):
    """Return an explainable business-purpose bucket from raw signal values.

    Takes plain values rather than ORM objects so it can be called at write
    time (before a TokenTransaction row exists) as well as at read time.
    """
    signals = " ".join(
        str(value or "").lower()
        for value in (
            origin_record_type, origin_record_name,
            context_type, source_record_type, agent_name,
        )
    )
    for label, keywords in BUSINESS_PURPOSE_RULES:
        if any(keyword in signals for keyword in keywords):
            return label
    return "Other / Unclassified"


def classify_business_purpose(tx, project=None, agent=None):
    """Return an explainable business-purpose bucket from persisted metadata."""
    return classify_business_purpose_fields(
        tx.origin_record_type,
        tx.origin_record_name,
        project.context_type if project else None,
        project.source_record_type if project else None,
        agent.name if agent else None,
    )


# How long a synced outcome is considered current before Ask CostPilot must
# call it out as potentially stale rather than presenting it as guaranteed
# fresh (see WorkItemOutcome.last_synced_at / core/outcome_adapters).
OUTCOME_FRESHNESS_WINDOW = timedelta(hours=24)


def _outcome_fields(outcome) -> dict:
    """Return the canonical outcome facts for one WorkItemOutcome row (or
    the "no outcome yet" shape if the work item hasn't been synced)."""
    if outcome is None:
        return {
            "outcome_status": None,
            "outcome_value": None,
            "outcome_date": None,
            "outcome_success": None,
            "outcome_is_closed": None,
            "outcome_source_system": None,
            "outcome_last_synced_at": None,
            "outcome_freshness": "unavailable",
        }
    age = datetime.utcnow() - outcome.last_synced_at if outcome.last_synced_at else None
    freshness = (
        "current" if age is not None and age <= OUTCOME_FRESHNESS_WINDOW
        else "potentially_stale"
    )
    return {
        "outcome_status": outcome.outcome_status,
        "outcome_value": outcome.outcome_value,
        "outcome_date": outcome.outcome_date.isoformat() if outcome.outcome_date else None,
        "outcome_success": outcome.outcome_success,
        "outcome_is_closed": outcome.is_closed,
        "outcome_source_system": outcome.source_system,
        "outcome_last_synced_at": outcome.last_synced_at.isoformat() if outcome.last_synced_at else None,
        "outcome_freshness": freshness,
    }


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
    context = business_context_json(item)
    origin_rows = (
        db.query(
            TokenTransaction.origin_record_id,
            TokenTransaction.origin_record_type,
            TokenTransaction.origin_record_name,
            func.count(TokenTransaction.id),
            func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
            func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.max(TokenTransaction.timestamp),
        )
        .filter(
            TokenTransaction.work_item_id == item.id,
            TokenTransaction.origin_record_id.isnot(None),
        )
        .group_by(
            TokenTransaction.origin_record_id,
            TokenTransaction.origin_record_type,
            TokenTransaction.origin_record_name,
        )
        .all()
    )
    origin_by_id = {
        str(row[0]): {
            "source_record_id": row[0],
            "source_record_type": row[1],
            "source_record_name": row[2] or row[0],
            "request_count": int(row[3] or 0),
            "total_tokens": int(row[4] or 0) + int(row[5] or 0),
            "spend_usd": round(float(row[6] or 0.0), 6),
            "last_activity_at": row[7].isoformat() if row[7] else None,
        }
        for row in origin_rows
    }
    related_record_activity = []
    for link in source_links:
        activity = origin_by_id.pop(str(link.source_record_id), None) or {
            "source_record_id": link.source_record_id,
            "source_record_type": link.source_record_type,
            "source_record_name": link.source_record_name or link.source_record_id,
            "request_count": 0,
            "total_tokens": 0,
            "spend_usd": 0.0,
            "last_activity_at": None,
        }
        activity["source_platform"] = link.source_platform
        activity["is_primary"] = bool(link.is_primary)
        related_record_activity.append(activity)
    related_record_activity.extend(origin_by_id.values())
    related_record_activity.sort(
        key=lambda row: (-row["spend_usd"], -row["request_count"], row["source_record_name"])
    )
    return {
        "id": item.id,
        "external_id": item.external_id,
        "name": context["name"],
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
        "context_type": context["type"],
        "context_template": item.context_template,
        "source_record_type": item.source_record_type,
        "source_record_id": item.source_record_id,
        "source_links": [_source_link_json(link) for link in source_links],
        "related_record_activity": related_record_activity,
        "merged_into_work_item_id": item.merged_into_work_item_id,
        "business_context": context,
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


@router.get("/accounts/{identifier}/profile")
def account_profile(
    identifier: str,
    workspace_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """
    Account-level rollup: AI investment + activity + savings across every
    WorkItem belonging to this account, plus business outcomes rolled up
    from WorkItemOutcome. This is the one thing that did not exist yet for
    a "Business Profile" page -- every other endpoint in this API operates
    at the work-item level, not the account level.

    Entirely SQL-side aggregation (SUM/COUNT/GROUP BY) -- no row loaded
    into Python beyond the small number of grouped result rows -- per the
    scalability principle that bit project_activity_reporting() before it
    was fixed (see that function's history).
    """
    account = (
        db.query(WorkAccount)
        .filter(WorkAccount.external_id == identifier)
        .first()
    )
    if not account and identifier.isdigit():
        account = db.query(WorkAccount).filter(WorkAccount.id == int(identifier)).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if workspace_id and account.workspace_id and account.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Account not found in this workspace")

    period_end = date_to or datetime.utcnow()
    period_start = date_from or (period_end - timedelta(days=days))
    if period_start >= period_end:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    work_item_ids_query = db.query(WorkItem.id).filter(WorkItem.account_id == account.id)
    work_item_ids = [row[0] for row in work_item_ids_query.all()]

    empty_kpis = {
        "ai_investment_usd": 0.0,
        "ai_activity_count": 0,
        "total_tokens": 0,
        "tokens_saved": 0,
        "ai_savings_usd_estimate": 0.0,
    }
    if not work_item_ids:
        return {
            "account": _account_json(account),
            "period": {"date_from": period_start.isoformat(), "date_to": period_end.isoformat(), "days": days},
            "work_item_count": 0,
            "kpis": empty_kpis,
            "business_function_breakdown": [],
            "outcomes": {
                "won_count": 0, "lost_count": 0, "open_count": 0,
                "pipeline_value_usd": 0.0, "closed_won_value_usd": 0.0,
            },
            "measurement_note": (
                "This account has no linked work items yet, so there is no "
                "AI activity or outcome data to report."
            ),
        }

    tx_base = db.query(TokenTransaction).filter(
        TokenTransaction.work_item_id.in_(work_item_ids),
        TokenTransaction.timestamp >= period_start,
        TokenTransaction.timestamp < period_end,
    )

    totals = tx_base.with_entities(
        func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        func.count(TokenTransaction.id),
        func.coalesce(func.sum(TokenTransaction.input_tokens + TokenTransaction.output_tokens), 0),
        func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
    ).first()
    spend_usd, activity_count, total_tokens, tokens_saved = totals

    # Simplified, clearly-labeled savings estimate (pruning-avoided tokens at
    # flagship input rate) -- not the full pruning+downgrade methodology
    # routes_reports.py's savings_report() uses, which requires per-row
    # tier-bucketing that isn't a clean SQL aggregate. Labeling this an
    # estimate rather than silently reusing a different, more precise number
    # under the same name.
    from api.routes_reports import FLAGSHIP_INPUT_COST
    savings_estimate = round(float(tokens_saved) * FLAGSHIP_INPUT_COST, 6)

    business_function_breakdown = [
        {"business_purpose": purpose or "Other / Unclassified", "spend_usd": round(float(spend), 6), "request_count": int(count)}
        for purpose, spend, count in (
            tx_base.with_entities(
                TokenTransaction.business_purpose,
                func.sum(TokenTransaction.cost_usd),
                func.count(TokenTransaction.id),
            )
            .group_by(TokenTransaction.business_purpose)
            .order_by(func.sum(TokenTransaction.cost_usd).desc())
            .all()
        )
    ]

    is_lost = and_(WorkItemOutcome.outcome_success.is_(False), WorkItemOutcome.is_closed.is_(True))
    is_open = WorkItemOutcome.is_closed.is_(False)
    is_won = WorkItemOutcome.outcome_success.is_(True)
    outcome_value = func.coalesce(WorkItemOutcome.outcome_value, 0.0)

    outcome_totals = (
        db.query(
            func.coalesce(func.sum(case((is_won, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_lost, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_open, 1), else_=0)), 0),
            func.coalesce(func.sum(case((is_open, outcome_value), else_=0.0)), 0.0),
            func.coalesce(func.sum(case((is_won, outcome_value), else_=0.0)), 0.0),
        )
        .filter(WorkItemOutcome.work_item_id.in_(work_item_ids))
        .first()
    )
    won_count, lost_count, open_count, pipeline_value, closed_won_value = outcome_totals

    return {
        "account": _account_json(account),
        "period": {"date_from": period_start.isoformat(), "date_to": period_end.isoformat(), "days": days},
        "work_item_count": len(work_item_ids),
        "kpis": {
            "ai_investment_usd": round(float(spend_usd), 6),
            "ai_activity_count": int(activity_count),
            "total_tokens": int(total_tokens),
            "tokens_saved": int(tokens_saved),
            "ai_savings_usd_estimate": savings_estimate,
        },
        "business_function_breakdown": business_function_breakdown,
        "outcomes": {
            "won_count": int(won_count or 0),
            "lost_count": int(lost_count or 0),
            "open_count": int(open_count or 0),
            "pipeline_value_usd": round(float(pipeline_value or 0.0), 2),
            "closed_won_value_usd": round(float(closed_won_value or 0.0), 2),
        },
        "measurement_note": (
            "AI investment reflects governed CostPilot activity across this "
            "account's work items. Business outcomes are synced from the "
            "connected system of record and are associated with, not "
            "caused by, this AI activity."
        ),
    }


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
            workspace_filter(TokenTransaction, workspace_id)
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


@router.get("/reporting")
def business_context_reporting(
    workspace_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=730),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Executive-safe parent totals and origin contributions counted once."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    item_query = db.query(WorkItem).filter(
        WorkItem.status != "archived",
        WorkItem.merged_into_work_item_id.is_(None),
    )
    if workspace_id:
        item_query = item_query.filter(WorkItem.workspace_id == workspace_id)
    items = item_query.all()
    item_by_id = {item.id: item for item in items}
    item_ids = list(item_by_id)

    tx_filters = [TokenTransaction.timestamp >= cutoff]
    audit_filters = [AuditEvent.timestamp >= cutoff]
    if workspace_id:
        tx_filters.append(workspace_filter(TokenTransaction, workspace_id))
        audit_filters.append(workspace_filter(AuditEvent, workspace_id))

    total_calls, total_input, total_output, total_saved, total_spend = (
        db.query(
            func.count(TokenTransaction.id),
            func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
            func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
            func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        )
        .filter(*tx_filters)
        .one()
    )
    attributed_rows = []
    if item_ids:
        attributed_rows = (
            db.query(
                TokenTransaction.work_item_id,
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.max(TokenTransaction.timestamp),
            )
            .filter(*tx_filters, TokenTransaction.work_item_id.in_(item_ids))
            .group_by(TokenTransaction.work_item_id)
            .all()
        )
    parent_stats = {
        row[0]: {
            "request_count": int(row[1] or 0),
            "input_tokens": int(row[2] or 0),
            "output_tokens": int(row[3] or 0),
            "tokens_saved": int(row[4] or 0),
            "spend_usd": float(row[5] or 0.0),
            "last_activity_at": row[6],
        }
        for row in attributed_rows
    }
    attributed_calls = sum(row["request_count"] for row in parent_stats.values())
    attributed_spend = sum(row["spend_usd"] for row in parent_stats.values())

    origin_stats: dict[int, list[dict]] = {}
    if item_ids:
        origin_rows = (
            db.query(
                TokenTransaction.work_item_id,
                TokenTransaction.origin_record_id,
                TokenTransaction.origin_record_type,
                TokenTransaction.origin_record_name,
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.input_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.output_tokens), 0),
                func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
                func.max(TokenTransaction.timestamp),
            )
            .filter(*tx_filters, TokenTransaction.work_item_id.in_(item_ids))
            .group_by(
                TokenTransaction.work_item_id,
                TokenTransaction.origin_record_id,
                TokenTransaction.origin_record_type,
                TokenTransaction.origin_record_name,
            )
            .all()
        )
        links_by_item = {
            item.id: {link.source_record_id: link for link in item.source_links}
            for item in items
        }
        for row in origin_rows:
            item_id, origin_id, origin_type, origin_name = row[:4]
            link = links_by_item.get(item_id, {}).get(origin_id)
            origin_stats.setdefault(item_id, []).append({
                "source_record_id": origin_id,
                "source_record_type": origin_type or (link.source_record_type if link else None),
                "source_record_name": origin_name or (link.source_record_name if link else None)
                    or ("Historical activity" if not origin_id else origin_id),
                "source_platform": link.source_platform if link else item_by_id[item_id].source_platform,
                "is_primary": bool(link.is_primary) if link else False,
                "origin_recorded": bool(origin_id),
                "request_count": int(row[4] or 0),
                "input_tokens": int(row[5] or 0),
                "output_tokens": int(row[6] or 0),
                "tokens_saved": int(row[7] or 0),
                "spend_usd": round(float(row[8] or 0.0), 6),
                "last_activity_at": row[9].isoformat() if row[9] else None,
            })

    risk_by_item = {}
    if item_ids:
        risk_rows = (
            db.query(AuditEvent.work_item_id, func.count(AuditEvent.id))
            .filter(
                *audit_filters,
                AuditEvent.work_item_id.in_(item_ids),
                func.lower(func.coalesce(AuditEvent.risk_level, "low")).in_(
                    ("medium", "high", "critical")
                ),
            )
            .group_by(AuditEvent.work_item_id)
            .all()
        )
        risk_by_item = {row[0]: int(row[1] or 0) for row in risk_rows}

    assigned_agents_by_item = {}
    observed_agents_by_item = {}
    if item_ids:
        assigned_agents_by_item = {
            row[0]: int(row[1] or 0)
            for row in db.query(WorkItemAgent.work_item_id, func.count(WorkItemAgent.id))
            .filter(WorkItemAgent.work_item_id.in_(item_ids))
            .group_by(WorkItemAgent.work_item_id)
            .all()
        }
        observed_agents_by_item = {
            row[0]: int(row[1] or 0)
            for row in db.query(
                TokenTransaction.work_item_id,
                func.count(func.distinct(TokenTransaction.agent_id)),
            )
            .filter(
                *tx_filters,
                TokenTransaction.work_item_id.in_(item_ids),
                TokenTransaction.agent_id.isnot(None),
            )
            .group_by(TokenTransaction.work_item_id)
            .all()
        }

    parents = []
    for item in items:
        stats = parent_stats.get(item.id, {
            "request_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens_saved": 0,
            "spend_usd": 0.0,
            "last_activity_at": None,
        })
        budget = float(item.monthly_ai_budget) if item.monthly_ai_budget is not None else None
        budget_used_pct = (
            round(stats["spend_usd"] / budget * 100, 1) if budget and budget > 0 else None
        )
        children = sorted(
            origin_stats.get(item.id, []),
            key=lambda row: (-row["spend_usd"], -row["request_count"]),
        )
        parents.append({
            "id": item.id,
            "external_id": item.external_id,
            "name": item.name,
            "context_type": item.context_type or "project",
            "status": item.status,
            "department": item.department,
            "source_platform": item.source_platform,
            "request_count": stats["request_count"],
            "input_tokens": stats["input_tokens"],
            "output_tokens": stats["output_tokens"],
            "total_tokens": stats["input_tokens"] + stats["output_tokens"],
            "tokens_saved": stats["tokens_saved"],
            "spend_usd": round(stats["spend_usd"], 6),
            "monthly_ai_budget": budget,
            "budget_used_pct": budget_used_pct,
            "risk_event_count": risk_by_item.get(item.id, 0),
            "last_activity_at": stats["last_activity_at"].isoformat()
                if stats["last_activity_at"] else None,
            "related_record_count": len(item.source_links),
            "assigned_agent_count": assigned_agents_by_item.get(item.id, 0),
            "agent_count": observed_agents_by_item.get(item.id, 0),
            "origin_coverage_pct": round(
                sum(child["request_count"] for child in children if child["origin_recorded"])
                / stats["request_count"] * 100,
                1,
            ) if stats["request_count"] else 0.0,
            "children": children,
        })
    parents.sort(key=lambda row: (-row["spend_usd"], -row["request_count"], row["name"]))

    active_types = [row["context_type"] for row in parents if row["request_count"]]
    context_type = max(set(active_types), key=active_types.count) if active_types else "work"
    labels = {
        "account": ("Account", "Accounts"),
        "customer": ("Customer", "Customers"),
        "matter": ("Matter", "Matters"),
        "project": ("Project", "Projects"),
        "case": ("Case", "Cases"),
        "opportunity": ("Opportunity", "Opportunities"),
        "engagement": ("Engagement", "Engagements"),
        "custom": ("Business Context", "Business Contexts"),
    }
    singular_label, plural_label = labels.get(
        context_type,
        (context_type.replace("_", " ").title(), f"{context_type.replace('_', ' ').title()}s"),
    )
    actions = []
    for parent in parents:
        signals = []
        if parent["budget_used_pct"] is not None and parent["budget_used_pct"] >= 100:
            signals.append("over_budget")
        elif parent["budget_used_pct"] is not None and parent["budget_used_pct"] >= 80:
            signals.append("budget_watch")
        if parent["risk_event_count"]:
            signals.append("risk")
        if signals:
            actions.append({
                "external_id": parent["external_id"],
                "name": parent["name"],
                "signals": signals,
                "budget_used_pct": parent["budget_used_pct"],
                "risk_event_count": parent["risk_event_count"],
                "spend_usd": parent["spend_usd"],
            })
    unattributed_calls = max(0, int(total_calls or 0) - attributed_calls)
    unattributed_spend = max(0.0, float(total_spend or 0.0) - attributed_spend)
    if unattributed_calls:
        actions.append({
            "external_id": None,
            "name": "Unattributed AI activity",
            "signals": ["unattributed"],
            "request_count": unattributed_calls,
            "spend_usd": round(unattributed_spend, 6),
        })

    return {
        "period_days": days,
        "context_type": context_type,
        "context_label": singular_label,
        "context_label_plural": plural_label,
        "company_totals": {
            "request_count": int(total_calls or 0),
            "input_tokens": int(total_input or 0),
            "output_tokens": int(total_output or 0),
            "total_tokens": int(total_input or 0) + int(total_output or 0),
            "tokens_saved": int(total_saved or 0),
            "spend_usd": round(float(total_spend or 0.0), 6),
        },
        "attribution": {
            "attributed_request_count": attributed_calls,
            "unattributed_request_count": unattributed_calls,
            "attributed_spend_usd": round(attributed_spend, 6),
            "unattributed_spend_usd": round(unattributed_spend, 6),
            "coverage_pct": round(
                attributed_calls / int(total_calls or 0) * 100,
                1,
            ) if total_calls else 0.0,
        },
        "active_context_count": sum(
            1 for row in parents if row["status"] == "active" and row["request_count"] > 0
        ),
        "parents": parents[:limit],
        "actions": sorted(
            actions,
            key=lambda row: (
                "over_budget" not in row["signals"],
                "risk" not in row["signals"],
                -float(row.get("spend_usd") or 0),
            ),
        )[:limit],
        "double_counting_protection": (
            "Company totals are calculated directly from transactions. Parent and child "
            "rows are alternate views of those same transactions and are never added together."
        ),
    }


@router.get("/activity-report")
def project_activity_reporting(
    workspace_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    days: int = Query(30, ge=1, le=365),
    project_id: Optional[str] = Query(None),
    user_external_id: Optional[str] = Query(None),
    agent_id: Optional[int] = Query(None),
    account_id: Optional[str] = Query(None),
    source_platform: Optional[str] = Query(None),
    record_type: Optional[str] = Query(None),
    model_tier: Optional[str] = Query(None),
    charged_unit: Optional[str] = None,
    business_purpose: Optional[str] = None,
    provider: Optional[str] = None,
    activity_limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Factual user → agent → account/project → token-cost attribution."""
    # Direct internal/test calls do not receive FastAPI's dependency coercion.
    if not isinstance(model_tier, (str, type(None))):
        model_tier = None
    period_end = date_to or datetime.utcnow()
    period_start = date_from or (period_end - timedelta(days=days))
    if period_start >= period_end:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    query = (
        db.query(
            TokenTransaction,
            WorkItem,
            WorkAccount,
            WorkUser,
            RegisteredAgent,
            WorkItemOutcome,
        )
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(WorkAccount, WorkItem.account_id == WorkAccount.id)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .outerjoin(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
        # AI activity <-> business outcome are joined here, at read time,
        # rather than ever copying outcome fields onto TokenTransaction --
        # an outcome belongs to the work item, not to any individual AI
        # event (see database/models.py WorkItemOutcome docstring).
        .outerjoin(WorkItemOutcome, WorkItem.id == WorkItemOutcome.work_item_id)
        .filter(
            TokenTransaction.timestamp >= period_start,
            TokenTransaction.timestamp < period_end,
        )
    )
    if workspace_id:
        query = query.filter(or_(
            TokenTransaction.workspace_id == workspace_id,
            TokenTransaction.workspace_id.is_(None) & (WorkItem.workspace_id == workspace_id),
        ))

    base_rows = query.order_by(TokenTransaction.timestamp.desc()).all()
    # Loaded once per report call, not once per row -- see
    # core/model_provider.py's resolve_provider() docstring for why a
    # per-row db lookup here would be a real N+1 query problem.
    provider_registry = load_provider_registry(db)

    def identity(row):
        tx, project, account, user, agent, outcome = row
        charged_unit_name = (
            (tx.charged_org_unit_name or "").strip()
            or (tx.department or "").split(":")[-1].strip()
            or "Unassigned"
        )
        return {
            **_outcome_fields(outcome),
            "project_external_id": project.external_id if project else None,
            "project_name": project.name if project else "Unattributed",
            "account_external_id": account.external_id if account else None,
            "account_name": account.name if account else "Unassigned account",
            "user_external_id": (
                user.external_id if user else tx.actor_external_id
            ),
            "user_name": user.name if user else (tx.actor_name or "Unknown user"),
            "user_email": user.email if user else tx.actor_email,
            "user_source_platform": (
                user.source_platform if user else tx.actor_source_platform
            ),
            "agent_id": agent.id if agent else tx.agent_id,
            "agent_name": agent.name if agent else "Unknown agent",
            "agent_platform": agent.source_platform if agent else tx.source_platform,
            "charged_unit": charged_unit_name,
            "actor_unit": tx.actor_org_unit_name,
            "agent_unit": tx.agent_org_unit_name,
            "work_unit": tx.work_org_unit_name,
            "attribution_source": tx.attribution_source,
            # Persisted at write time for rows created after the
            # business_purpose column existed (see models.py) -- only
            # reclassify here for older rows that predate it, so this isn't
            # redone from scratch on every report request for most data.
            "business_purpose": tx.business_purpose or classify_business_purpose(tx, project, agent),
        }

    def is_simulator_traffic(row):
        tx, project, _, user, agent, _outcome = row
        return bool(tx.is_simulation) or bool(
            agent
            and not project
            and not user
            and not tx.actor_external_id
            and not tx.origin_record_id
        )

    # identity()/is_simulator_traffic() are computed once per row here and
    # reused everywhere below (filtering, every breakdown, activities,
    # summary) instead of being recomputed on every access -- previously
    # each was called dozens of times per row across the function, which is
    # what actually made this scale badly (O(rows) work turning into
    # O(rows * ~24) work), not the lack of SQL GROUP BY per se.
    option_rows = [identity(row) for row in base_rows]
    sim_flags = [is_simulator_traffic(row) for row in base_rows]
    enriched_rows = list(zip(base_rows, option_rows, sim_flags))

    def matches(entry):
        row, item, _sim = entry
        tx = row[0]
        # A dropdown option can bundle several ids that share one display
        # name (see unique_options's mergeable dedupe) — the filter value
        # arrives as a comma-joined list in that case, a single id otherwise.
        if project_id and item["project_external_id"] not in project_id.split(","):
            return False
        if user_external_id and item["user_external_id"] not in user_external_id.split(","):
            return False
        if agent_id is not None and item["agent_id"] != agent_id:
            return False
        if account_id and item["account_external_id"] not in account_id.split(","):
            return False
        if source_platform and (tx.source_platform or "").lower() != source_platform.lower():
            return False
        if record_type and (tx.origin_record_type or "").lower() != record_type.lower():
            return False
        if model_tier and (tx.model_tier or "").lower() != model_tier.lower():
            return False
        if charged_unit and item["charged_unit"] != charged_unit:
            return False
        if business_purpose and item["business_purpose"] != business_purpose:
            return False
        if provider and resolve_provider(
            tx.model_name or tx.requested_model_name, registry=provider_registry
        ).lower() != provider.lower():
            return False
        return True

    filtered_entries = [entry for entry in enriched_rows if matches(entry)]
    rows = [entry[0] for entry in filtered_entries]
    governed_ids = {
        row[0].governed_request_id for row in rows
        if row[0].governed_request_id
    }
    audit_by_request = {}
    if governed_ids:
        audit_rows = (
            db.query(AuditEvent)
            .filter(AuditEvent.governed_request_id.in_(governed_ids))
            .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
            .all()
        )
        for audit in audit_rows:
            audit_by_request.setdefault(audit.governed_request_id, audit)

    # Each entry is (row, item, sim) where item = identity(row) and
    # sim = is_simulator_traffic(row), both computed once above. provider is
    # likewise resolved once per entry here rather than inside each lambda.
    entries_with_provider = [
        (row, item, sim, resolve_provider(row[0].model_name or row[0].requested_model_name, registry=provider_registry))
        for row, item, sim in filtered_entries
    ]

    def aggregate(key_fn):
        grouped = {}
        for entry in entries_with_provider:
            row = entry[0]
            tx = row[0]
            sim = entry[2]
            key, label, metadata = key_fn(entry)
            bucket = grouped.setdefault(key or "__unknown__", {
                "id": key,
                "label": label,
                **metadata,
                "request_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tokens_saved": 0,
                "spend_usd": 0.0,
                "simulation_count": 0,
            })
            bucket["request_count"] += 1
            bucket["input_tokens"] += int(tx.input_tokens or 0)
            bucket["output_tokens"] += int(tx.output_tokens or 0)
            bucket["tokens_saved"] += int(tx.tokens_saved or 0)
            bucket["spend_usd"] += float(tx.cost_usd or 0.0)
            bucket["simulation_count"] += 1 if sim else 0
        result = []
        for bucket in grouped.values():
            bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
            bucket["spend_usd"] = round(bucket["spend_usd"], 6)
            bucket["live_count"] = bucket["request_count"] - bucket["simulation_count"]
            result.append(bucket)
        return sorted(
            result,
            key=lambda bucket: (-bucket["spend_usd"], -bucket["request_count"], bucket["label"]),
        )

    project_breakdown = aggregate(lambda entry: (
        entry[1]["project_external_id"] or (
            "__simulator__" if entry[2] else None
        ),
        (
            "Simulator Traffic"
            if not entry[1]["project_external_id"] and entry[2]
            else entry[1]["project_name"]
        ),
        {
            "account_external_id": entry[1]["account_external_id"],
            "account_name": (
                "Synthetic workload"
                if not entry[1]["project_external_id"] and entry[2]
                else entry[1]["account_name"]
            ),
            # Outcome is a property of the work item, constant across every
            # AI event that rolls up to it -- safe to take from any one row
            # in the bucket rather than aggregated/summed like the metrics.
            "outcome_status": entry[1]["outcome_status"],
            "outcome_value": entry[1]["outcome_value"],
            "outcome_date": entry[1]["outcome_date"],
            "outcome_success": entry[1]["outcome_success"],
            "outcome_is_closed": entry[1]["outcome_is_closed"],
            "outcome_freshness": entry[1]["outcome_freshness"],
        },
    ))
    people_breakdown = aggregate(lambda entry: (
        entry[1]["user_external_id"] or (
            "__simulator__" if entry[2] else None
        ),
        (
            "Simulator User"
            if not entry[1]["user_external_id"] and entry[2]
            else entry[1]["user_name"]
        ),
        {
            "email": entry[1]["user_email"],
            "source_platform": (
                "CostPilot Simulator"
                if not entry[1]["user_external_id"] and entry[2]
                else entry[1]["user_source_platform"]
            ),
        },
    ))
    agent_breakdown = aggregate(lambda entry: (
        entry[1]["agent_id"],
        entry[1]["agent_name"],
        {"source_platform": entry[1]["agent_platform"]},
    ))
    # "Account" here means the business/customer entity (e.g. a company
    # record in Salesforce) — distinct from "people" (individual human
    # users). Account data was already tracked per-transaction (see
    # identity() above) and exposed as a filter option, but never
    # aggregated into its own ranked breakdown the way every other
    # dimension is — so "which accounts generated the most activity" had
    # no real data to answer from anywhere in the app.
    account_breakdown = aggregate(lambda entry: (
        entry[1]["account_external_id"],
        entry[1]["account_name"],
        {},
    ))
    purpose_breakdown = aggregate(lambda entry: (
        entry[1]["business_purpose"],
        entry[1]["business_purpose"],
        {},
    ))
    organizational_unit_breakdown = aggregate(lambda entry: (
        entry[1]["charged_unit"],
        entry[1]["charged_unit"],
        {},
    ))
    source_platform_breakdown = aggregate(lambda entry: (
        entry[0][0].source_platform or "Unknown platform",
        entry[0][0].source_platform or "Unknown platform",
        {},
    ))
    model_breakdown = aggregate(lambda entry: (
        entry[0][0].model_name or entry[0][0].model_tier or "Unknown model",
        entry[0][0].model_name or entry[0][0].model_tier or "Unknown model",
        {"model_tier": entry[0][0].model_tier},
    ))
    # Provider (Anthropic/OpenAI/...) is not a stored column on
    # TokenTransaction -- only model_name is -- so it's derived here rather
    # than filtered/grouped in SQL. Previously there was no provider
    # breakdown or filter at all, meaning "how much are we spending on
    # Claude" or "compare OpenAI and Anthropic" had no data to answer from
    # anywhere in the app even though the underlying model names were
    # perfectly resolvable to a provider.
    provider_breakdown = aggregate(lambda entry: (
        entry[3],
        entry[3],
        {},
    ))

    activities = []
    for row, item, sim, _provider in entries_with_provider[:activity_limit]:
        tx, project, account, user, agent, _outcome = row
        audit = audit_by_request.get(tx.governed_request_id)
        activities.append({
            "transaction_id": tx.id,
            "governed_request_id": tx.governed_request_id,
            "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
            **item,
            "source_platform": tx.source_platform,
            "source_record_id": tx.origin_record_id,
            "source_record_type": tx.origin_record_type,
            "source_record_name": tx.origin_record_name,
            "model_tier": tx.model_tier,
            "model_name": tx.model_name,
            "input_tokens": int(tx.input_tokens or 0),
            "output_tokens": int(tx.output_tokens or 0),
            "total_tokens": int(tx.input_tokens or 0) + int(tx.output_tokens or 0),
            "tokens_saved": int(tx.tokens_saved or 0),
            "cost_usd": round(float(tx.cost_usd or 0.0), 6),
            "was_pruned": bool(tx.was_pruned),
            "routing_reason": tx.routing_reason,
            "routing_policy_version": tx.routing_policy_version,
            "requested_model_name": tx.requested_model_name,
            "requested_model_tier": tx.requested_model_tier,
            "execution_status": tx.execution_status,
            "provider_status_code": tx.provider_status_code,
            "audit_event_id": audit.id if audit else None,
            "audit_rationale": audit.rationale if audit else None,
            "decision_outcome": audit.decision_outcome if audit else None,
            "risk_level": audit.risk_level if audit else None,
            "routing_reason_code": audit.routing_reason_code if audit else None,
            "is_simulation": sim,
        })

    def unique_options(key, label, extra=None, mergeable=False):
        # The same real-world person/account can end up with more than one
        # id in this data (e.g. one row seeded by the historical demo
        # script, another created later by the traffic simulator's
        # get-or-create resolver) — without this, filter dropdowns showed
        # the same name twice with no way to tell them apart. For
        # string-keyed filters (mergeable=True), dedupe by the displayed
        # label and fold every id sharing that label into one option whose
        # value is a comma-joined id list; matches() below accepts either a
        # single id or that list. agent_id is a typed int query param and
        # can't carry a comma list, so it keeps the plain per-id dedupe.
        if not mergeable:
            options = {}
            for item in option_rows:
                value = item.get(key)
                if value is None:
                    continue
                options[str(value)] = {
                    "value": value,
                    "label": item.get(label) or str(value),
                    **({extra: item.get(extra)} if extra else {}),
                }
            return sorted(options.values(), key=lambda item: item["label"].lower())

        by_label = {}
        for item in option_rows:
            value = item.get(key)
            if value is None:
                continue
            label_text = item.get(label) or str(value)
            bucket = by_label.setdefault(label_text, {"label": label_text, "ids": [], "extra": item.get(extra) if extra else None})
            if str(value) not in bucket["ids"]:
                bucket["ids"].append(str(value))
        options = []
        for bucket in by_label.values():
            entry = {
                "value": bucket["ids"][0] if len(bucket["ids"]) == 1 else ",".join(bucket["ids"]),
                "label": bucket["label"],
            }
            if extra:
                entry[extra] = bucket["extra"]
            options.append(entry)
        return sorted(options, key=lambda item: item["label"].lower())

    total_input = sum(int(row[0].input_tokens or 0) for row in rows)
    total_output = sum(int(row[0].output_tokens or 0) for row in rows)
    active_context_types = [
        (row[1].context_type or "").strip().lower()
        for row in rows
        if row[1] and (row[1].context_type or "").strip()
    ]
    context_type = (
        max(set(active_context_types), key=active_context_types.count)
        if active_context_types else "work"
    )
    context_labels = {
        "account": ("Account", "Accounts"),
        "customer": ("Customer", "Customers"),
        "matter": ("Matter", "Matters"),
        "project": ("Project", "Projects"),
        "case": ("Case", "Cases"),
        "opportunity": ("Opportunity", "Opportunities"),
        "engagement": ("Engagement", "Engagements"),
        "custom": ("Business Context", "Business Contexts"),
        "work": ("Work", "Work"),
    }
    context_label, context_label_plural = context_labels.get(
        context_type,
        (
            context_type.replace("_", " ").title(),
            f"{context_type.replace('_', ' ').title()}s",
        ),
    )
    return {
        "period": {
            "date_from": period_start.isoformat(),
            "date_to": period_end.isoformat(),
            "days": max(1, (period_end - period_start).days),
        },
        "filters": {
            "project_id": project_id,
            "user_external_id": user_external_id,
            "agent_id": agent_id,
            "account_id": account_id,
            "source_platform": source_platform,
            "record_type": record_type,
            "model_tier": model_tier,
            "charged_unit": charged_unit,
            "business_purpose": business_purpose,
        },
        "context_type": context_type,
        "context_label": context_label,
        "context_label_plural": context_label_plural,
        "filter_options": {
            "projects": unique_options("project_external_id", "project_name", "account_name", mergeable=True),
            "people": unique_options("user_external_id", "user_name", "user_email", mergeable=True),
            "agents": unique_options("agent_id", "agent_name", "agent_platform"),
            "accounts": unique_options("account_external_id", "account_name", mergeable=True),
            "source_platforms": sorted({
                row[0].source_platform for row in base_rows if row[0].source_platform
            }),
            "record_types": sorted({
                row[0].origin_record_type for row in base_rows if row[0].origin_record_type
            }),
            "organizational_units": unique_options("charged_unit", "charged_unit"),
            "business_purposes": unique_options("business_purpose", "business_purpose"),
        },
        "summary": {
            "request_count": len(rows),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "tokens_saved": sum(int(row[0].tokens_saved or 0) for row in rows),
            "spend_usd": round(sum(float(row[0].cost_usd or 0.0) for row in rows), 6),
            "people_count": len({e[1]["user_external_id"] for e in filtered_entries if e[1]["user_external_id"]}),
            "agent_count": len({e[1]["agent_id"] for e in filtered_entries if e[1]["agent_id"] is not None}),
            "project_count": len({e[1]["project_external_id"] for e in filtered_entries if e[1]["project_external_id"]}),
            "simulation_count": sum(1 for e in filtered_entries if e[2]),
            "live_count": sum(1 for e in filtered_entries if not e[2]),
        },
        "project_breakdown": project_breakdown,
        "people_breakdown": people_breakdown,
        "agent_breakdown": agent_breakdown,
        "account_breakdown": account_breakdown,
        "organizational_unit_breakdown": organizational_unit_breakdown,
        "business_purpose_breakdown": purpose_breakdown,
        "source_platform_breakdown": source_platform_breakdown,
        "model_breakdown": model_breakdown,
        "provider_breakdown": provider_breakdown,
        "activities": activities,
        "activity_count": len(rows),
        "activity_limit": activity_limit,
        "evidence_quality": {
            "request_identity_count": sum(
                1 for row in rows if row[0].governed_request_id
            ),
            "correlated_request_count": sum(
                1 for row in rows
                if row[0].governed_request_id in audit_by_request
            ),
            "total_request_count": len(rows),
        },
        "measurement_note": (
            "This report attributes AI consumption only. It does not score employee "
            "productivity or infer business outcomes."
        ),
    }


@router.get("/organizational-usage")
def organizational_usage_reporting(
    workspace_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    days: int = Query(30, ge=1, le=365),
    charged_unit: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Company to organizational-unit drill-down, with each transaction counted once."""
    period_end = date_to or datetime.utcnow()
    period_start = date_from or (period_end - timedelta(days=days))
    if period_start >= period_end:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")

    query = (
        db.query(TokenTransaction, WorkUser, RegisteredAgent, WorkItem)
        .outerjoin(WorkUser, TokenTransaction.work_user_id == WorkUser.id)
        .outerjoin(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .filter(
            TokenTransaction.timestamp >= period_start,
            TokenTransaction.timestamp < period_end,
        )
    )
    if workspace_id:
        query = query.filter(or_(
            TokenTransaction.workspace_id == workspace_id,
            TokenTransaction.workspace_id.is_(None) & (WorkItem.workspace_id == workspace_id),
        ))
    rows = query.all()

    def charged_name(tx):
        return (
            (tx.charged_org_unit_name or "").strip()
            or (tx.department or "").split(":")[-1].strip()
            or "Unassigned"
        )

    if charged_unit:
        rows = [row for row in rows if charged_name(row[0]) == charged_unit]

    def blank_bucket(label):
        return {
            "label": label, "request_count": 0, "input_tokens": 0,
            "output_tokens": 0, "tokens_pruned": 0, "spend_usd": 0.0,
        }

    company = blank_bucket("Company")
    units, users, agents, work = {}, {}, {}, {}
    for tx, user, agent, item in rows:
        unit_name = charged_name(tx)
        is_simulation = bool(tx.is_simulation) or bool(
            agent
            and not item
            and not user
            and not tx.actor_external_id
            and not tx.origin_record_id
        )
        user_key = (
            (user.external_id if user else tx.actor_external_id)
            or ("__simulator__" if is_simulation else "__unknown__")
        )
        agent_key = str(agent.id if agent else tx.agent_id or "__unknown__")
        work_key = (
            item.external_id
            if item
            else ("__simulator__" if is_simulation else "__unattributed__")
        )
        dimensions = (
            company,
            units.setdefault(unit_name, {**blank_bucket(unit_name), "unit_type": "department"}),
            users.setdefault((unit_name, user_key), {
                **blank_bucket(
                    user.name
                    if user
                    else (tx.actor_name or ("Simulator User" if is_simulation else "Unknown user"))
                ),
                "external_id": (
                    None if user_key in {"__unknown__", "__simulator__"} else user_key
                ),
                "charged_unit": unit_name,
            }),
            agents.setdefault((unit_name, agent_key), {
                **blank_bucket(display_agent_name(agent.name, agent.department, agent.source_platform) if agent else "Unknown agent"),
                "agent_id": agent.id if agent else tx.agent_id,
                "charged_unit": unit_name,
            }),
            work.setdefault((unit_name, work_key), {
                **blank_bucket(
                    item.name
                    if item
                    else ("Simulator Traffic" if is_simulation else "Unattributed work")
                ),
                "external_id": item.external_id if item else None,
                "charged_unit": unit_name,
            }),
        )
        for bucket in dimensions:
            bucket["request_count"] += 1
            bucket["input_tokens"] += int(tx.input_tokens or 0)
            bucket["output_tokens"] += int(tx.output_tokens or 0)
            bucket["tokens_pruned"] += int(tx.tokens_saved or 0)
            bucket["spend_usd"] += float(tx.cost_usd or 0.0)

    def finish(values):
        result = []
        for bucket in values:
            bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
            bucket["spend_usd"] = round(bucket["spend_usd"], 6)
            result.append(bucket)
        return sorted(result, key=lambda row: (-row["spend_usd"], -row["request_count"], row["label"]))

    finish([company])
    return {
        "period": {
            "date_from": period_start.isoformat(),
            "date_to": period_end.isoformat(),
            "workspace_id": workspace_id,
        },
        "company": company,
        "organizational_units": finish(list(units.values())),
        "users": finish(list(users.values())),
        "agents": finish(list(agents.values())),
        "work_items": finish(list(work.values())),
        "counting_rule": (
            "Company and drill-down rows are alternate dimensions of the same token "
            "transactions; only company totals should be summed."
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
    if target.status == "archived" or target.merged_into_work_item_id is not None:
        raise HTTPException(
            status_code=409,
            detail="The destination project is archived or already merged. Restore it before using it as the surviving project.",
        )
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


@router.post("/{identifier}/restore-merge")
def restore_merged_work_item(identifier: str, db: Session = Depends(get_db)):
    item = resolve_work_item(db, identifier)
    if not item:
        raise HTTPException(status_code=404, detail="Work item not found")
    if item.status != "archived" and item.merged_into_work_item_id is None:
        raise HTTPException(status_code=409, detail="This project is already active")
    item.status = "active"
    item.merged_into_work_item_id = None
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _work_item_json(item, db)


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
