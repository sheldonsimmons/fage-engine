"""
tests/test_agent_mode_endpoint.py — PATCH /api/agents/{agent_id}/mode.

RegisteredAgent.mode and its enforcement (routes_router.py's Control-mode
gate: a mode="control" request against an agent whose stored mode is
still "observe" is rejected) were already committed and live before this
test existed. This endpoint is the piece that was missing: the only way
to actually flip an agent from Observe to Control, per
docs/COSTPILOT_AGENT_MODE_LIFECYCLE.md step 9 ("moving an agent into
control must never happen silently").
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


def _agent(db, name="Agentforce Bot", mode="observe"):
    a = RegisteredAgent(name=name, department="WS-1:Sales", permissions="read,write",
                         status="idle", mode=mode)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_set_agent_mode_to_control():
    client, db = _client()
    agent = _agent(db, mode="observe")

    resp = client.patch(f"/api/agents/{agent.id}/mode", json={"mode": "control"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "control"

    db.refresh(agent)
    assert agent.mode == "control"


def test_set_agent_mode_back_to_observe():
    client, db = _client()
    agent = _agent(db, mode="control")

    resp = client.patch(f"/api/agents/{agent.id}/mode", json={"mode": "observe"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "observe"


def test_set_agent_mode_rejects_invalid_value():
    client, db = _client()
    agent = _agent(db)

    resp = client.patch(f"/api/agents/{agent.id}/mode", json={"mode": "optimize"})
    assert resp.status_code == 422
    db.refresh(agent)
    assert agent.mode == "observe"  # unchanged


def test_set_agent_mode_is_case_insensitive_and_trims_whitespace():
    client, db = _client()
    agent = _agent(db, mode="observe")

    resp = client.patch(f"/api/agents/{agent.id}/mode", json={"mode": " CONTROL "})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "control"


def test_set_agent_mode_404_for_unknown_agent():
    client, db = _client()
    resp = client.patch("/api/agents/999999/mode", json={"mode": "control"})
    assert resp.status_code == 404


def test_agent_spend_summary_surfaces_mode():
    client, db = _client()
    _agent(db, name="Observed Bot", mode="observe")
    _agent(db, name="Controlled Bot", mode="control")

    resp = client.get("/api/agents/spend")
    assert resp.status_code == 200
    by_name = {row["agent_name"]: row["mode"] for row in resp.json()}
    assert by_name["Observed Bot"] == "observe"
    assert by_name["Controlled Bot"] == "control"
