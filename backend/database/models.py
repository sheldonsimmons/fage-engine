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
    # Nullable/unbackfilled by design -- unlike WorkItem/TokenTransaction/
    # DepartmentBudget, a real chunk of existing rows have an unprefixed
    # department ("Engineering", "Sales", ...) with no reliable single-
    # workspace signal in their transaction history (confirmed live: one
    # such agent had 12,854 NULL-workspace transactions vs. 33 attributed
    # to SIM-HISTORICAL-2Y -- noise, not a real attribution). Only rows
    # with an unambiguous "workspace_id:Dept" prefix get backfilled; see
    # database/backfill_workspaces.py. workspace_filter() already falls
    # back to the department-prefix match for any row where this is NULL.
    workspace_id     = Column(String,   nullable=True, index=True)
    owner_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
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
    # Policy-aware routing (Routing 2.0, Phase 2): JSON list of allowed
    # ModelRegistry.provider values, e.g. '["anthropic"]' -- "approved for
    # Sonnet but not Opus" is expressed as a provider allow-list today, not
    # a model-family allow-list (ModelRegistry has no family concept yet).
    # Null/empty = unrestricted, same as every other agent today.
    allowed_providers_json = Column(Text, nullable=True)
    pruning_enabled  = Column(Boolean,  nullable=True, default=True)   # False = skip context pruner entirely for this agent
    discovery_source = Column(String,   nullable=True, default="manual")  # manual | event -- "event" means this row was auto-created on first traffic, not registered ahead of time
    mode             = Column(String,   nullable=True, default="observe")  # observe | control -- see docs/COSTPILOT_AGENT_MODE_LIFECYCLE.md; "optimize" is not a stored value, it's observe + recommendations

    # Governance/lifecycle metadata -- deliberately separate from `status`
    # above, which is pure runtime state (idle/active/locked/queued) owned
    # by AgentLake's collision logic. An agent can be Active + Unreviewed
    # at the same time; these two dimensions must never be merged into one
    # enum. Informational only for now -- approval_status does not gate
    # routing/collision behavior.
    business_purpose = Column(Text,     nullable=True)   # human-entered description of what this agent does
    owner            = Column(String,   nullable=True)   # accountable person/team (distinct from owner_org_unit_id, which is organizational)
    approval_status  = Column(String,   nullable=True, default="unreviewed")  # unreviewed | pending_review | approved | deprecated | retired

    token_transactions = relationship("TokenTransaction", back_populates="agent")
    audit_events       = relationship("AuditEvent",       back_populates="agent")
    project_assignments = relationship("WorkItemAgent", back_populates="agent", cascade="all, delete-orphan")

    @property
    def allowed_providers(self) -> list:
        import json
        if not self.allowed_providers_json:
            return []
        try:
            return json.loads(self.allowed_providers_json)
        except Exception:
            return []

    @allowed_providers.setter
    def allowed_providers(self, value: list):
        import json
        self.allowed_providers_json = json.dumps(value) if value else None


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
    workspace_id      = Column(String,   nullable=True, index=True)  # backfilled from the "workspace_id:DeptName" prefix on `department`


