"""
api/routes_keywords.py — Sensitive Term Library API

GET    /api/keywords              — list all terms
POST   /api/keywords              — add a new term
PATCH  /api/keywords/{id}         — update action or category
DELETE /api/keywords/{id}         — remove a term
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import AuditEvent, SensitiveTerm
from core.keywords import get_all_terms, add_term, delete_term, update_term, restore_defaults

router = APIRouter()


def _audit_policy_change(db: Session, outcome: str, details: dict):
    db.add(AuditEvent(
        event_type="POLICY",
        department="Policy",
        risk_level="low",
        decision_outcome=outcome,
        context_snapshot=json.dumps(details),
        rationale="Sensitive-term policy changed by an administrator.",
    ))
    db.commit()


class TermOut(BaseModel):
    id:         int
    term:       str
    category:   str
    action:     str
    department: Optional[str] = None
    enabled:    bool = True
    is_recommended: bool = False

    class Config:
        from_attributes = True


class AddTermRequest(BaseModel):
    term:       str
    category:   str = "custom"   # legal | hipaa | financial | hr | custom
    action:     str = "flag"     # flag | escalate | block
    department: Optional[str] = None


class UpdateTermRequest(BaseModel):
    action:   Optional[str] = None
    category: Optional[str] = None
    enabled:  Optional[bool] = None


@router.get("", response_model=List[TermOut])
def list_terms(db: Session = Depends(get_db)):
    """Return all sensitive terms, seeding defaults if empty."""
    return get_all_terms(db)


@router.post("", response_model=TermOut, status_code=201)
def create_term(body: AddTermRequest, db: Session = Depends(get_db)):
    """Add a new sensitive term."""
    if body.action not in ("flag", "escalate", "block"):
        raise HTTPException(status_code=400, detail="action must be flag, escalate, or block.")
    if body.category not in ("legal", "hipaa", "financial", "hr", "custom"):
        raise HTTPException(status_code=400, detail="category must be legal, hipaa, financial, hr, or custom.")
    try:
        term = add_term(db, body.term, body.category, body.action, body.department)
        _audit_policy_change(db, f"Sensitive term added: {term.term}", {
            "term_id": term.id, "action": term.action, "category": term.category,
        })
        return term
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{term_id}", response_model=TermOut)
def patch_term(term_id: int, body: UpdateTermRequest, db: Session = Depends(get_db)):
    """Update the action or category of an existing term."""
    if body.action is not None and body.action not in ("flag", "escalate", "block"):
        raise HTTPException(status_code=400, detail="action must be flag, escalate, or block.")
    if body.category is not None and body.category not in (
        "legal", "hipaa", "financial", "hr", "custom", "pii", "code"
    ):
        raise HTTPException(status_code=400, detail="category is not supported.")
    try:
        term = update_term(db, term_id, body.action, body.category, body.enabled)
        _audit_policy_change(db, f"Sensitive term updated: {term.term}", {
            "term_id": term.id,
            "action": term.action,
            "category": term.category,
            "enabled": term.enabled,
            "is_recommended": term.is_recommended,
        })
        return term
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{term_id}")
def remove_term(term_id: int, db: Session = Depends(get_db)):
    """Remove a sensitive term by ID."""
    term = db.query(SensitiveTerm).filter(
        SensitiveTerm.id == term_id,
        SensitiveTerm.deleted_at.is_(None),
    ).first()
    if not delete_term(db, term_id):
        raise HTTPException(status_code=404, detail=f"Term ID {term_id} not found.")
    _audit_policy_change(db, f"Sensitive term deleted: {term.term}", {
        "term_id": term_id,
        "term": term.term,
        "is_recommended": term.is_recommended,
    })
    return {"deleted": term_id}


@router.post("/restore-defaults", response_model=List[TermOut])
def restore_recommended_terms(db: Session = Depends(get_db)):
    """Restore and enable CostPilot's recommended starter term library."""
    terms = restore_defaults(db)
    _audit_policy_change(db, "Recommended sensitive-term defaults restored", {
        "restored_count": sum(1 for term in terms if term.is_recommended),
    })
    return terms
