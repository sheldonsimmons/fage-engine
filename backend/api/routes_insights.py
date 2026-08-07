"""
api/routes_insights.py — live "what stands out" signal feed.

GET /api/insights returns the top few genuinely-notable signals for a
workspace right now (see core/insights.py for the check library), ranked
by severity — not a fixed rotation of the same cards regardless of
whether anything is actually notable. Additive: existing dashboard/Ask
CostPilot endpoints are untouched.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.db import get_db
from core.insights import top_signals

router = APIRouter()


@router.get("")
def get_insights(
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
):
    signals = top_signals(db, workspace_id, limit=limit)
    return {"workspace_id": workspace_id, "count": len(signals), "signals": signals}