class OrganizationalUnit(Base):
    """A tenant-scoped department, team, cost center, or other reporting unit."""
    __tablename__ = "organizational_units"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_id", name="uq_organizational_unit_external_id"),
    )

    id              = Column(Integer, primary_key=True, index=True)
    workspace_id    = Column(String, nullable=False, default="default", index=True)
    external_id     = Column(String, nullable=False, index=True)
    name            = Column(String, nullable=False)
    unit_type       = Column(String, nullable=False, default="department")
    parent_id       = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    source_platform = Column(String, nullable=True)
    status          = Column(String, nullable=False, default="active")
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntegrationConnection(Base):
    """A tenant-scoped external platform connection and its approved mapping."""
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "platform", "display_name", name="uq_integration_connection_name"),
    )

    id                    = Column(Integer, primary_key=True, index=True)
    workspace_id          = Column(String, nullable=False, default="default", index=True)
    platform              = Column(String, nullable=False, index=True)
    display_name          = Column(String, nullable=False)
    status                = Column(String, nullable=False, default="draft")
    # Immutable identity for this connection, independent of the editable
    # `platform`/`display_name` strings -- generated once at creation
    # (Universal Connection feature) so an incoming event can be
    # attributed to exactly this connection even if its display name is
    # later renamed. Nullable/unique: existing (Salesforce/ServiceNow)
    # connections predate this and are never backfilled with one --
    # status computation falls back to platform-string matching for them.
    connection_key        = Column(String, nullable=True, unique=True, index=True)
    auth_base_url         = Column(String, nullable=True)
    instance_url          = Column(String, nullable=True)
    external_tenant_id    = Column(String, nullable=True)
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    oauth_state           = Column(String, nullable=True, index=True)
    selected_object       = Column(String, nullable=True)
    mapping_json          = Column(Text, nullable=True)
    discovery_json        = Column(Text, nullable=True)
    last_tested_at        = Column(DateTime, nullable=True)
    last_success_at       = Column(DateTime, nullable=True)
    last_error            = Column(Text, nullable=True)
    # Distinct from last_success_at (any successful API call -- test,
    # discovery, import, sync) -- this is specifically "when did the
    # automatic outcome-sync sweep last touch this connection," so the
    # sweep can log/report freshness without conflating it with unrelated
    # connection-health checks.
    last_outcome_sync_at  = Column(DateTime, nullable=True)
    # Objects the admin has opted into tracking beyond the single primary
    # `selected_object` that import actually runs against today -- recorded
    # now so the intent isn't lost, even though multi-object import (looping
    # the existing single-object import per tracked object) isn't wired up
    # yet. JSON array of object API names, e.g. ["Opportunity", "Case"].
    tracked_objects_json  = Column(Text, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkAccount(Base):
    """Optional customer, client, or business-unit parent for attributed work."""
    __tablename__ = "work_accounts"

    id          = Column(Integer,  primary_key=True, index=True)
    external_id = Column(String,   nullable=False, unique=True, index=True)
    name        = Column(String,   nullable=False)
    department  = Column(String,   nullable=True)
    status      = Column(String,   nullable=False, default="active")
    workspace_id = Column(String,  nullable=True, index=True)
    merged_into_work_account_id = Column(Integer, ForeignKey("work_accounts.id"), nullable=True, index=True)
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
    org_unit_id        = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    status             = Column(String,   nullable=False, default="active")
    monthly_ai_budget  = Column(Float,    nullable=True)
    budget_warning_pct = Column(Float,    nullable=False, default=80.0)
    budget_action      = Column(String,   nullable=False, default="warn")
    cost_treatment     = Column(String,   nullable=False, default="unspecified")
    source_platform    = Column(String,   nullable=True, default="CostPilot")
    workspace_id       = Column(String,   nullable=True, index=True)
    context_type       = Column(String,   nullable=False, default="project")
    context_template   = Column(String,   nullable=True)
    source_record_type = Column(String,   nullable=True)
    source_record_id   = Column(String,   nullable=True, index=True)
    merged_into_work_item_id = Column(Integer, ForeignKey("work_items.id"), nullable=True, index=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account            = relationship("WorkAccount", back_populates="work_items")
    token_transactions = relationship("TokenTransaction", back_populates="work_item")
    audit_events       = relationship("AuditEvent", back_populates="work_item")
    agent_assignments  = relationship("WorkItemAgent", back_populates="work_item", cascade="all, delete-orphan")
    user_assignments   = relationship("WorkItemUser", back_populates="work_item", cascade="all, delete-orphan")
    source_links       = relationship("WorkItemSourceLink", back_populates="work_item", cascade="all, delete-orphan")


class WorkItemSourceLink(Base):
    """A source-system record that rolls up to one canonical CostPilot project."""
    __tablename__ = "work_item_source_links"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_platform", "source_record_id",
            name="uq_work_item_source_record",
        ),
    )

    id                  = Column(Integer, primary_key=True, index=True)
    work_item_id        = Column(Integer, ForeignKey("work_items.id"), nullable=False, index=True)
    workspace_id        = Column(String, nullable=False, default="default", index=True)
    source_platform     = Column(String, nullable=False)
    source_record_type  = Column(String, nullable=True)
    source_record_id    = Column(String, nullable=False, index=True)
    source_record_name  = Column(String, nullable=True)
    account_external_id = Column(String, nullable=True, index=True)
    is_primary          = Column(Boolean, nullable=False, default=False)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_item = relationship("WorkItem", back_populates="source_links")


