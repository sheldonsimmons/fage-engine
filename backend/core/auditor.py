"""
core/auditor.py — AI Decision Auditor / Black Box Recorder  [Step 6]

Every high-stakes AI decision gets written to two places simultaneously:
  1. The audit_events DB table (queryable, filterable)
  2. An append-only JSONL flat file (fage_audit.jsonl) — simulates immutability

What counts as "high-stakes" and gets audited:
  - Any COMPLEX routing decision (flagship model invoked)
  - Any THROTTLED routing decision (budget cap enforced)
  - Any concurrency LOCK event (collision detected)
  - Any supervisor OVERRIDE granted or revoked

Each record captures:
  - Frozen system context snapshot (dept budget state at time of decision)
  - The exact prompt payload sent to the model
  - A plain-English rationale explaining the decision logic
  - Risk level classification (low / medium / high / critical)
  - The outcome
"""

import os
import json
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
            f"If this block was in error, review the sensitive term library in FAGE Setup."
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

    return f"Routine event logged for {department}. Decision: {routing_decision}. Reason: {routing_reason}."


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


def get_audit_events(db: Session, limit: int = 50) -> list:
    events = (
        db.query(AuditEvent)
        .order_by(AuditEvent.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [_serialize(e) for e in events]


def get_audit_event(db: Session, event_id: int):
    e = db.query(AuditEvent).filter_by(id=event_id).first()
    return _serialize(e, full=True) if e else None


def export_jsonl_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", AUDIT_LOG_DIR, AUDIT_LOG_FILENAME)
    )


def _serialize(e: AuditEvent, full: bool = False) -> dict:
    base = {
        "id":               e.id,
        "event_type":       e.event_type,
        "department":       e.department,
        "model_tier":       e.model_tier,
        "risk_level":       e.risk_level,
        "decision_outcome": e.decision_outcome,
        "timestamp":        e.timestamp.isoformat() if e.timestamp else None,
    }
    if full:
        base.update({
            "agent_id":         e.agent_id,
            "rationale":        e.rationale,
            "prompt_payload":   e.prompt_payload,
            "context_snapshot": e.context_snapshot,
        })
    return base
