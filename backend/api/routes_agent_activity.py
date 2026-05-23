"""
api/routes_agent_activity.py — Agent Activity Report  [Step 12]

GET /api/reports/agent-activity
  Returns per-agent call summaries and individual transaction logs,
  filtered by platform, department, agent, model tier, and date range.

  Query params (all optional):
    platform    — Salesforce | ServiceNow | HubSpot | Dynamics365 | Zendesk | Custom
    department  — Support | Sales | Marketing | Operations | ...
    agent_id    — integer, filter to one agent
    model_tier  — micro | flagship
    days        — integer (default 30)
"""

from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import TokenTransaction, RegisteredAgent

router = APIRouter()


@router.get("")
def get_agent_activity(
    platform:   Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    agent_id:   Optional[int] = Query(None),
    model_tier: Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    days:       int           = Query(30),
    db: Session = Depends(get_db),
):
    # Prefer explicit date_from/date_to; fall back to days
    if date_from:
        try:
            since = datetime.fromisoformat(date_from.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            since = datetime.utcnow() - timedelta(days=days)
    else:
        since = datetime.utcnow() - timedelta(days=days)

    if date_to:
        try:
            until = datetime.fromisoformat(date_to.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            until = datetime.utcnow()
    else:
        until = datetime.utcnow()

    # ── Base transaction query ──────────────────────────────────────────────────
    q = db.query(TokenTransaction).filter(
        TokenTransaction.timestamp >= since,
        TokenTransaction.timestamp <  until,
    )

    if department:
        q = q.filter(TokenTransaction.department == department)
    if model_tier:
        q = q.filter(TokenTransaction.model_tier == model_tier)
    if platform:
        q = q.filter(TokenTransaction.source_platform == platform)

    # agent_id filter applied at agent level below
    all_txs = q.all()

    # ── Load matching agents ────────────────────────────────────────────────────
    agent_q = db.query(RegisteredAgent)
    if agent_id:
        agent_q = agent_q.filter(RegisteredAgent.id == agent_id)
    if platform:
        agent_q = agent_q.filter(RegisteredAgent.source_platform == platform)
    if department:
        agent_q = agent_q.filter(RegisteredAgent.department == department)

    agents = agent_q.all()
    agent_map = {a.id: a for a in agents}

    # ── Group transactions by agent ────────────────────────────────────────────
    by_agent: dict = {}
    unassigned_txs = []

    for tx in all_txs:
        if tx.agent_id and tx.agent_id in agent_map:
            by_agent.setdefault(tx.agent_id, []).append(tx)
        elif not agent_id:  # include unassigned only when not filtering by agent
            unassigned_txs.append(tx)

    # ── Build per-agent summaries ──────────────────────────────────────────────
    agent_rows = []
    for ag in agents:
        txs = by_agent.get(ag.id, [])
        if not txs:
            continue

        total_calls    = len(txs)
        total_cost     = sum(t.cost_usd for t in txs)
        flagship_calls = sum(1 for t in txs if t.model_tier == "flagship")
        pruned_calls   = sum(1 for t in txs if t.was_pruned)
        tokens_saved   = sum(t.tokens_saved or 0 for t in txs)
        last_active    = max(t.timestamp for t in txs)

        # Sort transactions newest-first for the call log
        sorted_txs = sorted(txs, key=lambda t: t.timestamp, reverse=True)

        agent_rows.append({
            "id":            ag.id,
            "name":          ag.name,
            "department":    ag.department,
            "platform":      ag.source_platform or "Custom",
            "status":        ag.status,
            "calls":         total_calls,
            "cost_usd":      round(total_cost, 4),
            "avg_cost_usd":  round(total_cost / total_calls, 5) if total_calls else 0,
            "flagship_pct":  round(flagship_calls / total_calls * 100, 1) if total_calls else 0,
            "pruned_pct":    round(pruned_calls   / total_calls * 100, 1) if total_calls else 0,
            "tokens_saved":  tokens_saved,
            "last_active":   last_active.isoformat(),
            "transactions":  [_serialize_tx(t) for t in sorted_txs[:100]],  # cap at 100
        })

    # Sort agents by total cost descending
    agent_rows.sort(key=lambda a: a["cost_usd"], reverse=True)

    # ── Fleet summary ──────────────────────────────────────────────────────────
    total_calls = sum(a["calls"]    for a in agent_rows)
    total_cost  = sum(a["cost_usd"] for a in agent_rows)
    platforms   = list({a["platform"] for a in agent_rows})
    departments = list({a["department"] for a in agent_rows})

    return {
        "summary": {
            "total_calls":    total_calls,
            "total_cost_usd": round(total_cost, 4),
            "agents_count":   len(agent_rows),
            "platforms":      platforms,
            "departments":    departments,
            "days":           days,
        },
        "agents": agent_rows,
    }


def _serialize_tx(t: TokenTransaction) -> dict:
    return {
        "id":             t.id,
        "timestamp":      t.timestamp.isoformat(),
        "model_tier":     t.model_tier,
        "cost_usd":       round(t.cost_usd, 5),
        "routing_reason": t.routing_reason or "—",
        "was_pruned":     t.was_pruned,
        "tokens_saved":   t.tokens_saved or 0,
        "input_tokens":   t.input_tokens,
        "output_tokens":  t.output_tokens,
        "department":     t.department,
        "platform":       t.source_platform or "Custom",
    }
