"""
core/auditor.py — AI Decision Auditor / Black Box Recorder  [Step 6]

Every AI routing decision gets written to two places simultaneously:
  1. The audit_events DB table (queryable, filterable)
  2. An append-only JSONL flat file (fage_audit.jsonl) — simulates immutability

Every call is audited — including routine Scout calls — because:
  - PII may slip past keyword filters and reach the model
  - Without the payload on record, you cannot prove what data was exposed
  - GDPR Article 33 requires knowing exactly what data was compromised
  - Regulators and auditors want the complete picture, not just flagged events

Each record captures:
  - Frozen system context snapshot (dept budget state at time of decision)
  - The exact prompt payload sent to the model (encrypted at rest in production)
  - A plain-English rationale explaining why this model was chosen
  - Risk level classification (low / medium / high / critical)
  - The outcome
"""

import os
import json
import re
from datetime import datetime
from sqlalchemy.orm import Session

from database.models import AuditEvent
from config import AUDIT_LOG_DIR, AUDIT_LOG_FILENAME
from core.budget import effective_budget_context


# ─────────────────────────────────────────────────────────────────────────────
# Risk classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_risk(event_type: str, routing_decision: str, matched_keywords: list) -> str:
    critical_keywords = {"lawsuit", "fraud", "breach", "gdpr", "hipaa", "regulatory",
                         "ssn", "social security", "credit card", "card number",
                         "passport", "date of birth", "bank account", "routing number"}
    high_keywords     = {"legal", "compliance", "audit", "contract", "escalate",
                         "termination", "harassment", "discrimination"}

    kw_set = set(k.lower() for k in matched_keywords)

    if routing_decision == "BLOCKED":
        return "critical"   # blocked PII/sensitive data is always critical
    if kw_set & critical_keywords:
        return "critical"
    if event_type in ("LOCK", "COLLISION_LOCK"):
        return "high"
    if event_type in ("COLLISION_QUEUE", "COLLISION_SKIP"):
        return "medium"
    if kw_set & high_keywords or routing_decision == "THROTTLED":
        return "high"
    if routing_decision == "COMPLEX":
        return "medium"
    return "low"


# ─────────────────────────────────────────────────────────────────────────────
# Context snapshot builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_context_snapshot(db: Session, department: str) -> dict:
    budget = effective_budget_context(db, department)
    return {
        "captured_at":        datetime.utcnow().isoformat(),
        "department":         department,
        "budget_cap_usd":     budget.get("budget_cap_usd") if budget else None,
        "budget_spent_usd":   budget.get("budget_spent_usd") if budget else None,
        "budget_used_pct":    budget.get("budget_used_pct") if budget else None,
        "throttled":          budget.get("throttled") if budget else None,
        "override_granted":   budget.get("override_granted") if budget else None,
        "raw_retention_days": budget.get("raw_retention_days") if budget else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rationale generator
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val, spec="", fallback="N/A"):
    """Safely format a value that may be None."""
    if val is None:
        return fallback
    try:
        return format(val, spec)
    except (TypeError, ValueError):
        return str(val)


