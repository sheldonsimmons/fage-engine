"""
api/routes_budget.py — Budget Allocator API routes  [Step 4]

GET  /api/budget                     — all department budgets
GET  /api/budget/{department}        — single department detail
POST /api/budget/{department}/cap    — update monthly cap
POST /api/budget/{department}/override  — grant supervisor override
POST /api/budget/{department}/revoke    — revoke override
POST /api/budget/{department}/reset     — reset spend to zero (new month)
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from core.budget import (
    get_all_budgets, get_budget, set_cap,
    grant_override, revoke_override, reset_period, set_throttle_tier,
)

router = APIRouter()


class BudgetStatus(BaseModel):
    department:         str
    monthly_cap_usd:    float
    current_spend_usd:  float
    remaining_usd:      float
    used_pct:           float
    throttled:          bool
    override_granted:   bool
    period_start:       str
    state:              str   # healthy | warning | throttled
    throttle_tier:      int
    throttle_tier_name: str


class SetCapRequest(BaseModel):
    new_cap_usd: float


class SetThrottleTierRequest(BaseModel):
    tier: int  # 1=Scout, 2=Analyst, 3=Advisor, 4=Strategist


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[BudgetStatus])
def list_budgets(db: Session = Depends(get_db)):
    """Return real-time budget status for all departments."""
    return get_all_budgets(db)


@router.get("/{department}", response_model=BudgetStatus)
def dept_budget(department: str, db: Session = Depends(get_db)):
    """Return budget status for a single department."""
    result = get_budget(db, department)
    if not result:
        raise HTTPException(status_code=404, detail=f"Department '{department}' not found.")
    return result


@router.post("/{department}/cap", response_model=BudgetStatus)
def update_cap(department: str, body: SetCapRequest, db: Session = Depends(get_db)):
    """Supervisor action: update a department's monthly spending cap."""
    if body.new_cap_usd < 0:
        raise HTTPException(status_code=400, detail="Cap cannot be negative.")
    try:
        return set_cap(db, department, body.new_cap_usd)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/override", response_model=BudgetStatus)
def override_throttle(department: str, db: Session = Depends(get_db)):
    """Supervisor action: grant a throttle override so flagship models can run again."""
    try:
        return grant_override(db, department)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/revoke", response_model=BudgetStatus)
def revoke_throttle_override(department: str, db: Session = Depends(get_db)):
    """Supervisor action: revoke a previously granted override."""
    try:
        return revoke_override(db, department)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{department}/reset", response_model=BudgetStatus)
def reset_month(department: str, db: Session = Depends(get_db)):
    """Reset a department's spend to zero — simulates the start of a new billing period."""
    try:
        return reset_period(db, department)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{department}/throttle-tier", response_model=BudgetStatus)
def update_throttle_tier(department: str, body: SetThrottleTierRequest, db: Session = Depends(get_db)):
    """Set the minimum model tier a department is allowed to use when throttled."""
    try:
        return set_throttle_tier(db, department, body.tier)
    except ValueError as e:
        status_code = 400 if "must be" in str(e) else 404
        raise HTTPException(status_code=status_code, detail=str(e))
