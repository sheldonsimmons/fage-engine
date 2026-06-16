"""
api/routes_auditor.py — AI Decision Auditor API routes  [Step 6]

GET  /api/audit          — paginated list of audit events (newest first)
GET  /api/audit/{id}     — full detail for a single audit event
GET  /api/audit/export   — download the raw JSONL audit file
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db
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
    has_raw_payload:  Optional[bool] = False
    matched_keywords: Optional[List[str]] = Field(default_factory=list)
    timestamp:        Optional[str]


class AuditEventDetail(AuditEventSummary):
    rationale:        Optional[str]
    prompt_payload:   Optional[str]
    raw_payload:      Optional[str] = None
    raw_logged_at:    Optional[str] = None
    context_snapshot: Optional[str]


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
