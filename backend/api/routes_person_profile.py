"""
api/routes_person_profile.py — Person Intelligence Profile API routes.

GET /api/people/{external_id}/profile — one person's factual AI-usage
    story (economics, agents used, accounts, work items, model usage,
    activity timeline). Read-only -- no governance/edit endpoint, unlike
    the Agent profile: a person isn't a governed entity CostPilot owns
    configuration for, only an identity its ledger attributes activity to.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.db import get_db
from core.person_intelligence import get_person_intelligence_profile

router = APIRouter()


@router.get("/{external_id}/profile")
def person_intelligence_profile(
    external_id: str,
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    profile = get_person_intelligence_profile(db, workspace_id, external_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No activity found for person '{external_id}'.")
    return profile
