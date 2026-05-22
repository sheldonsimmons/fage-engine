"""
models.py — All SQLAlchemy ORM table definitions for FAGE.

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
    permissions      = Column(String,   nullable=False)   # e.g. "read,write"
    target_table     = Column(String,   nullable=True)
    target_record_id = Column(Integer,  nullable=True)
    status           = Column(String,   default="idle")   # idle | active | locked | queued
    collision_policy = Column(String,   default="lock")   # lock | queue | skip
    locked_at        = Column(DateTime, nullable=True)
    lock_reason      = Column(String,   nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

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


class TokenTransaction(Base):
    """
    A single AI model call — records cost, tier, routing reason, and pruning savings.
    This is the source of truth for all financial analytics.
    """
    __tablename__ = "token_transactions"

    id             = Column(Integer,  primary_key=True, index=True)
    department     = Column(String,   nullable=False)
    agent_id       = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    model_tier     = Column(String,   nullable=False)    # micro | flagship
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
    prompt_payload   = Column(Text,     nullable=True)    # The exact text sent to the model
    rationale        = Column(Text,     nullable=True)    # Plain-English justification
    decision_outcome = Column(String,   nullable=True)
    risk_level       = Column(String,   default="low")    # low | medium | high | critical
    timestamp        = Column(DateTime, default=datetime.utcnow)

    agent = relationship("RegisteredAgent", back_populates="audit_events")


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
