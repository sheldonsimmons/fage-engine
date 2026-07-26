"""
api/routes_router.py — Token Router & Model Cascader API routes  [Step 3]

POST /api/route
  Accepts a raw text payload, runs it through the full routing pipeline
  (prune → score → select model → simulate call), records the transaction
  in the database, and updates the department's running spend total.
"""

import re
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    DepartmentBudget,
    RegisteredAgent,
    TokenTransaction,
    WorkItem,
    WorkItemUser,
    WorkUser,
)
from core.router import route
from core.auditor import write_audit_event
from core.keywords import check_terms
from core.budget import effective_budget_context, reconcile_throttle_state

router = APIRouter()


class UniversalSourceContext(BaseModel):
    platform: str
    workspace_id: str
    agent_name: Optional[str] = None
    agent_id: Optional[int] = None
    department: Optional[str] = None


class UniversalActorContext(BaseModel):
    external_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    can_use_ai: Optional[bool] = None


class UniversalWorkContext(BaseModel):
    external_id: str
    type: str = "project"
    name: Optional[str] = None
    sync_if_missing: bool = False


class UniversalRequestContext(BaseModel):
    content: str
    task: Optional[str] = None
    payload_type: str = "text"
    auto_prune: bool = True


class RouteRequest(BaseModel):
    text:                   Optional[str] = None
    department:             str  = "Support"
    auto_prune:             bool = True
    agent_id:               Optional[int] = None
    agent_name:             Optional[str] = None   # If provided and agent_id not found, auto-registers the agent
    source_platform:        Optional[str] = None   # e.g. "Salesforce" — inferred from agent name if omitted
    voice_guard_processed:  bool = False           # True = Voice Guard already redacted PII numbers, skip PII keyword block
    min_tokens:             int  = 3               # Skip routing if pruned payload is below this token count (catches truly empty Salesforce on-create fires)
    is_test:                bool = False           # True = Sandbox mode — run pipeline but skip all DB writes (no transaction, no budget impact, no audit)
    synthetic_simulation:   bool = False           # Traffic simulator only: persist governed activity without waiting on a live provider response
    payload_type:           str  = "text"          # "text" = full pruning pipeline | "code" = skip pruner, secrets detection only | "transcript" = voice guard path
    work_item_id:           Optional[str] = None    # Public project/matter/engagement ID; optional for backward compatibility
    actor_external_id:      Optional[str] = None    # Human identity in the source platform
    actor_name:             Optional[str] = None
    actor_email:            Optional[str] = None
    actor_source_platform:  Optional[str] = None
    actor_workspace_id:     Optional[str] = None
    actor_role:             Optional[str] = None
    actor_status:           Optional[str] = None
    actor_can_use_ai:       Optional[bool] = None
    enforce_project_membership: bool = False
    contract_version:       Optional[str] = None
    mode:                   str = "control"
    source_context:         Optional[UniversalSourceContext] = Field(default=None, alias="source")
    actor_context:          Optional[UniversalActorContext] = Field(default=None, alias="actor")
    work_context:           Optional[UniversalWorkContext] = Field(default=None, alias="work")
    request_context:        Optional[UniversalRequestContext] = Field(default=None, alias="request")

    class Config:
        populate_by_name = True


def _normalize_universal_request(req: RouteRequest) -> RouteRequest:
    """Translate the universal envelope into the existing flat routing inputs."""
    if req.mode != "control":
        raise HTTPException(
            status_code=400,
            detail="POST /api/route supports mode='control'. Observe-mode ingestion is not available yet.",
        )
    if req.source_context:
        req.source_platform = req.source_context.platform
        req.actor_workspace_id = req.source_context.workspace_id
        if req.source_context.agent_name:
            req.agent_name = req.source_context.agent_name
        if req.source_context.agent_id is not None:
            req.agent_id = req.source_context.agent_id
        if req.source_context.department:
            req.department = req.source_context.department
    if req.actor_context:
        req.actor_external_id = req.actor_context.external_id
        req.actor_name = req.actor_context.name
        req.actor_email = req.actor_context.email
        req.actor_source_platform = req.source_platform
        req.actor_role = req.actor_context.role
        req.actor_status = req.actor_context.status
        req.actor_can_use_ai = req.actor_context.can_use_ai
    if req.request_context:
        req.text = req.request_context.content
        req.payload_type = req.request_context.payload_type
        req.auto_prune = req.request_context.auto_prune
    if not (req.text or "").strip():
        raise HTTPException(status_code=422, detail="A non-empty text or request.content value is required")
    return req


