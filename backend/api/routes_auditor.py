"""
api/routes_auditor.py — AI Decision Auditor API routes  [Step 6]

GET  /api/audit          — paginated list of audit events (newest first)
GET  /api/audit/{id}     — full detail for a single audit event
GET  /api/audit/export   — download the raw JSONL audit file
GET  /api/audit/review-status — blocked-event acknowledgement status
POST /api/audit/acknowledge-blocked — mark blocked events reviewed
"""

import json
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import AuditEvent, AuditReviewState
from core.workspace_scope import workspace_filter
from core.auditor import get_audit_events, get_audit_event, export_jsonl_path
from core.auth import check_membership
import os

router = APIRouter()


def _check_auditor_access(
    db: Session, authorization: Optional[str], workspace_id: Optional[str], permission: str = "view_reports",
):
    """
    Security fix: this entire file (the AI Activity/audit log page, and
    the Users page's per-person drill-down) had no membership check of
    any kind, including on /export -- the single highest-blast-radius
    endpoint in the app (capability assessment flagged this as one of
    four routers with zero auth surface, worse than a missing department
    filter). Soft-mode gated like every other retrofitted route; returns
    the resolved TenantContext (or None) so callers can apply
    department_scope to their query.
    """
    if not workspace_id:
        return None
    return check_membership(db, authorization, workspace_id, permission)


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
    authorization: Optional[str] = Header(default=None),
):
    """Return historical and still-unreviewed blocked-request counts."""
    _check_auditor_access(db, authorization, workspace_id)
    return _blocked_review_status(db, workspace_id)


@router.post("/acknowledge-blocked", response_model=BlockedReviewStatus)
def acknowledge_blocked_events(
    payload: BlockedReviewRequest,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Acknowledge blocked events without modifying or deleting audit records."""
    _check_auditor_access(db, authorization, payload.workspace_id, "manage_governance")
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
    work_user_id: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """Return the most recent audit events, newest first.

    work_user_id narrows to one person's full history -- callers filtering
    to a single user (the Users page) may need more than the global feed's
    reasonable default, so the cap only rises when this filter is present.
    date_from/date_to match the same optional-datetime convention already
    used by routes_reports.py.
    """
    ctx = _check_auditor_access(db, authorization, workspace_id)
    effective_limit = min(limit, 200) if work_user_id is not None else limit
    return get_audit_events(
        db,
        limit=effective_limit,
        workspace_id=workspace_id,
        work_user_id=work_user_id,
        date_from=date_from,
        date_to=date_to,
        department_scope=ctx.department_scope if ctx else None,
    )


class WorkUserSearchResult(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    source_platform: str


@router.get("/users", response_model=List[WorkUserSearchResult])
def search_work_users(
    q: str = "",
    workspace_id: str = None,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Search this workspace's human identities by name or email, for the
    Users page's picker.

    Membership-checked like every other route in this file; WorkUser has
    no direct department string column (only primary_org_unit_id), so
    department_scope isn't applied to this search itself -- a department-
    scoped caller can still find a person outside their department here,
    though list_audit_events above (the actual activity data) is scoped.
    Flagged as a known follow-up, not silently ignored.
    """
    _check_auditor_access(db, authorization, workspace_id)
    from database.models import WorkUser

    query = db.query(WorkUser)
    if workspace_id:
        query = query.filter(WorkUser.workspace_id == workspace_id)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(
            (WorkUser.name.ilike(like)) | (WorkUser.email.ilike(like))
        )
    results = query.order_by(WorkUser.name.asc()).limit(20).all()
    return [
        WorkUserSearchResult(id=u.id, name=u.name, email=u.email, source_platform=u.source_platform)
        for u in results
    ]


def _line_matches_workspace(record: dict, workspace_id: str) -> bool:
    """
    Same "workspace-prefixed department, else recorded attribution"
    precedence workspace_filter() uses for the DB-backed query path —
    applied here to the raw JSONL file, which has no SQL to filter with.
    A line with neither signal is treated as NOT matching (excluded, not
    included-by-default) since export is the highest-blast-radius finding
    in the security audit and should fail closed.
    """
    department = (record.get("department") or "")
    if department.startswith(f"{workspace_id}:"):
        return True
    attribution = (record.get("context_snapshot") or {}).get("organizational_attribution") or {}
    return attribution.get("workspace_id") == workspace_id


@router.get("/export")
def export_audit_log(
    workspace_id: str,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Download this workspace's slice of the append-only JSONL audit file.

    workspace_id is required, not optional: this used to stream every
    workspace's complete audit log -- including raw pre-redaction prompt
    payloads -- to any caller with no parameters at all (security audit
    finding). The underlying file has no per-line index, so this reads
    and filters it line by line rather than a single indexed query; for
    the file sizes an audit log realistically reaches, that's an
    acceptable cost for closing a full unscoped data-export path.

    Gated behind "export_data", not "view_reports" -- per core/rbac.py's
    own docstring, export_data is deliberately restricted to
    workspace_admin because this is one of the two most severe findings
    in the original security audit (raw prompt-payload export).
    """
    _check_auditor_access(db, authorization, workspace_id, "export_data")
    path = export_jsonl_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No audit log file found yet. Run some routing operations first.")
    lines = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if _line_matches_workspace(record, workspace_id):
                lines.append(line)
    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="fage_audit_{workspace_id}.jsonl"'},
    )


@router.get("/{event_id}", response_model=AuditEventDetail)
def get_event_detail(
    event_id: int,
    workspace_id: str,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    """
    Return full detail for a single audit event including rationale and
    context snapshot.

    workspace_id is required, not optional: event_id is a bare sequential
    integer PK, previously readable by anyone regardless of which
    workspace it belonged to (security audit finding). Scoped the same
    way every other AuditEvent query in this file already is. Also now
    membership-checked and department_scope-restricted like the rest of
    this file (capability assessment, P0); a real department-scoped
    caller gets a 404 for another department's event, same as an unknown
    id, rather than a 403 that would confirm the event exists.

    This does not yet separately gate raw_payload/prompt_payload behind
    "view_prompts" (core/rbac.py reserves that permission specifically
    for raw-payload exposure) -- doing so would need to split this
    endpoint's response rather than block it outright, since the same
    endpoint also serves the ordinary rationale/context view every
    Decision Timeline and Users-page drill-down depends on. Flagged as a
    known follow-up, not silently ignored.
    """
    ctx = _check_auditor_access(db, authorization, workspace_id)
    event = db.query(AuditEvent).filter(
        AuditEvent.id == event_id,
        workspace_filter(AuditEvent, workspace_id),
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Audit event {event_id} not found.")
    if ctx and ctx.department_scope:
        scoped_values = {ctx.department_scope, f"{workspace_id}:{ctx.department_scope}"}
        if (event.department or "") not in scoped_values:
            raise HTTPException(status_code=404, detail=f"Audit event {event_id} not found.")
    result = get_audit_event(db, event_id)
    return result
