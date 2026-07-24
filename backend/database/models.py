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
    Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
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
    project_assignments = relationship("WorkItemAgent", back_populates="agent", cascade="all, delete-orphan")


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
    archived          = Column(Boolean,  nullable=True, default=False)  # soft-hide stale departments without deleting history


class WorkAccount(Base):
    """Optional customer, client, or business-unit parent for attributed work."""
    __tablename__ = "work_accounts"

    id          = Column(Integer,  primary_key=True, index=True)
    external_id = Column(String,   nullable=False, unique=True, index=True)
    name        = Column(String,   nullable=False)
    department  = Column(String,   nullable=True)
    status      = Column(String,   nullable=False, default="active")
    workspace_id = Column(String,  nullable=True, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_items = relationship("WorkItem", back_populates="account")


class WorkItem(Base):
    """A project, matter, engagement, case, claim, or other unit of work."""
    __tablename__ = "work_items"

    id                 = Column(Integer,  primary_key=True, index=True)
    external_id        = Column(String,   nullable=False, unique=True, index=True)
    name               = Column(String,   nullable=False)
    account_id         = Column(Integer,  ForeignKey("work_accounts.id"), nullable=True)
    owner              = Column(String,   nullable=True)
    department         = Column(String,   nullable=True)
    status             = Column(String,   nullable=False, default="active")
    monthly_ai_budget  = Column(Float,    nullable=True)
    cost_treatment     = Column(String,   nullable=False, default="unspecified")
    source_platform    = Column(String,   nullable=True, default="CostPilot")
    workspace_id       = Column(String,   nullable=True, index=True)
    context_type       = Column(String,   nullable=False, default="project")
    context_template   = Column(String,   nullable=True)
    source_record_type = Column(String,   nullable=True)
    source_record_id   = Column(String,   nullable=True, index=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account            = relationship("WorkAccount", back_populates="work_items")
    token_transactions = relationship("TokenTransaction", back_populates="work_item")
    audit_events       = relationship("AuditEvent", back_populates="work_item")
    agent_assignments  = relationship("WorkItemAgent", back_populates="work_item", cascade="all, delete-orphan")
    user_assignments   = relationship("WorkItemUser", back_populates="work_item", cascade="all, delete-orphan")


class WorkItemAgent(Base):
    """An agent expected or approved to work on a project."""
    __tablename__ = "work_item_agents"
    __table_args__ = (
        UniqueConstraint("work_item_id", "agent_id", name="uq_work_item_agent"),
    )

    id          = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(Integer, ForeignKey("work_items.id"), nullable=False, index=True)
    agent_id    = Column(Integer, ForeignKey("registered_agents.id"), nullable=False, index=True)
    role        = Column(String, nullable=False, default="Contributor")
    status      = Column(String, nullable=False, default="assigned")
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(String, nullable=True)

    work_item = relationship("WorkItem", back_populates="agent_assignments")
    agent     = relationship("RegisteredAgent", back_populates="project_assignments")


class WorkUser(Base):
    """A human identity from Salesforce, ServiceNow, HubSpot, or another source."""
    __tablename__ = "work_users"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_platform",
            "external_id",
            name="uq_work_user_source_identity",
        ),
    )

    id              = Column(Integer, primary_key=True, index=True)
    workspace_id    = Column(String, nullable=False, default="default", index=True)
    source_platform = Column(String, nullable=False)
    external_id     = Column(String, nullable=False, index=True)
    name            = Column(String, nullable=False)
    email           = Column(String, nullable=True)
    status          = Column(String, nullable=False, default="active")
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project_assignments = relationship("WorkItemUser", back_populates="user", cascade="all, delete-orphan")
    token_transactions  = relationship("TokenTransaction", back_populates="work_user")
    audit_events        = relationship("AuditEvent", back_populates="work_user")