def _canonical_work_external_id(workspace_id: str, platform: str, source_record_id: str) -> str:
    raw = f"{workspace_id}:{platform}:{source_record_id}"
    return re.sub(r"[^A-Za-z0-9._:-]+", "-", raw).strip("-")[:240]


def _resolve_work_item(db: Session, req: RouteRequest, department: str) -> Optional[WorkItem]:
    """Resolve a legacy work ID or sync a universal platform work record."""
    if not req.work_item_id and not req.work_context:
        return None

    if req.work_context:
        work = req.work_context
        platform = (req.source_platform or "Custom").strip() or "Custom"
        workspace_id = (req.actor_workspace_id or "default").strip() or "default"
        source_record_id = work.external_id.strip()
        item = (
            db.query(WorkItem)
            .filter(
                WorkItem.workspace_id == workspace_id,
                WorkItem.source_platform == platform,
                WorkItem.source_record_id == source_record_id,
            )
            .first()
        )
        if not item:
            canonical_id = _canonical_work_external_id(workspace_id, platform, source_record_id)
            item = db.query(WorkItem).filter(WorkItem.external_id == canonical_id).first()
        if not item and work.sync_if_missing:
            from core.business_context import normalize_context_type
            item = WorkItem(
                external_id=_canonical_work_external_id(workspace_id, platform, source_record_id),
                name=(work.name or f"{work.type.title()} {source_record_id}").strip(),
                department=department,
                status="active",
                source_platform=platform,
                workspace_id=workspace_id,
                context_type=normalize_context_type(work.type),
                context_template=f"{platform.lower()}_{work.type.lower()}",
                source_record_type=work.type,
                source_record_id=source_record_id,
            )
            db.add(item)
            db.commit()
            db.refresh(item)
        if not item:
            raise HTTPException(
                status_code=404,
                detail=f"Work record '{source_record_id}' was not found. Set work.sync_if_missing=true to create it.",
            )
    else:
        public_id = req.work_item_id.strip()
        item = db.query(WorkItem).filter(WorkItem.external_id == public_id).first()
        if not item and public_id.isdigit():
            item = db.query(WorkItem).filter(WorkItem.id == int(public_id)).first()
        if not item:
            raise HTTPException(status_code=404, detail=f"Work item '{public_id}' was not found")

    if item.status != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Work item '{item.external_id}' is {item.status} and cannot receive new requests",
        )
    return item


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
    work_item_id:               Optional[str] = None
    work_item_name:             Optional[str] = None
    provider:                   Optional[str] = None
    model_mode:                 str = "simulated"


def _resolve_department(db: Session, req: RouteRequest) -> str:
    requested = (req.department or "Support").strip() or "Support"
    if db.query(DepartmentBudget).filter_by(department=requested).first():
        return requested

    if req.agent_id:
        agent = db.query(RegisteredAgent).filter_by(id=req.agent_id).first()
        if agent and agent.department:
            return agent.department

    if req.agent_name:
        agent = db.query(RegisteredAgent).filter_by(name=req.agent_name).first()
        if agent and agent.department:
            return agent.department

    return requested


