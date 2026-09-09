"""
tests/test_auth_phase2_enforcement.py — Phase 2 of the security architecture
assessment: the centralized require_membership()/check_membership()
enforcement layer, and its first two real applications (closing Finding 1
for real: API key reveal/regenerate, and Universal Connection creation).

Governed by AUTH_ENFORCEMENT_ENABLED, mirroring the exact soft-grace-period
pattern _check_workspace_api_key() already established in this codebase:
off (the default, and what production runs today) means a missing/invalid/
insufficient session never blocks the request -- only once it's explicitly
turned on does the same check start returning 401/403. Both modes are
tested here; the "off" tests are what proves this deploy is safe (nothing
in production behaves differently yet), the "on" tests are what proves the
mechanism actually works once someone deliberately flips it.
"""
import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import Role, RolePermission, User, UserWorkspaceMembership, Workspace
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


def _workspace(db, workspace_id="WS-A", api_key=None):
    ws = Workspace(workspace_id=workspace_id, name=workspace_id, api_key=api_key)
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


# ── Soft mode (production default) — nothing breaks ─────────────────────────

def test_reveal_key_works_with_no_session_when_enforcement_is_off(monkeypatch):
    _enforce(monkeypatch, False)
    client, db = _client()
    _workspace(db, "WS-A")

    resp = client.get("/api/workspaces/WS-A/api-key/reveal")
    assert resp.status_code == 200  # unchanged from today's behavior


def test_reveal_key_works_with_wrong_users_session_when_enforcement_is_off(monkeypatch):
    _enforce(monkeypatch, False)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    _workspace(db, "WS-B")
    token = _register_and_login(client, "intruder@example.com", workspace_id="WS-B")

    # Soft mode: even a session that clearly has no business in WS-A does
    # not get blocked yet -- this IS the current, honest production state.
    resp = client.get("/api/workspaces/WS-A/api-key/reveal", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ── Hard mode (once deliberately enabled) — the real fix ────────────────────

def test_reveal_key_requires_a_session_when_enforcement_is_on(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _workspace(db, "WS-A")

    resp = client.get("/api/workspaces/WS-A/api-key/reveal")
    assert resp.status_code == 401


def test_reveal_key_denies_a_user_with_no_membership_in_that_workspace(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    _workspace(db, "WS-B")
    token = _register_and_login(client, "intruder@example.com", workspace_id="WS-B")

    resp = client.get("/api/workspaces/WS-A/api-key/reveal", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_reveal_key_allows_that_workspaces_own_admin(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    token = _register_and_login(client, "owner@example.com", workspace_id="WS-A")

    resp = client.get("/api/workspaces/WS-A/api-key/reveal", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_reveal_key_denies_a_non_admin_role_in_the_right_workspace(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    ws = _workspace(db, "WS-A")

    # First user becomes admin; manually demote a second user to viewer
    # in the same workspace to prove the permission check (not just the
    # membership check) actually matters.
    admin_token = _register_and_login(client, "owner@example.com", workspace_id="WS-A")
    viewer_user = User(email="viewer@example.com", password_hash=None, status="active")
    db.add(viewer_user)
    db.commit()
    db.refresh(viewer_user)
    viewer_role = db.query(Role).filter_by(key="viewer").first()
    db.add(UserWorkspaceMembership(user_id=viewer_user.id, workspace_id=ws.id, role_id=viewer_role.id, status="active"))
    db.commit()

    from core.auth import create_session
    viewer_token = create_session(db, viewer_user)

    resp = client.get("/api/workspaces/WS-A/api-key/reveal", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403
    assert admin_token  # sanity: the admin token from setup is distinct and still valid


def test_universal_connection_create_requires_manage_integrations_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    ws = _workspace(db, "WS-A")

    # A department_manager does not have manage_integrations (see core/rbac.py).
    dept_user = User(email="dept@example.com", password_hash=None, status="active")
    db.add(dept_user)
    db.commit()
    db.refresh(dept_user)
    dept_role = db.query(Role).filter_by(key="department_manager").first()
    db.add(UserWorkspaceMembership(user_id=dept_user.id, workspace_id=ws.id, role_id=dept_role.id, status="active"))
    db.commit()

    from core.auth import create_session
    dept_token = create_session(db, dept_user)

    resp = client.post(
        "/api/integrations/connections/universal",
        json={"workspace_id": "WS-A", "platform": "Custom ERP"},
        headers={"Authorization": f"Bearer {dept_token}"},
    )
    assert resp.status_code == 403


def test_universal_connection_create_allows_workspace_admin_when_enforced(monkeypatch):
    _enforce(monkeypatch, True)
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")
    token = _register_and_login(client, "owner@example.com", workspace_id="WS-A")

    resp = client.post(
        "/api/integrations/connections/universal",
        json={"workspace_id": "WS-A", "platform": "Custom ERP"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_check_membership_and_require_membership_soft_mode_return_none_never_raise(monkeypatch):
    _enforce(monkeypatch, False)
    _client_unused, db = _client()
    from core.auth import check_membership
    result = check_membership(db, authorization=None, workspace_id="WS-does-not-exist", permission="manage_users")
    assert result is None
