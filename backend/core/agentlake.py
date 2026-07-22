"""
core/agentlake.py — Agentlake Registry & Concurrency Traffic Cop  [Step 5]

Responsibilities:
  - List all registered AI agents and their current status
  - Let an agent "claim" a record before writing to it
  - Detect when two agents claim the same record simultaneously (collision)
  - Apply each agent's lock, queue, or skip policy and audit the outcome
  - Allow a supervisor to release a lock manually
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from database.models import RegisteredAgent

# ── Platform inference from agent name prefix ─────────────────────────────────
_PREFIX_MAP = {
    "SF-":  "Salesforce",
    "SN-":  "ServiceNow",
    "HB-":  "HubSpot",
    "HS-":  "HubSpot",
    "MS-":  "Microsoft",
    "ZD-":  "Zendesk",
    "SAP-": "SAP",
    "ORG-": "Salesforce",  # Org-level SF bots
}

def infer_platform(name: str, explicit: str = None) -> str:
    """Return explicit platform if provided, else infer from agent name prefix."""
    if explicit:
        return explicit
    upper = name.upper()
    for prefix, platform in _PREFIX_MAP.items():
        if upper.startswith(prefix):
            return platform
    return "Custom"


def display_department(department: str) -> str:
    """Hide internal workspace prefixes like WORKSPACE_ID:Support from UI labels."""
    text = (department or "").strip()
    if ":" not in text:
        return text
    prefix, label = text.split(":", 1)
    # Trial workspace ids are stored as long uppercase hex-like keys. Keep other
    # colon labels intact if they are not workspace prefixes.
    if len(prefix) >= 12 and prefix.replace("-", "").isalnum():
        return label.strip() or text
    return label.strip() or text


def display_agent_name(name: str, department: str = None, platform: str = None) -> str:
    """Return a human-friendly agent label while preserving the raw name in storage."""
    text = (name or "").strip()
    dept = display_department(department) or "Agent"
    source = (platform or "").strip()
    if not text:
        return f"{source or 'AI'} {dept} Agent".strip()
    if ":" in text:
        prefix, label = text.split(":", 1)
        if len(prefix) >= 12 and prefix.replace("-", "").isalnum():
            return label.strip() or f"{dept} Agent"
    return text


def agent_active_recently(agent: RegisteredAgent, window_seconds: int = 5) -> bool:
    """Treat only active or claimed agents as live, with a short UI catch-up buffer."""
    status = (agent.status or "idle").lower()
    if status in ("locked", "queued"):
        return False
    if agent.target_record_id:
        return True
    if status != "active":
        return False
    if not agent.last_used_at:
        return False
    return datetime.utcnow() - agent.last_used_at <= timedelta(seconds=window_seconds)


def register_agent(db: Session, name: str, department: str, permissions: str, target_table: str, collision_policy: str = "lock", source_platform: str = None) -> dict:
    """Register a new AI agent in the Agentlake registry."""
    agent = RegisteredAgent(
        name=name,
        department=department,
        source_platform=infer_platform(name, source_platform),
        permissions=permissions,
        target_table=target_table,
        collision_policy=collision_policy,
        status="idle",
    )
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"An agent named '{name}' is already registered.")
    db.refresh(agent)
    return _serialize(agent)


def deregister_agent(db: Session, agent_id: int) -> dict:
    """Remove an agent from the registry entirely."""
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent ID {agent_id} not found.")
    name = agent.name
    db.delete(agent)
    db.commit()
    return {"deleted": True, "message": f"Agent '{name}' removed from registry."}


def list_agents(db: Session, include_archived: bool = False, workspace_id: str = None) -> list:
    """Return registered agents. Archived agents are hidden by default."""
    q = db.query(RegisteredAgent)
    if workspace_id:
        q = q.filter(RegisteredAgent.department.like(f"{workspace_id}:%"))
    if not include_archived:
        q = q.filter((RegisteredAgent.archived == False) | (RegisteredAgent.archived == None))
    return [_serialize(a) for a in q.all()]


def archive_agent(db: Session, agent_id: int) -> dict:
    """Soft-delete: hide an agent from the live grid while preserving its history."""
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent ID {agent_id} not found.")
    agent.archived        = True
    agent.status          = "idle"
    agent.target_record_id = None
    agent.locked_at       = None
    agent.lock_reason     = None
    db.commit()
    return {"archived": True, "message": f"Agent '{agent.name}' archived. History preserved in reports and audit log."}


def unarchive_agent(db: Session, agent_id: int) -> dict:
    """Restore an archived agent back to the live registry."""
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent ID {agent_id} not found.")
    agent.archived = False
    db.commit()
    return _serialize(agent)


def get_agent(db: Session, agent_id: int):
    """Return a single agent by ID."""
    a = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    return _serialize(a) if a else None


def claim_record(db: Session, agent_id: int, table: str, record_id: int) -> dict:
    """
    Traffic Cop — an agent declares intent to write a specific record.

    Logic:
      1. Check if any OTHER agent already holds a claim on (table, record_id)
      2. If yes  → apply the requesting agent's lock, queue, or skip policy.
      3. If no   → grant the claim. Set agent status to 'active'.
    """
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent ID {agent_id} not found.")

    # Look for any other active agent already targeting this exact record
    blocker = (
        db.query(RegisteredAgent)
        .filter(
            RegisteredAgent.target_table     == table,
            RegisteredAgent.target_record_id == record_id,
            RegisteredAgent.status.in_(["active", "locked"]),
            RegisteredAgent.id               != agent_id,
        )
        .first()
    )

    if blocker:
        lock_reason = (
            f"Concurrency collision: both '{agent.name}' and '{blocker.name}' "
            f"attempted to write {table} record #{record_id} simultaneously."
        )
        now = datetime.utcnow()
        policy = agent.collision_policy or "lock"

        agent.target_table     = table
        agent.target_record_id = record_id

        if policy == "skip":
            # ── SKIP — abandon silently, log it ───────────────────────────────
            agent.status      = "idle"
            agent.locked_at   = None
            agent.lock_reason = None
            db.commit()
            audit = _record_collision(db, agent, blocker, table, record_id, "skip", lock_reason)
            return {
                "collision":  True,
                "policy":     "skip",
                "agent":      _serialize(agent),
                "lock_reason": lock_reason,
                "message":    f"'{agent.name}' skipped {table} #{record_id} — already held by '{blocker.name}'.",
                "audit_id":   audit["id"],
            }

        elif policy == "queue":
            # ── QUEUE — hold the request for a later release or retry ──────────
            agent.status      = "queued"
            agent.locked_at   = now
            agent.lock_reason = lock_reason
            db.commit()
            audit = _record_collision(db, agent, blocker, table, record_id, "queue", lock_reason)
            return {
                "collision":  True,
                "policy":     "queue",
                "agent":      _serialize(agent),
                "lock_reason": lock_reason,
                "message":    f"'{agent.name}' queued for {table} #{record_id} — awaiting release or retry after '{blocker.name}' finishes.",
                "audit_id":   audit["id"],
            }

        else:
            # ── LOCK (default) — lock both, require supervisor ─────────────────
            agent.status      = "locked"
            agent.locked_at   = now
            agent.lock_reason = lock_reason

            blocker.status      = "locked"
            blocker.locked_at   = now
            blocker.lock_reason = lock_reason

            db.commit()
            audit = _record_collision(db, agent, blocker, table, record_id, "lock", lock_reason)
            return {
                "collision":     True,
                "policy":        "lock",
                "locked_agents": [_serialize(agent), _serialize(blocker)],
                "lock_reason":   lock_reason,
                "table":         table,
                "record_id":     record_id,
                "audit_id":      audit["id"],
            }

    # ── No collision — grant the claim ─────────────────────────────────────────
    agent.status           = "active"
    agent.target_table     = table
    agent.target_record_id = record_id
    agent.locked_at        = None
    agent.lock_reason      = None
    db.commit()

    return {
        "collision": False,
        "agent":     _serialize(agent),
        "message":   f"Claim granted. '{agent.name}' now holds exclusive write access to {table} record #{record_id}.",
    }


def release_lock(db: Session, agent_id: int) -> dict:
    """
    Supervisor action: release a locked or active agent back to idle.
    Clears the record claim so other agents can proceed.
    """
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise ValueError(f"Agent ID {agent_id} not found.")

    agent.status           = "idle"
    agent.target_record_id = None
    agent.locked_at        = None
    agent.lock_reason      = None
    db.commit()

    return {
        "released": True,
        "agent":    _serialize(agent),
        "message":  f"Agent '{agent.name}' released and set to idle.",
    }


def simulate_collision(db: Session, agent_id_1: int, agent_id_2: int, table: str, record_id: int) -> dict:
    """
    Demo helper: force two specific agents to simultaneously claim the same record.
    If agent_id_1/2 are not found, falls back to the first two available agents.
    Agent 1 claims first (succeeds), then Agent 2 claims (triggers collision).
    """
    # Resolve agents — fall back to first two available if hardcoded IDs not found
    a1 = db.query(RegisteredAgent).filter_by(id=agent_id_1).first()
    a2 = db.query(RegisteredAgent).filter_by(id=agent_id_2).first()

    if not a1 or not a2:
        all_agents = db.query(RegisteredAgent).order_by(RegisteredAgent.id).all()
        if len(all_agents) < 2:
            raise ValueError("At least two agents must be registered to simulate a collision.")
        a1 = all_agents[0]
        a2 = all_agents[1]

    # Reset both to idle so the sim starts clean
    for a in [a1, a2]:
        a.status           = "idle"
        a.target_record_id = None
        a.locked_at        = None
        a.lock_reason      = None
    db.commit()

    # Agent 1 claims the record — this will succeed
    claim_record(db, a1.id, table, record_id)

    # Agent 2 tries to claim the same record — collision fires
    result = claim_record(db, a2.id, table, record_id)

    return result


def _record_collision(
    db: Session,
    agent: RegisteredAgent,
    blocker: RegisteredAgent,
    table: str,
    record_id: int,
    policy: str,
    reason: str,
) -> dict:
    """Write one immutable audit event for every collision policy outcome."""
    from core.auditor import write_audit_event

    event_type = {
        "lock": "COLLISION_LOCK",
        "queue": "COLLISION_QUEUE",
        "skip": "COLLISION_SKIP",
    }[policy]
    outcome = (
        f"Collision {policy}: '{agent.name}' encountered '{blocker.name}' on "
        f"{table} record #{record_id}."
    )
    return write_audit_event(
        db=db,
        event_type=event_type,
        department=agent.department or "Unassigned",
        routing_decision="COLLISION",
        routing_reason=reason,
        prompt_payload=f"{table} record #{record_id}",
        model_tier=None,
        agent_id=agent.id,
        decision_outcome=outcome,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────────────────────

def _serialize(a: RegisteredAgent) -> dict:
    platform = a.source_platform or infer_platform(a.name)
    display_dept = display_department(a.department)
    display_name = display_agent_name(a.name, a.department, platform)
    return {
        "id":               a.id,
        "name":             a.name,
        "display_name":     display_name,
        "department":       a.department,
        "display_department": display_dept,
        "source_platform":  platform,
        "permissions":      a.permissions,
        "target_table":     a.target_table,
        "target_record_id": a.target_record_id,
        "status":           a.status,
        "active_recently":  agent_active_recently(a),
        "collision_policy": a.collision_policy or "lock",
        "locked_at":        a.locked_at.isoformat() if a.locked_at else None,
        "lock_reason":      a.lock_reason,
        "last_used_at":     a.last_used_at.isoformat() if a.last_used_at else None,
        "archived":         bool(a.archived),
        "min_tier":         a.min_tier if a.min_tier is not None else 1,
        "max_tier":         a.max_tier if a.max_tier is not None else 4,
        "pruning_enabled":  a.pruning_enabled if a.pruning_enabled is not None else True,
    }