class WorkItemUser(Base):
    """A human user assigned to a project, matter, engagement, case, or claim."""
    __tablename__ = "work_item_users"
    __table_args__ = (
        UniqueConstraint("work_item_id", "work_user_id", name="uq_work_item_user"),
    )

    id           = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(Integer, ForeignKey("work_items.id"), nullable=False, index=True)
    work_user_id = Column(Integer, ForeignKey("work_users.id"), nullable=False, index=True)
    role         = Column(String, nullable=False, default="Member")
    status       = Column(String, nullable=False, default="active")
    can_use_ai   = Column(Boolean, nullable=False, default=True)
    assigned_at  = Column(DateTime, default=datetime.utcnow)
    assigned_by  = Column(String, nullable=True)

    work_item = relationship("WorkItem", back_populates="user_assignments")
    user      = relationship("WorkUser", back_populates="project_assignments")


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
    work_item_id    = Column(Integer,  ForeignKey("work_items.id"), nullable=True, index=True)
    work_user_id    = Column(Integer,  ForeignKey("work_users.id"), nullable=True, index=True)
    actor_external_id = Column(String, nullable=True)
    actor_name      = Column(String, nullable=True)
    actor_email     = Column(String, nullable=True)
    actor_source_platform = Column(String, nullable=True)
    model_tier      = Column(String,   nullable=False)    # micro | flagship
    input_tokens   = Column(Integer,  nullable=False)
    output_tokens  = Column(Integer,  nullable=False)
    usage_source   = Column(String,   nullable=False, default="estimated")  # provider_reported | estimated
    cost_usd       = Column(Float,    nullable=False)
    timestamp      = Column(DateTime, default=datetime.utcnow)
    routing_reason = Column(String,   nullable=True)     # ROUTINE | COMPLEX | THROTTLED
    was_pruned     = Column(Boolean,  default=False)
    tokens_saved   = Column(Integer,  default=0)

    agent = relationship("RegisteredAgent", back_populates="token_transactions")
    work_item = relationship("WorkItem", back_populates="token_transactions")
    work_user = relationship("WorkUser", back_populates="token_transactions")


class AuditEvent(Base):
    """
    Immutable black-box record for every high-stakes AI decision.
    Written once, never modified. Exportable to compliance/legal.
    """
    __tablename__ = "audit_events"

    id               = Column(Integer,  primary_key=True, index=True)
    event_type       = Column(String,   nullable=False)   # ROUTING | THROTTLE | LOCK | DECISION
    agent_id         = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    work_item_id     = Column(Integer,  ForeignKey("work_items.id"), nullable=True, index=True)
    work_user_id     = Column(Integer,  ForeignKey("work_users.id"), nullable=True, index=True)
    actor_external_id = Column(String, nullable=True)
    actor_name       = Column(String, nullable=True)
    actor_email      = Column(String, nullable=True)
    actor_source_platform = Column(String, nullable=True)
    department       = Column(String,   nullable=False)
    model_tier       = Column(String,   nullable=True)
    context_snapshot = Column(Text,     nullable=True)    # JSON string — frozen system state
    prompt_payload   = Column(Text,     nullable=True)    # The exact pruned text sent to the model
    raw_payload           = Column(Text,     nullable=True)    # The original text before pruning
    raw_logged_at         = Column(DateTime, nullable=True)    # When raw payload was captured
    matched_keywords_json = Column(Text,     nullable=True)    # JSON array e.g. '["urgent","legal"]'
    rationale        = Column(Text,     nullable=True)    # Plain-English justification
    decision_outcome = Column(String,   nullable=True)
    risk_level       = Column(String,   default="low")    # low | medium | high | critical
    timestamp        = Column(DateTime, default=datetime.utcnow)

    agent = relationship("RegisteredAgent", back_populates="audit_events")
    work_item = relationship("WorkItem", back_populates="audit_events")
    work_user = relationship("WorkUser", back_populates="audit_events")


