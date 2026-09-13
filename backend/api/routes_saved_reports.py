"""
api/routes_saved_reports.py — Printable Reports Phase 4: saved, shareable
reports.

Deliberately thin: this router only persists and returns a RECIPE (report_
type + source params), never report data itself. Opening a saved report
means the frontend calls the SAME existing endpoints Phase 1-3 already
built (routes_dashboard.py's Business Impact family, routes_reports.py's
savings/risk/departments, or routes_efficiency.py's ask/report-data) with
the saved params -- so a saved report always re-derives fresh, permission-
checked data on every open, through code that's already trust-layer-
verified, rather than this router duplicating any report-building logic
or serving a stale/leaked data snapshot. See database/models.py's
SavedReport docstring for the full reasoning.
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth import check_membership
from database.db import get_db
from database.models import SavedReport

router = APIRouter()

REPORT_TYPES = {"business_impact", "savings", "risk", "departments", "ask_costpilot", "support_briefing"}


def _check_reporting_access(
    db: Session, authorization: Optional[str], workspace_id: Optional[str],
) -> Optional[str]:
    """Same retrofit helper as routes_dashboard.py/routes_reports.py -- see
    routes_dashboard.py's docstring for the full soft-mode-gated reasoning."""
    if not workspace_id:
        return None
    ctx = check_membership(db, authorization, workspace_id, "view_reports")
    return ctx.department_scope if ctx else None


class SaveReportRequest(BaseModel):
    workspace_id: Optional[str] = None
    title: str
    report_type: str
    source: dict = Field(default_factory=dict)


@router.post("")
def save_report(
    request: SaveReportRequest, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    if request.report_type not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown report_type: {request.report_type}")
    department_scope = _check_reporting_access(db, authorization, request.workspace_id)

    import json
    row = SavedReport(
        workspace_id=request.workspace_id,
        title=request.title.strip()[:200] or "Untitled report",
        report_type=request.report_type,
        source_json=json.dumps(request.source or {}),
        created_department_scope=department_scope,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.get("")
def list_saved_reports(
    workspace_id: Optional[str] = None, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    # Listing itself is not the sensitive step -- title/report_type carry
    # no department-scoped data -- the actual data access is re-checked
    # fresh every time a saved report is OPENED (see get_saved_report
    # below), same "re-derive under the current caller's live permissions"
    # principle as the rest of this router.
    _check_reporting_access(db, authorization, workspace_id)
    q = db.query(SavedReport)
    if workspace_id:
        q = q.filter(SavedReport.workspace_id == workspace_id)
    rows = q.order_by(SavedReport.created_at.desc()).limit(200).all()
    return {"reports": [_serialize(r) for r in rows]}


@router.get("/{report_id}")
def get_saved_report(
    report_id: int, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    row = db.query(SavedReport).filter(SavedReport.id == report_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Saved report not found")
    # Resolves the CURRENT caller's own department_scope -- deliberately
    # never trusts created_department_scope for access. The frontend uses
    # the returned `source` to call the underlying report endpoint itself
    # (with its own Authorization header), which applies this same fresh
    # check again -- this lookup's only job is returning the recipe.
    _check_reporting_access(db, authorization, row.workspace_id)
    row.last_viewed_at = datetime.utcnow()
    db.commit()
    return _serialize(row, include_source=True)


@router.delete("/{report_id}")
def delete_saved_report(
    report_id: int, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    row = db.query(SavedReport).filter(SavedReport.id == report_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Saved report not found")
    _check_reporting_access(db, authorization, row.workspace_id)
    db.delete(row)
    db.commit()
    return {"deleted": True}


def _serialize(row: SavedReport, include_source: bool = False) -> dict:
    import json
    out = {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "report_type": row.report_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_viewed_at": row.last_viewed_at.isoformat() if row.last_viewed_at else None,
    }
    if include_source:
        try:
            out["source"] = json.loads(row.source_json or "{}")
        except (ValueError, TypeError):
            out["source"] = {}
    return out
