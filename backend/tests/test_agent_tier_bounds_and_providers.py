"""
tests/test_agent_tier_bounds_and_providers.py — PATCH
/api/agents/{agent_id}/tier-bounds and /allowed-providers (now delegating
to core.agentlake.set_agent_tier_bounds/set_agent_allowed_providers, same
"one mutation, one place" pattern as set_agent_mode), plus the third and
fourth Action Proposal action_types built on top of them --
AGENT_TIER_BOUNDS_SET and AGENT_ALLOWED_PROVIDERS_SET (proposal ->
confirm -> execute, mirroring AGENT_MODE_SET's own coverage in
test_action_proposals.py / test_agent_mode_endpoint.py).
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import ActionProposal, RegisteredAgent
from core.action_proposals import EXECUTORS
from api.ask_costpilot_tools import (
    run_propose_agent_allowed_providers_change,
    run_propose_agent_tier_bounds_change,
)
from main import app


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal()


def _agent(db, name="SupportBot-Alpha", department="WS-1:Support", min_tier=1, max_tier=4, allowed_providers=None):
    a = RegisteredAgent(
        name=name, department=department, permissions="read,write", target_table="tickets",
        collision_policy="lock", status="active", workspace_id="WS-1",
        min_tier=min_tier, max_tier=max_tier, allowed_providers=allowed_providers or [],
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ── PATCH .../tier-bounds route (now delegating to core.agentlake) ──────

def test_set_tier_bounds_route_updates_agent():
    client, db = _client()
    agent = _agent(db, min_tier=1, max_tier=4)

    resp = client.patch(f"/api/agents/{agent.id}/tier-bounds", json={"min_tier": 2, "max_tier": 3})
    assert resp.status_code == 200
    assert resp.json()["min_tier"] == 2
    assert resp.json()["max_tier"] == 3

    db.refresh(agent)
    assert agent.min_tier == 2
    assert agent.max_tier == 3


def test_set_tier_bounds_route_rejects_out_of_range():
    client, db = _client()
    agent = _agent(db)
    resp = client.patch(f"/api/agents/{agent.id}/tier-bounds", json={"min_tier": 0, "max_tier": 5})
    assert resp.status_code == 422


def test_set_tier_bounds_route_rejects_min_over_max():
    client, db = _client()
    agent = _agent(db)
    resp = client.patch(f"/api/agents/{agent.id}/tier-bounds", json={"min_tier": 3, "max_tier": 2})
    assert resp.status_code == 422


def test_set_tier_bounds_route_404_for_unknown_agent():
    client, _db = _client()
    resp = client.patch("/api/agents/999999/tier-bounds", json={"min_tier": 1, "max_tier": 2})
    assert resp.status_code == 404


# ── PATCH .../allowed-providers route (now delegating to core.agentlake) ─

def test_set_allowed_providers_route_updates_agent():
    client, db = _client()
    agent = _agent(db)

    resp = client.patch(f"/api/agents/{agent.id}/allowed-providers", json={"allowed_providers": ["openai", " anthropic "]})
    assert resp.status_code == 200
    assert resp.json()["allowed_providers"] == ["openai", "anthropic"]  # trimmed

    db.refresh(agent)
    assert agent.allowed_providers == ["openai", "anthropic"]


def test_set_allowed_providers_route_empty_list_restricts_to_nothing():
    client, db = _client()
    agent = _agent(db, allowed_providers=["openai"])

    resp = client.patch(f"/api/agents/{agent.id}/allowed-providers", json={"allowed_providers": []})
    assert resp.status_code == 200
    assert resp.json()["allowed_providers"] == []


def test_set_allowed_providers_route_404_for_unknown_agent():
    client, _db = _client()
    resp = client.patch("/api/agents/999999/allowed-providers", json={"allowed_providers": ["openai"]})
    assert resp.status_code == 404


# ── run_propose_agent_tier_bounds_change: propose -> confirm -> execute ─

def _propose_confirm_execute(db, proposal_id):
    proposal = db.query(ActionProposal).filter_by(id=proposal_id).first()
    result = EXECUTORS[proposal.action_type](db, proposal)
    proposal.status = "executed"
    db.commit()
    return result


def test_propose_tier_bounds_change_creates_proposal_not_a_direct_mutation():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    agent = _agent(db, min_tier=1, max_tier=4)

    result = run_propose_agent_tier_bounds_change(db, "WS-1", "SupportBot", 1, 2, "cost control", user_id=1)
    assert result["found"] is True
    assert result["proposal"]["action_type"] == "AGENT_TIER_BOUNDS_SET"
    assert result["proposal"]["status"] == "awaiting_confirmation"

    db.refresh(agent)
    assert agent.min_tier == 1 and agent.max_tier == 4  # unchanged until confirmed

    outcome = _propose_confirm_execute(db, result["proposal"]["id"])
    assert outcome["min_tier"] == 1
    assert outcome["max_tier"] == 2
    db.refresh(agent)
    assert agent.max_tier == 2


def test_propose_tier_bounds_change_rejects_invalid_tiers():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    _agent(db)

    result = run_propose_agent_tier_bounds_change(db, "WS-1", "SupportBot", 0, 5, "x")
    assert result["found"] is False
    assert db.query(ActionProposal).count() == 0


def test_propose_tier_bounds_change_unchanged_when_already_set():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    _agent(db, min_tier=2, max_tier=3)

    result = run_propose_agent_tier_bounds_change(db, "WS-1", "SupportBot", 2, 3, "x")
    assert result["found"] is True
    assert result["unchanged"] is True
    assert db.query(ActionProposal).count() == 0


def test_propose_tier_bounds_change_not_found():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    result = run_propose_agent_tier_bounds_change(db, "WS-1", "NoSuchBot", 1, 2, "x")
    assert result["found"] is False


def test_propose_tier_bounds_change_department_scope_blocks_other_departments():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    _agent(db, name="FinanceBot", department="WS-1:Finance")

    result = run_propose_agent_tier_bounds_change(
        db, "WS-1", "FinanceBot", 1, 2, "x", department_scope="Support",
    )
    assert result["found"] is False


# ── run_propose_agent_allowed_providers_change: propose -> confirm -> execute ─

def test_propose_allowed_providers_change_creates_proposal_not_a_direct_mutation():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    agent = _agent(db, allowed_providers=[])

    result = run_propose_agent_allowed_providers_change(db, "WS-1", "SupportBot", ["openai"], "restrict", user_id=1)
    assert result["found"] is True
    assert result["proposal"]["action_type"] == "AGENT_ALLOWED_PROVIDERS_SET"

    db.refresh(agent)
    assert agent.allowed_providers == []  # unchanged until confirmed

    outcome = _propose_confirm_execute(db, result["proposal"]["id"])
    assert outcome["allowed_providers"] == ["openai"]
    db.refresh(agent)
    assert agent.allowed_providers == ["openai"]


def test_propose_allowed_providers_change_empty_list_is_a_real_change_not_a_noop():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    _agent(db, allowed_providers=["openai"])

    result = run_propose_agent_allowed_providers_change(db, "WS-1", "SupportBot", [], "lock down", user_id=1)
    assert result["found"] is True
    assert "unchanged" not in result
    assert result["proposal"]["proposed_value"]["allowed_providers"] == []
    assert result["proposal"]["risk_level"] == "medium"  # blocking all providers is a bigger deal than restricting to some


def test_propose_allowed_providers_change_unchanged_when_same_set_different_order():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    _agent(db, allowed_providers=["anthropic", "openai"])

    result = run_propose_agent_allowed_providers_change(db, "WS-1", "SupportBot", ["openai", "anthropic"], "x")
    assert result["found"] is True
    assert result["unchanged"] is True


def test_propose_allowed_providers_change_not_found():
    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    db = sessionmaker(bind=db_engine)()
    result = run_propose_agent_allowed_providers_change(db, "WS-1", "NoSuchBot", ["openai"], "x")
    assert result["found"] is False
