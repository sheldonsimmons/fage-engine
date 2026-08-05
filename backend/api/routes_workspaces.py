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