class WorkItemOutcome(Base):
    """
    Current business-outcome state for a work item (e.g. a Salesforce
    Opportunity's stage/amount/close date) -- the "what happened to the
    work" half of AI Event -> Work Item -> Outcome. One row per work item.

    CostPilot does not own this data; the source system (Salesforce, etc.)
    remains authoritative. This is a synced/cached minimum-fields copy, not
    a duplicate CRM -- see WorkItemOutcomeEvent for the append-only history
    and core/outcome_adapters/ for the source-specific field mapping.
    """
    __tablename__ = "work_item_outcomes"

    id                = Column(Integer, primary_key=True, index=True)
    work_item_id      = Column(Integer, ForeignKey("work_items.id"), nullable=False, unique=True, index=True)
    workspace_id      = Column(String, nullable=False, default="default", index=True)
    outcome_status    = Column(String, nullable=True)   # canonical OUTCOME_STATUS, e.g. source StageName
    outcome_value     = Column(Float, nullable=True)     # canonical OUTCOME_VALUE
    outcome_date      = Column(DateTime, nullable=True)  # canonical OUTCOME_DATE
    outcome_success   = Column(Boolean, nullable=True)   # canonical OUTCOME_SUCCESS (None = not yet decided)
    is_closed         = Column(Boolean, nullable=True)
    owner             = Column(String, nullable=True)
    source_system     = Column(String, nullable=False)   # "salesforce"
    source_object     = Column(String, nullable=False)   # "Opportunity"
    external_id       = Column(String, nullable=False, index=True)
    source_modified_at = Column(DateTime, nullable=True)  # source system's own LastModifiedDate
    last_synced_at    = Column(DateTime, nullable=False, default=datetime.utcnow)
    retrieval_method  = Column(String, nullable=False, default="sync")  # webhook | sync | on_demand | import | push
    # True for synthetic/test traffic (Universal Connection's "Send Test
    # Event" flow, core/outcome_ingestion.py) -- excluded from real
    # business-impact reporting the same way TokenTransaction.is_simulation
    # already excludes synthetic AI activity.
    is_simulation     = Column(Boolean, nullable=False, default=False)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_item = relationship("WorkItem", backref="outcome")


class WorkItemOutcomeEvent(Base):
    """
    Append-only outcome history -- one row per observed change, so future
    analysis (AI spend by lifecycle stage, cost accumulated across a sales
    cycle, etc.) has more than just the current snapshot to work from.
    """
    __tablename__ = "work_item_outcome_events"

    id              = Column(Integer, primary_key=True, index=True)
    work_item_id    = Column(Integer, ForeignKey("work_items.id"), nullable=False, index=True)
    workspace_id    = Column(String, nullable=False, default="default", index=True)
    outcome_status  = Column(String, nullable=True)
    outcome_value   = Column(Float, nullable=True)
    outcome_date    = Column(DateTime, nullable=True)
    outcome_success = Column(Boolean, nullable=True)
    is_closed       = Column(Boolean, nullable=True)
    retrieval_method = Column(String, nullable=False, default="sync")
    # Which platform reported THIS specific history row -- absent until
    # Universal Outcome Ingestion, since every event for one WorkItem used
    # to come from a single adapter/platform. Lets two disagreeing sources
    # (core/outcome_ingestion.py's "two systems disagree" rule) each keep
    # their own attributed row instead of one overwriting the other's
    # provenance.
    source_system   = Column(String, nullable=True)
    # Client-supplied idempotency key (mandatory for Universal Outcome
    # Ingestion calls, see core/outcome_ingestion.py) -- same
    # (workspace_id, event_id)-scoped uniqueness pattern as
    # TokenTransaction.event_id, so two different customers can safely
    # reuse the same event_id string. Nullable because pre-existing
    # pull-sync-written rows (core/outcome_adapters/*.py) never had one and
    # still don't need one -- CostPilot is the caller on that path, not an
    # external system replaying a webhook.
    event_id        = Column(String, nullable=True, index=True)
    is_simulation   = Column(Boolean, nullable=False, default=False)
    # Mirrors TokenTransaction.connection_key -- lets Universal Connection
    # verification (core/connection_status.py) scope an outcome event to
    # exactly one connection, the same way it already scopes activity
    # events, instead of falling back to a looser workspace+platform match.
    connection_key  = Column(String, nullable=True, index=True)
    recorded_at     = Column(DateTime, default=datetime.utcnow)

    work_item = relationship("WorkItem")


