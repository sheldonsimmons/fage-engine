"""
api/routes_budget.py — Budget Allocator API routes  [Step 4]

GET  /api/budget                     — all department budgets
GET  /api/budget/{department}        — single department detail
POST /api/budget/{department}/cap    — update monthly cap
POST /api/budget/{department}/override  — grant supervisor override
POST /api/budget/{department}/revoke    — revoke override
POST /api/budget/{department}/reset     — reset spend to zero (new month)
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from core.budget import (
    get_all_budgets, get_budget, set_cap,
    grant_override, revoke_override, reset_period, set_throttle_tier,
    set_raw_logging, archive_department,
)
from core.auditor import write_audit_event
from core.auth import check_membership

router = APIRouter()


def _check_budget_permission(db: Session, authorization: Optional[str], workspace_id: Optional[str]):
    """
    Permissioned Actions slice 1: soft-mode-gated permission check (see
    core/auth.py's AUTH_ENFORCEMENT_ENABLED docstring) -- a no-op today,
    real once turned on. Mirrors routes_agentlake.py's own
    _check_agent_permission() pattern exactly, since these routes key by
    `department`, not `{workspace_id}` in the path, so require_membership()'s
    Depends shape doesn't fit here without adding a new required query
    param that would break existing callers.
    """
    return check_membership(db, authorization, workspace_id or "default", "manage_budgets")


class BudgetStatus(BaseModel):
    department:                  str
    monthly_cap_usd:             float
    current_spend_usd:           float
    remaining_usd:               float
    used_pct:                    float
    throttled:                   bool
    override_granted:            bool
    period_start:                str
    state:                       str   # healthy | warning | throttled
    throttle_tier:               int
    throttle_tier_name:          str
    raw_payload_logging_enabled: bool  = False
    raw_retention_days:          int   = 30
    archived:                    bool  = False


class SetCapRequest(BaseModel):
    new_cap_usd: float


class SetThrottleTierRequest(BaseModel):
    tier: int  # 1=Scout, 2=Analyst, 3=Advisor, 4=Strategist

class SetRawLoggingRequest(BaseModel):
    enabled:         bool
    retention_days:  int = 30  # 30 | 90 | 180 | 365 | 0=indefinite


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[BudgetStatus])
def list_budgets(workspace_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Return real-time budget status for all departments in one workspace."""
    return get_all_budgets(db, workspace_id)


@router.get("/{department}", response_model=BudgetStatus)
def dept_budget(department: str, db: Session = Depends(get_db)):
    """Return budget status for a single department."""
    result = get_budget(db, department)
    if not result:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found.")
    return result


@router.post("/{department}/cap", response_model=BudgetStatus)
def update_cap(
    department: str, body: SetCapRequest, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Supervisor action: update a department's monthly spending cap."""
    if body.new_cap_usd < 0:
        raise HTTPException(status_code=400, detail="Cap cannot be negative.")
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = set_cap(db, department, body.new_cap_usd)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_CAP_SET",
            routing_reason=f"Monthly cap set to ${body.new_cap_usd:,.2f}",
            prompt_payload="", model_tier=None,
            decision_outcome=f"Monthly cap updated to ${body.new_cap_usd:,.2f}",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/override", response_model=BudgetStatus)
def override_throttle(
    department: str, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Supervisor action: grant a throttle override so flagship models can run again."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = grant_override(db, department)
        write_audit_event(
            db=db,
            event_type="BUDGET",
            department=department,
            routing_decision="BUDGET_OVERRIDE",
            routing_reason="Human supervisor granted a department budget throttle override",
            prompt_payload="",
            model_tier=None,
            decision_outcome="Budget throttle override granted",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/revoke", response_model=BudgetStatus)
def revoke_throttle_override(
    department: str, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Supervisor action: revoke a previously granted override."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = revoke_override(db, department)
        write_audit_event(
            db=db,
            event_type="BUDGET",
            department=department,
            routing_decision="BUDGET_OVERRIDE_REVOKED",
            routing_reason="Human supervisor revoked the department budget throttle override",
            prompt_payload="",
            model_tier=None,
            decision_outcome="Budget throttle override revoked",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/reset", response_model=BudgetStatus)
def reset_month(
    department: str, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Reset a department's spend to zero — simulates the start of a new billing period."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = reset_period(db, department)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_PERIOD_RESET",
            routing_reason="Supervisor reset department spend to zero for a new billing period",
            prompt_payload="", model_tier=None,
            decision_outcome="Spend reset to $0.00",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{department}/archive", response_model=BudgetStatus)
def archive_budget_department(
    department: str, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Soft-hide a stale department from default budget views."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = archive_department(db, department, True)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_DEPARTMENT_ARCHIVED",
            routing_reason="Supervisor archived this department from default budget views",
            prompt_payload="", model_tier=None,
            decision_outcome="Department archived",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{department}/restore", response_model=BudgetStatus)
def restore_budget_department(
    department: str, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Restore a previously hidden department to default budget views."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = archive_department(db, department, False)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_DEPARTMENT_RESTORED",
            routing_reason="Supervisor restored this department to default budget views",
            prompt_payload="", model_tier=None,
            decision_outcome="Department restored",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{department}/throttle-tier", response_model=BudgetStatus)
def update_throttle_tier(
    department: str, body: SetThrottleTierRequest, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Set the model tier ceiling a department is capped to when throttled."""
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = set_throttle_tier(db, department, body.tier)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_THROTTLE_TIER_SET",
            routing_reason=f"Throttle ceiling tier set to {body.tier}",
            prompt_payload="", model_tier=None,
            decision_outcome=f"Throttle tier ceiling set to {body.tier}",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        status_code = 400 if "must be" in str(e) else 404
        raise HTTPException(status_code=status_code, detail=str(e))


@router.patch("/{department}/raw-logging", response_model=BudgetStatus)
def update_raw_logging(
    department: str, body: SetRawLoggingRequest, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    """Toggle raw payload logging for a department and set the retention period."""
    if body.retention_days not in (0, 30, 90, 180, 365):
        raise HTTPException(status_code=422, detail="retention_days must be 0 (indefinite), 30, 90, 180, or 365.")
    ctx = _check_budget_permission(db, authorization, workspace_id)
    try:
        result = set_raw_logging(db, department, body.enabled, body.retention_days)
        write_audit_event(
            db=db, event_type="GOVERNANCE", department=department,
            routing_decision="BUDGET_RAW_LOGGING_SET",
            routing_reason=f"Raw payload logging {'enabled' if body.enabled else 'disabled'} "
                           f"(retention: {body.retention_days} days)",
            prompt_payload="", model_tier=None,
            decision_outcome=f"Raw logging {'on' if body.enabled else 'off'}, "
                             f"retention {body.retention_days}d",
            user_id=ctx.user.id if ctx else None,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