def _build_rationale(
    event_type:       str,
    routing_decision: str,
    routing_reason:   str,
    model_tier:       str,
    department:       str,
    matched_keywords: list,
    cost_usd:         float,
    context:          dict,
) -> str:
    # Workspace-qualified departments are internal routing keys. Audit
    # rationale is user-facing, so show only the business department name.
    department = (department or "Unknown").split(":")[-1].strip()
    kw_str   = ", ".join(f'"{k}"' for k in matched_keywords) if matched_keywords else "none"
    spent    = _fmt(context.get("budget_spent_usd"), ".4f")
    cap      = _fmt(context.get("budget_cap_usd"),   ".2f")
    used_pct = _fmt(context.get("budget_used_pct"),  ".1f")

    if event_type in ("LOCK", "COLLISION_LOCK"):
        return (
            f"CONCURRENCY LOCK TRIGGERED. Two agents simultaneously attempted to write "
            f"the same database record in the {department} department. "
            f"The Traffic Cop locked both agents to prevent data corruption. "
            f"No data was written. A supervisor must manually release the locks after reviewing "
            f"which agent should proceed first."
        )

    if event_type == "COLLISION_QUEUE":
        return (
            f"CONCURRENCY COLLISION QUEUED. Two agents simultaneously attempted to write "
            f"the same database record in the {department} department. "
            f"The requesting agent was placed in the queue without interrupting the agent "
            f"that already held the record. The queued operation requires a later release "
            f"or retry before it can proceed. Collision detail: {routing_reason}"
        )

    if event_type == "COLLISION_SKIP":
        return (
            f"CONCURRENCY COLLISION SKIPPED. Two agents simultaneously attempted to write "
            f"the same database record in the {department} department. "
            f"The requesting agent abandoned its operation, while the agent that already "
            f"held the record continued without interruption. Collision detail: {routing_reason}"
        )

    if routing_decision == "THROTTLED":
        return (
            f"BUDGET CAP ENFORCED. The {department} department reached {used_pct}% "
            f"of its ${cap} monthly cap (current spend: ${spent}). "
            f"The payload was classified as COMPLEX (trigger: {routing_reason}), but the flagship "
            f"model was blocked. Request was downgraded to the micro-model tier to prevent "
            f"budget overrun. Estimated cost: ${cost_usd:.6f}. "
            f"A supervisor override is required to restore flagship access."
        )

    if routing_decision == "BLOCKED":
        return (
            f"REQUEST BLOCKED — SENSITIVE DATA DETECTED. The payload submitted by the "
            f"{department} department was rejected before reaching any AI model. "
            f"Trigger: {routing_reason}. "
            f"Matched sensitive terms: {kw_str}. "
            f"No tokens were consumed. No data was sent to OpenAI or any external provider. "
            f"This event is logged for compliance review. "
            f"If this block was in error, review the sensitive term library in CostPilot Setup."
        )

    if routing_decision == "COMPLEX":
        return (
            f"FLAGSHIP MODEL INVOKED. Payload routed to the premium model tier "
            f"after complexity analysis for the {department} department. "
            f"Trigger: {routing_reason}. "
            f"High-risk keywords detected: {kw_str}. "
            f"Budget position at time of decision: {used_pct}% used "
            f"(${spent} of ${cap} cap). "
            f"Call cost: ${cost_usd:.6f}. "
            f"Decision: flagship routing is warranted given the signals present."
        )

    if routing_decision == "TIER_OVERRIDE":
        return (
            f"MODEL TIER OVERRIDE — {model_tier or 'requested tier'} explicitly selected "
            f"for {department}. Trigger: {routing_reason}. "
            f"Budget snapshot: {used_pct}% used (${spent} of ${cap}). "
            f"No supervisor budget override occurred. "
            f"Call cost: ${cost_usd:.6f}. This routing instruction is retained for review."
        )

    if routing_decision == "BUDGET_OVERRIDE":
        return (
            f"SUPERVISOR OVERRIDE GRANTED for {department} department. "
            f"A human supervisor has manually cleared the budget throttle, "
            f"restoring flagship model access. Budget remains at "
            f"{used_pct}% (${spent} spent). "
            f"This action is logged for compliance review."
        )

    if routing_decision == "BUDGET_OVERRIDE_REVOKED":
        return (
            f"SUPERVISOR OVERRIDE REVOKED for {department} department. "
            f"Human-authorized access beyond the configured budget throttle was removed. "
            f"Budget remains at {used_pct}% (${spent} spent). "
            f"Future requests will follow the department throttle policy."
        )

    # ROUTINE — keep the default view concise. The context snapshot retains
    # the complete budget, routing, retention, and evidence fields.
    tier_label = model_tier or "Scout"
    return (
        f"ROUTINE REQUEST — {tier_label} selected for {department}. "
        f"{routing_reason}. "
        f"No high-risk indicators detected. "
        f"Budget: {used_pct}% used (${spent} of ${cap}). "
        f"Control: no throttling applied. "
        f"Call cost: ${cost_usd:.6f}. "
        f"Audit evidence retained according to the configured retention policy."
    )


