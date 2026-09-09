"""
tests/test_connections_workspace_scoping.py — security architecture
assessment, Finding 6: _get_connection() (the shared resolver behind ~19
routes in routes_connections.py) used to take a bare integer connection_id
with no workspace check at all -- a caller who knew/guessed another
tenant's connection_id could read its config or trigger discover/
sync-outcomes/import-work-items against that tenant's real, live
connected system using ITS stored OAuth token.

workspace_id is OPTIONAL (same reasoning as Finding 5's
_agent_scoped_or_404): defaults to None so nothing breaks for any caller
that doesn't supply one; when supplied, a cross-tenant id 404s instead of
resolving. This file tests the shared resolver directly (the core of the
fix) plus a couple of representative endpoints through the real HTTP
layer.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import IntegrationConnection
from api.routes_connections import _get_connection
from main import app
from fastapi import HTTPException


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


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


def _connection(db, workspace_id="WS-A", platform="salesforce", display_name="Primary"):
    c = IntegrationConnection(workspace_id=workspace_id, platform=platform, display_name=display_name, status="connected")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── _get_connection() directly (the shared resolver, function-call style) ──

def test_get_connection_without_workspace_id_still_resolves():
    db = _session()
    conn = _connection(db, workspace_id="WS-A")
    result = _get_connection(db, conn.id)  # no workspace_id -- today's default behavior
    assert result.id == conn.id


def test_get_connection_matching_workspace_id_resolves():
    db = _session()
    conn = _connection(db, workspace_id="WS-A")
    result = _get_connection(db, conn.id, workspace_id="WS-A")
    assert result.id == conn.id


def test_get_connection_cross_tenant_workspace_id_404s():
    db = _session()
    conn = _connection(db, workspace_id="WS-A")
    try:
        _get_connection(db, conn.id, workspace_id="WS-B")
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 404


def test_get_connection_unknown_id_404s_regardless():
    db = _session()
    try:
        _get_connection(db, 999999, workspace_id="WS-A")
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 404


# ── representative endpoints, through the real HTTP layer ──────────────────

def test_get_connection_endpoint_cross_tenant_404s():
    client, db = _client()
    conn = _connection(db, workspace_id="WS-A")

    ok = client.get(f"/api/integrations/connections/{conn.id}", params={"workspace_id": "WS-A"})
    assert ok.status_code == 200

    denied = client.get(f"/api/integrations/connections/{conn.id}", params={"workspace_id": "WS-B"})
    assert denied.status_code == 404

    unscoped = client.get(f"/api/integrations/connections/{conn.id}")
    assert unscoped.status_code == 200  # today's default behavior, unchanged


def test_set_tracked_objects_cross_tenant_denied():
    client, db = _client()
    conn = _connection(db, workspace_id="WS-A")

    resp = client.put(
        f"/api/integrations/connections/{conn.id}/tracked-objects",
        params={"workspace_id": "WS-B"},
        json={"objects": ["Opportunity"]},
    )
    assert resp.status_code == 404
