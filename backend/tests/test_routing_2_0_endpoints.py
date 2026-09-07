"""
Routing 2.0 Phase 1/2 admin endpoints, end to end through the real FastAPI
app: PATCH /api/routing-config/budget-pressure-threshold and
PATCH /api/agents/{id}/allowed-providers.
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
    db = TestingSessionLocal()
    return TestClient(app), db


def test_budget_pressure_threshold_endpoint_roundtrips():
    client, db = _client()

    resp = client.patch("/api/routing-config/budget-pressure-threshold", json={"threshold_pct": 75.0})
    assert resp.status_code == 200
    assert resp.json()["budget_pressure_threshold_pct"] == 75.0

    resp = client.get("/api/routing-config")
    assert resp.json()["budget_pressure_threshold_pct"] == 75.0

    resp = client.patch("/api/routing-config/budget-pressure-threshold", json={"threshold_pct": None})
    assert resp.status_code == 200
    assert resp.json()["budget_pressure_threshold_pct"] is None

    app.dependency_overrides.clear()


def test_budget_pressure_threshold_rejects_out_of_range():
    client, db = _client()
    resp = client.patch("/api/routing-config/budget-pressure-threshold", json={"threshold_pct": 150.0})
    assert resp.status_code == 400
    app.dependency_overrides.clear()


def test_allowed_providers_endpoint_roundtrips():
    client, db = _client()
    agent = RegisteredAgent(name="TestBot", department="Support", permissions="read,write")
    db.add(agent)
    db.commit()
    db.refresh(agent)

    resp = client.patch(f"/api/agents/{agent.id}/allowed-providers", json={"allowed_providers": ["anthropic", "openai"]})
    assert resp.status_code == 200
    assert resp.json()["allowed_providers"] == ["anthropic", "openai"]

    resp = client.get(f"/api/agents/{agent.id}")
    assert resp.json()["allowed_providers"] == ["anthropic", "openai"]

    # Clearing it back to unrestricted
    resp = client.patch(f"/api/agents/{agent.id}/allowed-providers", json={"allowed_providers": []})
    assert resp.status_code == 200
    assert resp.json()["allowed_providers"] == []

    app.dependency_overrides.clear()


def test_allowed_providers_endpoint_404s_for_unknown_agent():
    client, db = _client()
    resp = client.patch("/api/agents/999999/allowed-providers", json={"allowed_providers": ["anthropic"]})
    assert resp.status_code == 404
    app.dependency_overrides.clear()
