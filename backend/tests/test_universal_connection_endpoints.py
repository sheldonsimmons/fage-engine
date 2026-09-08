"""
tests/test_universal_connection_endpoints.py — Universal Connection
creation + multi-step test-event verification (api/routes_connections_
universal.py). New feature: connect any/custom platform as a real saved
IntegrationConnection, distinct from routes_connections.py's OAuth-gated
create_connection() (untouched by this feature).
"""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import IntegrationConnection, TokenTransaction, Workspace
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


def _seed_workspace(db, workspace_id="WS-1"):
    db.add(Workspace(workspace_id=workspace_id, name="Test Workspace"))
    db.commit()


def test_create_universal_connection_issues_a_workspace_api_key():
    client, db = _client()
    _seed_workspace(db)

    resp = client.post("/api/integrations/connections/universal", json={
        "workspace_id": "WS-1", "platform": "Internal Copilot",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_key"].startswith("cp_live_")
    assert body["connection"]["platform"] == "Internal Copilot"
    assert body["connection"]["display_name"] == "Internal Copilot"
    assert body["connection"]["connection_key"].startswith("conn_")
    assert body["connection"]["activity_status"] == "no_events_yet"


def test_create_universal_connection_reuses_existing_workspace_api_key():
    client, db = _client()
    _seed_workspace(db)
    ws = db.query(Workspace).filter_by(workspace_id="WS-1").first()
    ws.api_key = "cp_live_existingkey"
    db.commit()

    resp = client.post("/api/integrations/connections/universal", json={
        "workspace_id": "WS-1", "platform": "Internal Copilot",
    })
    assert resp.json()["api_key"] == "cp_live_existingkey"


def test_create_universal_connection_rejects_duplicate_name():
    client, db = _client()
    _seed_workspace(db)
    client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"})

    resp = client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"})
    assert resp.status_code == 409


def test_create_universal_connection_rejects_unknown_workspace():
    client, db = _client()
    resp = client.post("/api/integrations/connections/universal", json={"workspace_id": "no-such-ws", "platform": "X"})
    assert resp.status_code == 404


def test_verify_test_event_reports_not_attempted_when_no_event_sent():
    client, db = _client()
    _seed_workspace(db)
    create = client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"}).json()
    connection_id = create["connection"]["id"]

    resp = client.get(f"/api/integrations/connections/universal/{connection_id}/verify-test-event")
    body = resp.json()
    assert body["fully_verified"] is False
    steps_by_key = {s["key"]: s for s in body["steps"]}
    assert steps_by_key["authentication"]["passed"] is False
    assert steps_by_key["reporting_visibility"]["passed"] is None


def test_verify_test_event_passes_every_step_for_a_real_keyed_event():
    from database.models import RegisteredAgent

    client, db = _client()
    _seed_workspace(db)
    create = client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"}).json()
    connection = create["connection"]

    agent = RegisteredAgent(name="Internal Copilot Agent", department="WS-1:Support", permissions="read,write")
    db.add(agent); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", source_platform="Internal Copilot",
        connection_key=connection["connection_key"], business_purpose="costpilot_connection_test",
        agent_id=agent.id, is_simulation=True,
        model_tier="Scout", model_name="gpt-4o-mini", input_tokens=100, output_tokens=50,
        cost_usd=0.01, timestamp=datetime.utcnow(),
    ))
    db.commit()

    resp = client.get(f"/api/integrations/connections/universal/{connection['id']}/verify-test-event")
    body = resp.json()
    for step in body["steps"]:
        # outcome_reporting is optional -- no mode="outcome" test event was
        # sent in this activity-only test, so it's correctly not_attempted
        # (passed=None) rather than failed, and must not block
        # fully_verified. Every required step still must be exactly True.
        if step["key"] == "outcome_reporting":
            assert step["passed"] is None, f"outcome_reporting: {step['detail']}"
            continue
        assert step["passed"] is True, f"{step['key']} failed: {step['detail']}"
    assert body["fully_verified"] is True


