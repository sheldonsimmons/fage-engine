"""
core/action_proposals.py — the reusable propose -> confirm -> execute ->
audit flow (Permissioned Actions phase).

The AI never decides authorization itself: check_membership() is the sole
authority, called independently by the propose and confirm API routes
(api/routes_action_proposals.py), never by anything in this module. This
module only stores/loads proposals and dispatches a confirmed one to its
real executor -- the same EXECUTORS-dict pattern api/ask_costpilot_tools.py
already uses for its own tool dispatch, so adding a second action_type
later means adding one function and one dict entry, not touching the API
routes or the confirm/reject logic.
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from database.models import ActionProposal
from core.auditor import write_audit_event


def create_proposal(
    db: Session,
    *,
    workspace_id: Optional[str],
    department: Optional[str],
    action_type: str,
    target_type: str,
    target_id: str,
    current_value: dict,
    proposed_value: dict,
    reason: str,
    estimated_impact: Optional[dict],
    risk_level: str,
    required_permission: str,
    user_id: Optional[int],
    simulation_result: Optional[dict] = None,
) -> ActionProposal:
    proposal = ActionProposal(
        workspace_id=workspace_id,
        department=department,
        proposed_by_user_id=user_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        current_value=json.dumps(current_value or {}),
        proposed_value=json.dumps(proposed_value or {}),
        reason=reason,
        estimated_impact=json.dumps(estimated_impact) if estimated_impact else None,
        simulation_result=json.dumps(simulation_result) if simulation_result else None,
        risk_level=risk_level,
        required_permission=required_permission,
        status="awaiting_confirmation",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    write_audit_event(
        db=db, event_type="GOVERNANCE", department=department or "global",
        routing_decision=f"{action_type}_PROPOSED",
        routing_reason=reason or "",
        prompt_payload="", model_tier=None,
        decision_outcome=f"Proposed: {target_type} {target_id} -> {proposed_value}",
        user_id=user_id, proposal_id=proposal.id,
    )
    return proposal


def serialize_proposal(proposal: ActionProposal) -> dict:
    return {
        "id": proposal.id,
        "workspace_id": proposal.workspace_id,
        "department": proposal.department,
        "action_type": proposal.action_type,
        "target_type": proposal.target_type,
        "target_id": proposal.target_id,
        "current_value": json.loads(proposal.current_value) if proposal.current_value else None,
        "proposed_value": json.loads(proposal.proposed_value),
        "reason": proposal.reason,
        "estimated_impact": json.loads(proposal.estimated_impact) if proposal.estimated_impact else None,
        "simulation_result": json.loads(proposal.simulation_result) if proposal.simulation_result else None,
        "risk_level": proposal.risk_level,
        "required_permission": proposal.required_permission,
        "status": proposal.status,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "resolved_at": proposal.resolved_at.isoformat() if proposal.resolved_at else None,
    }


def _execute_budget_cap_set(db: Session, proposal: ActionProposal) -> dict:
    from core.budget import set_cap

    proposed = json.loads(proposal.proposed_value)
    return set_cap(db, proposal.target_id, float(proposed["new_cap_usd"]))


EXECUTORS = {
    "BUDGET_CAP_SET": _execute_budget_cap_set,
}