class WorkspaceAnalyticsSettings(Base):
    """Tenant-specific calendar and verified ingestion boundaries for analytics."""
    __tablename__ = "workspace_analytics_settings"

    id                      = Column(Integer, primary_key=True, index=True)
    workspace_id            = Column(String, nullable=False, unique=True, index=True)
    timezone_name           = Column(String, nullable=False, default="UTC")
    week_starts_on          = Column(Integer, nullable=False, default=0)  # 0=Monday
    fiscal_year_start_month = Column(Integer, nullable=False, default=1)
    default_window_days     = Column(Integer, nullable=False, default=30)
    collection_started_at   = Column(DateTime, nullable=True)
    latest_complete_at      = Column(DateTime, nullable=True)
    created_at              = Column(DateTime, default=datetime.utcnow)
    updated_at              = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HistoricalDemoSeedState(Base):
    """Reversible snapshot of analytics settings changed by a historical demo seed."""
    __tablename__ = "historical_demo_seed_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "marker", name="uq_historical_demo_seed_state"),
    )

    id                = Column(Integer, primary_key=True, index=True)
    workspace_id      = Column(String, nullable=False, index=True)
    marker            = Column(String, nullable=False)
    settings_existed  = Column(Boolean, nullable=False, default=False)
    settings_snapshot = Column(Text, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)


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
    primary_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
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
    governed_request_id = Column(String, nullable=True, index=True)
    # Client-supplied idempotency key (RouteRequest.event_id) -- distinct
    # from governed_request_id, which CostPilot generates itself on every
    # call. Nullable, and uniqueness is scoped to (workspace_id, event_id)
    # -- see the migration's composite unique index -- not to event_id
    # alone, so two different customers reusing the same event_id string
    # can never collide with or see each other's transaction.
    event_id        = Column(String, nullable=True, index=True)
    department      = Column(String,   nullable=False)
    source_platform = Column(String,   nullable=True)    # Salesforce | ServiceNow | HubSpot | Custom | etc.
    # Optional stable link to the IntegrationConnection.connection_key that
    # reported this event (Universal Connection feature) -- lets a
    # connection's status be computed by an unambiguous match instead of
    # relying solely on the editable source_platform string. Never
    # required: RouteRequest's minimal payload doesn't need it, and most
    # existing rows (predating this feature, or from native connectors
    # that don't send one) will always be NULL here.
    connection_key  = Column(String,   nullable=True, index=True)
    agent_id        = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    work_item_id    = Column(Integer,  ForeignKey("work_items.id"), nullable=True, index=True)
    work_user_id    = Column(Integer,  ForeignKey("work_users.id"), nullable=True, index=True)
    origin_record_id = Column(String, nullable=True, index=True)
    origin_record_type = Column(String, nullable=True)
    origin_record_name = Column(String, nullable=True)
    actor_external_id = Column(String, nullable=True)
    actor_name      = Column(String, nullable=True)
    actor_email     = Column(String, nullable=True)
    actor_source_platform = Column(String, nullable=True)
    workspace_id    = Column(String, nullable=True, index=True)
    actor_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    actor_org_unit_name = Column(String, nullable=True)
    agent_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    agent_org_unit_name = Column(String, nullable=True)
    work_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    work_org_unit_name = Column(String, nullable=True)
    charged_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    charged_org_unit_name = Column(String, nullable=True)
    attribution_source = Column(String, nullable=True)
    attribution_confidence = Column(String, nullable=True)
    # Computed once at write time by classify_business_purpose_fields() so
    # reporting can GROUP BY it in SQL instead of reclassifying every row in
    # Python on every report request. Pure function of already-persisted
    # signals (origin_record_*, work item type, agent name) -- unlike
    # provider, there's no external registry that could make a persisted
    # value go stale later.
    business_purpose = Column(String, nullable=True, index=True)
    model_tier      = Column(String,   nullable=False)    # micro | flagship
    requested_model_name = Column(String, nullable=True)
    requested_model_tier = Column(String, nullable=True)
    model_name      = Column(String,   nullable=True)     # exact provider/registry model used
    resolved_model_tier = Column(String, nullable=True)   # actual tier after cascade
    model_source    = Column(String,   nullable=True)     # registry | built_in_fallback | provider_proxy
    routing_cascaded = Column(Boolean, default=False)
    is_simulation   = Column(Boolean,  nullable=False, default=False)
    input_tokens   = Column(Integer,  nullable=False)
    output_tokens  = Column(Integer,  nullable=False)
    usage_source   = Column(String,   nullable=False, default="estimated")  # provider_reported | estimated
    cost_usd       = Column(Float,    nullable=False)
    timestamp      = Column(DateTime, default=datetime.utcnow)
    routing_reason = Column(String,   nullable=True)     # ROUTINE | COMPLEX | THROTTLED
    routing_policy_version = Column(String, nullable=True)
    execution_status = Column(String, nullable=True)     # succeeded | failed | blocked
    provider_status_code = Column(Integer, nullable=True)
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
    governed_request_id = Column(String, nullable=True, index=True)
    event_type       = Column(String,   nullable=False)   # ROUTING | THROTTLE | LOCK | DECISION
    agent_id         = Column(Integer,  ForeignKey("registered_agents.id"), nullable=True)
    work_item_id     = Column(Integer,  ForeignKey("work_items.id"), nullable=True, index=True)
    work_user_id     = Column(Integer,  ForeignKey("work_users.id"), nullable=True, index=True)
    origin_record_id = Column(String, nullable=True, index=True)
    origin_record_type = Column(String, nullable=True)
    origin_record_name = Column(String, nullable=True)
    actor_external_id = Column(String, nullable=True)
    actor_name       = Column(String, nullable=True)
    actor_email      = Column(String, nullable=True)
    actor_source_platform = Column(String, nullable=True)
    # Phase 0 (security architecture assessment): a real, logged-in
    # CostPilot user, when the request that produced this event carried a
    # valid session -- distinct from actor_name/actor_email above, which
    # are free-text and describe the origin system's own actor (e.g. a
    # Salesforce user), not a CostPilot account. Nullable and unused by
    # any existing write site; wiring real callers to populate it is a
    # separate, later step.
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Permissioned Actions (Action Proposals slice 1): links a "proposed"
    # audit row to its later "executed"/"rejected" row for the same
    # proposal -- governed_request_id above ties to a different thing (one
    # governed LLM request), so a new dedicated key avoids corrupting its
    # existing meaning for every downstream reader.
    proposal_id      = Column(Integer, ForeignKey("action_proposals.id"), nullable=True, index=True)
    workspace_id       = Column(String, nullable=True, index=True)
    actor_org_unit_id  = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    actor_org_unit_name = Column(String, nullable=True)
    agent_org_unit_id  = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    agent_org_unit_name = Column(String, nullable=True)
    work_org_unit_id   = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    work_org_unit_name = Column(String, nullable=True)
    charged_org_unit_id = Column(Integer, ForeignKey("organizational_units.id"), nullable=True, index=True)
    charged_org_unit_name = Column(String, nullable=True)
    attribution_source = Column(String, nullable=True)
    attribution_confidence = Column(String, nullable=True)
    department       = Column(String,   nullable=False)
    model_tier       = Column(String,   nullable=True)
    requested_model_name = Column(String, nullable=True)
    requested_model_tier = Column(String, nullable=True)
    selected_model_name = Column(String, nullable=True)
    selected_model_tier = Column(String, nullable=True)
    routing_policy_version = Column(String, nullable=True)
    routing_reason_code = Column(String, nullable=True)
    execution_status = Column(String, nullable=True)
    context_snapshot = Column(Text,     nullable=True)    # JSON string — frozen system state
    prompt_payload   = Column(Text,     nullable=True)    # The exact pruned text sent to the model
    raw_payload           = Column(Text,     nullable=True)    # The original text before pruning
    raw_logged_at         = Column(DateTime, nullable=True)    # When raw payload was captured
    matched_keywords_json = Column(Text,     nullable=True)    # JSON array e.g. '["urgent","legal"]'
    # Routing 2.0 Phase 2 audit-trail fix: matched_keywords_json above only
    # ever kept the matched SensitiveTerm's text, not its row id, so "every
    # event where rule #17 fired" was never a queryable question -- only a
    # JSON-text search. This is the same matches list, but the ids.
    matched_term_ids_json = Column(Text,     nullable=True)    # JSON array of SensitiveTerm.id, e.g. '[17,42]'
    rationale        = Column(Text,     nullable=True)    # Plain-English justification
    decision_outcome = Column(String,   nullable=True)
    cost_usd          = Column(Float,    nullable=True)
    risk_level       = Column(String,   default="low")    # low | medium | high | critical
    is_simulation    = Column(Boolean,  nullable=False, default=False)
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
    workspace_id        = Column(String,   nullable=True, index=True)  # nullable: legacy rows predate this column
    raw_transcript      = Column(Text,     nullable=True)       # Original (stored only if no PII found)
    clean_transcript    = Column(Text,     nullable=True)       # Redacted version
    redactions_count    = Column(Integer,  default=0)
    pii_types_found     = Column(String,   nullable=True)       # JSON list: ["SSN", "CREDIT_CARD"]
    detection_method    = Column(String,   nullable=True)       # rule | ai | both | none
    confidence_score    = Column(Float,    nullable=True)       # 0.0 – 1.0
    flagged_for_review  = Column(Boolean,  default=False)
    processing_ms       = Column(Integer,  nullable=True)
    detection_details   = Column(Text,     nullable=True)       # JSON: [{pii_type, trigger_phrase, confidence, detection_method}]


