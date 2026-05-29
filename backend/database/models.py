"""
models.py — All SQLAlchemy ORM table definitions for CostPilot.

Seven tables cover the full POC surface area:
  customers          — mock enterprise CRM contacts
  tickets            — support tickets linked to customers
  crm_records        — key/value CRM fields (the records agents fight over)
  registered_agents  — active AI workers tracked by the Agentlake Registry
  department_budgets — per-department monthly spend caps and throttle state
  token_transactions — every AI call with cost, tier, and pruning stats
  audit_events       — immutable high-stakes decision log (the Black Box)
"""

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base


class Customer(Base):
    """A mock enterprise CRM customer."""
    __tablename__ = "customers"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String,  nullable=False)
    email      = Column(String,  unique=True, nullable=False)
    tier       = Column(String,  default="free")        # free | pro | enterprise
    department = Column(String,  nullable=False)

    tickets     = relationship("Ticket",    back_populates="customer")
    crm_records = relationship("CRMRecord", back_populates="customer")


class Ticket(Base):
    """A customer support ticket — used as the primary test payload source."""
    __tablename__ = "tickets"

    id          = Column(Integer,  primary_key=True, index=True)
    customer_id = Column(Integer,  ForeignKey("customers.id"))
    subject     = Column(String,   nullable=False)
    body        = Column(Text,     nullable=False)
    status      = Column(String,   default="open")      # open | in_progress | closed
    created_at  = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="tickets")


class CRMRecord(Base):
    """
    A single key/value field on a customer's CRM profile.
    These are the shared records that the Agentlake Traffic Cop protects.
    """
    __tablename__ = "crm_records"

    id          = Column(Integer,  primary_key=True, index=True)
    customer_id = Column(Integer,  ForeignKey("customers.id"))
    field_key   = Column(String,   nullable=False)
    field_value = Column(Text,     nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="crm_records")


class RegisteredAgent(Base):
    """
    An active AI digital worker tracked by the Agentlake Registry.
    When two agents target the same record, the Traffic Cop locks both.
    """
    __tablename__ = "registered_agents"

    id               = Column(Integer,  primary_key=True, index=True)
    name             = Column(String,   nullable=False, unique=True)
    department       = Column(String,   nullable=False)
    source_platform  = Column(String,   nullable=True)    # Salesforce | ServiceNow | HubSpot | Custom | etc.
    permissions      = Column(String,   nullable=False)   # e.g. "read,write"
    target_table     = Column(String,   nullable=True)
    target_record_id = Column(Integer,  nullable=True)
    status           = Column(String,   default="idle")   # idle | active | locked | queued
    collision_policy = Column(String,   default="lock")   # lock | queue | skip
    locked_at        = Column(DateTime, nullable=True)
    lock_reason      = Column(String,   nullable=True)
    last_used_at     = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    archived         = Column(Boolean,  nullable=True, default=False)  # soft-delete: hides from live grid, keeps history
    min_tier         = Column(Integer,  nullable=True, default=1)      # floor tier: routing never goes below this (1=Scout)
    max_tier         = Column(Integer,  nullable=True, default=4)      # ceiling tier: routing never goes above this (4=Strategist)
    pruning_enabled  = Column(Boolean,  nullable=True, default=True)   # False = skip context pruner entirely for this agent

    token_transactions = relationship("TokenTransaction", back_populates="agent")
    audit_events       = relationship("AuditEvent",       back_populates="agent")


class DepartmentBudget(Base):
    """
    Monthly AI spending cap for a department.
    Throttled flips to True when current_spend_usd hits the cap.
    """
    __tablename__ = "department_budgets"

    id                = Column(Integer,  primary_key=True, index=True)
    department        = Column(String,   unique=True, nullable=False)
    monthly_cap_usd   = Column(Float,    nullable=False)
    current_spend_usd = Column(Float,    default=0.0)
    period_start      = Column(DateTime, default=datetime.utcnow)
    throttled         = Column(Boolean,  default=False)
    override_granted  = Column(Boolean,  default=False)
    throttle_tier              = Column(Integer,  default=1)      # ceiling tier when throttled (1=Scout … 4=Strategist)
    raw_payload_logging_enabled = Column(Boolean, default=False)  # per-dept raw payload logging toggle
    raw_retention_days          = Column(Integer, default=30)     # 30 | 90 | 180 | 365 | 0=indefinite


class TokenTransaction(Base):
    """
    A single AI model call — records cost, tier, routing reason, and pruning savings.
    This is the source of truth for all financial analytics.
    """
    __tablename__ = "token_transactions"

    id              = Column(Integer,  primary_key=True, index=True)
    department      = Column(String,   nullable=False)
    source_platform = Column(String,   nullable=True)    # Salesforce | ServiceNow | HubSpot | Custom | etc.
    agent_id        = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    model_tier      = Column(String,   nullable=False)    # micro | flagship
    input_tokens   = Column(Integer,  nullable=False)
    output_tokens  = Column(Integer,  nullable=False)
    cost_usd       = Column(Float,    nullable=False)
    timestamp      = Column(DateTime, default=datetime.utcnow)
    routing_reason = Column(String,   nullable=True)     # ROUTINE | COMPLEX | THROTTLED
    was_pruned     = Column(Boolean,  default=False)
    tokens_saved   = Column(Integer,  default=0)

    agent = relationship("RegisteredAgent", back_populates="token_transactions")