def _resolve_work_user(
    db: Session,
    req: RouteRequest,
    work_item: Optional[WorkItem],
) -> Optional[WorkUser]:
    """Upsert a platform-neutral human identity and optional project membership."""
    external_id = (req.actor_external_id or "").strip()
    if not external_id:
        return None

    from fastapi import HTTPException

    platform = (req.actor_source_platform or req.source_platform or "Custom").strip() or "Custom"
    workspace_id = (
        (req.actor_workspace_id or "").strip()
        or ((work_item.workspace_id or "").strip() if work_item else "")
        or "default"
    )
    work_user = (
        db.query(WorkUser)
        .filter(
            WorkUser.workspace_id == workspace_id,
            WorkUser.source_platform == platform,
            WorkUser.external_id == external_id,
        )
        .first()
    )
    if not work_user:
        work_user = WorkUser(
            workspace_id=workspace_id,
            source_platform=platform,
            external_id=external_id,
            name=(req.actor_name or external_id).strip(),
            email=(req.actor_email or "").strip() or None,
            status="active",
        )
        db.add(work_user)
        db.flush()
    else:
        if req.actor_name:
            work_user.name = req.actor_name.strip()
        if req.actor_email:
            work_user.email = req.actor_email.strip()
    if work_user.status != "active":
        raise HTTPException(status_code=403, detail=f"User {work_user.name} is inactive")

    if work_item:
        membership = (
            db.query(WorkItemUser)
            .filter(
                WorkItemUser.work_item_id == work_item.id,
                WorkItemUser.work_user_id == work_user.id,
            )
            .first()
        )
        if not membership:
            membership = WorkItemUser(
                work_item_id=work_item.id,
                work_user_id=work_user.id,
                role=(req.actor_role or "Member").strip() or "Member",
                status=(req.actor_status or "active").strip().lower(),
                can_use_ai=True if req.actor_can_use_ai is None else req.actor_can_use_ai,
                assigned_by=platform,
            )
            db.add(membership)
        else:
            if req.actor_role:
                membership.role = req.actor_role.strip() or membership.role
            if req.actor_status:
                membership.status = req.actor_status.strip().lower()
            if req.actor_can_use_ai is not None:
                membership.can_use_ai = req.actor_can_use_ai

    db.commit()
    db.refresh(work_user)
    if work_item and req.enforce_project_membership:
        if membership.status != "active":
            raise HTTPException(
                status_code=403,
                detail=f"User {work_user.name} is not an active member of {work_item.name}",
            )
        if not membership.can_use_ai:
            raise HTTPException(
                status_code=403,
                detail=f"User {work_user.name} is not allowed to use AI for {work_item.name}",
            )
    return work_user


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
    req = _normalize_universal_request(req)
    department = _resolve_department(db, req)

    # ── Resolve optional project/matter/engagement context ───────────────────
    work_item = _resolve_work_item(db, req, department)

    work_user = _resolve_work_user(db, req, work_item) if not req.is_test else None

    # ── Resolve or auto-register agent FIRST ─────────────────────────────────
    # Agent resolution runs before pruning so the agent's pruning_enabled
    # setting can override the pruning decision.
    agent = None
    if req.agent_id:
        agent = db.query(RegisteredAgent).filter_by(id=req.agent_id).first()

    if not agent and req.agent_name:
        agent = (
            db.query(RegisteredAgent)
            .filter_by(name=req.agent_name, department=department)
            .first()
        )

    if not agent and req.agent_name:
        agent = db.query(RegisteredAgent).filter_by(name=req.agent_name).first()
        if agent and not req.is_test:
            from core.agentlake import infer_platform
            agent.department = department
            agent.source_platform = infer_platform(req.agent_name, req.source_platform)
            db.commit()

    if not agent and req.agent_name and not req.is_test:
        from core.agentlake import infer_platform
        agent = RegisteredAgent(
            name             = req.agent_name,
            department       = department,
            source_platform  = infer_platform(req.agent_name, req.source_platform),
            permissions      = "read,write",
            target_table     = "tickets",
            collision_policy = "lock",
            status           = "idle",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)

    if agent and not req.is_test:
        agent.status       = "active"
        agent.last_used_at = datetime.utcnow()
        db.commit()

    # ── payload_type gate — code payloads skip the pruner entirely ───────────
    # Agent pruning_enabled=False overrides everything — "off means off".
    # Otherwise: code payloads (Python, JS, SQL, configs) are never pruned.
    from core.pruner import detect_payload_type as _detect_type

    _agent_pruning_off = agent is not None and agent.pruning_enabled is False
    _explicit_code     = req.payload_type == "code"
    _auto_detected_type  = None
    _auto_detect_reason  = None

    if not _agent_pruning_off and not _explicit_code:
        _auto_detected_type, _auto_detect_reason = _detect_type(req.text)
        if _auto_detected_type == "code":
            _explicit_code = True

    _is_code              = _explicit_code
    _effective_auto_prune = req.auto_prune and not _is_code and not _agent_pruning_off

    # ── Sensitive term check ───────────────────────────────────────────────────
    # Run before the short-payload guard so a one-word record like "Legal" still
    # flags/escalates instead of being skipped as empty Salesforce noise.
    term_result = check_terms(db, req.text, department,
                              skip_pii=req.voice_guard_processed)
    if term_result["triggered"] and term_result["action"] == "block":
        write_audit_event(
            db               = db,
            event_type       = "DECISION",
            department       = department,
            routing_decision = "BLOCKED",
            routing_reason   = f"Sensitive term blocked: '{term_result['top_match']['term']}' ({term_result['top_match']['category']})",
            prompt_payload   = req.text[:2000],
            model_tier       = "none",
            agent_id         = agent.id if agent else req.agent_id,
            matched_keywords = [m["term"] for m in term_result["matches"]],
            cost_usd         = 0.0,
            decision_outcome = "Request blocked by sensitive term policy",
            work_item        = work_item,
            work_user        = work_user,
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

    # ── Minimum payload check ─────────────────────────────────────────────────
    from core.pruner import prune as _prune, estimate_tokens as _est
    _quick_text = _prune(req.text)["cleaned_text"] if _effective_auto_prune else req.text
    if _est(_quick_text) < req.min_tokens and not term_result["triggered"]:
        return RouteResponse(
            department               = department,
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
            work_item_id             = work_item.external_id if work_item else None,
            work_item_name           = work_item.name if work_item else None,
        )

    # Check throttle status from the department budget table
    budget       = db.query(DepartmentBudget).filter_by(department=department).first()
    if budget:
        reconcile_throttle_state(budget)
    budget_context = effective_budget_context(db, department)
    is_throttled   = bool(budget_context.get("throttled")) if budget_context else (budget.throttled if budget else False)
    throttle_tier  = (budget_context.get("throttle_tier") if budget_context else (getattr(budget, "throttle_tier", 1) if budget else 1)) or 1

    force_complex = term_result["triggered"] and term_result["action"] == "escalate"

    # Capture raw text before routing (for raw payload logging)
    _raw_text_for_logging = req.text

    # Run the routing pipeline
    result = route(
        req.text,
        department,
        db=db,
        auto_prune=_effective_auto_prune,
        is_throttled=is_throttled,
        throttle_tier=throttle_tier,
        force_complex=force_complex,
        agent_min_tier=agent.min_tier if agent else None,
        agent_max_tier=agent.max_tier if agent else None,
        force_simulated_model=req.synthetic_simulation,
    )

    if not req.is_test:
        # ── Persist the token transaction ──────────────────────────────────────
        from core.agentlake import infer_platform
        tx = TokenTransaction(
            department      = department,
            source_platform = agent.source_platform if agent else infer_platform(req.agent_name or "", req.source_platform),
            agent_id        = agent.id if agent else req.agent_id,
            work_item_id    = work_item.id if work_item else None,
            work_user_id    = work_user.id if work_user else None,
            actor_external_id = work_user.external_id if work_user else None,
            actor_name      = work_user.name if work_user else None,
            actor_email     = work_user.email if work_user else None,
            actor_source_platform = work_user.source_platform if work_user else None,
            model_tier      = result["model_tier"],
            model_name      = result["model_name"],
            resolved_model_tier = result.get("resolved_model_tier", result["model_tier"]),
            model_source    = result.get("model_source"),
            routing_cascaded = result.get("routing_cascaded", False),
            input_tokens   = result["input_tokens"],
            output_tokens  = result["output_tokens"],
            usage_source   = result.get("usage_source", "estimated"),
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
        # Keep "active" visible for 4s so the frontend polling can catch it
        if agent:
            import threading
            db.commit()
            def _reset_agent_status(agent_id):
                import time
                time.sleep(4)
                from database.db import SessionLocal
                from database.models import RegisteredAgent
                _db = SessionLocal()
                try:
                    _a = _db.query(RegisteredAgent).filter_by(id=agent_id).first()
                    if _a and _a.status == "active":
                        _a.status = "idle"
                        _db.commit()
                finally:
                    _db.close()
            threading.Thread(target=_reset_agent_status, args=(agent.id,), daemon=True).start()
        else:
            db.commit()

        # ── Write audit event for EVERY routing decision ──────────────────────
        # Every call is recorded — including routine Scout — so if PII slips
        # past the keyword filter, we have the full payload and can prove
        # exactly what was sent to the model, when, and by which agent.
        all_matched = result["matched_keywords"] + [m["term"] for m in term_result.get("matches", [])]

        # Determine if raw payload should be stored for this department
        _pruning_fired   = result.get("tokens_saved_by_pruning", 0) > 0
        _effective_budget_context = effective_budget_context(db, department) or budget_context or {}
        _raw_logging_on  = _effective_budget_context.get("raw_payload_logging_enabled", getattr(budget, "raw_payload_logging_enabled", False) if budget else False) or False
        _retention_days  = _effective_budget_context.get("raw_retention_days", getattr(budget, "raw_retention_days", 30) if budget else 30) or 30
        _raw_to_store    = _raw_text_for_logging[:5000] if (_pruning_fired and _raw_logging_on) else None

        try:
            write_audit_event(
                db               = db,
                event_type       = "ROUTING",
                department       = department,
                routing_decision = result["routing_decision"],
                routing_reason   = (
                    (f"[CODE LANE — auto-detected: {_auto_detect_reason}] "
                     if (_auto_detected_type == "code")
                     else "[CODE LANE — pruner bypassed (explicit)] ")
                    + (f"[SENSITIVE TERM: {term_result['top_match']['term']} → {term_result['action']}] " if term_result["triggered"] else "")
                    + result["routing_reason"]
                ) if _is_code else (
                    f"[SENSITIVE TERM: {term_result['top_match']['term']} → {term_result['action']}] "
                    + result["routing_reason"]
                ) if term_result["triggered"] else result["routing_reason"],
                prompt_payload   = req.text[:2000],
                model_tier       = result["model_tier"],
                agent_id         = agent.id if agent else req.agent_id,
                matched_keywords = all_matched,
                cost_usd         = result["cost_usd"],
                decision_outcome = f"{result['model_tier']} model used — ${result['cost_usd']:.6f}",
                tokens_saved     = result.get("tokens_saved_by_pruning", 0),
                raw_tokens       = result.get("tokens_saved_by_pruning", 0) + result.get("input_tokens", 0),
                clean_tokens     = result.get("input_tokens", 0),
                input_tokens     = result.get("input_tokens", 0),
                output_tokens    = result.get("output_tokens", 0),
                usage_source     = result.get("usage_source", "estimated"),
                model_name       = result.get("model_name"),
                resolved_model_tier = result.get("resolved_model_tier"),
                model_source     = result.get("model_source"),
                routing_cascaded = result.get("routing_cascaded", False),
                raw_payload      = _raw_to_store,
                work_item        = work_item,
                work_user        = work_user,
            )
        except Exception:
            pass  # Never let audit write failure break the routing response
    else:
        # Sandbox mode — no DB writes, just reset agent status in memory if needed
        if agent:
            agent.status = "idle"
            db.commit()

    # ── Budget stats for the response ──────────────────────────────────────────
    response_budget_context = effective_budget_context(db, department)
    if response_budget_context and response_budget_context.get("budget_cap_usd", 0) > 0:
        budget_used_pct      = response_budget_context.get("budget_used_pct", 0.0)
        budget_remaining_usd = round(
            response_budget_context.get("budget_cap_usd", 0.0)
            - response_budget_context.get("budget_spent_usd", 0.0),
            4,
        )
    elif budget and budget.monthly_cap_usd > 0:
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
        work_item_id              = work_item.external_id if work_item else None,
        work_item_name            = work_item.name if work_item else None,
    )