class AuditReviewState(Base):
    """Mutable review checkpoint kept separate from immutable audit events."""
    __tablename__ = "audit_review_states"

    scope_key           = Column(String, primary_key=True, default="global")
    reviewed_through_id = Column(Integer, nullable=False, default=0)
    reviewer            = Column(String, nullable=True)
    reviewed_at         = Column(DateTime, nullable=True)


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
    enabled    = Column(Boolean,  nullable=False, default=True)
    is_recommended = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class KnownModel(Base):
    """
    Provider-maintained list of known AI models available for selection.
    Admins can add/remove entries without any code deployment.
    Used to populate the 'Quick Select' preset dropdown in the Model Registry.
    """
    __tablename__ = "known_models"

    id                 = Column(Integer,  primary_key=True, index=True)
    display_name       = Column(String,   nullable=False)          # "Claude Opus 4.8"
    model_id           = Column(String,   nullable=False, unique=True)  # "claude-opus-4-8"
    provider           = Column(String,   nullable=False)          # Anthropic | OpenAI | Google | Mistral | Azure OpenAI
    provider_group     = Column(String,   nullable=False)          # "Anthropic — Claude 4.x" (shown as dropdown group label)
    tier               = Column(Integer,  nullable=False)          # 1=Scout 2=Analyst 3=Advisor 4=Strategist
    cost_input_per_1m  = Column(Float,    default=0.0)             # $ per 1M input tokens
    cost_output_per_1m = Column(Float,    default=0.0)             # $ per 1M output tokens
    is_active          = Column(Boolean,  default=True)            # False = hidden from dropdown but kept for history
    notes              = Column(String,   nullable=True)           # Optional admin note e.g. "deprecated - use 4.8"
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RoutingConfig(Base):
    """
    Persisted routing rule configuration — always exactly one row (id=1).
    Seeded from config.py defaults on first boot; updated by the Routing Rules panel.
    """
    __tablename__ = "routing_configs"

    id                         = Column(Integer,  primary_key=True, index=True)
    complexity_token_threshold = Column(Integer,  nullable=False, default=500)
    complexity_keywords_json   = Column(Text,     nullable=False, default="[]")
    tier_names_json            = Column(Text,     nullable=True)   # JSON: {"1":"Scout","2":"Analyst",...} — null = use defaults
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

    _DEFAULT_TIER_NAMES = {"1": "Scout", "2": "Analyst", "3": "Advisor", "4": "Strategist"}

    @property
    def tier_names(self) -> dict:
        import json
        if not self.tier_names_json:
            return dict(self._DEFAULT_TIER_NAMES)
        try:
            stored = json.loads(self.tier_names_json)
            # Merge with defaults so missing keys always have a value
            merged = dict(self._DEFAULT_TIER_NAMES)
            merged.update({str(k): str(v) for k, v in stored.items() if str(v).strip()})
            return merged
        except Exception:
            return dict(self._DEFAULT_TIER_NAMES)

    @tier_names.setter
    def tier_names(self, value: dict):
        import json
        self.tier_names_json = json.dumps({str(k): str(v) for k, v in value.items()})


class TrialAccount(Base):
    """
    A CostPilot free-trial customer.
    Created when a prospect connects their API key on savings.html.
    """
    __tablename__ = "trial_accounts"

    id           = Column(Integer,  primary_key=True, index=True)
    email        = Column(String,   nullable=False, unique=True)
    name         = Column(String,   nullable=False)
    company      = Column(String,   nullable=True)
    api_key_enc  = Column(Text,     nullable=False)          # base64-encoded key
    provider     = Column(String,   default="openai")        # openai | anthropic
    workspace_id = Column(String,   nullable=False, unique=True)  # e.g. "A1B2C3D4E5F6G7H8"
    secret_key   = Column(String,   nullable=True)           # sk-cp-xxx — authenticates proxy calls
    platform     = Column(String,   nullable=True)           # salesforce | servicenow | hubspot | python | nodejs | java | ruby | other
    setup_complete = Column(Boolean, default=False)          # True after getting-started wizard finished
    business_context_config_json = Column(Text, nullable=True)
    trial_start  = Column(DateTime, default=datetime.utcnow)
    trial_end    = Column(DateTime, nullable=False)
    plan         = Column(String,   default="trial")         # trial | starter | growth | business | enterprise
    requested_plan = Column(String, nullable=True)           # starter | growth | business | enterprise
    upgrade_requested_at = Column(DateTime, nullable=True)
    is_active    = Column(Boolean,  default=True)
    trial_call_cap = Column(Integer, default=500)
    trial_spend_cap_usd = Column(Float, default=10.0)
    created_at   = Column(DateTime, default=datetime.utcnow)
