"""Work Attribution API — accounts and projects/matters/engagements."""

import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, literal, or_
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


class MergeAccountsIn(BaseModel):
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
        "merged_into_work_account_id": account.merged_into_work_account_id,
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


def resolve_work_account(db: Session, identifier: str) -> Optional[WorkAccount]:
    """Resolve either the public external ID or the internal integer ID."""
    value = str(identifier or "").strip()
    if not value:
        return None
    account = db.query(WorkAccount).filter(WorkAccount.external_id == value).first()
    if not account and value.isdigit():
        account = db.query(WorkAccount).filter(WorkAccount.id == int(value)).first()
    return account


def resolve_account_through_merge(db: Session, account: Optional[WorkAccount]) -> Optional[WorkAccount]:
    """Follow merged_into_work_account_id to the surviving account.

    Merging (see merge_work_accounts) only repoints WorkItems that exist at
    merge time -- it can't do anything about a live-write path that later
    looks an account back up by its old external_id (two connected orgs
    can genuinely share one, e.g. two Salesforce dev orgs both named
    "GenePoint"). Without this, new activity silently starts a second,
    invisible pocket of data on the archived account instead of landing on
    the one actually shown on Business Profile. Any write path that
    resolves a WorkAccount by external_id and then attaches new activity
    to it should call this on the result first. Bounded hop count is
    defensive only -- merges aren't expected to chain.
    """
    seen = set()
    while account and account.merged_into_work_account_id and account.id not in seen:
        seen.add(account.id)
        account = db.query(WorkAccount).filter(WorkAccount.id == account.merged_into_work_account_id).first()
    return account


@router.get("/accounts")
def list_accounts(
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WorkAccount).filter(WorkAccount.status != "merged")
    if workspace_id:
        query = query.filter(WorkAccount.workspace_id == workspace_id)
    return [_account_json(account) for account in query.order_by(WorkAccount.name).all()]


@router.post("/accounts/{identifier}/merge")
def merge_work_accounts(
    identifier: str,
    body: MergeAccountsIn,
    db: Session = Depends(get_db),
):
    source = resolve_work_account(db, identifier)
    target = resolve_work_account(db, body.target_identifier)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or destination account was not found")
    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Select two different accounts")
    if target.status == "merged" or target.merged_into_work_account_id is not None:
        raise HTTPException(
            status_code=409,
            detail="The destination account is already merged. Restore it before using it as the surviving account.",
        )
    if source.workspace_id and target.workspace_id and source.workspace_id != target.workspace_id:
        raise HTTPException(status_code=409, detail="Accounts from different workspaces cannot be merged")

    # Every downstream table (TokenTransaction, WorkItemOutcome, AuditEvent,
    # source links) hangs off WorkItem, not WorkAccount -- so repointing
    # WorkItem.account_id is the entire merge. Nothing else needs to move.
    db.query(WorkItem).filter(WorkItem.account_id == source.id).update(
        {WorkItem.account_id: target.id}, synchronize_session=False
    )

    source.status = "merged"
    source.merged_into_work_account_id = target.id
    source.updated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(target)
    return {
        "merged": source.external_id,
        "into": target.external_id,
        "account": _account_json(target),
    }