class AskInteraction(Base):
    """
    One row per Ask CostPilot question -- Phase 1 of the governed
    continuous-learning plan (see the session's Ask CostPilot assessment):
    turns "zero visibility into real usage" into a real, queryable table.
    Purely observational -- nothing reads this table to change behavior
    yet; later phases (rephrase detection, intent-candidate mining) build
    on top of it, but this table by itself changes nothing about how
    Ask CostPilot answers a question.

    Written from the single outer ask_costpilot() wrapper so every intent
    branch (help/product/decision/agent-loop/deterministic fallback) is
    covered by one insertion point, not one per branch. Several fields
    are necessarily best-effort in this v1: the branches return different
    response shapes, and not every branch's internal state (e.g. the
    agent loop's tool_call_log/contract_issues) is threaded up to the
    outer wrapper today -- see routes_efficiency.py's _ask_log_interaction
    for exactly what's derived vs. directly captured.
    """
    __tablename__ = "ask_interactions"

    id                  = Column(Integer,  primary_key=True, index=True)
    workspace_id        = Column(String,   nullable=True, index=True)
    user_id             = Column(Integer,  ForeignKey("users.id"), nullable=True, index=True)  # populated only when the request carried a valid session (Phase 0 identity threading); NULL for every historical row and for any question asked without logging in
    session_id          = Column(String,   nullable=True, index=True)  # unpopulated until Phase 3 (rephrase detection)
    governed_request_id = Column(String,   nullable=True, index=True)
    timestamp           = Column(DateTime, default=datetime.utcnow, index=True)
    question_text       = Column(Text,     nullable=True)  # see Phase 1 rollout note: consider an opt-in/retention policy before enabling broadly, same pattern as DepartmentBudget.raw_payload_logging_enabled
    intent              = Column(String,   nullable=True)
    entity              = Column(String,   nullable=True)
    metric              = Column(String,   nullable=True)
    filters_json        = Column(Text,     nullable=True)
    period_key          = Column(String,   nullable=True)
    assistant_mode      = Column(String,   nullable=True)  # agent_tool_loop | contract_guardrail | deterministic_period_contract | etc. -- the actual response-path label already used elsewhere in this codebase
    tools_called_json   = Column(Text,     nullable=True)  # best-effort: derived from the agent loop's query_plan when present, not a direct tool_call_log capture
    validation_passed   = Column(Boolean,  nullable=True)  # best-effort: True unless a contract_guardrail response is what came back (an agent-loop validation failure never reaches this point at all -- it silently falls back to the deterministic path first)
    contract_issues_json = Column(Text,    nullable=True)
    fallback_used       = Column(Boolean,  nullable=True)  # best-effort approximation -- see _ask_log_interaction's comment for exactly how this is derived
    unsupported         = Column(Boolean,  nullable=True)
    latency_ms          = Column(Integer,  nullable=True)
    error_type          = Column(String,   nullable=True)
    evidence_label      = Column(String,   nullable=True)
    # CostPilot Voice (Phase 1) -- extends this table rather than adding a
    # new one, per the Voice feasibility assessment's own recommendation:
    # a voice question is the same event type as a typed one (one row per
    # Ask CostPilot question), just with a different modality and two
    # voice-specific fields. Nothing about the answer pipeline branches on
    # modality -- these columns are observational only, same as every
    # other field on this table.
    modality             = Column(String,   nullable=True)  # "text" | "voice" -- nullable/defaults to text for pre-Voice rows
    transcription_confidence = Column(Float, nullable=True)  # 0.0-1.0, from the STT provider; null for typed questions
    clarification_requested  = Column(Boolean, nullable=True)  # True when an ambiguous entity (e.g. two "Acme" accounts) forced a clarifying question instead of an answer


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
    # Budget-aware routing (Routing 2.0, Phase 1): when a department's spend
    # utilization reaches this %, eligible low-complexity requests (would-be
    # Scout/Analyst) get routed straight to Scout as a soft precaution —
    # distinct from DepartmentBudget.throttled, which is the existing hard
    # 100%+ cap. Global for now, same single-row pattern as the token
    # threshold above; null/unset disables the behavior entirely.
    budget_pressure_threshold_pct = Column(Float, nullable=True, default=80.0)
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


