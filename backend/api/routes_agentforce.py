"""Salesforce Agentforce proof-of-concept integration.

This adapter keeps Salesforce-specific record resolution at the edge while
reusing CostPilot's existing work-attribution and routing pipeline.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.routes_proxy import _get_account
from api.routes_router import RouteRequest, route_payload
from core.business_context import normalize_context_type
from database.db import get_db
from database.models import TokenTransaction, WorkAccount, WorkItem


router = APIRouter()


class AgentforceGovernRequest(BaseModel):
    record_id: str = Field(min_length=1, max_length=120)
    task_description: str = Field(min_length=1, max_length=12000)
    project_external_id: Optional[str] = Field(default=None, max_length=120)
    project_name: Optional[str] = Field(default=None, max_length=200)
    project_owner: Optional[str] = Field(default=None, max_length=200)
    project_status: str = Field(default="active", max_length=40)
    monthly_ai_budget: Optional[float] = Field(default=None, ge=0)
    department: str = Field(default="Sales", max_length=120)
    agent_name: str = Field(default="Salesforce Agentforce", max_length=200)
    requested_model: Optional[str] = Field(default=None, max_length=120)
    salesforce_user_id: Optional[str] = Field(default=None, max_length=120)
    salesforce_user_name: Optional[str] = Field(default=None, max_length=200)
    salesforce_user_email: Optional[str] = Field(default=None, max_length=320)
    project_member_role: Optional[str] = Field(default="Member", max_length=120)
    project_member_status: Optional[str] = Field(default="active", max_length=40)
    project_member_can_use_ai: Optional[bool] = True
    context_type: str = Field(default="project", max_length=40)
    context_template: str = Field(default="salesforce_project", max_length=120)
    source_record_type: str = Field(default="CostPilot_Project__c", max_length=120)
    customer_external_id: Optional[str] = Field(default=None, max_length=120)
    customer_name: Optional[str] = Field(default=None, max_length=200)


class AgentforceGovernResponse(BaseModel):
    allowed: bool
    decision: str
    reason: str
    project_id: str
    project_name: str
    selected_model: Optional[str] = None
    selected_tier: Optional[str] = None
    estimated_cost_usd: float = 0.0
    project_budget_remaining_usd: Optional[float] = None
    tracking_id: Optional[str] = None


def _project_identifier(body: AgentforceGovernRequest) -> str:
    value = (body.project_external_id or "").strip()
    return value or f"SF-{body.record_id.strip()}"


def _normalized_project_status(value: str) -> str:
    normalized = (value or "active").strip().lower().replace(" ", "_")
    aliases = {
        "in_progress": "active",
        "open": "active",
        "onhold": "on_hold",
        "closed": "completed",
        "complete": "completed",
        "canceled": "cancelled",
    }
    return aliases.get(normalized, normalized)


def _resolve_or_create_project(
    db: Session,
    workspace_id: str,
    body: AgentforceGovernRequest,
) -> WorkItem:
    external_id = _project_identifier(body)
    project = db.query(WorkItem).filter(WorkItem.external_id == external_id).first()

    if project and project.workspace_id not in (None, workspace_id):
        raise HTTPException(
            status_code=409,
            detail="That project identifier belongs to a different CostPilot workspace.",
        )

    account = _resolve_customer(db, workspace_id, body)
    status = _normalized_project_status(body.project_status)
    try:
        context_type = normalize_context_type(
            body.context_type,
            template_key=body.context_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    valid_statuses = {"active", "on_hold", "completed", "cancelled", "archived"}
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"project_status must be one of: {', '.join(sorted(valid_statuses))}",
        )

    if project:
        if body.project_name:
            project.name = body.project_name.strip()
        if body.project_owner:
            project.owner = body.project_owner.strip()
        if body.monthly_ai_budget is not None:
            project.monthly_ai_budget = body.monthly_ai_budget
        project.department = body.department.strip() or project.department
        project.status = status
        project.source_platform = "Salesforce"
        project.workspace_id = workspace_id
        project.context_type = context_type
        project.context_template = body.context_template
        project.source_record_type = body.source_record_type
        project.source_record_id = body.record_id.strip()
        if account:
            project.account_id = account.id
    else:
        project = WorkItem(
            external_id=external_id,
            name=(body.project_name or f"Salesforce work {body.record_id}").strip(),
            owner=(body.project_owner or "").strip() or None,
            department=body.department.strip() or "Sales",
            status=status,
            monthly_ai_budget=body.monthly_ai_budget,
            cost_treatment="unspecified",
            source_platform="Salesforce",
            workspace_id=workspace_id,
            context_type=context_type,
            context_template=body.context_template,
            source_record_type=body.source_record_type,
            source_record_id=body.record_id.strip(),
            account_id=account.id if account else None,
        )
        db.add(project)

    db.commit()
    db.refresh(project)
    return project


def _resolve_customer(
    db: Session,
    workspace_id: str,
    body: AgentforceGovernRequest,
) -> Optional[WorkAccount]:
    external_id = (body.customer_external_id or "").strip()
    name = (body.customer_name or "").strip()
    if not external_id and not name:
        return None
    external_id = external_id or f"SF-CUSTOMER-{name.lower().replace(' ', '-')}"
    account = db.query(WorkAccount).filter(WorkAccount.external_id == external_id).first()
    if account and account.workspace_id not in (None, workspace_id):
        raise HTTPException(
            status_code=409,
            detail="That customer identifier belongs to a different CostPilot workspace.",
        )
    if not account:
        account = WorkAccount(
            external_id=external_id,
            name=name or external_id,
            department=body.department.strip() or None,
            status="active",
            workspace_id=workspace_id,
        )
        db.add(account)
        db.flush()
    else:
        account.name = name or account.name
        account.workspace_id = workspace_id
    return account


@router.post(
    "/{workspace_id}/govern",
    response_model=AgentforceGovernResponse,
    summary="Govern and attribute Salesforce Agentforce work",
)
def govern_agentforce_work(
    workspace_id: str,
    body: AgentforceGovernRequest,
    x_costpilot_key: str = Header(default="", alias="X-CostPilot-Key"),
    db: Session = Depends(get_db),
):
    """Resolve the Salesforce project, run CostPilot, and return agent-ready output."""
    _get_account(workspace_id, x_costpilot_key, db)
    project = _resolve_or_create_project(db, workspace_id, body)

    if project.status != "active":
        return AgentforceGovernResponse(
            allowed=False,
            decision="BLOCKED",
            reason=f"Project {project.name} is {project.status.replace('_', ' ')}.",
            project_id=project.external_id,
            project_name=project.name,
            project_budget_remaining_usd=_project_budget_remaining(db, project),
        )

    department = body.department.strip() or project.department or "Sales"
    workspace_department = f"{workspace_id}:{department}"

    try:
        result = route_payload(
            RouteRequest(
                text=body.task_description,
                department=workspace_department,
                auto_prune=True,
                agent_name=body.agent_name.strip() or "Salesforce Agentforce",
                source_platform="Salesforce Agentforce",
                work_item_id=project.external_id,
                actor_external_id=body.salesforce_user_id,
                actor_name=body.salesforce_user_name,
                actor_email=body.salesforce_user_email,
                actor_source_platform="Salesforce",
                actor_workspace_id=workspace_id,
                actor_role=body.project_member_role,
                actor_status=body.project_member_status,
                actor_can_use_ai=body.project_member_can_use_ai,
                enforce_project_membership=bool(body.salesforce_user_id),
            ),
            db,
        )
    except HTTPException as exc:
        if exc.status_code not in (403, 409, 451):
            raise
        detail = exc.detail if isinstance(exc.detail, dict) else {"reason": str(exc.detail)}
        return AgentforceGovernResponse(
            allowed=False,
            decision=str(detail.get("error") or "BLOCKED"),
            reason=str(detail.get("reason") or exc.detail),
            project_id=project.external_id,
            project_name=project.name,
            project_budget_remaining_usd=_project_budget_remaining(db, project),
        )

    transaction = (
        db.query(TokenTransaction)
        .filter(TokenTransaction.work_item_id == project.id)
        .order_by(TokenTransaction.id.desc())
        .first()
    )
    return AgentforceGovernResponse(
        allowed=True,
        decision=result.routing_decision,
        reason=result.routing_reason,
        project_id=project.external_id,
        project_name=project.name,
        selected_model=result.model_name,
        selected_tier=result.model_tier,
        estimated_cost_usd=result.cost_usd,
        project_budget_remaining_usd=_project_budget_remaining(db, project),
        tracking_id=f"CP-TX-{transaction.id}" if transaction else None,
    )


def _project_budget_remaining(db: Session, project: WorkItem) -> Optional[float]:
    if project.monthly_ai_budget is None:
        return None
    spend = sum(
        float(row.cost_usd or 0.0)
        for row in db.query(TokenTransaction)
        .filter(TokenTransaction.work_item_id == project.id)
        .all()
    )
    return round(float(project.monthly_ai_budget) - spend, 6)
