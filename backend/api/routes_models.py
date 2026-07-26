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

import json
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import AuditEvent, ModelRegistry, TokenTransaction
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

    alerts = []
    if fallback_calls:
        alerts.append({
            "code": "built_in_fallback",
            "severity": "critical",
            "title": f"{fallback_calls:,} call{'s' if fallback_calls != 1 else ''} used a built-in fallback",
            "detail": "No eligible registry model was available for these requests. Review enabled models and tier coverage.",
            "action": "Review model eligibility",
            "model_key": None,
        })
    if cascaded_calls:
        alerts.append({
            "code": "routing_cascade",
            "severity": "warning",
            "title": f"{cascaded_calls:,} call{'s' if cascaded_calls != 1 else ''} changed tiers",
            "detail": "The requested tier had no matching eligible model, so CostPilot selected another tier.",
            "action": "Check tier coverage",
            "model_key": None,
        })
    if unused_eligible:
        first_unused = unused_eligible[0]
        alerts.append({
            "code": "eligible_unused",
            "severity": "info",
            "title": f"{len(unused_eligible):,} eligible model{'s are' if len(unused_eligible) != 1 else ' is'} unused",
            "detail": "Eligible models with no attributed calls may be intentional, or may indicate routing configuration that never selects them.",
            "action": "Inspect unused model",
            "model_key": first_unused["model_id"],
        })

    spend_concentration_pct = 0.0
    if model_rows and total_spend > 0:
        top_model = model_rows[0]
        spend_concentration_pct = round(top_model["spend_usd"] / total_spend * 100, 1)
        if total_calls >= 10 and spend_concentration_pct >= 70:
            alerts.append({
                "code": "spend_concentration",
                "severity": "warning",
                "title": f"{spend_concentration_pct:.1f}% of model spend is on {top_model['display_name']}",
                "detail": "High concentration is not automatically a problem, but it deserves review when a premium model dominates total spend.",
                "action": "Inspect spending evidence",
                "model_key": top_model["model_key"],
            })

    precise_coverage = recorded_calls / total_calls * 100 if total_calls else 0.0
    return {
        "days": days,
        "total_calls": total_calls,
        "total_spend_usd": round(total_spend, 6),
        "avg_cost_usd": round(total_spend / total_calls, 6) if total_calls else 0.0,
        "recorded_calls": recorded_calls,
        "inferred_calls": total_calls - recorded_calls,
        "telemetry_coverage_pct": round(precise_coverage, 1),
        "telemetry_coverage_pct_precise": round(precise_coverage, 4),
        "cascaded_calls": cascaded_calls,
        "fallback_calls": fallback_calls,
        "spend_concentration_pct": spend_concentration_pct,
        "unused_eligible_count": len(unused_eligible),
        "unused_eligible": unused_eligible,
        "alerts": alerts,
        "models": model_rows,
    }


