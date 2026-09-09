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
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import User, UserSession

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
