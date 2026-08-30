"""
core/agentlake.py's registration/claim/collision/release logic had zero
direct test coverage before this file -- every other "agent" test only
uses RegisteredAgent as a fixture for unrelated subsystems. This is the
safety net for the Agent Registry -> Agent Intelligence Profile work:
these lock in today's collision/lock/queue/skip behavior so a later,
purely-additive schema change (workspace_id, business_purpose, owner,
approval_status columns) can't silently break AgentLake's live-traffic
logic without a test failing.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import AuditEvent, RegisteredAgent
from core.agentlake import (
    claim_record, register_agent, release_lock, simulate_collision,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _agent(db, name, collision_policy="lock", department="WS-1:Sales"):
    a = RegisteredAgent(name=name, department=department, permissions="read,write",
                         collision_policy=collision_policy, status="idle")
    db.add(a); db.commit(); db.refresh(a)
    return a


# ── register_agent ──────────────────────────────────────────────────────────

def test_register_agent_creates_idle_agent():
    db = _session()
    result = register_agent(db, name="Agent A", department="WS-1:Sales",
                             permissions="read,write", target_table="opportunities")
    assert result["status"] == "idle"
    assert result["name"] == "Agent A"
    # Governance/lifecycle fields (added for the Agent Intelligence
    # Profile) default sensibly and don't disturb runtime status.
    assert result["approval_status"] == "unreviewed"
    assert result["business_purpose"] is None
    assert result["owner"] is None


def test_register_agent_rejects_duplicate_name_and_leaves_session_usable():
    db = _session()
    register_agent(db, name="Agent A", department="WS-1:Sales",
                    permissions="read,write", target_table="opportunities")
    try:
        register_agent(db, name="Agent A", department="WS-1:Support",
                        permissions="read,write", target_table="cases")
        assert False, "expected ValueError on duplicate name"
    except ValueError as e:
        assert "already registered" in str(e)
    # The IntegrityError's rollback must not poison the session -- a
    # subsequent, unrelated write should still succeed.
    register_agent(db, name="Agent B", department="WS-1:Support",
                    permissions="read,write", target_table="cases")
    assert db.query(RegisteredAgent).count() == 2


# ── claim_record ─────────────────────────────────────────────────────────────

def test_claim_record_grants_when_no_collision():
    db = _session()
    a = _agent(db, "Agent A")
    result = claim_record(db, a.id, "opportunities", 100)
    assert result["collision"] is False
    db.refresh(a)
    assert a.status == "active"
    assert a.target_table == "opportunities"
    assert a.target_record_id == 100


def test_claim_record_lock_policy_locks_both_agents_and_writes_audit_event():
    db = _session()
    a = _agent(db, "Agent A", collision_policy="lock")
    b = _agent(db, "Agent B", collision_policy="lock")
    claim_record(db, a.id, "opportunities", 100)  # a holds the record

    result = claim_record(db, b.id, "opportunities", 100)  # b collides

    assert result["collision"] is True
    assert result["policy"] == "lock"
    db.refresh(a); db.refresh(b)
    assert a.status == "locked"
    assert b.status == "locked"
    assert a.lock_reason == b.lock_reason
    assert db.query(AuditEvent).filter_by(event_type="COLLISION_LOCK").count() == 1


def test_claim_record_skip_policy_abandons_silently():
    db = _session()
    a = _agent(db, "Agent A", collision_policy="lock")
    b = _agent(db, "Agent B", collision_policy="skip")
    claim_record(db, a.id, "opportunities", 100)

    result = claim_record(db, b.id, "opportunities", 100)

    assert result["collision"] is True
    assert result["policy"] == "skip"
    db.refresh(a); db.refresh(b)
    assert a.status == "active"   # the original holder is untouched
    assert b.status == "idle"     # the skipper backs off quietly
    assert b.lock_reason is None
    assert db.query(AuditEvent).filter_by(event_type="COLLISION_SKIP").count() == 1


def test_claim_record_queue_policy_queues_requester_only():
    db = _session()
    a = _agent(db, "Agent A", collision_policy="lock")
    b = _agent(db, "Agent B", collision_policy="queue")
    claim_record(db, a.id, "opportunities", 100)

    result = claim_record(db, b.id, "opportunities", 100)

    assert result["collision"] is True
    assert result["policy"] == "queue"
    db.refresh(a); db.refresh(b)
    assert a.status == "active"   # the original holder is untouched
    assert b.status == "queued"
    assert b.lock_reason is not None
    assert db.query(AuditEvent).filter_by(event_type="COLLISION_QUEUE").count() == 1


def test_claim_record_unknown_agent_raises():
    db = _session()
    try:
        claim_record(db, 999, "opportunities", 100)
        assert False, "expected ValueError for unknown agent id"
    except ValueError as e:
        assert "not found" in str(e)


# ── release_lock ─────────────────────────────────────────────────────────────

def test_release_lock_clears_a_locked_agent():
    db = _session()
    a = _agent(db, "Agent A", collision_policy="lock")
    b = _agent(db, "Agent B", collision_policy="lock")
    claim_record(db, a.id, "opportunities", 100)
    claim_record(db, b.id, "opportunities", 100)  # both now locked

    release_lock(db, a.id)

    db.refresh(a)
    assert a.status == "idle"
    assert a.target_record_id is None
    assert a.locked_at is None
    assert a.lock_reason is None


def test_release_lock_on_already_idle_agent_is_a_harmless_noop():
    db = _session()
    a = _agent(db, "Agent A")
    result = release_lock(db, a.id)  # never claimed anything
    assert result["released"] is True
    db.refresh(a)
    assert a.status == "idle"


# ── simulate_collision ───────────────────────────────────────────────────────

def test_simulate_collision_first_agent_holds_second_locks():
    db = _session()
    a = _agent(db, "Agent A", collision_policy="lock")
    b = _agent(db, "Agent B", collision_policy="lock")

    result = simulate_collision(db, a.id, b.id, "opportunities", 100)

    assert result["collision"] is True
    db.refresh(a); db.refresh(b)
    assert a.status == "locked"
    assert b.status == "locked"


def test_simulate_collision_requires_at_least_two_registered_agents():
    db = _session()
    _agent(db, "Agent A")
    try:
        simulate_collision(db, 999, 998, "opportunities", 100)  # neither id exists, only 1 agent registered
        assert False, "expected ValueError with fewer than two agents"
    except ValueError as e:
        assert "at least two agents" in str(e).lower()
