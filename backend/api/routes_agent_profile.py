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
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import RegisteredAgent
from core.agent_intelligence import get_agent_intelligence_profile
from core.auditor import write_audit_event
from core.auth import check_membership

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
def update_agent_governance(
    agent_id: int, body: AgentGovernanceRequest, db: Session = Depends(get_db),
    workspace_id: Optional[str] = Query(None), authorization: Optional[str] = Header(default=None),
):
    agent = db.query(RegisteredAgent).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent ID {agent_id} not found.")

    if body.approval_status is not None:
        if body.approval_status not in VALID_APPROVAL_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"approval_status must be one of {sorted(VALID_APPROVAL_STATUSES)}",
            )
    # Permissioned Actions slice 1: soft-mode-gated (see core/auth.py's
    # AUTH_ENFORCEMENT_ENABLED docstring) -- a no-op today, real once
    # turned on. Same "manage_agents" permission routes_agentlake.py's
    # own mutations already use for this agent.
    ctx = check_membership(db, authorization, workspace_id or "default", "manage_agents")

    if body.approval_status is not None:
        agent.approval_status = body.approval_status
    if body.business_purpose is not None:
        agent.business_purpose = body.business_purpose
    if body.owner is not None:
        agent.owner = body.owner
    db.commit()
    write_audit_event(
        db=db, event_type="GOVERNANCE", department=agent.department or "global",
        agent_id=agent.id,
        routing_decision="AGENT_GOVERNANCE_UPDATED",
        routing_reason=f"Governance fields updated for agent '{agent.name}'",
        prompt_payload="", model_tier=None,
        decision_outcome=(
            f"approval_status={agent.approval_status}, owner={agent.owner}, "
            f"business_purpose={agent.business_purpose}"
        ),
        user_id=ctx.user.id if ctx else None,
    )

    return get_agent_intelligence_profile(db, agent_id)
