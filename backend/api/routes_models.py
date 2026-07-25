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

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import ModelRegistry, TokenTransaction
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
    selected = _get_model_from_registry(tier, db, department=requested_department)
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


@router.get("/routing-outcomes")
def get_model_routing_outcomes(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Summarize exact model telemetry and clearly identify legacy tier-inferred rows."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(
            TokenTransaction.model_name,
            TokenTransaction.model_tier,
            TokenTransaction.resolved_model_tier,
            TokenTransaction.model_source,
            TokenTransaction.routing_cascaded,
            TokenTransaction.department,
            func.count(TokenTransaction.id).label("calls"),
            func.sum(TokenTransaction.cost_usd).label("spend"),
        )
        .filter(
            TokenTransaction.timestamp >= cutoff,
            or_(
                TokenTransaction.routing_reason.is_(None),
                ~TokenTransaction.routing_reason.in_(("VOICE_GUARD_PRUNE", "BLOCKED", "SKIPPED")),
            ),
        )
        .group_by(
            TokenTransaction.model_name,
            TokenTransaction.model_tier,
            TokenTransaction.resolved_model_tier,
            TokenTransaction.model_source,
            TokenTransaction.routing_cascaded,
            TokenTransaction.department,
        )
        .all()
    )

    catalog = db.query(ModelRegistry).all()
    enabled = [m for m in catalog if m.is_enabled]
    tier_number = {
        "scout": 1, "micro": 1,
        "analyst": 2,
        "advisor": 3, "flagship": 3,
        "strategist": 4,
    }
    inferred_defaults = {}
    for tier in range(1, 5):
        candidates = [m for m in enabled if m.tier == tier and not m.department]
        inferred_defaults[tier] = (
            next((m for m in candidates if m.is_default), None)
            or (candidates[0] if candidates else None)
        )

    outcomes = {}
    total_calls = 0
    total_spend = 0.0
    recorded_calls = 0
    cascaded_calls = 0
    fallback_calls = 0

    for row in rows:
        calls = int(row.calls or 0)
        spend = float(row.spend or 0.0)
        total_calls += calls
        total_spend += spend
        exact = bool(row.model_name)
        matched = None

        if exact:
            recorded_calls += calls
            matched = next(
                (
                    m for m in catalog
                    if m.model_id == row.model_name or m.display_name == row.model_name
                ),
                None,
            )
            model_key = matched.model_id if matched else row.model_name
            display_name = matched.display_name if matched else row.model_name
        else:
            tier_label = row.resolved_model_tier or row.model_tier or ""
            matched = inferred_defaults.get(tier_number.get(tier_label.lower()))
            model_key = matched.model_id if matched else f"unattributed:{tier_label or 'unknown'}"
            display_name = matched.display_name if matched else f"Unattributed {tier_label or 'model'}"

        item = outcomes.setdefault(model_key, {
            "model_key": model_key,
            "display_name": display_name,
            "provider": matched.provider if matched else None,
            "calls": 0,
            "spend_usd": 0.0,
            "exact_calls": 0,
            "inferred_calls": 0,
            "departments": {},
        })
        item["calls"] += calls
        item["spend_usd"] += spend
        item["exact_calls" if exact else "inferred_calls"] += calls
        department = row.department or "Unassigned"
        item["departments"][department] = item["departments"].get(department, 0) + calls
        if row.routing_cascaded:
            cascaded_calls += calls
        if row.model_source == "built_in_fallback":
            fallback_calls += calls

    model_rows = sorted(outcomes.values(), key=lambda item: item["spend_usd"], reverse=True)
    for item in model_rows:
        item["spend_usd"] = round(item["spend_usd"], 6)
        item["avg_cost_usd"] = round(item["spend_usd"] / item["calls"], 6) if item["calls"] else 0.0
        item["telemetry"] = (
            "exact"
            if item["exact_calls"] == item["calls"]
            else "inferred"
            if item["inferred_calls"] == item["calls"]
            else "mixed"
        )
        item["top_departments"] = [
            {"department": department, "calls": calls}
            for department, calls in sorted(
                item.pop("departments").items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:3]
        ]

    used_keys = set(outcomes)
    unused_eligible = [
        {
            "id": m.id,
            "display_name": m.display_name,
            "model_id": m.model_id,
            "tier": m.tier,
            "department": m.department,
        }
        for m in enabled
        if m.model_id not in used_keys and m.display_name not in used_keys
    ]

    return {
        "days": days,
        "total_calls": total_calls,
        "total_spend_usd": round(total_spend, 6),
        "avg_cost_usd": round(total_spend / total_calls, 6) if total_calls else 0.0,
        "recorded_calls": recorded_calls,
        "inferred_calls": total_calls - recorded_calls,
        "telemetry_coverage_pct": round(recorded_calls / total_calls * 100, 1) if total_calls else 0.0,
        "cascaded_calls": cascaded_calls,
        "fallback_calls": fallback_calls,
        "unused_eligible_count": len(unused_eligible),
        "unused_eligible": unused_eligible,
        "models": model_rows,
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