class AuditEvent(Base):
    """
    Immutable black-box record for every high-stakes AI decision.
    Written once, never modified. Exportable to compliance/legal.
    """
    __tablename__ = "audit_events"

    id               = Column(Integer,  primary_key=True, index=True)
    event_type       = Column(String,   nullable=False)   # ROUTING | THROTTLE | LOCK | DECISION
    agent_id         = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    department       = Column(String,   nullable=False)
    model_tier       = Column(String,   nullable=True)
    context_snapshot = Column(Text,     nullable=True)    # JSON string — frozen system state
    prompt_payload   = Column(Text,     nullable=True)    # The exact pruned text sent to the model
    raw_payload      = Column(Text,     nullable=True)    # The original text before pruning (stored only when dept has raw logging enabled and pruning fired)
    raw_logged_at    = Column(DateTime, nullable=True)    # When raw payload was captured (for retention expiry check)
    rationale        = Column(Text,     nullable=True)    # Plain-English justification
    decision_outcome = Column(String,   nullable=True)
    risk_level       = Column(String,   default="low")    # low | medium | high | critical
    timestamp        = Column(DateTime, default=datetime.utcnow)

    agent = relationship("RegisteredAgent", back_populates="audit_events")


class ModelRegistry(Base):
    """
    Company-registered AI models with tier classification and cost rates.
    Tiers: 1=Scout, 2=Analyst, 3=Advisor, 4=Strategist
    The router picks from this table based on complexity, risk, and budget.
    """
    __tablename__ = "model_registry"

    id                 = Column(Integer,  primary_key=True, index=True)
    display_name       = Column(String,   nullable=False)          # "GPT-4o mini"
    model_id           = Column(String,   nullable=False)          # "gpt-4o-mini" (API identifier)
    provider           = Column(String,   nullable=False)          # OpenAI | Anthropic | Azure | Google | Custom
    tier               = Column(Integer,  nullable=False)          # 1 | 2 | 3 | 4
    cost_input_per_1m  = Column(Float,    default=0.0)             # $ per 1M input tokens
    cost_output_per_1m = Column(Float,    default=0.0)             # $ per 1M output tokens
    is_enabled         = Column(Boolean,  default=True)
    is_default         = Column(Boolean,  default=False)           # default choice for this tier
    department         = Column(String,   nullable=True)           # None = global (all departments); set to limit to one BU
    notes              = Column(String,   nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)


class VoiceEvent(Base):
    """
    A voice transcript processed by Voice Guard.
    Tracks every redaction event — what was found, how it was caught, confidence score.
    """
    __tablename__ = "voice_events"

    id                  = Column(Integer,  primary_key=True, index=True)
    timestamp           = Column(DateTime, default=datetime.utcnow)
    call_id             = Column(String,   nullable=True)       # ID from upstream platform
    platform            = Column(String,   nullable=True)       # Genesys | AWS Connect | Salesforce Voice | etc.
    department          = Column(String,   nullable=True)
    raw_transcript      = Column(Text,     nullable=True)       # Original (stored only if no PII found)
    clean_transcript    = Column(Text,     nullable=True)       # Redacted version
    redactions_count    = Column(Integer,  default=0)
    pii_types_found     = Column(String,   nullable=True)       # JSON list: ["SSN", "CREDIT_CARD"]
    detection_method    = Column(String,   nullable=True)       # rule | ai | both | none
    confidence_score    = Column(Float,    nullable=True)       # 0.0 – 1.0
    flagged_for_review  = Column(Boolean,  default=False)
    processing_ms       = Column(Integer,  nullable=True)
    detection_details   = Column(Text,     nullable=True)       # JSON: [{pii_type, trigger_phrase, confidence, detection_method}]


class SensitiveTerm(Base):
    """
    A company-configured sensitive word or phrase.
    When matched in a payload, triggers escalation, flagging, or blocking.
    """
    __tablename__ = "sensitive_terms"

    id         = Column(Integer,  primary_key=True, index=True)
    term       = Column(String,   nullable=False, unique=True)
    category   = Column(String,   default="custom")   # legal | hipaa | financial | hr | custom
    action     = Column(String,   default="flag")      # flag | escalate | block
    department = Column(String,   nullable=True)       # None = global (all departments)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoutingConfig(Base):
    """
    Persisted routing rule configuration — always exactly one row (id=1).
    Seeded from config.py defaults on first boot; updated by the Routing Rules panel.
    """
    __tablename__ = "routing_configs"

    id                         = Column(Integer,  primary_key=True, index=True)
    complexity_token_threshold = Column(Integer,  nullable=False, default=500)
    complexity_keywords_json   = Column(Text,     nullable=False, default="[]")
    updated_at                 = Column(DateTime, default=datetime.utcnow)

    @property
    def complexity_keywords(self) -> list:
        import json
        try:
            return json.loads(self.complexity_keywords_json)
        except Exception:
            return []

    @complexity_keywords.setter
    def complexity_keywords(self, value: list):
        import json
        self.complexity_keywords_json = json.dumps(value)
