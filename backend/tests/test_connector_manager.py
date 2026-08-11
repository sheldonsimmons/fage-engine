"""
tests/test_connector_manager.py — correctness checks for the Connector
Manager backend additions: GET .../health (workspace onboarding health
score) and PUT .../tracked-objects (multi-object opt-in intent), plus the
"event"-tagged discovery_source on auto-created agents.
"""
import os

os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import IntegrationConnection, WorkItem, WorkItemOutcome, WorkItemSourceLink, TokenTransaction
from main import app
from api.routes_connections import _encrypt


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


def test_health_with_nothing_connected_is_zero_except_sync_health():
    client, db = _client()
    resp = client.get("/api/integrations/connections/health", params={"workspace_id": "EMPTY"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["categories"]["ai_sources"] == 0.0
    assert body["categories"]["business_context"] == 0.0
    # No syncable connections exist -- sync_health is vacuously 100, not
    # penalized for a category that doesn't apply yet.
    assert body["categories"]["sync_health"] == 100.0
    assert "Connect an AI source" in " ".join(body["recommendations"])


def test_health_reflects_real_connection_and_outcome_data():
    client, db = _client()
    conn = IntegrationConnection(
        workspace_id="WS1", platform="salesforce", display_name="sf", status="active",
        instance_url="https://x.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        mapping_json='{"work_id": "Id"}',
        last_success_at=datetime.utcnow(),
        last_outcome_sync_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(conn)
    wi1 = WorkItem(external_id="1", workspace_id="WS1", name="Deal A")
    wi2 = WorkItem(external_id="2", workspace_id="WS1", name="Deal B")
    db.add_all([wi1, wi2])
    db.commit()
    db.refresh(wi1)
    db.add(WorkItemOutcome(
        work_item_id=wi1.id, workspace_id="WS1", source_system="salesforce",
        source_object="Opportunity", external_id="1",
    ))
    db.add(TokenTransaction(
        department="Sales", model_tier="micro", input_tokens=1, output_tokens=1, cost_usd=0.01,
        timestamp=datetime.utcnow(), workspace_id="WS1", work_item_id=wi1.id,
        is_simulation=False, usage_source="estimated",
    ))
    db.commit()

    resp = client.get("/api/integrations/connections/health", params={"workspace_id": "WS1"})
    body = resp.json()
    assert body["categories"]["ai_sources"] == 100.0
    assert body["categories"]["business_context"] == 100.0
    assert body["categories"]["field_mapping"] == 100.0
    assert body["categories"]["outcome_coverage"] == 50.0  # 1 of 2 work items has an outcome
    assert body["categories"]["sync_health"] == 100.0  # synced within 24h
    assert body["categories"]["data_quality"] == 100.0  # the one transaction is resolved


def test_set_tracked_objects_trims_dedupes_and_persists():
    client, db = _client()
    conn = IntegrationConnection(
        workspace_id="WS2", platform="salesforce", display_name="sf", status="mapping",
        access_token_encrypted=_encrypt("x"),
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)

    resp = client.put(f"/api/integrations/connections/{conn.id}/tracked-objects", json={
        "objects": ["Opportunity", "  Case ", "Opportunity", ""]
    })
    assert resp.status_code == 200
    assert resp.json()["tracked_objects"] == ["Case", "Opportunity"]

    get_resp = client.get(f"/api/integrations/connections/{conn.id}")
    assert get_resp.json()["tracked_objects"] == ["Case", "Opportunity"]


def test_public_connection_includes_work_item_count_scoped_by_workspace_and_platform():
    client, db = _client()
    conn = IntegrationConnection(
        workspace_id="WS3", platform="salesforce", display_name="sf", status="active",
        access_token_encrypted=_encrypt("x"),
    )
    db.add(conn)
    wi = WorkItem(external_id="ext-1", workspace_id="WS3", name="Deal")
    db.add(wi)
    db.commit()
    db.refresh(wi)
    db.add(WorkItemSourceLink(work_item_id=wi.id, workspace_id="WS3", source_platform="salesforce", source_record_id="ext-1"))
    db.commit()

    resp = client.get("/api/integrations/connections", params={"workspace_id": "WS3"})
    assert resp.json()["connections"][0]["work_item_count"] == 1


def test_work_item_count_matches_capitalized_source_platform():
    # Found live in production: the import/outcome-sync code writes
    # source_platform capitalized ("Salesforce"/"ServiceNow"), while
    # IntegrationConnection.platform is lowercase ("salesforce") -- an
    # exact-match filter silently zeroed this count for every real
    # connection.
    client, db = _client()
    conn = IntegrationConnection(
        workspace_id="WS4", platform="salesforce", display_name="sf", status="active",
        access_token_encrypted=_encrypt("x"),
    )
    db.add(conn)
    wi = WorkItem(external_id="006ABC", workspace_id="WS4", name="Deal")
    db.add(wi)
    db.commit()
    db.refresh(wi)
    db.add(WorkItemSourceLink(work_item_id=wi.id, workspace_id="WS4", source_platform="Salesforce", source_record_id="006ABC"))
    db.commit()

    resp = client.get("/api/integrations/connections", params={"workspace_id": "WS4"})
    assert resp.json()["connections"][0]["work_item_count"] == 1
