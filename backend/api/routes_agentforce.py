"""Salesforce Agentforce proof-of-concept integration.

This adapter keeps Salesforce-specific record resolution at the edge while
reusing CostPilot's existing work-attribution and routing pipeline.
"""

import json
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.routes_proxy import _get_account
from api.routes_router import RouteRequest, route_payload
from api.routes_work_items import resolve_account_through_merge
from core.business_context import normalize_context_type
from core.model_client import get_mode_info
from database.db import get_db
from database.models import (
    IntegrationConnection,
    TokenTransaction,
    WorkAccount,
    WorkItem,
    WorkItemSourceLink,
)


router = APIRouter()
logger = logging.getLogger(__name__)


class AgentforceGovernRequest(BaseModel):
    record_id: str = Field(min_length=1, max_length=120)
    task_description: str = Field(min_length=1, max_length=12000)
    project_external_id: Optional[str] = Field(default=None, max_length=120)
    project_name: Optional[str] = Field(default=None, max_length=200)
    source_record_name: Optional[str] = Field(default=None, max_length=200)
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
    source_type: Optional[str] = Field(default=None, max_length=120)
    source_system: str = Field(default="Salesforce", max_length=120)
    source_record_type: str = Field(default="CostPilot_Project__c", max_length=120)
    customer_external_id: Optional[str] = Field(default=None, max_length=120)
    customer_name: Optional[str] = Field(default=None, max_length=200)
    simulation_mode: bool = False


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
    ai_response: Optional[str] = None
    execution_mode: Optional[str] = None


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
    *,
    force_canonical_parent: bool = False,
) -> WorkItem:
    source_platform = body.source_system.strip() or "Salesforce"
    source_record_id = body.record_id.strip()
    source_record_type = (body.source_type or body.source_record_type).strip()
    account = _resolve_customer(db, workspace_id, body)

    source_link = (
        db.query(WorkItemSourceLink)
        .filter(
            WorkItemSourceLink.workspace_id == workspace_id,
            WorkItemSourceLink.source_platform == source_platform,
            WorkItemSourceLink.source_record_id == source_record_id,
        )
        .first()
    )
    project = (
        db.query(WorkItem).filter(WorkItem.id == source_link.work_item_id).first()
        if source_link else None
    )
    if force_canonical_parent:
        project = None

    is_explicit_project = source_record_type.lower() == "costpilot_project__c"
    # Opportunity now has full deterministic per-record identity via the
    # bulk importer (routes_connections.py:_import_salesforce_work_items,
    # external_id f"SF-{TYPE}-{record_id}"). Routing live AI activity for
    # an Opportunity into the account-level bucket instead of its own
    # WorkItem is exactly the "reactive fallback merges unrelated records"
    # pattern the bulk importer's claimed_work_item_ids self-heal exists to
    # clean up after the fact -- better to never create the mis-shared link
    # in the first place. Case is deliberately excluded here: it has its
    # own tested, intentional account-rollup contract (see
    # test_business_context.py::test_salesforce_case_with_customer_is_a_stable_account_rollup_with_origin_activity)
    # where the origin Case stays visible via related_record_activity
    # rather than getting a standalone WorkItem -- that's a different,
    # deliberate design, not the same bug.
    has_deterministic_identity = source_record_type == "Opportunity"
    grouped_by_account = False
    # Normal requests retain the legacy account-grouping fallback. When an
    # onboarding-approved relationship resolved a canonical parent, use that
    # parent's stable external ID instead of selecting an arbitrary older work
    # item that happens to share the account.
    if (
        not project
        and account
        and not is_explicit_project
        and not has_deterministic_identity
        and not force_canonical_parent
    ):
        project = (
            db.query(WorkItem)
            .filter(
                WorkItem.workspace_id == workspace_id,
                WorkItem.account_id == account.id,
                WorkItem.status != "archived",
            )
            .order_by(WorkItem.created_at)
            .first()
        )
        if not project:
            project = (
                db.query(WorkItem)
                .filter(
                    WorkItem.workspace_id == workspace_id,
                    WorkItem.external_id.in_(
                        (account.external_id, f"SF-{account.external_id}")
                    ),
                )
                .first()
            )
        grouped_by_account = project is not None

    external_id = _project_identifier(body)
    if has_deterministic_identity:
        # Matches the shape _import_salesforce_work_items uses so a record
        # created reactively here and later bulk-imported resolve to the
        # same WorkItem via this external_id too, not just the source link.
        external_id = f"SF-{source_record_type.upper()}-{source_record_id}"
    elif account and not is_explicit_project:
        external_id = f"SF-ACCOUNT-{account.external_id}"
    if not project:
        project = db.query(WorkItem).filter(WorkItem.external_id == external_id).first()
    if not project and body.project_external_id:
        legacy_external_id = f"SF-{body.record_id.strip()}"
        project = (
            db.query(WorkItem)
            .filter(WorkItem.external_id == legacy_external_id)
            .first()
        )
        if project:
            project.external_id = external_id

    if project and project.workspace_id not in (None, workspace_id):
        raise HTTPException(
            status_code=409,
            detail="That project identifier belongs to a different CostPilot workspace.",
        )

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

    # A non-project Salesforce record carrying an Account relationship is
    # stored as an Account rollup. Keep the canonical row's identity stable;
    # the originating Case, Contact, or Opportunity remains available through
    # its source link and per-origin transaction fields. Opportunity/Case
    # get their own deterministic WorkItem instead (see
    # has_deterministic_identity above), not the account rollup treatment.
    is_account_rollup = bool(account and not is_explicit_project and not has_deterministic_identity)
    if is_account_rollup:
        context_type = "account"

    if project:
        if is_account_rollup:
            project.name = account.name
            project.source_record_type = "Account"
            project.source_record_id = account.external_id
        elif body.project_name and not grouped_by_account:
            project.name = body.project_name.strip()
        if body.project_owner:
            project.owner = body.project_owner.strip()
        if body.monthly_ai_budget is not None:
            project.monthly_ai_budget = body.monthly_ai_budget
        project.department = body.department.strip() or project.department
        project.status = status
        project.source_platform = source_platform
        project.workspace_id = workspace_id
        project.context_type = context_type
        project.context_template = body.context_template
        if not project.source_record_id:
            project.source_record_type = source_record_type
            project.source_record_id = source_record_id
        if account:
            project.account_id = account.id
    else:
        project = WorkItem(
            external_id=external_id,
            name=(
                body.customer_name
                if is_account_rollup and body.customer_name
                else body.project_name or f"Salesforce work {body.record_id}"
            ).strip(),
            owner=(body.project_owner or "").strip() or None,
            department=body.department.strip() or "Sales",
            status=status,
            monthly_ai_budget=body.monthly_ai_budget,
            cost_treatment="unspecified",
            source_platform=source_platform,
            workspace_id=workspace_id,
            context_type=context_type,
            context_template=body.context_template,
            source_record_type="Account" if is_account_rollup else source_record_type,
            source_record_id=account.external_id if is_account_rollup else source_record_id,
            account_id=account.id if account else None,
        )
        db.add(project)

    db.flush()
    if not source_link:
        source_link = WorkItemSourceLink(
            work_item_id=project.id,
            workspace_id=workspace_id,
            source_platform=source_platform,
            source_record_type=source_record_type,
            source_record_id=source_record_id,
            source_record_name=body.source_record_name or body.project_name,
            account_external_id=body.customer_external_id,
            is_primary=not bool(project.source_links),
        )
        db.add(source_link)
    else:
        if source_link.work_item_id != project.id:
            source_link.work_item_id = project.id
        source_link.source_record_type = source_record_type
        source_link.source_record_name = (
            body.source_record_name or body.project_name or source_link.source_record_name
        )
        source_link.account_external_id = body.customer_external_id or source_link.account_external_id
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
    account = resolve_account_through_merge(db, account)
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


