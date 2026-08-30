"""
api/routes_agent_profile.py — Agent Intelligence Profile API routes.

GET   /api/agents/{id}/intelligence-profile — one agent's full financial
      + business-impact story (economics, attention signals, model usage,
      WorkItem/outcome attribution, activity timeline).
PATCH /api/agents/{id}/governance           — set business_purpose/owner/
      approval_status. Kept separate from routes_agentlake.py's existing
      tier-bounds/pruning/mode/name PATCH endpoints (same single-
      responsibility pattern), and from core/agentlake.py's collision/
      claim logic entirely -- this file never imports from it.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import RegisteredAgent
from core.agent_intelligence import get_agent_intelligence_profile

router = APIRouter()


@router.get("/{agent_id}/intelligence-profile")
def agent_intelligence_profile(agent_id: int, db: Session = Depends(get_db)):
    profile = get_agent_intelligence_profile(db, agent_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Agent ID {agent_id} not found.")
    return profile


class AgentGovernanceRequest(BaseModel):
    business_purpose: Optional[str] = None
    owner: Optional[str] = None
    approval_status: Optional[str] = None  # unreviewed | pending_review | approved | deprecated | retired


VALID_APPROVAL_STATUSES = {"unreviewed", "pending_review", "approved", "deprecated", "retired"}


@router.patch("/{agent_id}/governance")
def update_agent_governance(agent_id: int, body: AgentGovernanceRequest, db: Session = Depends(get_db)):
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent ID {agent_id} not found.")

    if body.approval_status is not None:
        if body.approval_status not in VALID_APPROVAL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"approval_status must be one of {sorted(VALID_APPROVAL_STATUSES)}",
            )
        agent.approval_status = body.approval_status
    if body.business_purpose is not None:
        agent.business_purpose = body.business_purpose
    if body.owner is not None:
        agent.owner = body.owner
    db.commit()

    return get_agent_intelligence_profile(db, agent_id)