def test_verify_test_event_flags_workspace_isolation_failure():
    client, db = _client()
    _seed_workspace(db)
    _seed_workspace(db, workspace_id="WS-2")
    create = client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"}).json()
    connection = create["connection"]

    # Event keyed to this connection but recorded under a DIFFERENT
    # workspace -- a real isolation bug this check exists to catch.
    db.add(TokenTransaction(
        department="WS-2:Support", workspace_id="WS-2", source_platform="Internal Copilot",
        connection_key=connection["connection_key"], business_purpose="costpilot_connection_test",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=0.01, timestamp=datetime.utcnow(),
    ))
    db.commit()

    resp = client.get(f"/api/integrations/connections/universal/{connection['id']}/verify-test-event")
    body = resp.json()
    steps_by_key = {s["key"]: s for s in body["steps"]}
    assert steps_by_key["workspace_isolation"]["passed"] is False
    assert body["fully_verified"] is False


def test_verify_test_event_ignores_events_outside_lookback_window():
    client, db = _client()
    _seed_workspace(db)
    create = client.post("/api/integrations/connections/universal", json={"workspace_id": "WS-1", "platform": "Internal Copilot"}).json()
    connection = create["connection"]

    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", source_platform="Internal Copilot",
        connection_key=connection["connection_key"], business_purpose="costpilot_connection_test",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=0.01,
        timestamp=datetime.utcnow() - timedelta(hours=2),
    ))
    db.commit()

    resp = client.get(f"/api/integrations/connections/universal/{connection['id']}/verify-test-event")
    assert resp.json()["fully_verified"] is False
    assert resp.json()["steps"][0]["passed"] is False


def test_verify_test_event_outcome_step_passes_when_outcome_test_event_matches_the_activity_one():
    client, db = _client()
    _seed_workspace(db)
    create = client.post("/api/integrations/connections/universal", json={
        "workspace_id": "WS-1", "platform": "Custom Claims App",
    }).json()
    connection = create["connection"]
    connection_key = connection["connection_key"]

    activity = client.post("/api/route", json={
        "mode": "observe",
        "connection_key": connection_key,
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1", "agent_name": "Claims Assistant"},
        "work": {
            "external_id": "CLAIM-TEST-1", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "usage": {"model_name": "gpt-4.1-mini", "input_tokens": 100, "output_tokens": 50},
    })
    assert activity.status_code == 200

    outcome = client.post("/api/route", json={
        "mode": "outcome",
        "connection_key": connection_key,
        "event_id": "verify-outcome-evt-1",
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1"},
        "work": {
            "external_id": "CLAIM-TEST-1", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": {"status": "approved", "value": 500.0, "success": True, "date": datetime.utcnow().isoformat()},
    })
    assert outcome.status_code == 200
    assert outcome.json()["outcome_recorded"] is True

    resp = client.get(f"/api/integrations/connections/universal/{connection['id']}/verify-test-event")
    body = resp.json()
    steps_by_key = {s["key"]: s for s in body["steps"]}
    assert steps_by_key["outcome_reporting"]["passed"] is True, steps_by_key["outcome_reporting"]["detail"]
    assert body["fully_verified"] is True


def test_verify_test_event_outcome_step_fails_when_outcome_targets_a_different_workitem():
    client, db = _client()
    _seed_workspace(db)
    create = client.post("/api/integrations/connections/universal", json={
        "workspace_id": "WS-1", "platform": "Custom Claims App",
    }).json()
    connection = create["connection"]
    connection_key = connection["connection_key"]

    client.post("/api/route", json={
        "mode": "observe",
        "connection_key": connection_key,
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1", "agent_name": "Claims Assistant"},
        "work": {
            "external_id": "CLAIM-A", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "usage": {"model_name": "gpt-4.1-mini", "input_tokens": 100, "output_tokens": 50},
    })
    client.post("/api/route", json={
        "mode": "outcome",
        "connection_key": connection_key,
        "event_id": "verify-outcome-evt-2",
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1"},
        "work": {
            "external_id": "CLAIM-B", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": {"status": "approved", "date": datetime.utcnow().isoformat()},
    })

    resp = client.get(f"/api/integrations/connections/universal/{connection['id']}/verify-test-event")
    steps_by_key = {s["key"]: s for s in resp.json()["steps"]}
    assert steps_by_key["outcome_reporting"]["passed"] is False
