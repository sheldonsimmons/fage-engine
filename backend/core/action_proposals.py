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


def _execute_agent_mode_set(db: Session, proposal: ActionProposal) -> dict:
    """
    Second action_type on this scaffold (Permissioned Actions, proving out
    the "one function + one dict entry" extension path the module
    docstring promises). target_id is the agent's integer id, stored as a
    string like every other ActionProposal.target_id.
    """
    from core.agentlake import set_agent_mode

    proposed = json.loads(proposal.proposed_value)
    return set_agent_mode(db, int(proposal.target_id), proposed["mode"])


EXECUTORS = {
    "BUDGET_CAP_SET": _execute_budget_cap_set,
    "AGENT_MODE_SET": _execute_agent_mode_set,
}


def measure_budget_cap_outcome(db: Session, workspace_id: Optional[str], department: str) -> dict:
    """
    Measure phase: did an already-executed budget-cap change actually work?
    Compares real spend since the change to the daily rate Simulation
    projected at propose time (ActionProposal.simulation_result) -- the
    first proposal-history query anywhere in this module; every prior use
    of ActionProposal was either create or a single-row lookup by id.

    department must be the exact workspace-prefixed value stored on the
    row (e.g. "WORKSPACE-1:Support") -- callers doing flexible name
    matching (api/ask_costpilot_tools.py's run_measure_budget_cap_outcome)
    resolve that first via core.budget.get_all_budgets, same as the
    propose/simulate tools already do.
    """
    from core.budget import recomputed_department_spend

    proposal = (
        db.query(ActionProposal)
        .filter(
            ActionProposal.workspace_id == workspace_id,
            ActionProposal.department == department,
            ActionProposal.status == "executed",
            ActionProposal.action_type == "BUDGET_CAP_SET",
        )
        .order_by(ActionProposal.resolved_at.desc())
        .first()
    )
    if proposal is None:
        return {
            "found": False,
            "message": f"No executed budget-cap change found for '{department.split(':')[-1]}'.",
        }

    days_since_change = max((datetime.utcnow() - proposal.resolved_at).days, 0)
    result = {
        "found": True,
        "department": department.split(":")[-1],
        "reason": proposal.reason or "",
        "current_value": json.loads(proposal.current_value) if proposal.current_value else None,
        "proposed_value": json.loads(proposal.proposed_value),
        "resolved_at": proposal.resolved_at.isoformat(),
        "days_since_change": days_since_change,
    }
    if days_since_change < 1:
        result["too_soon"] = True
        result["message"] = "This change was made less than a day ago -- too soon to measure a meaningful spend trend."
        return result
    result["too_soon"] = False

    spend_by_department = recomputed_department_spend(db, workspace_id, date_from=proposal.resolved_at)
    actual_spend_since_change = spend_by_department.get(department.split(":")[-1].casefold(), 0.0)
    actual_daily_rate_usd = round(actual_spend_since_change / days_since_change, 4)
    result["actual_spend_since_change_usd"] = round(actual_spend_since_change, 2)
    result["actual_daily_rate_usd"] = actual_daily_rate_usd

    simulation = json.loads(proposal.simulation_result) if proposal.simulation_result else None
    if simulation and "daily_rate_usd" in simulation:
        projected_daily_rate_usd = float(simulation["daily_rate_usd"])
        result["projected_daily_rate_usd"] = projected_daily_rate_usd
        if projected_daily_rate_usd > 0:
            rate_change_pct = round(
                (actual_daily_rate_usd - projected_daily_rate_usd) / projected_daily_rate_usd * 100, 1
            )
        else:
            rate_change_pct = None
        result["rate_change_usd"] = round(actual_daily_rate_usd - projected_daily_rate_usd, 4)
        result["rate_change_pct"] = rate_change_pct
        # A +/-10% dead zone -- daily spend is naturally noisy day to day,
        # and calling a 3% wobble "accelerated" or "slowed" would read as a
        # confident verdict the data doesn't actually support.
        if rate_change_pct is None:
            result["trend"] = "unknown"
        elif rate_change_pct > 10:
            result["trend"] = "accelerated"
        elif rate_change_pct < -10:
            result["trend"] = "slowed"
        else:
            result["trend"] = "about the same"
    return result
