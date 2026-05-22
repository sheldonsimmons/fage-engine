"""
api/routes_keywords.py — Sensitive Term Library API

GET    /api/keywords              — list all terms
POST   /api/keywords              — add a new term
PATCH  /api/keywords/{id}         — update action or category
DELETE /api/keywords/{id}         — remove a term
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from core.keywords import get_all_terms, add_term, delete_term, update_term

router = APIRouter()


class TermOut(BaseModel):
    id:         int
    term:       str
    category:   str
    action:     str
    department: Optional[str] = None

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
        return add_term(db, body.term, body.category, body.action, body.department)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{term_id}", response_model=TermOut)
def patch_term(term_id: int, body: UpdateTermRequest, db: Session = Depends(get_db)):
    """Update the action or category of an existing term."""
    try:
        return update_term(db, term_id, body.action, body.category)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{term_id}")
def remove_term(term_id: int, db: Session = Depends(get_db)):
    """Remove a sensitive term by ID."""
    if not delete_term(db, term_id):
        raise HTTPException(status_code=404, detail=f"Term ID {term_id} not found.")
    return {"deleted": term_id}
