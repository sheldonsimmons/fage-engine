"""
core/auth.py — password hashing, session tokens, and current-user resolution.

Phase 1 of the security architecture assessment (Foundation). Deliberately
stdlib-only (hashlib.pbkdf2_hmac) rather than adding passlib/bcrypt as a
new dependency -- PBKDF2-SHA256 with a high iteration count is the same
approach Django defaults to, and it avoids a new native-extension
dependency in the Heroku build for a v1 that explicitly isn't trying to
be more than "real, correct, and boring."

Nothing in this module is wired into any existing route yet. It backs
api/routes_auth.py (register/login/logout/me) only. Retrofitting existing
routes to require a session is Phase 2, kept deliberately separate so
this file's addition carries zero regression risk to anything that works
today.
"""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Role, User, UserSession, UserWorkspaceMembership, Workspace

PBKDF2_ITERATIONS = 260_000
SESSION_TOKEN_BYTES = 32
SESSION_TTL = timedelta(days=14)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time comparison -- never short-circuits on the first mismatched byte."""
    try:
        algorithm, iterations, salt_hex, hash_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> str:
    """Issues a new bearer token, storing only its hash. Returns the raw token once."""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    session = UserSession(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=datetime.utcnow() + SESSION_TTL,
    )
    db.add(session)
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    session = db.query(UserSession).filter_by(token_hash=_hash_token(token)).first()
    if session and not session.revoked_at:
        session.revoked_at = datetime.utcnow()
        db.commit()


def resolve_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization[7:].strip() or None


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI dependency, mirrors get_db's shape. Not used by any existing
    route yet -- only api/routes_auth.py's own endpoints (logout, me)
    depend on it today. This is the seam Phase 2's
    get_current_membership() (which additionally resolves a specific
    workspace + role from a session like this) will build on.
    """
    token = resolve_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    session = db.query(UserSession).filter_by(token_hash=_hash_token(token)).first()
    if not session or session.revoked_at or session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session is invalid or has expired")
    user = db.query(User).filter_by(id=session.user_id).first()
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="Account is not active")
    return user


# ── Phase 2: centralized workspace-membership enforcement ──────────────────
#
# Everything below is the retrofit layer from the security architecture
# assessment (Point 13) -- a single dependency, applied route by route,
# that resolves "does this caller actually have access to this
# workspace" instead of trusting a caller-supplied workspace_id string
# outright (the root cause behind nearly every confirmed finding in the
# assessment).
#
# GOVERNED BY A SOFT GRACE PERIOD, deliberately mirroring the exact
# pattern routes_router.py's _check_workspace_api_key() already
# established for API-key enforcement: AUTH_ENFORCEMENT_ENABLED unset or
# false (the default) means every retrofitted route still resolves
# identity when a valid session is presented (so a logged-in caller's
# request is genuinely scoped/authorized), but a MISSING or INVALID
# session never blocks the request -- it just proceeds with no identity,
# exactly like every page in this app behaves today. Flipping the flag to
# true makes the same check hard-fail (401/403) instead of passing
# through. This exists so the retrofit itself can ship, be tested against
# real traffic, and be verified correct BEFORE it can lock anyone out --
# turning it on is a separate, deliberate decision for once every real
# production workspace has at least one real admin membership (today,
# only workspaces someone has manually registered against have any).

def auth_enforcement_enabled() -> bool:
    return os.environ.get("AUTH_ENFORCEMENT_ENABLED", "").strip().lower() in ("1", "true", "yes")


@dataclass
class TenantContext:
    """
    What a retrofitted route actually gets once membership resolves:
    who's asking, which workspace they're asking about, what role they
    hold there, and what that role can do. `None` in soft mode means "no
    verified identity" -- callers must treat that the same as today's
    status quo (trust the caller-supplied workspace_id as before), not
    as an error.
    """
    user: User
    workspace_id: str
    role: str
    permissions: set = field(default_factory=set)
    department_scope: Optional[str] = None

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


def _resolve_membership(db: Session, user: User, workspace_id: str) -> Optional[TenantContext]:
    row = (
        db.query(UserWorkspaceMembership, Role)
        .join(Workspace, UserWorkspaceMembership.workspace_id == Workspace.id)
        .join(Role, UserWorkspaceMembership.role_id == Role.id)
        .filter(
            UserWorkspaceMembership.user_id == user.id,
            Workspace.workspace_id == workspace_id,
            UserWorkspaceMembership.status == "active",
        )
        .first()
    )
    if not row:
        return None
    membership, role = row
    from database.models import RolePermission
    perms = {p.permission for p in db.query(RolePermission).filter_by(role_id=role.id).all()}
    return TenantContext(
        user=user, workspace_id=workspace_id, role=role.key,
        permissions=perms, department_scope=membership.department_scope,
    )


def check_membership(
    db: Session, authorization: Optional[str], workspace_id: str, permission: Optional[str] = None,
) -> Optional[TenantContext]:
    """
    The actual resolution logic, callable directly (not just as a FastAPI
    dependency) for routes where workspace_id doesn't live in the path or
    query string -- e.g. create_universal_connection, where it's a field
    on the JSON request body, which a dependency function has no clean
    way to reach on its own. require_membership() below is a thin
    dependency-shaped wrapper around this for the common path/query case.

    Returns None in soft mode for any failure (no session, expired
    session, no membership, missing permission) -- never raises unless
    AUTH_ENFORCEMENT_ENABLED is true, in which case each of those becomes
    the appropriate 401/403.
    """
    enforced = auth_enforcement_enabled()
    token = resolve_bearer_token(authorization)
    if not token:
        if enforced:
            raise HTTPException(status_code=401, detail="Sign in required.")
        return None

    session = db.query(UserSession).filter_by(token_hash=_hash_token(token)).first()
    if not session or session.revoked_at or session.expires_at < datetime.utcnow():
        if enforced:
            raise HTTPException(status_code=401, detail="Session is invalid or has expired.")
        return None

    user = db.query(User).filter_by(id=session.user_id).first()
    if not user or user.status != "active":
        if enforced:
            raise HTTPException(status_code=401, detail="Account is not active.")
        return None

    ctx = _resolve_membership(db, user, workspace_id)
    if not ctx:
        if enforced:
            raise HTTPException(status_code=403, detail=f"No access to workspace '{workspace_id}'.")
        return None

    if permission and not ctx.has_permission(permission):
        if enforced:
            raise HTTPException(status_code=403, detail=f"Missing required permission: {permission}.")
        return None

    return ctx


def require_membership(permission: Optional[str] = None):
    """
    Dependency factory for the common case: workspace_id lives in the
    route's own path or query string. `require_membership()` just
    resolves who's calling and whether they belong to the workspace;
    `require_membership("manage_budgets")` additionally requires that
    specific permission.

    Reads `workspace_id` from wherever the route's own path template puts
    it -- a plain, unannotated `str` parameter lets FastAPI infer path vs.
    query the same way it would for the route function itself, so this
    one dependency works for both `/{workspace_id}/...` path-param routes
    (routes_workspaces.py) and `?workspace_id=...` query-param routes
    (everywhere else) without a second version. FastAPI merges a
    dependency's own parameters with the route's, so declaring it here
    does not require every route to redeclare it separately.

    For a body-sourced workspace_id, call check_membership() directly
    inside the route body instead (see create_universal_connection).
    """
    def _dependency(
        workspace_id: str,
        authorization: Optional[str] = Header(default=None),
        db: Session = Depends(get_db),
    ) -> Optional[TenantContext]:
        return check_membership(db, authorization, workspace_id, permission)

    return _dependency
