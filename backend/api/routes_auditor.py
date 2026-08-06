"""
api/routes_auditor.py — AI Decision Auditor API routes  [Step 6]

GET  /api/audit          — paginated list of audit events (newest first)
GET  /api/audit/{id}     — full detail for a single audit event
GET  /api/audit/export   — download the raw JSONL audit file
GET  /api/audit/review-status — blocked-event acknowledgement status
POST /api/audit/acknowledge-blocked — mark blocked events reviewed
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import AuditEvent, AuditReviewState
from core.workspace_scope import workspace_filter
from core.auditor import get_audit_events, get_audit_event, export_jsonl_path
import os

router = APIRouter()


class AuditEventSummary(BaseModel):
    id:               int
    event_type:       str
    department:       str
    source_department: Optional[str] = None
    agent_id:         Optional[int] = None
    agent_name:       Optional[str] = None
    display_agent_name: Optional[str] = None
    display_department: Optional[str] = None
    source_platform:  Optional[str] = None
    model_tier:       Optional[str]
    risk_level:       str
    decision_outcome: Optional[str]
    cost_usd:         Optional[float] = None
    raw_tokens:       Optional[int] = None
    clean_tokens:     Optional[int] = None
    tokens_saved:     Optional[int] = 0
    compression_pct:  Optional[float] = None
    usage_source:     Optional[str] = None
    has_raw_payload:  Optional[bool] = False
    budget_controlled: Optional[bool] = False
    is_simulation:    Optional[bool] = False
    matched_keywords: Optional[List[str]] = Field(default_factory=list)
    timestamp:        Optional[str]


class AuditEventDetail(AuditEventSummary):
    rationale:        Optional[str]
    prompt_payload:   Optional[str]
    raw_payload:      Optional[str] = None
    raw_logged_at:    Optional[str] = None
    context_snapshot: Optional[str]


class BlockedReviewRequest(BaseModel):
    reviewer: Optional[str] = Field(default=None, max_length=200)
    through_event_id: Optional[int] = Field(default=None, ge=0)
    workspace_id: Optional[str] = Field(default=None, max_length=200)


class BlockedReviewStatus(BaseModel):
    scope_key: str
    blocked_total: int
    unreviewed_blocked_count: int
    latest_blocked_event_id: int
    reviewed_through_id: int
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None


def _review_scope(workspace_id: Optional[str]) -> str:
    value = (workspace_id or "").strip()
    return f"workspace:{value}" if value else "global"


def _blocked_query(db: Session, workspace_id: Optional[str] = None):
    query = db.query(AuditEvent).filter(AuditEvent.decision_outcome.ilike("%blocked%"))
    if workspace_id:
        query = query.filter(workspace_filter(AuditEvent, workspace_id))
    return query


def _blocked_review_status(db: Session, workspace_id: Optional[str] = None) -> dict:
    scope_key = _review_scope(workspace_id)
    state = db.query(AuditReviewState).filter_by(scope_key=scope_key).first()
    reviewed_through_id = int(state.reviewed_through_id or 0) if state else 0
    blocked_query = _blocked_query(db, workspace_id)
    blocked_total = blocked_query.count()
    latest_blocked_event_id = int(
        blocked_query.with_entities(func.max(AuditEvent.id)).scalar() or 0
    )
    unreviewed_blocked_count = blocked_query.filter(
        AuditEvent.id > reviewed_through_id
    ).count()
    return {
        "scope_key": scope_key,
        "blocked_total": blocked_total,
        "unreviewed_blocked_count": unreviewed_blocked_count,
        "latest_blocked_event_id": latest_blocked_event_id,
        "reviewed_through_id": reviewed_through_id,
        "reviewer": state.reviewer if state else None,
        "reviewed_at": state.reviewed_at.isoformat() if state and state.reviewed_at else None,
    }


@router.get("/review-status", response_model=BlockedReviewStatus)
def get_blocked_review_status(
    workspace_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return historical and still-unreviewed blocked-request counts."""
    return _blocked_review_status(db, workspace_id)


@router.post("/acknowledge-blocked", response_model=BlockedReviewStatus)
def acknowledge_blocked_events(
    payload: BlockedReviewRequest,
    db: Session = Depends(get_db),
):
    """Acknowledge blocked events without modifying or deleting audit records."""
    scope_key = _review_scope(payload.workspace_id)
    blocked_query = _blocked_query(db, payload.workspace_id)
    latest_blocked_event_id = int(
        blocked_query.with_entities(func.max(AuditEvent.id)).scalar() or 0
    )
    requested_id = payload.through_event_id
    reviewed_through_id = latest_blocked_event_id if requested_id is None else min(
        int(requested_id), latest_blocked_event_id
    )
    state = db.query(AuditReviewState).filter_by(scope_key=scope_key).first()
    if not state:
        state = AuditReviewState(scope_key=scope_key)
        db.add(state)
    state.reviewed_through_id = max(
        int(state.reviewed_through_id or 0), reviewed_through_id
    )
    state.reviewer = (payload.reviewer or "Executive dashboard user").strip()[:200]
    state.reviewed_at = datetime.utcnow()
    db.commit()
    return _blocked_review_status(db, payload.workspace_id)


@router.get("", response_model=List[AuditEventSummary])
def list_audit_events(
    limit: int = 50,
    workspace_id: str = None,
    db: Session = Depends(get_db),
):
    """Return the most recent audit events, newest first."""
    return get_audit_events(db, limit=limit, workspace_id=workspace_id)


@router.get("/export")
def export_audit_log():
    """Download the full append-only JSONL audit file."""
    path = export_jsonl_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No audit log file found yet. Run some routing operations first.")
    return FileResponse(
        path=path,
        media_type="application/x-ndjson",
        filename="fage_audit.jsonl",
    )


@router.get("/{event_id}", response_model=AuditEventDetail)
def get_event_detail(event_id: int, db: Session = Depends(get_db)):
    """Return full detail for a single audit event including rationale and context snapshot."""
    result = get_audit_event(db, event_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Audit event {event_id} not found.")
    return result
