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
from database.models import DepartmentBudget, TokenTransaction, RegisteredAgent
from core.router import route
from core.auditor import write_audit_event
from core.keywords import check_terms

router = APIRouter()


class RouteRequest(BaseModel):
    text:                   str
    department:             str  = "Support"
    auto_prune:             bool = True
    agent_id:               Optional[int] = None
    agent_name:             Optional[str] = None   # If provided and agent_id not found, auto-registers the agent
    source_platform:        Optional[str] = None   # e.g. "Salesforce" — inferred from agent name if omitted
    voice_guard_processed:  bool = False           # True = Voice Guard already redacted PII numbers, skip PII keyword block
    min_tokens:             int  = 3               # Skip routing if pruned payload is below this token count (catches truly empty Salesforce on-create fires)
    is_test:                bool = False           # True = Sandbox mode — run pipeline but skip all DB writes (no transaction, no budget impact, no audit)


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
    sensitive_term_triggered:   bool = False
    sensitive_term_action:      Optional[str] = None
    sensitive_term_matches:     List[str] = []


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
    # ── Minimum payload check ─────────────────────────────────────────────────
    # Salesforce Flows often fire on record create before the description is filled in.
    # Prune first to get an accurate token count, then skip if it's too short.
    from core.pruner import prune as _prune, estimate_tokens as _est
    _quick_text = _prune(req.text)["cleaned_text"] if req.auto_prune else req.text
    if _est(_quick_text) < req.min_tokens:
        return RouteResponse(
            department               = req.department,
            complexity               = "SKIPPED",
            routing_decision         = "SKIPPED",
            routing_reason           = f"Payload too short after pruning ({_est(_quick_text)} tokens < {req.min_tokens} minimum) — likely an empty on-create trigger. No AI call made.",
            matched_keywords         = [],
            model_tier               = "none",
            model_name               = "none",
            input_tokens             = 0,
            output_tokens            = 0,
            cost_usd                 = 0.0,
            simulated_response       = "",
            was_pruned               = req.auto_prune,
            tokens_saved_by_pruning  = 0,
            pruning_cost_saved_usd   = 0.0,
            total_cost_without_pruning = 0.0,
            budget_used_pct          = 0.0,
            budget_remaining_usd     = 0.0,
            was_throttled            = False,
        )

    # Check throttle status from the department budget table
    budget       = db.query(DepartmentBudget).filter_by(department=req.department).first()
    is_throttled   = budget.throttled    if budget else False
    throttle_tier  = getattr(budget, "throttle_tier", 1) or 1  if budget else 1

    # ── Sensitive term check ───────────────────────────────────────────────────
    # If Voice Guard already processed this transcript, skip PII category terms —
    # the actual numbers are already redacted; only context words remain.
    term_result = check_terms(db, req.text, req.department,
                              skip_pii=req.voice_guard_processed)
    if term_result["triggered"] and term_result["action"] == "block":
        # Write audit event and reject immediately
        write_audit_event(
            db               = db,
            event_type       = "DECISION",
            department       = req.department,
            routing_decision = "BLOCKED",
            routing_reason   = f"Sensitive term blocked: '{term_result['top_match']['term']}' ({term_result['top_match']['category']})",
            prompt_payload   = req.text[:2000],
            model_tier       = "none",
            agent_id         = req.agent_id,
            matched_keywords = [m["term"] for m in term_result["matches"]],
            cost_usd         = 0.0,
            decision_outcome = "Request blocked by sensitive term policy",
        )
        from fastapi import HTTPException
        raise HTTPException(
            status_code=451,
            detail={
                "error":   "BLOCKED",
                "reason":  f"Request contains a blocked sensitive term: '{term_result['top_match']['term']}'",
                "category": term_result["top_match"]["category"],
                "matches": [m["term"] for m in term_result["matches"]],
            }
        )

    # If escalate: force COMPLEX routing regardless of content score
    force_complex = term_result["triggered"] and term_result["action"] == "escalate"

    # ── Resolve or auto-register agent ────────────────────────────────────────
    agent = None
    if req.agent_id:
        agent = db.query(RegisteredAgent).filter_by(id=req.agent_id).first()

    # If agent_id not found but agent_name provided, look up by name
    if not agent and req.agent_name:
        agent = db.query(RegisteredAgent).filter_by(name=req.agent_name).first()

    # If still not found and we have a name, auto-register the agent
    if not agent and req.agent_name:
        from core.agentlake import infer_platform
        agent = RegisteredAgent(
            name             = req.agent_name,
            department       = req.department,
            source_platform  = infer_platform(req.agent_name, req.source_platform),
            permissions      = "read,write",
            target_table     = "tickets",
            collision_policy = "lock",
            status           = "idle",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

    if agent:
        agent.status       = "active"
        agent.last_used_at = datetime.utcnow()
        db.commit()

    # Run the routing pipeline
    result = route(req.text, req.department, db=db, auto_prune=req.auto_prune, is_throttled=is_throttled, throttle_tier=throttle_tier, force_complex=force_complex)

    if not req.is_test:
        # ── Persist the token transaction ──────────────────────────────────────
        from core.agentlake import infer_platform
        tx = TokenTransaction(
            department      = req.department,
            source_platform = agent.source_platform if agent else infer_platform(req.agent_name or "", req.source_platform),
            agent_id        = agent.id if agent else req.agent_id,
            model_tier      = result["model_tier"],
            input_tokens   = result["input_tokens"],
            output_tokens  = result["output_tokens"],
            cost_usd       = result["cost_usd"],
            timestamp      = datetime.utcnow(),
            routing_reason = result["routing_decision"],
            was_pruned     = result["was_pruned"],
            tokens_saved   = result["tokens_saved_by_pruning"],
        )
        db.add(tx)

        # ── Update department running spend ────────────────────────────────────
        if budget:
            budget.current_spend_usd = round(
                budget.current_spend_usd + result["cost_usd"], 6
            )
            if budget.current_spend_usd >= budget.monthly_cap_usd and not budget.override_granted:
                budget.throttled = True

        # ── Set agent back to idle after routing ──────────────────────────────
        if agent:
            agent.status = "idle"
            db.commit()
        else:
            db.commit()

        # ── Write audit event for EVERY routing decision ──────────────────────
        # Every call is recorded — including routine Scout — so if PII slips
        # past the keyword filter, we have the full payload and can prove
        # exactly what was sent to the model, when, and by which agent.
        all_matched = result["matched_keywords"] + [m["term"] for m in term_result.get("matches", [])]
        try:
            write_audit_event(
                db               = db,
                event_type       = "ROUTING",
                department       = req.department,
                routing_decision = result["routing_decision"],
                routing_reason   = (
                    f"[SENSITIVE TERM: {term_result['top_match']['term']} → {term_result['action']}] "
                    + result["routing_reason"]
                ) if term_result["triggered"] else result["routing_reason"],
                prompt_payload   = req.text[:2000],
                model_tier       = result["model_tier"],
                agent_id         = req.agent_id,
                matched_keywords = all_matched,
                cost_usd         = result["cost_usd"],
                decision_outcome = f"{result['model_tier']} model used — ${result['cost_usd']:.6f}",
                tokens_saved     = result.get("tokens_saved_by_pruning", 0),
                raw_tokens       = result.get("tokens_saved_by_pruning", 0) + result.get("input_tokens", 0),
                clean_tokens     = result.get("input_tokens", 0),
            )
        except Exception:
            pass  # Never let audit write failure break the routing response
    else:
        # Sandbox mode — no DB writes, just reset agent status in memory if needed
        if agent:
            agent.status = "idle"
            db.commit()

    # ── Budget stats for the response ──────────────────────────────────────────
    if budget and budget.monthly_cap_usd > 0:
        budget_used_pct      = round((budget.current_spend_usd / budget.monthly_cap_usd) * 100, 1)
        budget_remaining_usd = round(budget.monthly_cap_usd - budget.current_spend_usd, 4)
    else:
        budget_used_pct      = 0.0
        budget_remaining_usd = 0.0

    return RouteResponse(
        **result,
        budget_used_pct           = budget_used_pct,
        budget_remaining_usd      = budget_remaining_usd,
        was_throttled             = is_throttled,
        sensitive_term_triggered  = term_result["triggered"],
        sensitive_term_action     = term_result.get("action"),
        sensitive_term_matches    = [m["term"] for m in term_result.get("matches", [])],
    )
