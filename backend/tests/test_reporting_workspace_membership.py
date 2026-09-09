"""
tests/test_reporting_workspace_membership.py — security architecture
assessment, Finding 9 (partial closure, highest-sensitivity slice):
get_dashboard, get_business_impact (+ its two sibling endpoints), and
POST /api/metrics/query already correctly scope their query WHEN GIVEN a
workspace_id -- the gap is that workspace_id is optional everywhere in
the reporting surface, and every one of these endpoints silently
aggregates across every workspace when it's omitted.

Unlike Findings 5/6/8, this is NOT closed by making workspace_id
required (that would break every legitimate "all workspaces" admin view
still relied on elsewhere) -- instead, when a workspace_id IS supplied,
a soft-mode-gated membership check now runs (same AUTH_ENFORCEMENT_ENABLED
gating as the rest of Phase 2). Dozens of other reporting endpoints
across the app still have the bare "optional, defaults to global" gap
undisturbed -- this covers only the highest-sensitivity slice
(Business Impact's real financial figures, the Executive Dashboard, and
the general-purpose metrics query engine).
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import Role, RolePermission, Workspace
from core.rbac import BUILTIN_ROLES
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


def _seed_roles(db):
    for key, spec in BUILTIN_ROLES.items():
        role = Role(key=key, label=spec["label"], is_builtin=True)
        db.add(role)
        db.commit()
        db.refresh(role)
        for permission in spec["permissions"]:
            db.add(RolePermission(role_id=role.id, permission=permission))
    db.commit()


def _workspace(db, workspace_id="WS-A"):
    ws = Workspace(workspace_id=workspace_id, name=workspace_id)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _register_and_login(client, email, workspace_id=None):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "correcthorsebattery", "workspace_id": workspace_id,
    })
    assert resp.status_code == 200
    return resp.json()["token"]


def _enforce(monkeypatch, on: bool):
    monkeypatch.setenv("AUTH_ENFORCEMENT_ENABLED", "true" if on else "false")


# ── soft mode (production default): nothing breaks ──────────────────────────

def test_get_dashboard_with_workspace_id_no_session_soft_mode():
    client, db = _client()
    _workspace(db, "WS-A")
    resp = client.get("/api/dashboard", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200  # unchanged from today's behavior


def test_get_dashboard_without_workspace_id_soft_mode():
    client, db = _client()
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200  # "all workspaces" view, unaffected either way


def test_business_impact_with_workspace_id_no_session_soft_mode():
    client, db = _client()
    _workspace(db, "WS-A")
    resp = client.get("/api/dashboard/business-impact", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200


def test_metrics_query_with_workspace_id_no_session_soft_mode():
    client, db = _client()
    _workspace(db, "WS-A")
    resp = client.post("/api/metrics/query", json={"workspace_id": "WS-A", "metrics": ["ai_spend"]})
    assert resp.status_code == 200


# ── hard mode (once deliberately enabled) ────────────────────────────────────

def test_get_dashboard_requires_session_when_workspace_id_given_and_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _workspace(db, "WS-A")
    resp = client.get("/api/dashboard", params={"workspace_id": "WS-A"})
    assert resp.status_code == 401


def test_get_dashboard_still_open_without_workspace_id_even_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    # No workspace_id at all -- the deliberate "all workspaces" admin view.
    # Enforcement only ever applies to a request that DID name a workspace.
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200


def test_get_dashboard_denies_wrong_workspace_membership_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    _workspace(db, "WS-B")
    token = _register_and_login(client, "intruder@example.com", workspace_id="WS-B")

    resp = client.get("/api/dashboard", params={"workspace_id": "WS-A"}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_get_dashboard_allows_correct_workspace_membership_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    token = _register_and_login(client, "owner@example.com", workspace_id="WS-A")

    resp = client.get("/api/dashboard", params={"workspace_id": "WS-A"}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_business_impact_denies_wrong_workspace_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    _workspace(db, "WS-B")
    token = _register_and_login(client, "intruder@example.com", workspace_id="WS-B")

    resp = client.get(
        "/api/dashboard/business-impact", params={"workspace_id": "WS-A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_metrics_query_denies_wrong_workspace_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    _workspace(db, "WS-B")
    token = _register_and_login(client, "intruder@example.com", workspace_id="WS-B")

    resp = client.post(
        "/api/metrics/query", json={"workspace_id": "WS-A", "metrics": ["ai_spend"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_metrics_query_allows_correct_workspace_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    token = _register_and_login(client, "owner@example.com", workspace_id="WS-A")

    resp = client.post(
        "/api/metrics/query", json={"workspace_id": "WS-A", "metrics": ["ai_spend"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
