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

from database.models import AuditEvent, DepartmentBudget
from config import AUDIT_LOG_DIR, AUDIT_LOG_FILENAME


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
    if event_type == "LOCK":
        return "high"
    if kw_set & high_keywords or routing_decision == "THROTTLED":
        return "high"
    if routing_decision == "COMPLEX":
        return "medium"
    return "low"


# ─────────────────────────────────────────────────────────────────────────────
# Context snapshot builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_context_snapshot(db: Session, department: str) -> dict:
    budget = db.query(DepartmentBudget).filter_by(department=department).first()
    return {
        "captured_at":        datetime.utcnow().isoformat(),
        "department":         department,
        "budget_cap_usd":     budget.monthly_cap_usd   if budget else None,
        "budget_spent_usd":   round(budget.current_spend_usd, 4) if budget else None,
        "budget_used_pct":    round((budget.current_spend_usd / budget.monthly_cap_usd) * 100, 1)
                              if budget else None,
        "throttled":          budget.throttled          if budget else None,
        "override_granted":   budget.override_granted   if budget else None,
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
    kw_str   = ", ".join(f'"{k}"' for k in matched_keywords) if matched_keywords else "none"
    spent    = _fmt(context.get("budget_spent_usd"), ".4f")
    cap      = _fmt(context.get("budget_cap_usd"),   ".2f")
    used_pct = _fmt(context.get("budget_used_pct"),  ".1f")

    if event_type == "LOCK":
        return (
            f"CONCURRENCY LOCK TRIGGERED. Two agents simultaneously attempted to write "
            f"the same database record in the {department} department. "
            f"The Traffic Cop locked both agents to prevent data corruption. "
            f"No data was written. A supervisor must manually release the locks after reviewing "
            f"which agent should proceed first."
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

    if routing_decision == "OVERRIDE":
        return (
            f"SUPERVISOR OVERRIDE GRANTED for {department} department. "
            f"A human supervisor has manually cleared the budget throttle, "
            f"restoring flagship model access. Budget remains at "
            f"{used_pct}% (${spent} spent). "
            f"This action is logged for compliance review."
        )

    # ROUTINE — Scout tier. Every call logged for compliance.
    tier_label = model_tier or "Scout"
    return (
        f"ROUTINE CALL — {tier_label} tier selected for {department} department. "
        f"Routing analysis: {routing_reason}. "
        f"No high-risk keywords detected (checked against: {kw_str if matched_keywords else 'none matched'}). "
        f"Department budget at time of call: {used_pct}% used "
        f"(${spent} of ${cap} monthly cap) — within threshold, no throttle applied. "
        f"Call cost: ${cost_usd:.6f}. "
        f"Full payload retained in audit record. If PII is found in this payload, "
        f"this record is the evidence that it reached the {tier_label} model."
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
    usage_source:     str       = None,
    raw_payload:      str       = None,   # original text before pruning (None = not logged)
) -> dict:
    if matched_keywords is None:
        matched_keywords = []

    context    = _build_context_snapshot(db, department)

    # Attach pruning stats to the context snapshot
    if tokens_saved or raw_tokens or clean_tokens:
        context["raw_tokens"]    = raw_tokens
        context["clean_tokens"]  = clean_tokens
        context["tokens_saved"]  = tokens_saved
        context["compression_pct"] = round((tokens_saved / raw_tokens) * 100, 1) if raw_tokens > 0 else 0.0
    if usage_source:
        context["usage_source"] = usage_source
    risk_level = classify_risk(event_type, routing_decision, matched_keywords)
    rationale  = _build_rationale(
        event_type, routing_decision, routing_reason,
        model_tier, department, matched_keywords, cost_usd, context,
    )
    now = datetime.utcnow()

    event = AuditEvent(
        event_type       = event_type,
        agent_id         = agent_id,
        department       = department,
        model_tier       = model_tier,
        context_snapshot = json.dumps(context),
        prompt_payload   = prompt_payload[:2000],
        raw_payload      = raw_payload[:5000] if raw_payload else None,
        raw_logged_at    = now if raw_payload else None,
        matched_keywords_json = json.dumps(matched_keywords or []),
        rationale        = rationale,
        decision_outcome = decision_outcome,
        risk_level       = risk_level,
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
        "model_tier":       model_tier,
        "routing_decision": routing_decision,
        "risk_level":       risk_level,
        "decision_outcome": decision_outcome,
        "cost_usd":         cost_usd,
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
        "model_tier":       model_tier,
        "risk_level":       risk_level,
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
        "tokens_saved":     context.get("tokens_saved", 0) or 0,
        "compression_pct":  context.get("compression_pct"),
        "usage_source":     context.get("usage_source"),
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