# ─────────────────────────────────────────────────────────────────────────────
# JSONL file writer (simulates immutable black-box)
# ─────────────────────────────────────────────────────────────────────────────

def _write_to_file(record: dict):
    log_path = os.path.join(
        os.path.dirname(__file__), "..", AUDIT_LOG_DIR, AUDIT_LOG_FILENAME
    )
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main public functions
# ─────────────────────────────────────────────────────────────────────────────

def write_audit_event(
    db:               Session,
    event_type:       str,
    department:       str,
    routing_decision: str,
    routing_reason:   str,
    prompt_payload:   str,
    model_tier:       str       = "micro",
    agent_id:         int       = None,
    matched_keywords: list      = None,
    cost_usd:         float     = 0.0,
    decision_outcome: str       = "",
    tokens_saved:     int       = 0,
    raw_tokens:       int       = 0,
    clean_tokens:     int       = 0,
    input_tokens:     int       = None,
    output_tokens:    int       = None,
    usage_source:     str       = None,
    model_name:       str       = None,
    resolved_model_tier: str    = None,
    model_source:     str       = None,
    routing_cascaded: bool      = False,
    raw_payload:      str       = None,   # original text before pruning (None = not logged)
    work_item                    = None,   # optional WorkItem ORM record
    work_user                    = None,   # optional human WorkUser ORM record
    origin_record_id: str        = None,
    origin_record_type: str      = None,
    origin_record_name: str      = None,
    is_simulation:    bool       = False,
    attribution:      dict       = None,
) -> dict:
    if matched_keywords is None:
        matched_keywords = []

    context    = _build_context_snapshot(db, department)
    context["is_simulation"] = bool(is_simulation)
    attribution = attribution or {}
    context["organizational_attribution"] = attribution

    # Attach pruning stats to the context snapshot
    if tokens_saved or raw_tokens or clean_tokens:
        context["raw_tokens"]    = raw_tokens
        context["clean_tokens"]  = clean_tokens
        context["tokens_saved"]  = tokens_saved
        context["compression_pct"] = round((tokens_saved / raw_tokens) * 100, 1) if raw_tokens > 0 else 0.0
    if input_tokens is not None:
        context["input_tokens"] = input_tokens
    if output_tokens is not None:
        context["output_tokens"] = output_tokens
    if usage_source:
        context["usage_source"] = usage_source
    if model_name:
        context["model_name"] = model_name
    if resolved_model_tier:
        context["resolved_model_tier"] = resolved_model_tier
    if model_source:
        context["model_source"] = model_source
    context["routing_cascaded"] = bool(routing_cascaded)
    if work_item:
        context["work_item_id"] = work_item.external_id
        context["work_item_name"] = work_item.name
        context["work_item_internal_id"] = work_item.id
    if origin_record_id:
        context["origin_record_id"] = origin_record_id
        context["origin_record_type"] = origin_record_type
        context["origin_record_name"] = origin_record_name
    if work_user:
        context["actor_user_id"] = work_user.id
        context["actor_external_id"] = work_user.external_id
        context["actor_name"] = work_user.name
        context["actor_email"] = work_user.email
        context["actor_source_platform"] = work_user.source_platform
    risk_level = classify_risk(event_type, routing_decision, matched_keywords)
    rationale  = _build_rationale(
        event_type, routing_decision, routing_reason,
        model_tier, department, matched_keywords, cost_usd, context,
    )
    now = datetime.utcnow()

    event = AuditEvent(
        event_type       = event_type,
        agent_id         = agent_id,
        work_item_id     = work_item.id if work_item else None,
        work_user_id     = work_user.id if work_user else None,
        origin_record_id = origin_record_id,
        origin_record_type = origin_record_type,
        origin_record_name = origin_record_name,
        actor_external_id = work_user.external_id if work_user else None,
        actor_name       = work_user.name if work_user else None,
        actor_email      = work_user.email if work_user else None,
        actor_source_platform = work_user.source_platform if work_user else None,
        workspace_id       = attribution.get("workspace_id"),
        actor_org_unit_id  = attribution.get("actor_org_unit_id"),
        actor_org_unit_name = attribution.get("actor_org_unit_name"),
        agent_org_unit_id  = attribution.get("agent_org_unit_id"),
        agent_org_unit_name = attribution.get("agent_org_unit_name"),
        work_org_unit_id   = attribution.get("work_org_unit_id"),
        work_org_unit_name = attribution.get("work_org_unit_name"),
        charged_org_unit_id = attribution.get("charged_org_unit_id"),
        charged_org_unit_name = attribution.get("charged_org_unit_name"),
        attribution_source = attribution.get("attribution_source"),
        attribution_confidence = attribution.get("attribution_confidence"),
        department       = department,
        model_tier       = model_tier,
        context_snapshot = json.dumps(context),
        prompt_payload   = prompt_payload[:2000],
        raw_payload      = raw_payload[:5000] if raw_payload else None,
        raw_logged_at    = now if raw_payload else None,
        matched_keywords_json = json.dumps(matched_keywords or []),
        rationale        = rationale,
        decision_outcome = decision_outcome,
        cost_usd          = cost_usd,
        risk_level       = risk_level,
        is_simulation    = bool(is_simulation),
        timestamp        = now,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    file_record = {
        "audit_id":         event.id,
        "timestamp":        now.isoformat(),
        "event_type":       event_type,
        "department":       department,
        "agent_id":         agent_id,
        "work_item_id":     work_item.external_id if work_item else None,
        "work_item_name":   work_item.name if work_item else None,
        "origin_record_id": origin_record_id,
        "origin_record_type": origin_record_type,
        "origin_record_name": origin_record_name,
        "actor_external_id": work_user.external_id if work_user else None,
        "actor_name":       work_user.name if work_user else None,
        "actor_email":      work_user.email if work_user else None,
        "actor_source_platform": work_user.source_platform if work_user else None,
        "model_tier":       model_tier,
        "routing_decision": routing_decision,
        "risk_level":       risk_level,
        "decision_outcome": decision_outcome,
        "cost_usd":         cost_usd,
        "is_simulation":    bool(is_simulation),
        "matched_keywords": matched_keywords,
        "context_snapshot": context,
        "prompt_payload":   prompt_payload[:2000],
        "rationale":        rationale,
    }
    _write_to_file(file_record)

    return {
        "id":               event.id,
        "event_type":       event_type,
        "department":       department,
        "work_item_id":     work_item.external_id if work_item else None,
        "work_item_name":   work_item.name if work_item else None,
        "actor_external_id": work_user.external_id if work_user else None,
        "actor_name":       work_user.name if work_user else None,
        "model_tier":       model_tier,
        "risk_level":       risk_level,
        "is_simulation":    bool(is_simulation),
        "decision_outcome": decision_outcome,
        "rationale":        rationale,
        "timestamp":        now.isoformat(),
    }


def get_audit_events(db: Session, limit: int = 50, workspace_id: str = None) -> list:
    q = db.query(AuditEvent)
    if workspace_id:
        q = q.filter(AuditEvent.department.like(f"{workspace_id}:%"))
    events = q.order_by(AuditEvent.timestamp.desc()).limit(limit).all()
    return [_serialize(e) for e in events]


def get_audit_event(db: Session, event_id: int):
    e = db.query(AuditEvent).filter_by(id=event_id).first()
    return _serialize(e, full=True) if e else None


def export_jsonl_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", AUDIT_LOG_DIR, AUDIT_LOG_FILENAME)
    )