def _approved_relationship(mapping: dict, source_type: str) -> Optional[dict]:
    """Return the approved relationship for a source object, case-insensitively."""
    normalized = (source_type or "").strip().lower()
    for child in mapping.get("children") or []:
        if str(child.get("child_object") or "").strip().lower() == normalized:
            return child
    return None


async def _apply_approved_relationship_mapping(
    db: Session,
    workspace_id: str,
    body: AgentforceGovernRequest,
) -> bool:
    """Resolve an originating Salesforce record to its approved reporting parent.

    The source record remains on the request for audit and drill-down. Only the
    canonical work identity used for attribution is changed.
    """
    if (body.source_system or "").strip().lower() != "salesforce":
        return False

    source_type = (body.source_type or body.source_record_type or "").strip()
    if not source_type:
        return False

    connection = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.status.in_(("connected", "active")),
        )
        .order_by(
            IntegrationConnection.last_success_at.desc(),
            IntegrationConnection.updated_at.desc(),
        )
        .first()
    )
    if not connection or not connection.mapping_json:
        return False

    try:
        mapping = json.loads(connection.mapping_json)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid Salesforce relationship mapping for workspace %s", workspace_id)
        return False

    parent_object = str(mapping.get("parent_object") or "").strip()
    if not parent_object:
        return False

    # Preserve what the user actually opened even when its cost rolls upward.
    body.source_record_name = body.source_record_name or body.project_name

    if source_type.lower() == parent_object.lower():
        body.project_external_id = body.record_id
        body.context_type = "account" if parent_object.lower() == "account" else "project"
        if parent_object.lower() == "account":
            body.customer_external_id = body.record_id
            body.customer_name = body.project_name or body.customer_name
        return True

    relationship = _approved_relationship(mapping, source_type)
    if not relationship:
        return False
    behavior = str(relationship.get("behavior") or "separate").strip().lower()
    if behavior not in {"track_and_rollup", "rollup_only"}:
        return False

    parent_field = str(relationship.get("parent_field") or "").strip()
    if not parent_field:
        return False

    # Metadata was approved during onboarding. Resolve only the configured field,
    # never an arbitrary field supplied by the AI request.
    from api.routes_connections import _salesforce_get

    try:
        child = await _salesforce_get(
            connection,
            "sobjects/{}/{}?fields={}".format(
                quote(source_type, safe=""),
                quote(body.record_id.strip(), safe=""),
                quote(parent_field, safe=""),
            ),
        )
        parent_id = child.get(parent_field)
        if isinstance(parent_id, dict):
            parent_id = parent_id.get("Id") or parent_id.get("id")
        parent_id = str(parent_id or "").strip()
        if not parent_id:
            return False

        describe = await _salesforce_get(
            connection,
            f"sobjects/{quote(parent_object, safe='')}/describe",
        )
        name_fields = [
            str(field.get("name"))
            for field in describe.get("fields", [])
            if field.get("nameField") and field.get("name")
        ]
        name_field = name_fields[0] if name_fields else "Name"
        parent = await _salesforce_get(
            connection,
            "sobjects/{}/{}?fields={}".format(
                quote(parent_object, safe=""),
                quote(parent_id, safe=""),
                quote(name_field, safe=""),
            ),
        )
        parent_name = str(parent.get(name_field) or parent_id).strip()
    except HTTPException as exc:
        # Governance must continue even if Salesforce metadata is briefly
        # unavailable. The call remains attributed to its exact source record.
        logger.warning(
            "Salesforce parent resolution failed for workspace %s (%s): %s",
            workspace_id,
            source_type,
            exc.detail,
        )
        return False

    body.project_external_id = parent_id
    body.project_name = parent_name
    body.context_type = "account" if parent_object.lower() == "account" else "project"
    if parent_object.lower() == "account":
        body.customer_external_id = parent_id
        body.customer_name = parent_name
    return True


