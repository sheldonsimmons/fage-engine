"""
api/routes_auth.py — user registration, login, logout, and "who am I."

Phase 1 (Foundation) of the security architecture assessment. These are
new, additive endpoints -- nothing here changes the behavior of any
existing route. No page in the app requires a session yet; that
retrofit is Phase 2, deliberately kept separate.

POST /api/auth/register — create a user. If workspace_id is supplied and
    that workspace has no members yet, the new user is bootstrapped as
    its Workspace Admin (a self-serve alternative to the backfill script
    the assessment describes for existing workspaces -- the first person
    to register for a given workspace becomes its owner, same pattern
    almost every multi-tenant SaaS product uses for its very first user).
POST /api/auth/login    — verify credentials, issue a session token.
POST /api/auth/logout   — revoke the session token used to call it.
GET  /api/auth/me       — the current user + every workspace membership.
"""

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Role, User, UserWorkspaceMembership, Workspace
from core.auth import (
    create_session, get_current_user, hash_password, revoke_session, verify_password,
    resolve_bearer_token,
)

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: Optional[str] = None
    workspace_id: Optional[str] = None  # external string id, e.g. from localStorage cp_workspace_id


class LoginRequest(BaseModel):
    email: str
    password: str


class MembershipOut(BaseModel):
    workspace_id: str
    workspace_name: str
    role: str
    role_label: str
    department_scope: Optional[str] = None
    status: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    memberships: List[MembershipOut] = []


class AuthResponse(BaseModel):
    token: str
    user: UserOut


def _memberships_for(db: Session, user: User) -> List[MembershipOut]:
    rows = (
        db.query(UserWorkspaceMembership, Workspace, Role)
        .join(Workspace, UserWorkspaceMembership.workspace_id == Workspace.id)
        .join(Role, UserWorkspaceMembership.role_id == Role.id)
        .filter(UserWorkspaceMembership.user_id == user.id)
        .all()
    )
    return [
        MembershipOut(
            workspace_id=ws.workspace_id,
            workspace_name=ws.name,
            role=role.key,
            role_label=role.label,
            department_scope=membership.department_scope,
            status=membership.status,
        )
        for membership, ws, role in rows
    ]


def _user_out(db: Session, user: User) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name,
        memberships=_memberships_for(db, user),
    )


@router.post("/register", response_model=AuthResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(
        email=email,
        display_name=(body.display_name or "").strip() or None,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    workspace_id = (body.workspace_id or "").strip()
    if workspace_id:
        workspace = db.query(Workspace).filter_by(workspace_id=workspace_id).first()
        if workspace:
            already_has_members = db.query(UserWorkspaceMembership).filter_by(
                workspace_id=workspace.id
            ).first() is not None
            if not already_has_members:
                admin_role = db.query(Role).filter_by(key="workspace_admin").first()
                if admin_role:
                    db.add(UserWorkspaceMembership(
                        user_id=user.id, workspace_id=workspace.id, role_id=admin_role.id,
                        status="active",
                    ))
                    db.commit()

    token = create_session(db, user)
    return AuthResponse(token=token, user=_user_out(db, user))


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.query(User).filter_by(email=email).first()
    # Constant-shape response either way (no "user not found" vs "wrong
    # password" distinction) -- don't help an attacker enumerate accounts.
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="This account has been disabled.")

    from datetime import datetime
    user.last_login_at = datetime.utcnow()
    db.commit()

    token = create_session(db, user)
    return AuthResponse(token=token, user=_user_out(db, user))


@router.post("/logout")
def logout(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    token = resolve_bearer_token(authorization)
    if token:
        revoke_session(db, token)
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_out(db, user)
