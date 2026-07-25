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
from config import FLAGSHIP_MODEL, MICRO_MODEL

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
    department:         Optional[str] = None   # None = global; set to restrict to one business unit
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
        "department":         m.department or None,
        "notes":              m.notes or "",
        "created_at":         m.created_at.isoformat() if m.created_at else None,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/tiers")
def get_tiers():
    """Returns tier metadata — names, descriptions, examples."""
    return [{"tier": k, **v} for k, v in TIER_META.items()]


@router.get("/routing-preview")
def preview_model_routing(
    tier: int = Query(..., ge=1, le=4),
    department: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Explain the router's selection policy without making an AI call."""
    from core.router import _get_model_from_registry

    requested_department = (department or "").strip() or None
    # The legacy recursive resolver can revisit Tier 2 when Tier 2 and Tier 3 are
    # both empty. Bound the read-only preview to the documented cascade order so
    # an incomplete registry cannot make this diagnostic endpoint recurse.
    cascade_order = {
        1: [1],
        2: [2, 3, 1],
        3: [3, 2, 1],
        4: [4, 3, 2, 1],
    }[tier]
    eligible_tier = None
    for candidate_tier in cascade_order:
        query = db.query(ModelRegistry).filter(
            ModelRegistry.tier == candidate_tier,
            ModelRegistry.is_enabled == True,
        )
        if requested_department:
            query = query.filter(
                (ModelRegistry.department == requested_department)
                | (ModelRegistry.department == None)
            )
        else:
            query = query.filter(ModelRegistry.department == None)
        if query.first():
            eligible_tier = candidate_tier
            break

    selected = (
        _get_model_from_registry(eligible_tier, db, department=requested_department)
        if eligible_tier is not None
        else None
    )
    if selected:
        resolved_tier = int(selected["tier"])
        return {
            "source": "registry",
            "requested_tier": tier,
            "requested_tier_name": TIER_META[tier]["name"],
            "resolved_tier": resolved_tier,
            "resolved_tier_name": TIER_META[resolved_tier]["name"],
            "cascaded": resolved_tier != tier,
            "department": requested_department,
            "scope": "department" if selected["department_scoped"] else "global",
            "display_name": selected["display_name"],
            "model_id": selected["model_id"],
            "cost_input_per_1m": selected["cost_input_per_million"],
            "cost_output_per_1m": selected["cost_output_per_million"],
            "reason": (
                f"No enabled Tier {tier} model matched, so the existing router cascaded "
                f"to Tier {resolved_tier}."
                if resolved_tier != tier
                else (
                    f"Selected the {requested_department} scoped eligible model."
                    if selected["department_scoped"]
                    else "Selected the eligible global model for this tier."
                )
            ),
        }

    fallback = MICRO_MODEL if tier <= 2 else FLAGSHIP_MODEL
    return {
        "source": "built_in_fallback",
        "requested_tier": tier,
        "requested_tier_name": TIER_META[tier]["name"],
        "resolved_tier": tier,
        "resolved_tier_name": TIER_META[tier]["name"],
        "cascaded": False,
        "department": requested_department,
        "scope": "system",
        "display_name": fallback["display_name"],
        "model_id": fallback["name"],
        "cost_input_per_1m": fallback["input_cost_per_million"],
        "cost_output_per_1m": fallback["output_cost_per_million"],
        "reason": "No eligible registry model was found, so the existing built-in fallback would be used.",
    }


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

    # If this is set as default, clear existing default for same tier + same department scope
    if body.is_default:
        q = db.query(ModelRegistry).filter(
            ModelRegistry.tier == body.tier,
            ModelRegistry.is_default == True,
        )
        if body.department:
            q = q.filter(ModelRegistry.department == body.department)
        else:
            q = q.filter(ModelRegistry.department == None)
        q.update({"is_default": False})

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

    # If setting as default, clear others in same tier + same department scope
    if body.is_default:
        q = db.query(ModelRegistry).filter(
            ModelRegistry.tier == body.tier,
            ModelRegistry.is_default == True,
            ModelRegistry.id != model_id,
        )
        if body.department:
            q = q.filter(ModelRegistry.department == body.department)
        else:
            q = q.filter(ModelRegistry.department == None)
        q.update({"is_default": False})

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
