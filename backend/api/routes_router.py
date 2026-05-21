"""
api/routes_router.py — Token Router & Model Cascader API routes  [Step 3]

POST /api/route
  Accepts a raw text payload, runs it through the full routing pipeline
  (prune → score → select model → simulate call), records the transaction
  in the database, and updates the department's running spend total.
"""

from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import DepartmentBudget, TokenTransaction
from core.router import route
from core.auditor import write_audit_event

router = APIRouter()


class RouteRequest(BaseModel):
    text:       str
    department: str  = "Support"
    auto_prune: bool = True
    agent_id:   Optional[int] = None


class RouteResponse(BaseModel):
    department:                 str
    complexity:                 str
    routing_decision:           str
    routing_reason:             str
    matched_keywords:           List[str]
    model_tier:                 str
    model_name:                 str
    input_tokens:               int
    output_tokens:              int
    cost_usd:                   float
    simulated_response:         str
    was_pruned:                 bool
    tokens_saved_by_pruning:    int
    pruning_cost_saved_usd:     float
    total_cost_without_pruning: float
    budget_used_pct:            float
    budget_remaining_usd:       float
    was_throttled:              bool


@router.post("", response_model=RouteResponse)
def route_payload(req: RouteRequest, db: Session = Depends(get_db)):
    """
    Run the full routing pipeline on a text payload:
      1. Prune junk (if auto_prune=True)
      2. Score complexity (ROUTINE or COMPLEX)
      3. Check department throttle status
      4. Select micro or flagship model tier
      5. Simulate model call + calculate real cost
      6. Record transaction and update department spend in the DB
    """
    # Check throttle status from the department budget table
    budget       = db.query(DepartmentBudget).filter_by(department=req.department).first()
    is_throttled = budget.throttled if budget else False

    # Run the routing pipeline
    result = route(req.text, req.department, req.auto_prune, is_throttled)

    # ── Persist the token transaction ──────────────────────────────────────────
    tx = TokenTransaction(
        department     = req.department,
        agent_id       = req.agent_id,
        model_tier     = result["model_tier"],
        input_tokens   = result["input_tokens"],
        output_tokens  = result["output_tokens"],
        cost_usd       = result["cost_usd"],
        timestamp      = datetime.utcnow(),
        routing_reason = result["routing_decision"],
        was_pruned     = result["was_pruned"],
        tokens_saved   = result["tokens_saved_by_pruning"],
    )
    db.add(tx)

    # ── Update department running spend ────────────────────────────────────────
    if budget:
        budget.current_spend_usd = round(
            budget.current_spend_usd + result["cost_usd"], 6
        )
        # Auto-throttle when cap is reached
        if budget.current_spend_usd >= budget.monthly_cap_usd and not budget.override_granted:
            budget.throttled = True

    db.commit()

    # ── Write audit event for high-stakes decisions ────────────────────────────
    if result["routing_decision"] in ("COMPLEX", "THROTTLED"):
        write_audit_event(
            db               = db,
            event_type       = "ROUTING",
            department       = req.department,
            routing_decision = result["routing_decision"],
            routing_reason   = result["routing_reason"],
            prompt_payload   = req.text[:2000],
            model_tier       = result["model_tier"],
            agent_id         = req.agent_id,
            matched_keywords = result["matched_keywords"],
            cost_usd         = result["cost_usd"],
            decision_outcome = f"{result['model_tier']} model used — ${result['cost_usd']:.6f}",
        )

    # ── Budget stats for the response ──────────────────────────────────────────
    if budget:
        budget_used_pct      = round((budget.current_spend_usd / budget.monthly_cap_usd) * 100, 1)
        budget_remaining_usd = round(budget.monthly_cap_usd - budget.current_spend_usd, 4)
    else:
        budget_used_pct      = 0.0
        budget_remaining_usd = 0.0

    return RouteResponse(
        **result,
        budget_used_pct      = budget_used_pct,
        budget_remaining_usd = budget_remaining_usd,
        was_throttled        = is_throttled,
    )
