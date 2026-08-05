"""
api/routes_workspaces.py — workspace registry endpoint  [Phase 1]

GET /api/workspaces
  Lists real workspaces from the `workspaces` table (see database/models.py
  Workspace, backfilled by database/backfill_workspaces.py). Replaces the
  previous approach of hardcoding workspace options in frontend JS, which
  meant any workspace not in that hardcoded list was invisible and
  unreachable through the UI the moment a user touched the switcher.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import nullslast
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import Workspace

router = APIRouter()


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
    include_non_production: bool = Query(False),
    db: Session = Depends(get_db),
):
    """
    Real customer workspaces by default (workspace_type='production').
    Pass include_non_production=true to also list demo/simulation/legacy
    workspaces — used by an admin/debug view, not the default switcher.
    """
    query = db.query(Workspace).filter(Workspace.is_active.is_(True))
    if not include_non_production:
        query = query.filter(Workspace.workspace_type == "production")
    # Most-recently-active first, empty/unused accounts last. This also
    # determines the frontend's default selection when nothing is saved
    # yet (global-nav.js falls back to the first entry in this list) — an
    # alphabetical sort put an empty test account ("CostPilot Technology")
    # ahead of the one real account with actual data, making it look like
    # data had vanished for anyone who'd never explicitly picked a workspace.
    workspaces = query.order_by(
        nullslast(Workspace.last_activity_at.desc()), Workspace.name
    ).all()
    return {"workspaces": [_workspace_json(w) for w in workspaces]}
