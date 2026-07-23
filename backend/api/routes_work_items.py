"""Work Attribution API — accounts and projects/matters/engagements."""

import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction, WorkAccount, WorkItem


router = APIRouter()

VALID_STATUSES = {"active", "completed", "archived"}
VALID_COST_TREATMENTS = {
    "unspecified",
    "overhead",
    "internal_allocation",
    "fixed_fee",
    "recoverable",
    "nonbillable",
    "review_required",
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
    cost_treatment: str = "unspecified"
    source_platform: Optional[str] = Field(default="CostPilot", max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)


class WorkItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    external_id: Optional[str] = Field(default=None, min_length=1, max_length=120)
    account_id: Optional[int] = None
    owner: Optional[str] = Field(default=None, max_length=200)
    department: Optional[str] = Field(default=None, max_length=120)
    status: Optional[str] = None
    monthly_ai_budget: Optional[float] = Field(default=None, ge=0)
    cost_treatment: Optional[str] = None
    source_platform: Optional[str] = Field(default=None, max_length=120)


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
    request_count = 0
    if include_stats:
        request_count, spend_usd = (
            db.query(
                func.count(TokenTransaction.id),
                func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
            )
            .filter(TokenTransaction.work_item_id == item.id)
            .one()
        )
    budget = item.monthly_ai_budget
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
        "cost_treatment": item.cost_treatment,
        "source_platform": item.source_platform,
        "workspace_id": item.workspace_id,
        "request_count": int(request_count or 0),
        "spend_usd": round(float(spend_usd or 0.0), 6),
        "budget_remaining_usd": (
            round(float(budget) - float(spend_usd or 0.0), 6)
            if budget is not None
            else None
        ),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


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


@router.post("", status_code=201)
def create_work_item(body: WorkItemIn, db: Session = Depends(get_db)):
    _validate_status(body.status)
    _validate_cost_treatment(body.cost_treatment)
    external_id = _clean_external_id(body.external_id, "PROJECT")
    if db.query(WorkItem).filter(WorkItem.external_id == external_id).first():
        raise HTTPException(status_code=409, detail="A work item with that external_id already exists")
    if body.account_id and not db.query(WorkAccount).filter(WorkAccount.id == body.account_id).first():
        raise HTTPException(status_code=404, detail="Account not found")
    item = WorkItem(
        **body.model_dump(exclude={"external_id"}),
        external_id=external_id,
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
