"""
api/routes_models.py — Model Registry CRUD  [Step 13]

Manages the company's registered AI models with tier classification.

Tiers:
  1 = Scout      — Fast, affordable, handles routine tasks
  2 = Analyst    — Balanced reasoning for most business tasks
  3 = Advisor    — Deep reasoning for complex or sensitive work
  4 = Strategist — Highest capability for mission-critical decisions

Endpoints:
  GET    /api/models           — list all models (optionally filter by tier/provider/enabled)
  POST   /api/models           — register a new model
  PUT    /api/models/{id}      — update a model
  PATCH  /api/models/{id}/toggle — enable / disable a model
  DELETE /api/models/{id}      — remove a model
  GET    /api/models/tiers     — return tier metadata (names, descriptions)
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import ModelRegistry

router = APIRouter()

TIER_META = {
    1: {
        "name":     "Scout",
        "tagline":  "Fast, affordable, handles routine tasks",
        "best_for": "FAQs, status lookups, simple summaries",
        "examples": "GPT-4o mini, Claude Haiku",
        "color":    "#3fb950",
        "icon":     "⚡",
    },
    2: {
        "name":     "Analyst",
        "tagline":  "Balanced reasoning for most business tasks",
        "best_for": "Customer emails, data summarization, drafting",
        "examples": "GPT-4o, Claude Sonnet",
        "color":    "#58a6ff",
        "icon":     "🔍",
    },
    3: {
        "name":     "Advisor",
        "tagline":  "Deep reasoning for complex or sensitive work",
        "best_for": "Contract review, escalations, multi-step analysis",
        "examples": "GPT-4 Turbo, Claude Opus",
        "color":    "#bc8cff",
        "icon":     "💡",
    },
    4: {
        "name":     "Strategist",
        "tagline":  "Highest capability for mission-critical decisions",
        "best_for": "Legal, financial, compliance-heavy tasks",
        "examples": "o3, Claude Opus Max",
        "color":    "#d29922",
        "icon":     "🎯",
    },
}


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ModelIn(BaseModel):
    display_name:       str
    model_id:           str
    provider:           str
    tier:               int
    cost_input_per_1m:  float = 0.0
    cost_output_per_1m: float = 0.0
    is_enabled:         bool  = True
    is_default:         bool  = False
    notes:              Optional[str] = None


def _serialize(m: ModelRegistry) -> dict:
    tier_info = TIER_META.get(m.tier, {})
    return {
        "id":                 m.id,
        "display_name":       m.display_name,
        "model_id":           m.model_id,
        "provider":           m.provider,
        "tier":               m.tier,
        "tier_name":          tier_info.get("name", f"Tier {m.tier}"),
        "tier_tagline":       tier_info.get("tagline", ""),
        "tier_color":         tier_info.get("color", "#8b949e"),
        "tier_icon":          tier_info.get("icon", "◈"),
        "cost_input_per_1m":  m.cost_input_per_1m,
        "cost_output_per_1m": m.cost_output_per_1m,
        "is_enabled":         m.is_enabled,
        "is_default":         m.is_default,
        "notes":              m.notes or "",
        "created_at":         m.created_at.isoformat() if m.created_at else None,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/tiers")
def get_tiers():
    """Returns tier metadata — names, descriptions, examples."""
    return [{"tier": k, **v} for k, v in TIER_META.items()]


@router.get("")
def list_models(
    tier:     Optional[int]  = Query(None),
    provider: Optional[str]  = Query(None),
    enabled:  Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ModelRegistry)
    if tier     is not None: q = q.filter(ModelRegistry.tier == tier)
    if provider is not None: q = q.filter(ModelRegistry.provider == provider)
    if enabled  is not None: q = q.filter(ModelRegistry.is_enabled == enabled)
    models = q.order_by(ModelRegistry.tier, ModelRegistry.display_name).all()
    return [_serialize(m) for m in models]


@router.post("")
def create_model(body: ModelIn, db: Session = Depends(get_db)):
    if body.tier not in TIER_META:
        raise HTTPException(status_code=400, detail="tier must be 1, 2, 3, or 4")

    # If this is set as default, clear existing default for that tier
    if body.is_default:
        db.query(ModelRegistry).filter(
            ModelRegistry.tier == body.tier,
            ModelRegistry.is_default == True,
        ).update({"is_default": False})

    m = ModelRegistry(**body.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.put("/{model_id}")
def update_model(model_id: int, body: ModelIn, db: Session = Depends(get_db)):
    m = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")

    if body.tier not in TIER_META:
        raise HTTPException(status_code=400, detail="tier must be 1, 2, 3, or 4")

    # If setting as default, clear others in same tier
    if body.is_default:
        db.query(ModelRegistry).filter(
            ModelRegistry.tier == body.tier,
            ModelRegistry.is_default == True,
            ModelRegistry.id != model_id,
        ).update({"is_default": False})

    for field, value in body.model_dump().items():
        setattr(m, field, value)

    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.patch("/{model_id}/toggle")
def toggle_model(model_id: int, db: Session = Depends(get_db)):
    m = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")
    m.is_enabled = not m.is_enabled
    db.commit()
    return {"id": m.id, "is_enabled": m.is_enabled}


@router.delete("/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    m = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found")
    db.delete(m)
    db.commit()
    return {"status": "ok", "deleted_id": model_id}
