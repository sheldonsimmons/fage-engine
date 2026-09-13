"""
api/routes_agentlake.py — Agentlake Registry & Traffic Cop API routes  [Step 5]

GET  /api/agents                    — list all registered agents
GET  /api/agents/{id}               — single agent detail
POST /api/agents/claim              — agent claims a record (collision check runs here)
POST /api/agents/simulate-collision — demo: force two agents to collide on the same record
POST /api/agents/{id}/release       — supervisor releases a locked agent
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import RegisteredAgent, TokenTransaction
from core.agentlake import (
    list_agents, get_agent, claim_record,
    release_lock, simulate_collision,
    register_agent, deregister_agent,
    archive_agent, unarchive_agent,
    display_agent_name, display_department, agent_active_recently,
)
from core.auth import check_membership
from core.workspace_scope import workspace_filter

router = APIRouter()


def _agent_scoped_or_404(db: Session, agent_id: int, workspace_id: Optional[str]) -> RegisteredAgent:
    """
    Security architecture assessment, Finding 5: every by-ID route in this
    file used to resolve an agent by bare integer id with no workspace
    check at all -- any caller who knew/guessed another tenant's agent_id
    could read or mutate its full governance config. workspace_id is
    OPTIONAL here (unlike the Phase 0 audit/voice endpoints, which made it
    required) specifically to preserve this page's legitimate "all
    workspaces" operator view (operate.html's agentlakeWorkspaceId() can
    deliberately be empty) -- when a workspace_id IS supplied, the match
    is enforced and a mismatch 404s exactly like an unknown id, rather
    than leaking which id belongs to a different tenant.
    """
    query = db.query(RegisteredAgent).filter(RegisteredAgent.id == agent_id)
    if workspace_id:
        query = query.filter(workspace_filter(RegisteredAgent, workspace_id))
    agent = query.first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    return agent


def _check_agent_permission(
    db: Session, authorization: Optional[str], workspace_id: Optional[str], permission: str = "manage_agents",
) -> None:
    """
    Soft-mode-gated permission check (see core/auth.py's module docstring
    on AUTH_ENFORCEMENT_ENABLED) -- a no-op today, real once turned on.
    Skipped entirely when workspace_id is empty (the deliberate "all
    workspaces" operator view has no single workspace to check membership
    against, same reasoning as _agent_scoped_or_404 above).
    """
    if workspace_id:
        check_membership(db, authorization, workspace_id, permission)


class AgentStatus(BaseModel):
    id:               int
    name:             str
    display_name:     Optional[str] = None
    department:       str
    display_department: Optional[str] = None
    source_platform:  Optional[str]
    permissions:      str
    target_table:     Optional[str]
    target_record_id: Optional[int]
    status:           str
    collision_policy: Optional[str]
    locked_at:        Optional[str]
    lock_reason:      Optional[str]
    last_used_at:     Optional[str] = None
    archived:         Optional[bool] = False
    active_recently:  Optional[bool] = False
    min_tier:         Optional[int]  = 1
    max_tier:         Optional[int]  = 4
    pruning_enabled:  Optional[bool] = True
    workspace_id:     Optional[str]  = None
    business_purpose: Optional[str]  = None
    owner:            Optional[str]  = None
    approval_status:  Optional[str]  = "unreviewed"
    allowed_providers: Optional[list] = None  # empty/None = unrestricted


class TierBoundsRequest(BaseModel):
    min_tier: int  # 1–4
    max_tier: int  # 1–4


class AllowedProvidersRequest(BaseModel):
    # Empty list = explicitly restrict to nothing (blocks all routing at
    # this agent, same as any other misconfiguration); None/omit clears
    # the restriction entirely -- use PATCH with [] vs. not calling this
    # endpoint at all to tell the two apart.
    allowed_providers: list = []


class DepartmentTierBoundsRequest(TierBoundsRequest):
    department: str
    workspace_id: Optional[str] = None


class PruningToggleRequest(BaseModel):
    enabled: bool


class ModeRequest(BaseModel):
    mode: str  # "observe" | "control" -- see docs/COSTPILOT_AGENT_MODE_LIFECYCLE.md


class RenameAgentRequest(BaseModel):
    name: str


class RegisterRequest(BaseModel):
    name:             str
    department:       str
    source_platform:  Optional[str] = None   # explicit; inferred from name prefix if omitted
    permissions:      str = "read,write"
    target_table:     str = "tickets"
    collision_policy: str = "lock"


class ClaimRequest(BaseModel):
    agent_id:  int
    table:     str
    record_id: int


class SimulateCollisionRequest(BaseModel):
    agent_id_1: int = 1   # SupportBot-Alpha
    agent_id_2: int = 2   # SupportBot-Beta
    table:      str = "tickets"
    record_id:  int = 3   # The billing dispute ticket


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AgentStatus)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new AI agent in the Agentlake registry."""
    try:
        return register_agent(db, req.name, req.department, req.permissions, req.target_table, req.collision_policy, req.source_platform)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{agent_id}")
