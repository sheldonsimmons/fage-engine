"""
api/routes_connections_universal.py — Universal Connection: connect any
platform, including a fully custom/unlisted system, as a real saved,
named IntegrationConnection.

Deliberately a separate file from routes_connections.py, never touching
its SUPPORTED_PLATFORMS gate or any Salesforce/ServiceNow/HubSpot OAuth
code path -- this is a parallel, additive creation path for platforms
that aren't (and may never be) one of those three, reusing the exact
same canonical event contract (POST /api/route) every native connector
already funnels through.
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import IntegrationConnection, TokenTransaction
from api.routes_workspaces import _new_api_key, _require_workspace, check_admin_access
from core.connection_status import compute_connection_status, connection_scope

router = APIRouter()

# A test event is identified by connection scope + recency alone, not a
# business_purpose marker -- observe-mode's RouteRequest has no caller-
# settable business_purpose field at all (it's always server-derived via
# classify_business_purpose_fields), confirmed by sending a real event
# against this endpoint before trusting the design. A brand-new
# connection has zero real traffic until the customer wires up their
# actual system, so "the most recent event in this connection's scope,
# within the lookback window" is unambiguously the test event.
TEST_EVENT_LOOKBACK = timedelta(minutes=15)


def _new_connection_key() -> str:
    return f"conn_{secrets.token_hex(12)}"


def _connection_json(connection: IntegrationConnection, db: Session) -> dict:
    status = compute_connection_status(db, connection)
    return {
        "id": connection.id,
        "workspace_id": connection.workspace_id,
        "platform": connection.platform,
        "display_name": connection.display_name,
        "status": connection.status,
        "connection_key": connection.connection_key,
        "created_at": connection.created_at,
        **status,
    }


class UniversalConnectionCreateRequest(BaseModel):
    workspace_id: str
    platform: str  # free text, customer-chosen platform/app name -- not gated by SUPPORTED_PLATFORMS
    display_name: Optional[str] = None


@router.post("/universal")
def create_universal_connection(body: UniversalConnectionCreateRequest, db: Session = Depends(get_db)):
    check_admin_access()
    platform = (body.platform or "").strip()
    if not platform:
        raise HTTPException(status_code=422, detail="platform is required")
    display_name = (body.display_name or "").strip() or platform

    workspace = _require_workspace(db, body.workspace_id)
    if not workspace.api_key:
        workspace.api_key = _new_api_key()

    existing = db.query(IntegrationConnection).filter_by(
        workspace_id=body.workspace_id, platform=platform, display_name=display_name,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A connection named '{display_name}' for platform '{platform}' already exists.",
        )

    connection = IntegrationConnection(
        workspace_id=body.workspace_id,
        platform=platform,
        display_name=display_name,
        status="pending",
        connection_key=_new_connection_key(),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)

    return {
        "connection": _connection_json(connection, db),
        # Full value returned once, at creation time -- same convention as
        # POST /api-key/regenerate. The frontend must show it to the
        # customer now; retrieving it again later goes through the
        # explicit reveal endpoint, not this one.
        "api_key": workspace.api_key,
    }


@router.get("/universal/{connection_id}")
def get_universal_connection(connection_id: int, db: Session = Depends(get_db)):
    connection = db.query(IntegrationConnection).filter_by(id=connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection was not found")
    return _connection_json(connection, db)


@router.get("/universal/{connection_id}/verify-test-event")
def verify_test_event(connection_id: int, db: Session = Depends(get_db)):
    """
    Independently checks a real test event all the way through the
    pipeline -- not just "did the POST return 200." Each step is reported
    pass/fail on its own so a setup problem is diagnosable instead of a
    silent dead end. Steps after the first that finds no event are marked
    not_attempted rather than failed -- there's nothing to check yet, and
    "failed" would wrongly imply something is broken rather than "nothing
    sent yet."
    """
    connection = db.query(IntegrationConnection).filter_by(id=connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection was not found")

    steps = []

    def step(key: str, label: str, passed: Optional[bool], detail: str):
        steps.append({"key": key, "label": label, "passed": passed, "detail": detail})

    scope = connection_scope(connection)
    test_row = (
        db.query(TokenTransaction)
        .filter(
            scope,
            TokenTransaction.timestamp >= datetime.utcnow() - TEST_EVENT_LOOKBACK,
        )
        .order_by(TokenTransaction.timestamp.desc())
        .first()
    )

    if not test_row:
        step("authentication", "Authentication", False,
             "No test event was received in the last 15 minutes. Check the API key and platform "
             "name, then send the test event again.")
        for key, label in [
            ("workspace_isolation", "Workspace isolation"),
            ("persistence", "Persistence"),
            ("connection_association", "Connection matched"),
            ("agent_recognition", "Agent recognized"),
            ("reporting_visibility", "Visible in reporting"),
        ]:
            step(key, label, None, "Not attempted — no test event to check yet.")
        return {"connection_id": connection_id, "fully_verified": False, "steps": steps}

    step("authentication", "Authentication", True, "A test event was accepted and recorded.")

    isolated = test_row.workspace_id == connection.workspace_id
    step("workspace_isolation", "Workspace isolation", isolated,
         "The event is scoped to this connection's own workspace."
         if isolated else
         f"The event was recorded under workspace '{test_row.workspace_id}', not this connection's "
         f"'{connection.workspace_id}'.")

    # Re-fetched by primary key -- a distinct check from "authentication"
    # above (which only proves a row existed at query time), confirming
    # it's durably in the database, not a transaction-local artifact.
    persisted_row = db.query(TokenTransaction).filter_by(id=test_row.id).first()
    step("persistence", "Persistence", persisted_row is not None,
         "Confirmed via a fresh database query." if persisted_row
         else "The event could not be re-read from the database.")

    associated = bool(
        (connection.connection_key and test_row.connection_key == connection.connection_key)
        or (test_row.source_platform or "").lower() == connection.platform.lower()
    )
    step("connection_association", "Connection matched", associated,
         "The event is attributed to this exact connection." if associated
         else "The event's platform/connection identity doesn't match this connection.")

    agent_recognized = test_row.agent_id is not None
    step("agent_recognition", "Agent recognized", agent_recognized,
         "An agent identity was recognized for this event." if agent_recognized
         else "No agent identity was recognized — the test event may not have included one.")

    from core.metrics_query import run_metrics_query

    # Real datetime objects, not .isoformat() strings -- run_metrics_query
    # passes timeframe bounds straight into a SQLAlchemy filter, and an
    # ISO string compared against SQLite's own datetime text format (space
    # separator, not "T") silently matches nothing despite being
    # lexicographically close. datetime objects work correctly on both
    # SQLite and Postgres. Caught by this endpoint's own test suite.
    window_start = test_row.timestamp - timedelta(minutes=1)
    window_end = datetime.utcnow() + timedelta(minutes=1)
    result = run_metrics_query(
        db, connection.workspace_id, metrics=["ai_requests"],
        filters={"platform": connection.platform},
        timeframe={"start": window_start, "end": window_end},
    )
    visible_count = int(result.rows[0].get("ai_requests", 0)) if result.rows else 0
    visible = visible_count >= 1
    step("reporting_visibility", "Visible in reporting", visible,
         "The event is counted by the same trusted reporting layer dashboards and Ask CostPilot use."
         if visible else
         "The event was not found through the trusted reporting layer's query path.")

    fully_verified = all(s["passed"] is True for s in steps)
    if fully_verified and connection.status != "connected":
        connection.status = "connected"
        connection.last_success_at = datetime.utcnow()
        db.commit()

    return {"connection_id": connection_id, "fully_verified": fully_verified, "steps": steps}
