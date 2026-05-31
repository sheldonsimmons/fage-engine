"""
api/routes_known_models.py — Known Models Preset List

Manages the admin-maintained list of AI models that appear in the
'Quick Select a Known Model' dropdown when adding a model to the registry.

Admins can add, update, or remove entries here without any code deployment.
The frontend fetches this list at modal-open time so changes are instant.

Endpoints:
  GET    /api/models/known           — list all active known models (for dropdown)
  GET    /api/models/known/all       — list all including inactive (for admin panel)
  POST   /api/models/known           — add a new known model
  PUT    /api/models/known/{id}      — update a known model
  DELETE /api/models/known/{id}      — remove a known model
  PATCH  /api/models/known/{id}/toggle — activate / deactivate without deleting
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import KnownModel

router = APIRouter()

TIER_NAMES = {1: "Scout", 2: "Analyst", 3: "Advisor", 4: "Strategist"}


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class KnownModelIn(BaseModel):
    display_name:       str
    model_id:           str
    provider:           str
    provider_group:     str
    tier:               int
    cost_input_per_1m:  float = 0.0
    cost_output_per_1m: float = 0.0
    is_active:          bool  = True
    notes:              Optional[str] = None


def _serialize(m: KnownModel) -> dict:
    return {
        "id":                 m.id,
        "display_name":       m.display_name,
        "model_id":           m.model_id,
        "provider":           m.provider,
        "provider_group":     m.provider_group,
        "tier":               m.tier,
        "tier_name":          TIER_NAMES.get(m.tier, f"Tier {m.tier}"),
        "cost_input_per_1m":  m.cost_input_per_1m,
        "cost_output_per_1m": m.cost_output_per_1m,
        "is_active":          m.is_active,
        "notes":              m.notes or "",
        "created_at":         m.created_at.isoformat() if m.created_at else None,
        "updated_at":         m.updated_at.isoformat() if m.updated_at else None,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_known_models(db: Session = Depends(get_db)):
    """
    Returns active known models grouped for the Quick Select dropdown.
    Only returns is_active=True entries.
    """
    models = (
        db.query(KnownModel)
        .filter(KnownModel.is_active == True)
        .order_by(KnownModel.provider_group, KnownModel.tier, KnownModel.display_name)
        .all()
    )
    # Group by provider_group for the dropdown optgroup structure
    groups = {}
    for m in models:
        if m.provider_group not in groups:
            groups[m.provider_group] = []
        groups[m.provider_group].append(_serialize(m))

    return {"groups": [{"label": label, "models": items} for label, items in groups.items()]}


@router.get("/all")
def list_all_known_models(db: Session = Depends(get_db)):
    """Returns all known models including inactive — for the admin management panel."""
    models = (
        db.query(KnownModel)
        .order_by(KnownModel.provider_group, KnownModel.tier, KnownModel.display_name)
        .all()
    )
    return [_serialize(m) for m in models]


@router.post("")
def create_known_model(body: KnownModelIn, db: Session = Depends(get_db)):
    if body.tier not in TIER_NAMES:
        raise HTTPException(status_code=400, detail="Tier must be 1, 2, 3, or 4.")
    existing = db.query(KnownModel).filter_by(model_id=body.model_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Model ID '{body.model_id}' already exists in the known models list.")
    m = KnownModel(**body.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.put("/{model_id}")
def update_known_model(model_id: int, body: KnownModelIn, db: Session = Depends(get_db)):
    m = db.query(KnownModel).filter_by(id=model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Known model not found.")
    if body.tier not in TIER_NAMES:
        raise HTTPException(status_code=400, detail="Tier must be 1, 2, 3, or 4.")
    # Check model_id uniqueness (allow same record to keep its own model_id)
    conflict = db.query(KnownModel).filter(KnownModel.model_id == body.model_id, KnownModel.id != model_id).first()
    if conflict:
        raise HTTPException(status_code=409, detail=f"Model ID '{body.model_id}' already exists.")
    for field, value in body.model_dump().items():
        setattr(m, field, value)
    m.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.patch("/{model_id}/toggle")
def toggle_known_model(model_id: int, db: Session = Depends(get_db)):
    """Activate or deactivate a known model — hides/shows it in the dropdown."""
    m = db.query(KnownModel).filter_by(id=model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Known model not found.")
    m.is_active = not m.is_active
    m.updated_at = datetime.utcnow()
    db.commit()
    return {"id": m.id, "display_name": m.display_name, "is_active": m.is_active}


@router.delete("/{model_id}")
def delete_known_model(model_id: int, db: Session = Depends(get_db)):
    m = db.query(KnownModel).filter_by(id=model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Known model not found.")
    db.delete(m)
    db.commit()
    return {"status": "ok", "deleted_id": model_id}