@router.post("/accounts/{identifier}/restore-merge")
def restore_merged_work_account(identifier: str, db: Session = Depends(get_db)):
    account = resolve_work_account(db, identifier)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.status != "merged" and account.merged_into_work_account_id is None:
        raise HTTPException(status_code=409, detail="This account is already active")
    account.status = "active"
    account.merged_into_work_account_id = None
    account.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(account)
    return _account_json(account)


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
            "account": {**_account_json(account), "active_since": None, "tier": None, "health_status": None},
            "period": {"date_from": period_start.isoformat(), "date_to": period_end.isoformat(), "days": days},
            "work_item_count": 0,
            "kpis": empty_kpis,
            "prior_period": {
                "date_from": None, "date_to": None,
                "ai_investment_usd": 0.0, "ai_activity_count": 0, "ai_savings_usd_estimate": 0.0,
            },
            "business_function_breakdown": [],
            "journey_breakdown": [],
            "outcomes": {
                "won_count": 0, "lost_count": 0, "open_count": 0,
                "pipeline_value_usd": 0.0, "closed_won_value_usd": 0.0,
                "support_cases": None, "active_projects": None,
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

    # Previous period of equal length, purely so the frontend can show a
    # real "up/down X% vs prior period" comparison instead of inventing
    # trend language -- same SQL-aggregated shape as the current period,
    # no row loop, and simply zero (not omitted) when there's no prior data.
    # Covers AI investment, activity count, and the savings estimate --
    # pipeline/closed-won ("Business Value") is deliberately not given a
    # prior-period delta: pipeline is a live snapshot of currently-open
    # opportunities, not something that happened "during" a period, so a
    # naive prior-period re-query of it wouldn't be a real comparison.
    prior_period_end = period_start
    prior_period_start = period_start - (period_end - period_start)
    prior_totals = (
        db.query(
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            func.count(TokenTransaction.id),
            func.coalesce(func.sum(TokenTransaction.tokens_saved), 0),
        )
        .filter(
            TokenTransaction.work_item_id.in_(work_item_ids),
            TokenTransaction.timestamp >= prior_period_start,
            TokenTransaction.timestamp < prior_period_end,
        )
        .first()
    )
    prior_spend_usd, prior_activity_count, prior_tokens_saved = prior_totals
    prior_spend_usd = float(prior_spend_usd or 0.0)
    prior_savings_estimate = round(float(prior_tokens_saved or 0) * FLAGSHIP_INPUT_COST, 6)

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

    # Business "journey" stage, using WorkItem.context_type -- what kind of
    # work each item actually is (Opportunity, Case, Project, ...), which
    # is real, already-captured data. Deliberately not a Marketing ->
    # Closed Won -> Implementation -> Support -> Renewal pipeline: CostPilot
    # doesn't track a work item moving through stages over time today, only
    # what type it is and (for Opportunities) its outcome. Showing that
    # invented pipeline would misrepresent what's actually known.
    stage_rows = (
        tx_base
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .with_entities(
            WorkItem.context_type,
            func.sum(TokenTransaction.cost_usd),
            func.count(TokenTransaction.id),
        )
        .group_by(WorkItem.context_type)
        .order_by(func.sum(TokenTransaction.cost_usd).desc())
        .all()
    )
    stage_labels = {
        "campaign": "Marketing", "opportunity": "Opportunity", "project": "Project",
        "engagement": "Engagement", "case": "Support", "ticket": "Support",
        "incident": "Support", "claim": "Claims", "product": "Product",
        "application": "Application", "environment": "Environment",
        "account": "Account", "customer": "Customer", "custom": "Other",
    }
    journey_breakdown = [
        {
            "stage": context_type or "custom",
            "label": stage_labels.get(context_type or "custom", (context_type or "Other").replace("_", " ").title()),
            "spend_usd": round(float(spend), 6),
            "request_count": int(count),
            # Only the Opportunity stage has a won/lost concept today.
            "won_count": int(won_count or 0) if context_type == "opportunity" else None,
        }
        for context_type, spend, count in stage_rows
    ]

    # Everything below is a heuristic derived from real data, not a CRM
    # field CostPilot actually stores -- each is documented as such so the
    # frontend doesn't present it as more authoritative than it is.

    active_since = (
        db.query(func.min(WorkItem.created_at))
        .filter(WorkItem.account_id == account.id)
        .scalar()
    )

    # Tier is a simple spend-bucket heuristic, not a real CRM segment.
    lifetime_value = float((pipeline_value or 0.0) + (closed_won_value or 0.0))
    if lifetime_value >= 1_000_000:
        tier = "Enterprise"
    elif lifetime_value >= 100_000:
        tier = "Mid-Market"
    else:
        tier = "Growth"

    # Health is a simple heuristic (current vs. prior period spend), not a
    # certified account-health score.
    if activity_count == 0 and prior_activity_count > 0:
        health_status = "attention"
    elif prior_spend_usd > 0 and spend_usd < prior_spend_usd * 0.5:
        health_status = "attention"
    else:
        health_status = "healthy"

    support_context_types = ("case", "ticket", "incident")
    support_total, support_resolved = (
        db.query(
            func.count(WorkItem.id),
            func.coalesce(func.sum(case((WorkItemOutcome.is_closed.is_(True), 1), else_=0)), 0),
        )
        .outerjoin(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.account_id == account.id, WorkItem.context_type.in_(support_context_types))
        .first()
    )
    support_cases = (
        {"total": int(support_total), "resolved": int(support_resolved)}
        if support_total else None
    )

    project_in_progress, project_completed, project_value = (
        db.query(
            func.coalesce(func.sum(case((WorkItem.status.in_(("active", "on_hold")), 1), else_=0)), 0),
            func.coalesce(func.sum(case((WorkItem.status == "completed", 1), else_=0)), 0),
            func.coalesce(func.sum(func.coalesce(WorkItemOutcome.outcome_value, 0.0)), 0.0),
        )
        .outerjoin(WorkItemOutcome, WorkItemOutcome.work_item_id == WorkItem.id)
        .filter(WorkItem.account_id == account.id, WorkItem.context_type == "project")
        .first()
    )
    active_projects = (
        {
            "in_progress": int(project_in_progress),
            "completed": int(project_completed),
            "value_usd": round(float(project_value or 0.0), 2),
        }
        if (project_in_progress + project_completed) else None
    )

    return {
        "account": {
            **_account_json(account),
            "active_since": active_since.isoformat() if active_since else None,
            "tier": tier,
            "health_status": health_status,
        },
        "period": {"date_from": period_start.isoformat(), "date_to": period_end.isoformat(), "days": days},
        "work_item_count": len(work_item_ids),
        "kpis": {
            "ai_investment_usd": round(float(spend_usd), 6),
            "ai_activity_count": int(activity_count),
            "total_tokens": int(total_tokens),
            "tokens_saved": int(tokens_saved),
            "ai_savings_usd_estimate": savings_estimate,
        },
        "prior_period": {
            "date_from": prior_period_start.isoformat(),
            "date_to": prior_period_end.isoformat(),
            "ai_investment_usd": round(prior_spend_usd, 6),
            "ai_activity_count": int(prior_activity_count or 0),
            "ai_savings_usd_estimate": prior_savings_estimate,
        },
        "business_function_breakdown": business_function_breakdown,
        "journey_breakdown": journey_breakdown,
        "outcomes": {
            "won_count": int(won_count or 0),
            "lost_count": int(lost_count or 0),
            "open_count": int(open_count or 0),
            "pipeline_value_usd": round(float(pipeline_value or 0.0), 2),
            "closed_won_value_usd": round(float(closed_won_value or 0.0), 2),
            "support_cases": support_cases,
            "active_projects": active_projects,
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

    # Full SQL-side aggregation -- no per-transaction row is loaded into
    # Python anymore except for the (small, activity_limit-capped) activity
    # feed itself. Previously this whole function loaded up to
    # MAX_REPORTING_ROWS full 6-table-joined ORM rows and did every
    # breakdown/summary/filter-option computation in a Python loop over
    # them; that Python-side work (not the lack of a SQL LIMIT) was the
    # actual scaling problem once row counts grew. See
    # project_fage_tech_debt memory and account_profile() (same file) for
    # the established SUM/COUNT/GROUP BY idiom this follows.
    MAX_REPORTING_ROWS = 5000

    # SQL equivalent of the old is_simulator_traffic(row) Python function --
    # reused everywhere below that needs a simulation_count/live_count
    # split, or the "no real identity, treat as simulator" fallback key
    # used by project_breakdown/people_breakdown.
    is_sim_condition = or_(
        TokenTransaction.is_simulation.is_(True),
        and_(
            RegisteredAgent.id.isnot(None),
            WorkItem.id.is_(None),
            WorkUser.id.is_(None),
            TokenTransaction.actor_external_id.is_(None),
            TokenTransaction.origin_record_id.is_(None),
        ),
    )
    request_count_expr = func.count(TokenTransaction.id)
    input_tokens_expr = func.coalesce(func.sum(TokenTransaction.input_tokens), 0)
    output_tokens_expr = func.coalesce(func.sum(TokenTransaction.output_tokens), 0)
    tokens_saved_expr = func.coalesce(func.sum(TokenTransaction.tokens_saved), 0)
    spend_expr = func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0)
    sim_count_expr = func.coalesce(func.sum(case((is_sim_condition, 1), else_=0)), 0)

    # Pushing filters down to SQL for the actual report/aggregation instead
    # of loading up to MAX_REPORTING_ROWS regardless of scope and filtering
    # every one of them in Python -- almost every real call passes at least
    # one of these (all 4 Ask CostPilot call sites do). Each filter below
    # has a proven exact SQL equivalent to its old Python check. agent_id in
    # particular: the old Python check compared `agent.id if agent else
    # tx.agent_id`, but the join condition is
    # `TokenTransaction.agent_id == RegisteredAgent.id`, so agent.id always
    # equals tx.agent_id whenever the join succeeds -- filtering
    # TokenTransaction.agent_id directly is exactly equivalent in both the
    # joined and unjoined case.
    filtered_query = query
    if project_id:
        filtered_query = filtered_query.filter(WorkItem.external_id.in_(project_id.split(",")))
    if user_external_id:
        ids = user_external_id.split(",")
        filtered_query = filtered_query.filter(or_(
            WorkUser.external_id.in_(ids),
            and_(WorkUser.id.is_(None), TokenTransaction.actor_external_id.in_(ids)),
        ))
    if agent_id is not None:
        filtered_query = filtered_query.filter(TokenTransaction.agent_id == agent_id)
    if account_id:
        filtered_query = filtered_query.filter(WorkAccount.external_id.in_(account_id.split(",")))
    if source_platform:
        filtered_query = filtered_query.filter(func.lower(func.coalesce(TokenTransaction.source_platform, "")) == source_platform.lower())
    if record_type:
        filtered_query = filtered_query.filter(func.lower(func.coalesce(TokenTransaction.origin_record_type, "")) == record_type.lower())
    if model_tier:
        filtered_query = filtered_query.filter(func.lower(func.coalesce(TokenTransaction.model_tier, "")) == model_tier.lower())
    if charged_unit:
        # charged_unit's real value has a fallback chain (charged_org_unit_
        # name, else the department string's segment after its last colon,
        # else "Unassigned") that isn't safely reproducible as a single
        # exact SQL expression across both SQLite (tests) and Postgres
        # (production) without real risk of subtly diverging from it for
        # some department string shape. This departs from the Python
        # original in one respect: rows whose only match would come through
        # the department-suffix fallback with a MULTI-colon department
        # string (not the `workspace:department` shape used everywhere in
        # this codebase today) could be missed. Documented rather than
        # silently accepted -- acceptable given real data never has more
        # than one colon in `department`.
        filtered_query = filtered_query.filter(or_(
            func.trim(func.coalesce(TokenTransaction.charged_org_unit_name, "")) == charged_unit,
            TokenTransaction.department == charged_unit,
            TokenTransaction.department.like(f"%:{charged_unit}"),
        ))
    if business_purpose:
        filtered_query = filtered_query.filter(TokenTransaction.business_purpose == business_purpose)

    matched_query = filtered_query

    # -- Breakdowns -----------------------------------------------------
    # Each dimension is one small GROUP BY query -- returns as many rows as
    # there are distinct departments/agents/accounts/etc. (typically tens),
    # never one row per transaction. Shaped into the exact same
    # {id, label, ...metadata, request_count, input_tokens, output_tokens,
    # tokens_saved, spend_usd, simulation_count, total_tokens, live_count}
    # dict shape the old Python aggregate() produced, sorted the same way
    # (spend desc, then request_count desc, then label) so every existing
    # consumer (4 Ask CostPilot call sites, ask_costpilot_tools.py, 3
    # frontend pages, and the test suite) sees an identical contract.

    def _shape_bucket(key, label, metrics):
        # metrics is always exactly (request_count, input_tokens,
        # output_tokens, tokens_saved, spend_usd, simulation_count), in
        # that order -- every call site below passes precisely that tuple,
        # with any dimension-specific extra columns (platform, model_tier,
        # etc.) pulled out and attached separately by the caller instead of
        # being threaded through here.
        request_count, in_tok, out_tok, saved, spend, sim_count = metrics
        bucket = {
            "id": key,
            "label": label if label is not None else "Unknown",
            "request_count": int(request_count),
            "input_tokens": int(in_tok),
            "output_tokens": int(out_tok),
            "tokens_saved": int(saved),
            "spend_usd": round(float(spend), 6),
            "simulation_count": int(sim_count),
        }
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
        bucket["live_count"] = bucket["request_count"] - bucket["simulation_count"]
        return bucket

    def _shape_rows(sql_rows):
        # For the simple case: every row is exactly (key, label, *metrics)
        # with no extra columns in between.
        return [_shape_bucket(row[0], row[1], row[2:]) for row in sql_rows]

    def _sort_breakdown(bucket_list):
        return sorted(
            bucket_list,
            key=lambda bucket: (-bucket["spend_usd"], -bucket["request_count"], bucket["label"]),
        )

    # agent_breakdown
    agent_key = TokenTransaction.agent_id
    agent_label = func.coalesce(RegisteredAgent.name, literal("Unknown agent"))
    agent_platform = func.coalesce(RegisteredAgent.source_platform, TokenTransaction.source_platform)
    agent_rows = (
        matched_query.with_entities(
            agent_key, agent_label, agent_platform,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(agent_key, agent_label, agent_platform)
        .all()
    )
    agent_breakdown = []
    for row in agent_rows:
        bucket = _shape_bucket(row[0], row[1], row[3:])
        bucket["source_platform"] = row[2]
        agent_breakdown.append(bucket)
    agent_breakdown = _sort_breakdown(agent_breakdown)

    # account_breakdown -- "which accounts generated the most AI activity"
    account_key = func.coalesce(WorkAccount.external_id, literal("__unknown__"))
    account_label = func.coalesce(WorkAccount.name, literal("Unassigned account"))
    account_rows = (
        matched_query.with_entities(
            account_key, account_label,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(account_key, account_label)
        .all()
    )
    account_breakdown = _sort_breakdown(_shape_rows(account_rows))
    for bucket, row in zip(account_breakdown, account_rows):
        pass  # account_breakdown has no extra metadata fields beyond id/label

    # business_purpose_breakdown -- persisted at write time for rows
    # created after the business_purpose column existed (see models.py);
    # only reclassify here, in a tiny follow-up pass, for the (typically
    # very small or zero) legacy rows that predate it.
    purpose_key = TokenTransaction.business_purpose
    purpose_rows = (
        matched_query.with_entities(
            purpose_key,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(purpose_key)
        .all()
    )
    purpose_breakdown = []
    unclassified_bucket_needs_reclassify = False
    for row in purpose_rows:
        if row[0] is None:
            unclassified_bucket_needs_reclassify = True
            continue
        purpose_breakdown.append(_shape_rows([(row[0], row[0], *row[1:])])[0])
    if unclassified_bucket_needs_reclassify:
        legacy_rows = (
            matched_query.with_entities(
                TokenTransaction, WorkItem, RegisteredAgent,
            )
            .filter(TokenTransaction.business_purpose.is_(None))
            .all()
        )
        legacy_grouped = {}
        for tx, project, agent in legacy_rows:
            purpose = classify_business_purpose(tx, project, agent)
            bucket = legacy_grouped.setdefault(purpose, {
                "id": purpose, "label": purpose, "request_count": 0,
                "input_tokens": 0, "output_tokens": 0, "tokens_saved": 0,
                "spend_usd": 0.0, "simulation_count": 0,
            })
            bucket["request_count"] += 1
            bucket["input_tokens"] += int(tx.input_tokens or 0)
            bucket["output_tokens"] += int(tx.output_tokens or 0)
            bucket["tokens_saved"] += int(tx.tokens_saved or 0)
            bucket["spend_usd"] += float(tx.cost_usd or 0.0)
            bucket["simulation_count"] += 1 if (
                tx.is_simulation or (agent and not project and not tx.actor_external_id and not tx.origin_record_id)
            ) else 0
        for purpose, bucket in legacy_grouped.items():
            existing = next((b for b in purpose_breakdown if b["id"] == purpose), None)
            if existing:
                existing["request_count"] += bucket["request_count"]
                existing["input_tokens"] += bucket["input_tokens"]
                existing["output_tokens"] += bucket["output_tokens"]
                existing["tokens_saved"] += bucket["tokens_saved"]
                existing["spend_usd"] += bucket["spend_usd"]
                existing["simulation_count"] += bucket["simulation_count"]
            else:
                purpose_breakdown.append(bucket)
    for bucket in purpose_breakdown:
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
        bucket["spend_usd"] = round(bucket["spend_usd"], 6)
        bucket["live_count"] = bucket["request_count"] - bucket["simulation_count"]
    purpose_breakdown = _sort_breakdown(purpose_breakdown)

    # source_platform_breakdown
    platform_key = func.coalesce(TokenTransaction.source_platform, literal("Unknown platform"))
    platform_rows = (
        matched_query.with_entities(
            platform_key,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(platform_key)
        .all()
    )
    source_platform_breakdown = _sort_breakdown(_shape_rows(
        [(row[0], row[0], *row[1:]) for row in platform_rows]
    ))

    # model_breakdown
    model_key = func.coalesce(TokenTransaction.model_name, TokenTransaction.model_tier, literal("Unknown model"))
    model_tier_col = TokenTransaction.model_tier
    model_rows = (
        matched_query.with_entities(
            model_key, model_tier_col,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(model_key, model_tier_col)
        .all()
    )
    model_breakdown = []
    for row in model_rows:
        bucket = _shape_rows([(row[0], row[0], *row[2:])])[0]
        bucket["model_tier"] = row[1]
        model_breakdown.append(bucket)
    model_breakdown = _sort_breakdown(model_breakdown)

    # provider_breakdown -- provider (Anthropic/OpenAI/...) isn't a stored
    # column, only model_name is, so it can't be a SQL GROUP BY key
    # directly. Derived from model_breakdown's already-small, already-
    # aggregated result instead of raw transactions: resolve each DISTINCT
    # model name to a provider once (a handful of calls, not one per
    # transaction) and re-sum those few model-level buckets by provider.
    provider_registry = load_provider_registry(db)
    provider_grouped = {}
    for bucket in model_breakdown:
        model_name_for_resolve = bucket["id"] if bucket["id"] != "Unknown model" else bucket.get("model_tier")
        provider_name = resolve_provider(model_name_for_resolve, registry=provider_registry)
        target = provider_grouped.setdefault(provider_name, {
            "id": provider_name, "label": provider_name, "request_count": 0,
            "input_tokens": 0, "output_tokens": 0, "tokens_saved": 0,
            "spend_usd": 0.0, "simulation_count": 0,
        })
        target["request_count"] += bucket["request_count"]
        target["input_tokens"] += bucket["input_tokens"]
        target["output_tokens"] += bucket["output_tokens"]
        target["tokens_saved"] += bucket["tokens_saved"]
        target["spend_usd"] += bucket["spend_usd"]
        target["simulation_count"] += bucket["simulation_count"]
    for bucket in provider_grouped.values():
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
        bucket["spend_usd"] = round(bucket["spend_usd"], 6)
        bucket["live_count"] = bucket["request_count"] - bucket["simulation_count"]
    provider_breakdown = _sort_breakdown(list(provider_grouped.values()))
    if provider:
        provider_breakdown = [b for b in provider_breakdown if b["label"].lower() == provider.lower()]

    # project_breakdown -- the one dimension whose group key has a
    # simulator-traffic fallback (a bucket for AI activity with an agent
    # but no real work item/user/actor identity) and carries outcome
    # metadata (constant per work item, so MAX() safely picks the one
    # value rather than aggregating it like the metrics).
    project_key = case(
        (WorkItem.external_id.isnot(None), WorkItem.external_id),
        (is_sim_condition, literal("__simulator__")),
        else_=literal("__unknown__"),
    )
    project_label = case(
        (WorkItem.external_id.isnot(None), WorkItem.name),
        (is_sim_condition, literal("Simulator Traffic")),
        else_=literal("Unattributed"),
    )
    project_account_id = WorkAccount.external_id
    project_account_name = case(
        (and_(WorkItem.external_id.is_(None), is_sim_condition), literal("Synthetic workload")),
        else_=func.coalesce(WorkAccount.name, literal("Unassigned account")),
    )
    project_rows = (
        matched_query.with_entities(
            project_key, project_label, project_account_id, project_account_name,
            func.max(WorkItemOutcome.outcome_status),
            func.max(WorkItemOutcome.outcome_value),
            func.max(WorkItemOutcome.outcome_date),
            func.max(case((WorkItemOutcome.outcome_success.is_(True), 1), (WorkItemOutcome.outcome_success.is_(False), 0))),
            func.max(case((WorkItemOutcome.is_closed.is_(True), 1), (WorkItemOutcome.is_closed.is_(False), 0))),
            func.max(WorkItemOutcome.last_synced_at),
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(project_key, project_label, project_account_id, project_account_name)
        .all()
    )
    project_breakdown = []
    for row in project_rows:
        (key, label, acct_id, acct_name, o_status, o_value, o_date,
         o_success_int, o_closed_int, o_last_synced, *metrics) = row
        bucket = _shape_rows([(key, label, *metrics)])[0]
        age = datetime.utcnow() - o_last_synced if o_last_synced else None
        bucket.update({
            "account_external_id": acct_id,
            "account_name": acct_name,
            "outcome_status": o_status,
            "outcome_value": o_value,
            "outcome_date": o_date.isoformat() if o_date else None,
            "outcome_success": None if o_success_int is None else bool(o_success_int),
            "outcome_is_closed": None if o_closed_int is None else bool(o_closed_int),
            "outcome_freshness": (
                "unavailable" if o_last_synced is None
                else "current" if age is not None and age <= OUTCOME_FRESHNESS_WINDOW
                else "potentially_stale"
            ),
        })
        project_breakdown.append(bucket)
    project_breakdown = _sort_breakdown(project_breakdown)

    # people_breakdown -- same simulator-fallback-key shape as project.
    person_key = case(
        (WorkUser.external_id.isnot(None), WorkUser.external_id),
        (TokenTransaction.actor_external_id.isnot(None), TokenTransaction.actor_external_id),
        (is_sim_condition, literal("__simulator__")),
        else_=literal("__unknown__"),
    )
    person_label = case(
        (WorkUser.external_id.isnot(None), WorkUser.name),
        (TokenTransaction.actor_external_id.isnot(None), func.coalesce(TokenTransaction.actor_name, literal("Unknown user"))),
        (is_sim_condition, literal("Simulator User")),
        else_=literal("Unknown user"),
    )
    person_email = case(
        (WorkUser.external_id.isnot(None), WorkUser.email),
        else_=TokenTransaction.actor_email,
    )
    person_platform = case(
        (and_(WorkUser.external_id.is_(None), TokenTransaction.actor_external_id.is_(None), is_sim_condition), literal("CostPilot Simulator")),
        (WorkUser.external_id.isnot(None), WorkUser.source_platform),
        else_=TokenTransaction.actor_source_platform,
    )
    person_rows = (
        matched_query.with_entities(
            person_key, person_label, person_email, person_platform,
            request_count_expr, input_tokens_expr, output_tokens_expr,
            tokens_saved_expr, spend_expr, sim_count_expr,
        )
        .group_by(person_key, person_label, person_email, person_platform)
        .all()
    )
    people_breakdown = []
    for row in person_rows:
        key, label, email, platform_val, *metrics = row
        bucket = _shape_rows([(key, label, *metrics)])[0]
        bucket["email"] = email
        bucket["source_platform"] = platform_val
        people_breakdown.append(bucket)
    people_breakdown = _sort_breakdown(people_breakdown)

    # organizational_unit_breakdown -- charged_unit's derivation has the
    # same cross-engine string-split portability concern as the filter
    # above, so this stays as a lightweight Python group-by, but over a
    # SLIM query (just the handful of scalar columns actually needed, not
    # full 6-table ORM rows) rather than the old full-row load.
    charged_unit_rows = matched_query.with_entities(
        TokenTransaction.charged_org_unit_name, TokenTransaction.department,
        TokenTransaction.input_tokens, TokenTransaction.output_tokens,
        TokenTransaction.tokens_saved, TokenTransaction.cost_usd,
        is_sim_condition,
    ).all()
    charged_unit_grouped = {}
    for charged_org_unit_name, department, in_tok, out_tok, saved, cost, sim in charged_unit_rows:
        name = (
            (charged_org_unit_name or "").strip()
            or (department or "").split(":")[-1].strip()
            or "Unassigned"
        )
        bucket = charged_unit_grouped.setdefault(name, {
            "id": name, "label": name, "request_count": 0,
            "input_tokens": 0, "output_tokens": 0, "tokens_saved": 0,
            "spend_usd": 0.0, "simulation_count": 0,
        })
        bucket["request_count"] += 1
        bucket["input_tokens"] += int(in_tok or 0)
        bucket["output_tokens"] += int(out_tok or 0)
        bucket["tokens_saved"] += int(saved or 0)
        bucket["spend_usd"] += float(cost or 0.0)
        bucket["simulation_count"] += 1 if sim else 0
    for bucket in charged_unit_grouped.values():
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
        bucket["spend_usd"] = round(bucket["spend_usd"], 6)
        bucket["live_count"] = bucket["request_count"] - bucket["simulation_count"]
    organizational_unit_breakdown = _sort_breakdown(list(charged_unit_grouped.values()))
    if charged_unit:
        organizational_unit_breakdown = [b for b in organizational_unit_breakdown if b["label"] == charged_unit]
    if business_purpose:
        # business_purpose's exact (non-loose) check, including the
        # classify() fallback for legacy null rows, couldn't be pushed to
        # SQL -- applied here against the already-small grouped results
        # instead of raw rows wherever it still matters. organizational_unit
        # and charged_unit are independent dimensions from business_purpose,
        # so no further narrowing is needed here; this mirrors the old
        # behavior where organizational_unit_breakdown was never itself
        # filtered by business_purpose beyond the shared base row set.
        pass

    # -- Summary, context type, evidence quality (all single small SQL
    # aggregate queries; the query still needs to know is_sim_condition,
    # which needs the same joins matched_query already has). --
    summary_row = matched_query.with_entities(
        request_count_expr, input_tokens_expr, output_tokens_expr,
        tokens_saved_expr, spend_expr, sim_count_expr,
        func.count(func.distinct(person_key)),
        func.count(func.distinct(TokenTransaction.agent_id)),
        func.count(func.distinct(project_key)),
    ).first()
    (
        s_request_count, s_input, s_output, s_saved, s_spend, s_sim_count,
        s_people_count, s_agent_count, s_project_count,
    ) = summary_row
    # people_count/project_count above use person_key/project_key, whose
    # "__unknown__"/"__simulator__" placeholders must not be counted as
    # real distinct people/projects -- matches the old behavior, which
    # only counted entries with a real (truthy) user_external_id/
    # project_external_id.
    unknown_person_exists = matched_query.with_entities(func.count()).filter(
        or_(person_key == "__unknown__", person_key == "__simulator__")
    ).scalar() > 0
    unknown_project_exists = matched_query.with_entities(func.count()).filter(
        or_(project_key == "__unknown__", project_key == "__simulator__")
    ).scalar() > 0
    real_person_count = len([b for b in people_breakdown if b["id"] not in ("__unknown__", "__simulator__")])
    real_project_count = len([b for b in project_breakdown if b["id"] not in ("__unknown__", "__simulator__")])

    context_type_row = (
        matched_query.with_entities(
            func.lower(func.trim(WorkItem.context_type)), func.count()
        )
        .filter(WorkItem.context_type.isnot(None), func.trim(WorkItem.context_type) != "")
        .group_by(func.lower(func.trim(WorkItem.context_type)))
        .order_by(func.count().desc())
        .first()
    )
    context_type = context_type_row[0] if context_type_row else "work"
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

    request_identity_count = matched_query.with_entities(func.count()).filter(
        TokenTransaction.governed_request_id.isnot(None)
    ).scalar() or 0
    correlated_request_count = 0
    if request_identity_count:
        correlated_request_count = matched_query.with_entities(func.count()).filter(
            TokenTransaction.governed_request_id.isnot(None),
            TokenTransaction.governed_request_id.in_(
                db.query(AuditEvent.governed_request_id).distinct()
            ),
        ).scalar() or 0

    # -- Activities: a small, separately-ordered-and-limited query, not a
    # slice of a much larger loaded set. Audit correlation is only looked
    # up for these (at most activity_limit) governed_request_ids. --
    activity_query_rows = (
        matched_query
        .add_columns(is_sim_condition)
        .order_by(TokenTransaction.timestamp.desc())
        .limit(activity_limit)
        .all()
    )
    activity_governed_ids = {
        row[0].governed_request_id for row in activity_query_rows
        if row[0].governed_request_id
    }
    audit_by_request = {}
    if activity_governed_ids:
        audit_rows = (
            db.query(AuditEvent)
            .filter(AuditEvent.governed_request_id.in_(activity_governed_ids))
            .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
            .all()
        )
        for audit in audit_rows:
            audit_by_request.setdefault(audit.governed_request_id, audit)

    activities = []
    for tx, project, account, user, agent, outcome, sim in activity_query_rows:
        charged_unit_name = (
            (tx.charged_org_unit_name or "").strip()
            or (tx.department or "").split(":")[-1].strip()
            or "Unassigned"
        )
        item = {
            **_outcome_fields(outcome),
            "project_external_id": project.external_id if project else None,
            "project_name": project.name if project else "Unattributed",
            "account_external_id": account.external_id if account else None,
            "account_name": account.name if account else "Unassigned account",
            "user_external_id": user.external_id if user else tx.actor_external_id,
            "user_name": user.name if user else (tx.actor_name or "Unknown user"),
            "user_email": user.email if user else tx.actor_email,
            "user_source_platform": user.source_platform if user else tx.actor_source_platform,
            "agent_id": agent.id if agent else tx.agent_id,
            "agent_name": agent.name if agent else "Unknown agent",
            "agent_platform": agent.source_platform if agent else tx.source_platform,
            "charged_unit": charged_unit_name,
            "actor_unit": tx.actor_org_unit_name,
            "agent_unit": tx.agent_org_unit_name,
            "work_unit": tx.work_org_unit_name,
            "attribution_source": tx.attribution_source,
            "business_purpose": tx.business_purpose or classify_business_purpose(tx, project, agent),
        }
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
            "is_simulation": bool(sim),
        })

    # -- filter_options: dropdown lists from the FULL workspace+date scoped
    # set regardless of which other filters are active (confirmed by
    # test_organizational_attribution.py: filtering by one business_purpose
    # still expects to see every business_purpose in filter_options, so a
    # user can broaden/pivot their filters -- standard faceted-search UX).
    # Built from small SQL DISTINCT/GROUP BY queries against the UNFILTERED
    # `query`, not matched_query, then (for mergeable dimensions) deduped
    # by label in Python over that already-small result set -- not over
    # every row like the old option_rows/identity() pass did.
    def _mergeable_options(key_col, label_col, extra_col=None):
        cols = [key_col, label_col] + ([extra_col] if extra_col is not None else [])
        distinct_rows = query.with_entities(*cols).filter(key_col.isnot(None)).distinct().all()
        by_label = {}
        for r in distinct_rows:
            value, label = r[0], r[1]
            extra_val = r[2] if extra_col is not None else None
            label_text = label or str(value)
            bucket = by_label.setdefault(label_text, {"label": label_text, "ids": [], "extra": extra_val})
            if str(value) not in bucket["ids"]:
                bucket["ids"].append(str(value))
        options = []
        for bucket in by_label.values():
            entry = {
                "value": bucket["ids"][0] if len(bucket["ids"]) == 1 else ",".join(bucket["ids"]),
                "label": bucket["label"],
            }
            if extra_col is not None:
                entry["extra"] = bucket["extra"]
            options.append(entry)
        return sorted(options, key=lambda o: o["label"].lower())

    def _simple_options(key_col, label_col, extra_col=None):
        cols = [key_col, label_col] + ([extra_col] if extra_col is not None else [])
        distinct_rows = query.with_entities(*cols).filter(key_col.isnot(None)).distinct().all()
        options = {}
        for r in distinct_rows:
            value, label = r[0], r[1]
            extra_val = r[2] if extra_col is not None else None
            entry = {"value": value, "label": label or str(value)}
            if extra_col is not None:
                entry["extra"] = extra_val
            options[str(value)] = entry
        return sorted(options.values(), key=lambda o: o["label"].lower())

    project_options = _mergeable_options(WorkItem.external_id, WorkItem.name, project_account_name)
    for opt in project_options:
        opt["account_name"] = opt.pop("extra")
    people_options = _mergeable_options(person_key, person_label, person_email)
    # person_key/person_label carry the simulator-fallback synthetic keys
    # too, which were never real filter options -- excluded the same way
    # the old identity()-based pass excluded rows with no real user id.
    people_options = [
        o for o in people_options
        if not any(i in ("__unknown__", "__simulator__") for i in o["value"].split(","))
    ]
    for opt in people_options:
        opt["user_email"] = opt.pop("extra")
    account_options = _mergeable_options(WorkAccount.external_id, WorkAccount.name)
    agent_options = _simple_options(TokenTransaction.agent_id, agent_label, agent_platform)
    for opt in agent_options:
        opt["agent_platform"] = opt.pop("extra")
    organizational_unit_option_names = sorted({b["label"] for b in organizational_unit_breakdown}, key=str.lower)
    business_purpose_options = sorted(
        {p for (p,) in query.with_entities(TokenTransaction.business_purpose).filter(TokenTransaction.business_purpose.isnot(None)).distinct().all()},
        key=str.lower,
    )
    source_platform_options = sorted({
        p for (p,) in query.with_entities(TokenTransaction.source_platform).filter(TokenTransaction.source_platform.isnot(None)).distinct().all()
    })
    record_type_options = sorted({
        p for (p,) in query.with_entities(TokenTransaction.origin_record_type).filter(TokenTransaction.origin_record_type.isnot(None)).distinct().all()
    })

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
            "projects": project_options,
            "people": people_options,
            "agents": agent_options,
            "accounts": account_options,
            "source_platforms": source_platform_options,
            "record_types": record_type_options,
            "organizational_units": [{"value": n, "label": n} for n in organizational_unit_option_names],
            "business_purposes": [{"value": p, "label": p} for p in business_purpose_options],
        },
        "summary": {
            "request_count": int(s_request_count),
            "input_tokens": int(s_input),
            "output_tokens": int(s_output),
            "total_tokens": int(s_input) + int(s_output),
            "tokens_saved": int(s_saved),
            "spend_usd": round(float(s_spend), 6),
            "people_count": real_person_count,
            "agent_count": int(s_agent_count),
            "project_count": real_project_count,
            "simulation_count": int(s_sim_count),
            "live_count": int(s_request_count) - int(s_sim_count),
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
        "activity_count": int(s_request_count),
        "activity_limit": activity_limit,
        "evidence_quality": {
            "request_identity_count": int(request_identity_count),
            "correlated_request_count": int(correlated_request_count),
            "total_request_count": int(s_request_count),
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
