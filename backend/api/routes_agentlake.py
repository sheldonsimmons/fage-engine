"""
api/routes_agentlake.py — Agentlake Registry & Traffic Cop API routes  [Step 5]

GET  /api/agents                    — list all registered agents
GET  /api/agents/{id}               — single agent detail
POST /api/agents/claim              — agent claims a record (collision check runs here)
POST /api/agents/simulate-collision — demo: force two agents to collide on the same record
POST /api/agents/{id}/release       — supervisor releases a locked agent
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from core.agentlake import (
    list_agents, get_agent, claim_record,
    release_lock, simulate_collision,
    register_agent, deregister_agent,
)

router = APIRouter()


class AgentStatus(BaseModel):
    id:               int
    name:             str
    department:       str
    permissions:      str
    target_table:     Optional[str]
    target_record_id: Optional[int]
    status:           str
    collision_policy: Optional[str]
    locked_at:        Optional[str]
    lock_reason:      Optional[str]


class RegisterRequest(BaseModel):
    name:             str
    department:       str
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
        return register_agent(db, req.name, req.department, req.permissions, req.target_table, req.collision_policy)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{agent_id}")
def deregister(agent_id: int, db: Session = Depends(get_db)):
    """Remove an agent from the registry."""
    try:
        return deregister_agent(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=List[AgentStatus])
def get_agents(db: Session = Depends(get_db)):
    """List all registered AI agents and their current status."""
    return list_agents(db)


@router.get("/{agent_id}", response_model=AgentStatus)
def get_single_agent(agent_id: int, db: Session = Depends(get_db)):
    result = get_agent(db, agent_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found.")
    return result


@router.post("/claim")
def claim(req: ClaimRequest, db: Session = Depends(get_db)):
    """
    Traffic Cop endpoint: an agent requests exclusive write access to a record.
    If another agent already holds that record, a collision is triggered and
    both agents are locked until a supervisor intervenes.
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
def release_agent(agent_id: int, db: Session = Depends(get_db)):
    """Supervisor action: release a locked agent back to idle."""
    try:
        return release_lock(db, agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