class Workspace(Base):
    """
    The canonical registry of workspaces. Before this table existed,
    "workspace" was only ever inferred from a "workspace_id:DeptName"
    string-prefix convention on department_budgets.department (and, for
    token_transactions/audit_events, a workspace_id column with no backing
    table) — no single place could answer "what workspaces exist" or "is
    this one real customer data or leftover test data."
    """
    __tablename__ = "workspaces"

    id                 = Column(Integer, primary_key=True, index=True)
    workspace_id       = Column(String, unique=True, nullable=False, index=True)
    name               = Column(String, nullable=False)
    workspace_type     = Column(String, nullable=False, default="production")  # production | demo | simulation | legacy
    source             = Column(String, nullable=True)  # trial_signup | historical_backfill | manual_seed | sandbox
    owner_trial_account_id = Column(Integer, ForeignKey("trial_accounts.id"), nullable=True)
    # Authenticates production /api/route callers via X-CostPilot-Key --
    # distinct from TrialAccount.secret_key (the trial-signup credential).
    # Nullable: existing workspaces have none until one is generated for
    # them, which is exactly the unauthenticated-but-accepted grace-period
    # state /api/route's auth check is built around.
    api_key            = Column(String, nullable=True, unique=True, index=True)
    is_active          = Column(Boolean, default=True)
    last_activity_at   = Column(DateTime, nullable=True)
    default_monthly_budget_usd = Column(Float, nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)
    archived_at        = Column(DateTime, nullable=True)
    notes              = Column(Text, nullable=True)