def _extract_cost_usd(e: AuditEvent):
    cost = getattr(e, "cost_usd", None)
    if cost is not None:
        return cost
    # Budget-control events do not consume model tokens. Older rows predate
    # the explicit cost column, so do not mistake budget snapshot dollars for
    # an AI-call cost.
    if getattr(e, "event_type", None) == "BUDGET":
        return 0.0

    for text in (getattr(e, "decision_outcome", None), getattr(e, "rationale", None)):
        if not text:
            continue
        match = re.search(r"(?:Call cost:|Estimated cost:|model used —)\s*\$([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
        if not match:
            match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def _display_department(department: str) -> str:
    if not department:
        return department
    prefix, sep, rest = department.partition(":")
    if sep and len(prefix) >= 8 and rest:
        return rest
    return department


def _serialize(e: AuditEvent, full: bool = False) -> dict:
    # Parse matched keywords — stored as JSON array, available on list and detail views
    try:
        kw_json = getattr(e, "matched_keywords_json", None)
        matched_kws = json.loads(kw_json) if kw_json else []
    except Exception:
        matched_kws = []

    try:
        context = json.loads(e.context_snapshot or "{}")
    except Exception:
        context = {}

    agent = getattr(e, "agent", None)
    work_item = getattr(e, "work_item", None)
    cost_usd = _extract_cost_usd(e)
    display_dept = _display_department(e.department)
    display_agent = None
    rationale_text = (getattr(e, "rationale", None) or "").lower()
    outcome_text = (getattr(e, "decision_outcome", None) or "").lower()
    budget_controlled = (
        e.event_type == "THROTTLE"
        or "budget cap enforced" in rationale_text
        or "budget cap" in outcome_text
        or "capped at" in rationale_text
        or "downgraded to the micro-model tier" in rationale_text
    )
    if agent:
        try:
            from core.agentlake import display_agent_name
            display_agent = display_agent_name(agent.name, agent.department, agent.source_platform)
        except Exception:
            display_agent = agent.name

    base = {
        "id":               e.id,
        "event_type":       e.event_type,
        "department":       display_dept,
        "display_department": display_dept,
        "source_department": e.department,
        "agent_id":         e.agent_id,
        "agent_name":       agent.name if agent else None,
        "display_agent_name": display_agent,
        "source_platform":  agent.source_platform if agent else None,
        "work_item_id":     work_item.external_id if work_item else context.get("work_item_id"),
        "work_item_name":   work_item.name if work_item else context.get("work_item_name"),
        "actor_user_id":    getattr(e, "work_user_id", None),
        "actor_external_id": getattr(e, "actor_external_id", None) or context.get("actor_external_id"),
        "actor_name":       getattr(e, "actor_name", None) or context.get("actor_name"),
        "actor_email":      getattr(e, "actor_email", None) or context.get("actor_email"),
        "actor_source_platform": getattr(e, "actor_source_platform", None) or context.get("actor_source_platform"),
        "model_tier":       e.model_tier,
        "risk_level":       e.risk_level,
        "decision_outcome": e.decision_outcome,
        "cost_usd":         cost_usd,
        "timestamp":        e.timestamp.isoformat() if e.timestamp else None,
        "has_raw_payload":  bool(getattr(e, "raw_payload", None)),
        "budget_controlled": budget_controlled,
        "matched_keywords": matched_kws,
        "raw_tokens":       context.get("raw_tokens"),
        "clean_tokens":     context.get("clean_tokens"),
        "input_tokens":     context.get("input_tokens"),
        "output_tokens":    context.get("output_tokens"),
        "tokens_saved":     context.get("tokens_saved", 0) or 0,
        "compression_pct":  context.get("compression_pct"),
        "usage_source":     context.get("usage_source"),
        "is_simulation":    bool(getattr(e, "is_simulation", False) or context.get("is_simulation", False)),
    }
    if full:
        raw = getattr(e, "raw_payload", None)
        raw_logged_at = getattr(e, "raw_logged_at", None)
        if raw and raw_logged_at:
            retention_days = 30
            try:
                retention_days = context.get("raw_retention_days", 30) or 30
            except Exception:
                pass
            if retention_days > 0:
                age_days = (datetime.utcnow() - raw_logged_at).days
                if age_days > retention_days:
                    raw = None
        base.update({
            "rationale":        e.rationale,
            "prompt_payload":   e.prompt_payload,
            "raw_payload":      raw,
            "raw_logged_at":    raw_logged_at.isoformat() if raw_logged_at else None,
            "context_snapshot": e.context_snapshot,
        })
    return base