def deregister(
    agent_id: int, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Remove an agent from the registry."""
    _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    try:
        return deregister_agent(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/spend")
def agent_spend_summary(workspace_id: str = None, db: Session = Depends(get_db)):
    """
    Per-agent spend summary — aggregates TokenTransaction by agent_id.
    Returns every registered agent with their total cost, token counts,
    call volume, and top model tier used. Ordered by total cost descending.

    Had no workspace_id parameter at all until this pass -- confirmed
    live (2026-09-13): this always returned EVERY registered agent across
    EVERY workspace combined, regardless of which workspace the Agent
    Lake page had selected (that page's own fetch was also missing the
    param, a separate frontend bug fixed alongside this one). Filtered by
    department prefix, not the RegisteredAgent.workspace_id column
    directly, to match core.agentlake.list_agents()'s own established
    convention for this exact table -- using a different filter here
    would just create a second, differently-scoped definition of
    "this workspace's agents."
    """
    from database.models import RegisteredAgent

    TIER_ORDER = {"Strategist": 4, "Advisor": 3, "flagship": 3,
                  "Analyst": 2, "Scout": 1, "micro": 1}

    query = db.query(RegisteredAgent).filter(RegisteredAgent.archived.isnot(True))
    if workspace_id:
        query = query.filter(RegisteredAgent.department.like(f"{workspace_id}:%"))
    agents = query.all()

    results = []
    for agent in agents:
        txns = db.query(TokenTransaction).filter(
            TokenTransaction.agent_id == agent.id
        ).all()

        total_cost     = round(sum(t.cost_usd or 0       for t in txns), 6)
        total_input    = sum(t.input_tokens or 0  for t in txns)
        total_output   = sum(t.output_tokens or 0 for t in txns)
        total_saved    = sum(t.tokens_saved or 0  for t in txns)
        call_count     = len(txns)
        simulation_calls = sum(1 for t in txns if bool(t.is_simulation))

        # Most-used tier
        tier_counts: dict = {}
        for t in txns:
            tier = t.model_tier or "Scout"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        top_tier = max(tier_counts, key=tier_counts.get) if tier_counts else "—"

        results.append({
            "agent_id":        agent.id,
            "agent_name":      agent.name,
            "display_name":    display_agent_name(agent.name, agent.department, agent.source_platform),
            "department":      agent.department,
            "display_department": display_department(agent.department),
            "source_platform": agent.source_platform,
            "status":          agent.status,
            "mode":            agent.mode or "observe",
            "active_recently": agent_active_recently(agent),
            "last_used_at":    agent.last_used_at.isoformat() if agent.last_used_at else None,
            "call_count":      call_count,
            "simulation_call_count": simulation_calls,
            "total_cost_usd":  total_cost,
            "total_input_tokens":  total_input,
            "total_output_tokens": total_output,
            "tokens_saved":    total_saved,
            "top_tier":        top_tier,
        })

    results.sort(key=lambda r: r["total_cost_usd"], reverse=True)
    return results


@router.get("", response_model=List[AgentStatus])
def get_agents(
    include_archived: bool = False,
    workspace_id: str = None,
    db: Session = Depends(get_db),
):
    """List registered agents. Pass ?include_archived=true to include archived agents."""
    return list_agents(db, include_archived=include_archived, workspace_id=workspace_id)


@router.patch("/department-tier-bounds")
def set_department_tier_bounds(
    req: DepartmentTierBoundsRequest,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """
    Apply min/max tier bounds to every non-archived agent in a department.

    Filtered by bare department string, not workspace_id, on purpose:
    department is frequently unprefixed (see RegisteredAgent's own
    docstring on backfill limits), so a caller without workspace_id could
    otherwise bulk-mutate every workspace's same-named department at
    once. req.workspace_id narrows the match to workspace_filter()'s
    scoped rows when supplied; omitted, this keeps today's behavior
    (matches by department string alone) rather than silently changing
    what an existing caller's bulk update touches.
    """
    if not (1 <= req.min_tier <= 4) or not (1 <= req.max_tier <= 4):
        raise HTTPException(status_code=422, detail="Tier values must be between 1 and 4.")
    if req.min_tier > req.max_tier:
        raise HTTPException(status_code=422, detail="min_tier cannot exceed max_tier.")

    department = (req.department or "").strip()
    if not department:
        raise HTTPException(status_code=422, detail="Department is required.")
    _check_agent_permission(db, authorization, req.workspace_id)

    agents = db.query(RegisteredAgent).filter(
        RegisteredAgent.department == department,
        (RegisteredAgent.archived == False) | (RegisteredAgent.archived == None),
    )
    if req.workspace_id:
        agents = agents.filter(workspace_filter(RegisteredAgent, req.workspace_id))
    agents = agents.all()
    if not agents:
        raise HTTPException(status_code=404, detail=f"No visible agents found for department '{department}'.")

    for agent in agents:
        agent.min_tier = req.min_tier
        agent.max_tier = req.max_tier
    db.commit()

    return {
        "department": department,
        "updated": len(agents),
        "min_tier": req.min_tier,
        "max_tier": req.max_tier,
    }


@router.get("/{agent_id}", response_model=AgentStatus)
def get_single_agent(agent_id: int, workspace_id: Optional[str] = None, db: Session = Depends(get_db)):
    _agent_scoped_or_404(db, agent_id, workspace_id)  # raises 404 on cross-tenant mismatch
    result = get_agent(db, agent_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    return result


@router.post("/claim")
def claim(req: ClaimRequest, db: Session = Depends(get_db)):
    """
    Traffic Cop endpoint: an agent requests exclusive write access to a record.
    If another agent already holds that record, the requesting agent's
    configured lock, queue, or skip collision policy is applied.
    """
    try:
        return claim_record(db, req.agent_id, req.table, req.record_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/simulate-collision")
def run_collision_simulation(
    req: SimulateCollisionRequest,
    db: Session = Depends(get_db),
):
    """
    Demo endpoint: forces two agents to collide on the same record so you can
    see the Traffic Cop locking mechanism in action on the dashboard.
    """
    try:
        return simulate_collision(
            db, req.agent_id_1, req.agent_id_2, req.table, req.record_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{agent_id}/release")
def release_agent(
    agent_id: int, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Supervisor action: release a locked agent back to idle."""
    _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    try:
        return release_lock(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{agent_id}/tier-bounds")
def set_tier_bounds(
    agent_id: int, req: TierBoundsRequest, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Set the min/max routing tier for an agent. Clamps future routing decisions."""
    if not (1 <= req.min_tier <= 4) or not (1 <= req.max_tier <= 4):
        raise HTTPException(status_code=422, detail="Tier values must be between 1 and 4.")
    if req.min_tier > req.max_tier:
        raise HTTPException(status_code=422, detail="min_tier cannot exceed max_tier.")
    agent = _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    agent.min_tier = req.min_tier
    agent.max_tier = req.max_tier
    db.commit()
    db.refresh(agent)
    return agent


@router.patch("/{agent_id}/allowed-providers")
def set_allowed_providers(
    agent_id: int, req: AllowedProvidersRequest, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """
    Restrict which ModelRegistry.provider values this agent's routing may
    use (Routing 2.0, Phase 2 -- policy-aware routing). An empty list
    restricts the agent to no providers at all -- routing then proceeds on
    whatever the tier lookup picked anyway, with a note in the audit trail,
    since a real request is never hard-blocked by a policy misconfiguration
    (see core/router.py's _apply_provider_policy()).
    """
    agent = _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    cleaned = [p.strip() for p in (req.allowed_providers or []) if p and p.strip()]
    agent.allowed_providers = cleaned
    db.commit()
    db.refresh(agent)
    return {"id": agent.id, "allowed_providers": agent.allowed_providers}


@router.patch("/{agent_id}/pruning")
def toggle_pruning(
    agent_id: int, req: PruningToggleRequest, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Enable or disable context pruning for a specific agent."""
    agent = _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    agent.pruning_enabled = req.enabled
    db.commit()
    db.refresh(agent)
    from core.agentlake import _serialize
    return _serialize(agent)


@router.patch("/{agent_id}/mode")
def set_agent_mode(
    agent_id: int, req: ModeRequest, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """
    Set an agent's Observe/Control mode. This should only ever be called from
    an explicit admin action -- moving an agent into "control" must never
    happen silently. See docs/COSTPILOT_AGENT_MODE_LIFECYCLE.md step 9.
    """
    normalized = (req.mode or "").strip().lower()
    if normalized not in ("observe", "control"):
        raise HTTPException(status_code=422, detail="mode must be 'observe' or 'control'.")
    agent = _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id, "enable_agent_control")
    agent.mode = normalized
    db.commit()
    db.refresh(agent)
    from core.agentlake import _serialize
    return _serialize(agent)


@router.patch("/{agent_id}/name", response_model=AgentStatus)
def rename_agent(
    agent_id: int, req: RenameAgentRequest, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Rename an agent's display/source name without changing its history."""
    clean = (req.name or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Agent name cannot be blank.")
    from core.agentlake import _serialize
    agent = _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    agent.name = clean
    db.commit()
    db.refresh(agent)
    return _serialize(agent)


@router.post("/{agent_id}/archive")
def archive(
    agent_id: int, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Soft-delete: hide agent from live grid, preserve all history in reports and audit log."""
    _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    try:
        return archive_agent(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{agent_id}/unarchive", response_model=AgentStatus)
def unarchive(
    agent_id: int, workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db),
):
    """Restore an archived agent back to the live registry."""
    _agent_scoped_or_404(db, agent_id, workspace_id)
    _check_agent_permission(db, authorization, workspace_id)
    try:
        return unarchive_agent(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