# ── Auth / RBAC — Phase 1 of the security architecture assessment ──────────
#
# Foundation only: these tables exist and are usable via api/routes_auth.py
# (register/login/logout/me), but nothing else in the app checks them yet.
# No existing route has been retrofitted to require a session -- that's
# Phase 2 (a centralized get_current_membership() dependency applied route
# by route), deliberately kept separate so this addition is purely
# additive and carries no regression risk to any page that works today.

class User(Base):
    """
    A logged-in human, independent of any workspace -- workspace access
    is entirely UserWorkspaceMembership's job, not this table's. No
    "global superadmin" flag here on purpose: any cross-workspace admin
    capability, if ever needed, belongs on the membership/role layer, not
    baked into the user record itself.
    """
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String, unique=True, nullable=False, index=True)
    display_name  = Column(String, nullable=True)
    status        = Column(String, nullable=False, default="active")  # active | disabled
    auth_provider = Column(String, nullable=False, default="email")   # email | google | microsoft (future)
    password_hash = Column(String, nullable=True)  # null if a future non-password provider is used
    created_at    = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class Role(Base):
    """
    A fixed catalog of four roles for v1 (workspace_admin,
    governance_manager, department_manager, viewer) -- deliberately no
    custom-role editor yet; that's an explicitly deferred future-enterprise
    feature, not an oversight. is_builtin exists so a later custom-role
    feature can distinguish the four seeded rows from anything an admin
    creates themselves, without a schema change.
    """
    __tablename__ = "roles"

    id         = Column(Integer, primary_key=True, index=True)
    key        = Column(String, unique=True, nullable=False)  # workspace_admin | governance_manager | department_manager | viewer
    label      = Column(String, nullable=False)
    is_builtin = Column(Boolean, default=True)