@router.post(
    "/{workspace_id}/govern",
    response_model=AgentforceGovernResponse,
    summary="Govern and attribute Salesforce Agentforce work",
)
async def govern_agentforce_work(
    workspace_id: str,
    body: AgentforceGovernRequest,
    x_costpilot_key: str = Header(default="", alias="X-CostPilot-Key"),
    db: Session = Depends(get_db),
):
    """Resolve the Salesforce project, run CostPilot, and return agent-ready output."""
    _get_account(workspace_id, x_costpilot_key, db)
    canonical_parent = await _apply_approved_relationship_mapping(db, workspace_id, body)
    project = _resolve_or_create_project(
        db,
        workspace_id,
        body,
        force_canonical_parent=canonical_parent,
    )

    if project.status != "active":
        return AgentforceGovernResponse(
            allowed=False,
            decision="BLOCKED",
            reason=f"Project {project.name} is {project.status.replace('_', ' ')}.",
            project_id=project.external_id,
            project_name=project.name,
            project_budget_remaining_usd=_project_budget_remaining(db, project),
        )

    mode_info = get_mode_info()
    if mode_info["mode"] != "live" and not body.simulation_mode:
        raise HTTPException(
            status_code=503,
            detail=(
                "Agentforce execution requires CostPilot live model mode. "
                "Simulation is disabled for Salesforce AI responses."
            ),
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
                origin_record_id=body.record_id.strip(),
                origin_record_type=(body.source_type or body.source_record_type).strip(),
                origin_record_name=(body.source_record_name or body.project_name or "").strip() or None,
                actor_external_id=body.salesforce_user_id,
                actor_name=body.salesforce_user_name,
                actor_email=body.salesforce_user_email,
                actor_source_platform="Salesforce",
                actor_workspace_id=workspace_id,
                actor_role=body.project_member_role,
                actor_status=body.project_member_status,
                actor_can_use_ai=body.project_member_can_use_ai,
                enforce_project_membership=bool(body.salesforce_user_id),
                synthetic_simulation=body.simulation_mode,
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
        ai_response=result.simulated_response,
        execution_mode=result.model_mode,
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