@router.get("/routing-outcomes/detail")
def get_model_routing_outcome_detail(
    model_key: str = Query(..., min_length=1, max_length=200),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Return evidence behind one model outcome without changing routing state."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    catalog = db.query(ModelRegistry).all()
    model = next(
        (m for m in catalog if m.model_id == model_key or m.display_name == model_key),
        None,
    )
    tier_labels = {
        1: ("Scout", "micro"),
        2: ("Analyst",),
        3: ("Advisor", "flagship"),
        4: ("Strategist",),
    }

    exact_values = {model_key}
    if model:
        exact_values.update((model.model_id, model.display_name))
    match_conditions = [TokenTransaction.model_name.in_(exact_values)]
    allow_inferred = False
    relevant_tiers = set()

    if model:
        relevant_tiers = set(tier_labels.get(model.tier, ()))
        global_candidates = [
            m for m in catalog
            if m.is_enabled and m.tier == model.tier and not m.department
        ]
        current_selection = (
            next((m for m in global_candidates if m.is_default), None)
            or (global_candidates[0] if global_candidates else None)
        )
        allow_inferred = bool(current_selection and current_selection.id == model.id)
        if allow_inferred:
            match_conditions.append(and_(
                TokenTransaction.model_name.is_(None),
                TokenTransaction.model_tier.in_(relevant_tiers),
            ))
    elif model_key.startswith("unattributed:"):
        inferred_label = model_key.split(":", 1)[1]
        relevant_tiers = {inferred_label}
        allow_inferred = True
        match_conditions = [and_(
            TokenTransaction.model_name.is_(None),
            TokenTransaction.model_tier == inferred_label,
        )]

    transactions = (
        db.query(TokenTransaction)
        .filter(
            TokenTransaction.timestamp >= cutoff,
            or_(
                TokenTransaction.routing_reason.is_(None),
                ~TokenTransaction.routing_reason.in_(("VOICE_GUARD_PRUNE", "BLOCKED", "SKIPPED")),
            ),
            or_(*match_conditions),
        )
        .order_by(TokenTransaction.timestamp.desc())
        .all()
    )

    departments = {}
    agents = {}
    exact_calls = 0
    inferred_calls = 0
    total_spend = 0.0
    cascaded_calls = 0
    fallback_calls = 0
    recent_calls = []

    for tx in transactions:
        exact = bool(tx.model_name)
        exact_calls += int(exact)
        inferred_calls += int(not exact)
        total_spend += float(tx.cost_usd or 0.0)
        cascaded_calls += int(bool(tx.routing_cascaded))
        fallback_calls += int(tx.model_source == "built_in_fallback")

        department = tx.department or "Unassigned"
        dept = departments.setdefault(department, {"department": department, "calls": 0, "spend_usd": 0.0})
        dept["calls"] += 1
        dept["spend_usd"] += float(tx.cost_usd or 0.0)

        agent_name = tx.agent.name if tx.agent else "Unassigned"
        agent_key = str(tx.agent_id) if tx.agent_id else f"unassigned:{department}"
        agent = agents.setdefault(agent_key, {
            "agent_id": tx.agent_id,
            "agent_name": agent_name,
            "department": department,
            "source_platform": tx.agent.source_platform if tx.agent else tx.source_platform,
            "calls": 0,
            "spend_usd": 0.0,
        })
        agent["calls"] += 1
        agent["spend_usd"] += float(tx.cost_usd or 0.0)

        if len(recent_calls) < 12:
            recent_calls.append({
                "id": tx.id,
                "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
                "department": department,
                "agent_name": agent_name,
                "requested_tier": tx.model_tier,
                "resolved_tier": tx.resolved_model_tier or tx.model_tier,
                "routing_reason": tx.routing_reason,
                "cost_usd": round(float(tx.cost_usd or 0.0), 6),
                "routing_cascaded": bool(tx.routing_cascaded),
                "model_source": tx.model_source,
                "telemetry": "exact" if exact else "inferred",
            })

    department_rows = sorted(departments.values(), key=lambda item: (-item["calls"], item["department"]))
    agent_rows = sorted(agents.values(), key=lambda item: (-item["calls"], item["agent_name"]))
    for item in department_rows + agent_rows:
        item["spend_usd"] = round(item["spend_usd"], 6)

    reason_totals = {}
    review_candidate_calls = 0
    review_candidate_spend = 0.0
    for tx in transactions:
        reason = (tx.routing_reason or "UNKNOWN").upper()
        reason_item = reason_totals.setdefault(reason, {"reason": reason, "calls": 0, "spend_usd": 0.0})
        reason_item["calls"] += 1
        reason_item["spend_usd"] += float(tx.cost_usd or 0.0)
        if reason == "OVERRIDE" or bool(tx.routing_cascaded) or tx.model_source == "built_in_fallback":
            review_candidate_calls += 1
            review_candidate_spend += float(tx.cost_usd or 0.0)

    routing_reasons = sorted(
        reason_totals.values(),
        key=lambda item: (-item["calls"], -item["spend_usd"], item["reason"]),
    )
    for item in routing_reasons:
        item["spend_usd"] = round(item["spend_usd"], 6)
        item["share_pct"] = round(item["calls"] / len(transactions) * 100, 1) if transactions else 0.0

    top_agent = max(agent_rows, key=lambda item: item["spend_usd"], default=None)
    top_department = max(department_rows, key=lambda item: item["spend_usd"], default=None)
    if top_agent:
        top_agent = {
            **top_agent,
            "spend_share_pct": round(top_agent["spend_usd"] / total_spend * 100, 1) if total_spend else 0.0,
        }
    if top_department:
        top_department = {
            **top_department,
            "spend_share_pct": round(top_department["spend_usd"] / total_spend * 100, 1) if total_spend else 0.0,
        }

    lower_model = None
    if model and model.tier > 1:
        requested_department = model.department
        for candidate_tier in range(model.tier - 1, 0, -1):
            tier_candidates = [
                candidate for candidate in catalog
                if candidate.is_enabled and candidate.tier == candidate_tier
            ]
            if requested_department:
                scoped = [candidate for candidate in tier_candidates if candidate.department == requested_department]
                lower_model = (
                    next((candidate for candidate in scoped if candidate.is_default), None)
                    or (scoped[0] if scoped else None)
                )
            if not lower_model:
                global_candidates = [candidate for candidate in tier_candidates if not candidate.department]
                lower_model = (
                    next((candidate for candidate in global_candidates if candidate.is_default), None)
                    or (global_candidates[0] if global_candidates else None)
                )
            if lower_model:
                break

    optimization = {
        "status": "insufficient_data",
        "headline": "More usage is needed before CostPilot can model an opportunity.",
        "guidance": "No routing change is recommended.",
        "confidence": (
            "exact" if exact_calls and not inferred_calls
            else "mixed" if exact_calls and inferred_calls
            else "inferred" if inferred_calls
            else "none"
        ),
        "routing_reasons": routing_reasons,
        "review_candidate_calls": review_candidate_calls,
        "review_candidate_spend_usd": round(review_candidate_spend, 6),
        "top_agent": top_agent,
        "top_department": top_department,
        "scenario": None,
    }
    if transactions and model and model.tier == 1:
        optimization.update({
            "status": "lowest_tier",
            "headline": "This model is already in CostPilot’s lowest-cost routing tier.",
            "guidance": "Review usage and pricing, but there is no lower registered tier to model.",
        })
    elif transactions and model and not lower_model:
        optimization.update({
            "status": "no_lower_model",
            "headline": "No lower-tier eligible model is available for comparison.",
            "guidance": "Register or enable a lower-tier model before evaluating a savings scenario.",
        })
    elif transactions and model and lower_model:
        estimated_cost = sum(
            (
                int(tx.input_tokens or 0) * float(lower_model.cost_input_per_1m or 0.0)
                + int(tx.output_tokens or 0) * float(lower_model.cost_output_per_1m or 0.0)
            ) / 1_000_000
            for tx in transactions
        )
        savings = max(0.0, total_spend - estimated_cost)
        savings_pct = savings / total_spend * 100 if total_spend else 0.0
        optimization.update({
            "status": "review" if savings > 0 else "no_savings",
            "headline": (
                f"Review {model.display_name} usage before changing routing."
                if savings > 0
                else f"{lower_model.display_name} does not produce savings with the current recorded rates."
            ),
            "guidance": (
                "Start with overrides, cascades, and the leading spend driver. Complex or sensitive requests should not be downgraded without a quality review."
                if savings > 0
                else "Keep the current route unless quality, latency, or provider strategy supports a change."
            ),
            "scenario": {
                "scope": "All attributed calls in this period",
                "candidate_model_key": lower_model.model_id,
                "candidate_display_name": lower_model.display_name,
                "candidate_tier": lower_model.tier,
                "candidate_tier_name": TIER_META[lower_model.tier]["name"],
                "current_spend_usd": round(total_spend, 6),
                "estimated_spend_usd": round(estimated_cost, 6),
                "estimated_savings_usd": round(savings, 6),
                "estimated_savings_pct": round(savings_pct, 1),
                "annualized_savings_usd": round(savings * (365 / days), 2) if days else 0.0,
                "disclaimer": "Illustrative cost scenario only. It does not determine that these calls are safe to move and does not change routing.",
            },
        })

    audit_conditions = []
    for value in exact_values:
        audit_conditions.extend((
            AuditEvent.context_snapshot.contains(f'"model_name": "{value}"'),
            AuditEvent.context_snapshot.contains(f'"model_name":"{value}"'),
        ))
    if allow_inferred and relevant_tiers:
        audit_conditions.append(and_(
            AuditEvent.model_tier.in_(relevant_tiers),
            ~AuditEvent.context_snapshot.contains('"model_name"'),
        ))
    audit_query = db.query(AuditEvent).filter(AuditEvent.timestamp >= cutoff)
    if audit_conditions:
        audit_query = audit_query.filter(or_(*audit_conditions))
    audit_candidates = audit_query.order_by(AuditEvent.timestamp.desc()).limit(100).all()
    audit_rows = []
    for event in audit_candidates:
        try:
            context = json.loads(event.context_snapshot or "{}")
        except Exception:
            context = {}
        audit_model_name = context.get("model_name")
        exact_audit = audit_model_name in exact_values if audit_model_name else False
        inferred_audit = not audit_model_name and allow_inferred
        if not exact_audit and not inferred_audit:
            continue
        audit_rows.append({
            "id": event.id,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "department": event.department,
            "event_type": event.event_type,
            "decision_outcome": event.decision_outcome,
            "risk_level": event.risk_level,
            "telemetry": "exact" if exact_audit else "tier_related",
        })
        if len(audit_rows) == 8:
            break

    total_calls = len(transactions)
    return {
        "model": {
            "id": model.id if model else None,
            "model_key": model.model_id if model else model_key,
            "display_name": model.display_name if model else model_key,
            "provider": model.provider if model else None,
            "tier": model.tier if model else None,
            "tier_name": TIER_META[model.tier]["name"] if model else None,
            "is_enabled": model.is_enabled if model else None,
            "is_default": model.is_default if model else None,
            "department": model.department if model else None,
        },
        "days": days,
        "total_calls": total_calls,
        "total_spend_usd": round(total_spend, 6),
        "avg_cost_usd": round(total_spend / total_calls, 6) if total_calls else 0.0,
        "exact_calls": exact_calls,
        "inferred_calls": inferred_calls,
        "cascaded_calls": cascaded_calls,
        "fallback_calls": fallback_calls,
        "optimization": optimization,
        "departments": department_rows,
        "agents": agent_rows,
        "recent_calls": recent_calls,
        "audit_events": audit_rows,
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