class RolePermission(Base):
    """
    What each role can do -- a real table (so route handlers eventually
    check has_permission(user, workspace, "manage_budgets") rather than
    role == "governance_manager" directly) even though v1 has no UI to
    edit it; the four roles' bundles are seeded once at migration time.
    """
    __tablename__ = "role_permissions"

    id         = Column(Integer, primary_key=True, index=True)
    role_id    = Column(Integer, ForeignKey("roles.id"), nullable=False)
    permission = Column(String, nullable=False)

    __table_args__ = (UniqueConstraint("role_id", "permission", name="uq_role_permission"),)


class UserWorkspaceMembership(Base):
    """
    Which users can access which workspaces, with what role and
    (optionally) department restriction. workspace_id here is a REAL
    foreign key to workspaces.id (the surrogate integer PK) -- a
    deliberate departure from the loose "workspace_id as an unindexed
    string" convention every other table in this codebase uses, because
    that loose-string pattern is exactly what caused real bugs elsewhere
    (see workspace_scope.py's docstring) and Workspace itself was already
    built to be the canonical, FK-able registry. A new table is the right
    place to finally do it the strict way.
    """
    __tablename__ = "user_workspace_memberships"

    id                  = Column(Integer, primary_key=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)
    workspace_id        = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    role_id             = Column(Integer, ForeignKey("roles.id"), nullable=False)
    department_scope    = Column(String, nullable=True)  # null = whole-workspace access
    status              = Column(String, nullable=False, default="active")  # invited | active | disabled
    invited_by_user_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_user_workspace"),)


class UserSession(Base):
    """
    A logged-in session, identified by a bearer token. Only the SHA-256
    hash of the token is stored (mirrors how workspace/trial credentials
    are already never returned after creation in this codebase) -- a
    database leak alone does not hand out working session tokens.
    """
    __tablename__ = "user_sessions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash  = Column(String, unique=True, nullable=False, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    expires_at  = Column(DateTime, nullable=False)
    revoked_at  = Column(DateTime, nullable=True)


class ActionProposal(Base):
    """
    A governance action Ask CostPilot has proposed but not yet executed --
    the reusable propose -> confirm -> execute -> audit flow (Permissioned
    Actions phase). One row per proposal; `action_type` looks up the real
    executor in core/action_proposals.py's EXECUTORS dict, so adding a new
    kind of proposal later means adding one executor function, not a new
    table or a new confirm/reject endpoint.

    current_value/proposed_value/estimated_impact are JSON strings (this
    codebase's existing convention for JSON-shaped fields -- see
    AuditEvent.context_snapshot -- never a native JSON column type).
    estimated_impact is always a simple, honestly-labeled estimate, not a
    verified number; the AI never decides authorization itself -- that's
    check_membership()'s job alone, called independently at both propose
    and confirm time.
    """
    __tablename__ = "action_proposals"

    id                    = Column(Integer, primary_key=True, index=True)
    workspace_id          = Column(String, nullable=True, index=True)
    department            = Column(String, nullable=True)
    proposed_by_user_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_by_user_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    action_type           = Column(String, nullable=False)
    target_type           = Column(String, nullable=False)
    target_id             = Column(String, nullable=False)
    current_value         = Column(Text, nullable=True)    # JSON string
    proposed_value        = Column(Text, nullable=False)   # JSON string
    reason                = Column(Text, nullable=True)
    estimated_impact      = Column(Text, nullable=True)    # JSON string -- always "Estimated", never measured
    # JSON string -- a real run-rate projection from core.budget.project_department_spend
    # (Simulation phase), a different epistemic category from estimated_impact above:
    # a genuine computed forecast, not a bare cap-delta placeholder. Populated only for
    # action types whose executor computes one (BUDGET_CAP_SET today); null otherwise.
    simulation_result     = Column(Text, nullable=True)
    risk_level            = Column(String, default="low")  # low | medium | high | critical
    required_permission   = Column(String, nullable=False)
    status                = Column(String, nullable=False, default="awaiting_confirmation")
    # awaiting_confirmation | executed | rejected | expired
    created_at            = Column(DateTime, default=datetime.utcnow)
    resolved_at           = Column(DateTime, nullable=True)
