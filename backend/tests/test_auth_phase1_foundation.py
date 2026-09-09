"""
tests/test_auth_phase1_foundation.py — Phase 1 (Foundation) of the security
architecture assessment: User/Role/UserWorkspaceMembership schema, password
hashing, session tokens, and the register/login/logout/me endpoints.

Deliberately does NOT test that any existing route now requires a session
-- nothing does yet. That retrofit is Phase 2 and is out of scope here.
This file only proves the new, additive auth surface itself works
correctly: registration, the first-user-becomes-admin bootstrap, login
success/failure, session validation, and role/permission seeding.
"""
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
    """Mirrors migrate.py's seeding logic without going through the real migration runner."""
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


def test_register_creates_user_with_no_password_leaked():
    client, db = _client()
    resp = client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "dana@example.com"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert body["token"]

    user = db.query(User).filter_by(email="dana@example.com").first()
    assert user.password_hash != "correcthorsebattery"  # never stored in plaintext
    assert user.password_hash.startswith("pbkdf2_sha256$")


def test_register_rejects_duplicate_email():
    client, db = _client()
    client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    resp = client.post("/api/auth/register", json={"email": "dana@example.com", "password": "differentpassword"})
    assert resp.status_code == 409


def test_register_rejects_short_password():
    client, db = _client()
    resp = client.post("/api/auth/register", json={"email": "dana@example.com", "password": "short"})
    assert resp.status_code == 422


def test_first_user_for_workspace_becomes_workspace_admin():
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")

    resp = client.post("/api/auth/register", json={
        "email": "dana@example.com", "password": "correcthorsebattery", "workspace_id": "WS-A",
    })
    assert resp.status_code == 200
    memberships = resp.json()["user"]["memberships"]
    assert len(memberships) == 1
    assert memberships[0]["workspace_id"] == "WS-A"
    assert memberships[0]["role"] == "workspace_admin"


def test_second_user_for_workspace_does_not_become_admin():
    client, db = _client()
    _seed_roles(db)
    _workspace(db, "WS-A")

    client.post("/api/auth/register", json={
        "email": "dana@example.com", "password": "correcthorsebattery", "workspace_id": "WS-A",
    })
    resp = client.post("/api/auth/register", json={
        "email": "sam@example.com", "password": "correcthorsebattery", "workspace_id": "WS-A",
    })
    assert resp.status_code == 200
    # No membership at all -- Phase 1 has no invite flow yet, only the
    # first-user bootstrap. A real admin has to invite them (Phase 4).
    assert resp.json()["user"]["memberships"] == []


def test_login_success_and_failure():
    client, db = _client()
    client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})

    ok = client.post("/api/auth/login", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    assert ok.status_code == 200
    assert ok.json()["token"]

    wrong = client.post("/api/auth/login", json={"email": "dana@example.com", "password": "wrongpassword"})
    assert wrong.status_code == 401

    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever12"})
    assert unknown.status_code == 401


def test_login_error_does_not_distinguish_unknown_email_from_wrong_password():
    client, db = _client()
    client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    wrong = client.post("/api/auth/login", json={"email": "dana@example.com", "password": "wrongpassword"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrongpassword"})
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_me_requires_valid_session():
    client, db = _client()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    bad_token = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert bad_token.status_code == 401


def test_me_returns_current_user_with_session():
    client, db = _client()
    reg = client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    token = reg.json()["token"]

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "dana@example.com"


def test_logout_revokes_session():
    client, db = _client()
    reg = client.post("/api/auth/register", json={"email": "dana@example.com", "password": "correcthorsebattery"})
    token = reg.json()["token"]

    still_valid = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert still_valid.status_code == 200

    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    now_invalid = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert now_invalid.status_code == 401


def test_password_hash_verifies_correctly_and_rejects_wrong_password():
    from core.auth import hash_password, verify_password
    encoded = hash_password("correcthorsebattery")
    assert verify_password("correcthorsebattery", encoded) is True
    assert verify_password("wrongpassword", encoded) is False


def test_builtin_role_permission_bundles_seed_correctly():
    client, db = _client()
    _seed_roles(db)

    admin_role = db.query(Role).filter_by(key="workspace_admin").first()
    admin_perms = {p.permission for p in db.query(RolePermission).filter_by(role_id=admin_role.id).all()}
    assert "manage_users" in admin_perms
    assert "view_prompts" in admin_perms
    assert "export_data" in admin_perms

    viewer_role = db.query(Role).filter_by(key="viewer").first()
    viewer_perms = {p.permission for p in db.query(RolePermission).filter_by(role_id=viewer_role.id).all()}
    assert "manage_budgets" not in viewer_perms
    assert "view_prompts" not in viewer_perms
    assert "view_dashboard" in viewer_perms

    governance_role = db.query(Role).filter_by(key="governance_manager").first()
    governance_perms = {p.permission for p in db.query(RolePermission).filter_by(role_id=governance_role.id).all()}
    # Deliberately restricted to workspace_admin only -- see core/rbac.py's docstring.
    assert "view_prompts" not in governance_perms
    assert "export_data" not in governance_perms
    assert "manage_users" not in governance_perms
