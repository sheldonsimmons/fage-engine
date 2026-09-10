"""
api/routes_action_proposals.py — propose / confirm / reject a governance
action (Permissioned Actions phase).

POST /api/ask/actions/propose        — create a proposal
POST /api/ask/actions/{id}/confirm   — execute a pending proposal
POST /api/ask/actions/{id}/reject    — cancel a pending proposal

check_membership() is the sole authority on whether a caller may propose
or confirm -- called independently at each step (a permission could
change between propose and confirm), never inferred by the AI. Soft-mode
today (AUTH_ENFORCEMENT_ENABLED unset): a missing/insufficient permission
never blocks the call, exactly like every other route touched during the
Permissioned Actions work -- real gating is a deliberate, separate later
decision.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import ActionProposal
from core.auth import check_membership
from core.auditor import write_audit_event
from core.action_proposals import create_proposal, serialize_proposal, EXECUTORS

router = APIRouter()

PROPOSAL_TTL = timedelta(hours=1)


class ProposeRequest(BaseModel):
    workspace_id: Optional[str] = None
    department: Optional[str] = None
    action_type: str
    target_type: str
    target_id: str
    current_value: dict = {}
    proposed_value: dict
    reason: str = ""
    estimated_impact: Optional[dict] = None
    risk_level: str = "low"
    required_permission: str


def _load_proposal(db: Session, proposal_id: int) -> ActionProposal:
    proposal = db.query(ActionProposal).filter_by(id=proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found.")
    return proposal


def _expire_if_stale(db: Session, proposal: ActionProposal) -> None:
    if proposal.status != "awaiting_confirmation":
        return
    if datetime.utcnow() - proposal.created_at > PROPOSAL_TTL:
        proposal.status = "expired"
        proposal.resolved_at = datetime.utcnow()
        db.commit()


@router.post("/propose")
def propose_action(
    body: ProposeRequest, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    ctx = check_membership(db, authorization, body.workspace_id or "default", body.required_permission)
    proposal = create_proposal(
        db,
        workspace_id=body.workspace_id, department=body.department,
        action_type=body.action_type, target_type=body.target_type, target_id=body.target_id,
        current_value=body.current_value, proposed_value=body.proposed_value,
        reason=body.reason, estimated_impact=body.estimated_impact,
        risk_level=body.risk_level, required_permission=body.required_permission,
        user_id=ctx.user.id if ctx else None,
    )
    return serialize_proposal(proposal)


@router.post("/{proposal_id}/confirm")
def confirm_action(
    proposal_id: int, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    proposal = _load_proposal(db, proposal_id)
    _expire_if_stale(db, proposal)
    if proposal.status == "expired":
        raise HTTPException(status_code=410, detail="This proposal has expired. Ask again to create a new one.")
    if proposal.status != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail=f"Proposal is already {proposal.status}.")

    ctx = check_membership(db, authorization, proposal.workspace_id or "default", proposal.required_permission)

    executor = EXECUTORS.get(proposal.action_type)
    if executor is None:
        raise HTTPException(status_code=400, detail=f"No executor registered for action_type '{proposal.action_type}'.")

    try:
        result = executor(db, proposal)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    proposal.status = "executed"
    proposal.resolved_at = datetime.utcnow()
    proposal.resolved_by_user_id = ctx.user.id if ctx else None
    db.commit()

    write_audit_event(
        db=db, event_type="GOVERNANCE", department=proposal.department or "global",
        routing_decision=f"{proposal.action_type}_EXECUTED",
        routing_reason=f"Confirmed proposal #{proposal.id}",
        prompt_payload="", model_tier=None,
        decision_outcome=f"Executed: {proposal.target_type} {proposal.target_id}",
        user_id=ctx.user.id if ctx else None, proposal_id=proposal.id,
    )

    return {"proposal": serialize_proposal(proposal), "result": result}


@router.post("/{proposal_id}/reject")
def reject_action(
    proposal_id: int, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    proposal = _load_proposal(db, proposal_id)
    _expire_if_stale(db, proposal)
    if proposal.status != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail=f"Proposal is already {proposal.status}.")

    ctx = check_membership(db, authorization, proposal.workspace_id or "default", proposal.required_permission)

    proposal.status = "rejected"
    proposal.resolved_at = datetime.utcnow()
    proposal.resolved_by_user_id = ctx.user.id if ctx else None
    db.commit()

    write_audit_event(
        db=db, event_type="GOVERNANCE", department=proposal.department or "global",
        routing_decision=f"{proposal.action_type}_REJECTED",
        routing_reason=f"Rejected proposal #{proposal.id}",
        prompt_payload="", model_tier=None,
        decision_outcome=f"Rejected: {proposal.target_type} {proposal.target_id}",
        user_id=ctx.user.id if ctx else None, proposal_id=proposal.id,
    )

    return serialize_proposal(proposal)
