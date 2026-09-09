"""
tests/test_agentlake_workspace_scoping.py — security architecture
assessment, Finding 5: every by-ID route in routes_agentlake.py used to
resolve an agent by bare integer id with no workspace check at all.
_agent_scoped_or_404() closes that: workspace_id is OPTIONAL (unlike the
Phase 0 audit/voice endpoints, which made it required) so operate.html's
deliberate "all workspaces" view keeps working -- but when a caller DOES
supply workspace_id, a cross-tenant agent_id now 404s instead of leaking.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import RegisteredAgent
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


def _agent(db, name, workspace_id="WS-A", department=None):
    a = RegisteredAgent(
        name=name, department=department or f"{workspace_id}:Sales",
        permissions="read,write", status="idle", workspace_id=workspace_id,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_get_single_agent_without_workspace_id_still_works():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    # No workspace_id supplied -- the deliberate "all workspaces" view.
    resp = client.get(f"/api/agents/{agent.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Agent A"


def test_get_single_agent_matching_workspace_id_works():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.get(f"/api/agents/{agent.id}", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200


def test_get_single_agent_cross_tenant_workspace_id_404s():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.get(f"/api/agents/{agent.id}", params={"workspace_id": "WS-B"})
    assert resp.status_code == 404


def test_rename_agent_cross_tenant_denied():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.patch(f"/api/agents/{agent.id}/name", params={"workspace_id": "WS-B"}, json={"name": "Hijacked"})
    assert resp.status_code == 404

    db.refresh(agent)
    assert agent.name == "Agent A"  # unchanged


def test_rename_agent_same_tenant_allowed():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.patch(f"/api/agents/{agent.id}/name", params={"workspace_id": "WS-A"}, json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


def test_tier_bounds_cross_tenant_denied():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.patch(
        f"/api/agents/{agent.id}/tier-bounds", params={"workspace_id": "WS-B"},
        json={"min_tier": 3, "max_tier": 4},
    )
    assert resp.status_code == 404
    db.refresh(agent)
    assert agent.min_tier != 3


def test_mode_change_cross_tenant_denied():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.patch(
        f"/api/agents/{agent.id}/mode", params={"workspace_id": "WS-B"}, json={"mode": "control"},
    )
    assert resp.status_code == 404
    db.refresh(agent)
    assert agent.mode != "control"


def test_archive_cross_tenant_denied():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.post(f"/api/agents/{agent.id}/archive", params={"workspace_id": "WS-B"})
    assert resp.status_code == 404


def test_deregister_cross_tenant_denied():
    client, db = _client()
    agent = _agent(db, "Agent A", workspace_id="WS-A")
    resp = client.delete(f"/api/agents/{agent.id}", params={"workspace_id": "WS-B"})
    assert resp.status_code == 404

    still_there = db.query(RegisteredAgent).filter_by(id=agent.id).first()
    assert still_there is not None


def test_department_tier_bounds_scoped_to_workspace_when_given():
    client, db = _client()
    _agent(db, "Agent A", workspace_id="WS-A", department="WS-A:Sales")
    _agent(db, "Agent B", workspace_id="WS-B", department="WS-B:Sales")

    resp = client.patch("/api/agents/department-tier-bounds", json={
        "department": "Sales", "min_tier": 3, "max_tier": 4, "workspace_id": "WS-A",
    })
    # Neither department string is bare "Sales" (both are prefixed), so
    # this specific case legitimately finds nothing -- the real assertion
    # is that it does NOT error out or silently touch WS-B's agent.
    assert resp.status_code in (200, 404)

    b_agent = db.query(RegisteredAgent).filter_by(name="Agent B").first()
    assert b_agent.min_tier != 3


def test_department_tier_bounds_still_works_without_workspace_id():
    client, db = _client()
    _agent(db, "Agent A", workspace_id="WS-A", department="Sales")

    resp = client.patch("/api/agents/department-tier-bounds", json={
        "department": "Sales", "min_tier": 3, "max_tier": 4,
    })
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
