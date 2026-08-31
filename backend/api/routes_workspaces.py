"""
api/routes_workspaces.py — workspace registry endpoint  [Phase 1]

GET /api/workspaces
  Lists real workspaces from the `workspaces` table (see database/models.py
  Workspace, backfilled by database/backfill_workspaces.py). Replaces the
  previous approach of hardcoding workspace options in frontend JS, which
  meant any workspace not in that hardcoded list was invisible and
  unreachable through the UI the moment a user touched the switcher.
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Workspace

router = APIRouter()


def _new_api_key() -> str:
    # "cp_live_" prefix mirrors the trial flow's TRIAL_SK naming so both
    # credential types are visually identifiable in logs and generated code.
    return f"cp_live_{secrets.token_hex(24)}"


def _workspace_json(w: Workspace) -> dict:
    return {
        "workspace_id": w.workspace_id,
        "name": w.name,
        "workspace_type": w.workspace_type,
        "is_active": bool(w.is_active),
        "last_activity_at": w.last_activity_at.isoformat() if w.last_activity_at else None,
    }


@router.get("")
def list_workspaces(
    include_non_production: bool = Query(True),
    db: Session = Depends(get_db),
):
    """
    Lists every active workspace — deliberately just is_active=True, no
    type filtering. Every workspace that shouldn't be selectable (dead
    test signups, redundant demo scenarios) is archived (is_active=False)
    instead of filtered out at query time, so there's exactly one place
    ("archived or not") that decides what shows up here, not a type-based
    rule that quietly hides things depending on how they were created.
    Down to two by design: one Production workspace, one Simulated one.
    include_non_production is accepted for backwards compatibility but has
    no effect — kept so old callers don't break.
    """
    # Most-recently-active first. Also determines the frontend's default
    # selection when nothing is saved yet (global-nav.js falls back to the
    # first entry) — an alphabetical sort previously put an empty test
    # account ahead of the one with real data.
    workspaces = db.query(Workspace).filter(Workspace.is_active.is_(True)).order_by(
        nullslast(Workspace.last_activity_at.desc()), Workspace.name
    ).all()
    return {"workspaces": [_workspace_json(w) for w in workspaces]}


def _require_workspace(db: Session, workspace_id: str) -> Workspace:
    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).first()
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace '{workspace_id}' not found.")
    return workspace


@router.get("/{workspace_id}/api-key")
def get_api_key(workspace_id: str, db: Session = Depends(get_db)):
    """
    Returns whether a key has been issued, without exposing the key value
    itself outside of the moment it's issued/regenerated -- avoids the key
    sitting in a GET response (and browser history/logs) every time the
    Policy page loads.
    """
    workspace = _require_workspace(db, workspace_id)
    return {
        "workspace_id": workspace.workspace_id,
        "has_key": bool(workspace.api_key),
        "key_preview": f"{workspace.api_key[:12]}…{workspace.api_key[-4:]}" if workspace.api_key else None,
    }


def check_admin_access(request_user=None) -> None:
    """
    Swappable seam for restricting credential reveal/regenerate to
    authorized admins -- a no-op today because no user/role/auth model
    exists anywhere in this codebase (confirmed while building the
    Universal Connection feature). Real enforcement can be dropped in
    here later without touching any call site that already imports this.
    """
    return None


@router.get("/{workspace_id}/api-key/reveal")
def reveal_api_key(workspace_id: str, db: Session = Depends(get_db)):
    """
    Returns the CURRENT full key value without rotating it -- distinct
    from regenerate below, which always issues a new one. Generates a key
    if the workspace doesn't have one yet (first-time reveal during
    Universal Connection setup shouldn't require a separate "create a key"
    step). Gated by the same admin-access seam as regenerate.
    """
    check_admin_access()
    workspace = _require_workspace(db, workspace_id)
    if not workspace.api_key:
        workspace.api_key = _new_api_key()
        db.commit()
    return {"workspace_id": workspace.workspace_id, "api_key": workspace.api_key}


@router.post("/{workspace_id}/api-key/regenerate")
def regenerate_api_key(workspace_id: str, db: Session = Depends(get_db)):
    """
    Issues a new key, invalidating any previous one immediately -- there is
    no "list of valid keys," just the single current value on the row, so
    regenerating is also how a leaked key gets revoked. Every existing
    integration using the old key (any Universal Connection, and the
    packaged Salesforce connector) stops authenticating the moment this
    runs -- the frontend must warn about that before calling this, not
    just before showing a confirm dialog for its own sake.
    """
    check_admin_access()
    workspace = _require_workspace(db, workspace_id)
    workspace.api_key = _new_api_key()
    db.commit()
    return {
        "workspace_id": workspace.workspace_id,
        "api_key": workspace.api_key,
    }
