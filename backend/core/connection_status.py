"""
core/connection_status.py — live-computed activity status for one
IntegrationConnection (Universal Connection feature).

Every number here is a real query against TokenTransaction -- no
separately-tracked status field that could drift from reality. Generic:
usable for any connection (Universal or native Salesforce/ServiceNow),
though it's the first real dashboard presence Universal Connections get.

Identity resolution prefers an exact connection_key match (see
IntegrationConnection.connection_key / TokenTransaction.connection_key)
when the connection has ever received a keyed event, falling back to the
looser source_platform string match otherwise -- this is what makes the
fallback safe for connections created before connection_key existed, or
whose caller never sends one.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database.models import IntegrationConnection, TokenTransaction, WorkItemOutcomeEvent

STALE_AFTER = timedelta(hours=24)


def connection_scope(connection: IntegrationConnection):
    """
    Returns a SQLAlchemy filter clause matching TokenTransaction rows
    belonging to this connection: exact connection_key match OR (no
    connection_key on the row) AND workspace+platform string match. Never
    both at once -- a keyed event from a DIFFERENT connection sharing the
    same platform string must not count towards this one.
    """
    keyed = TokenTransaction.connection_key == connection.connection_key if connection.connection_key else None
    platform_fallback = (
        (TokenTransaction.workspace_id == connection.workspace_id)
        & (func.lower(TokenTransaction.source_platform) == connection.platform.lower())
        & (TokenTransaction.connection_key.is_(None))
    )
    if keyed is not None:
        return or_(keyed, platform_fallback)
    return platform_fallback


def outcome_connection_scope(connection: IntegrationConnection):
    """
    connection_scope()'s sibling for WorkItemOutcomeEvent (Universal
    Outcome Ingestion, core/outcome_ingestion.py) -- same exact-key-or-
    workspace+platform-fallback shape, just against source_system instead
    of TokenTransaction's source_platform (WorkItemOutcomeEvent has no
    source_platform column; source_system is its equivalent, matching
    WorkItemOutcome's existing column of the same name). A separate
    function rather than parameterizing connection_scope() itself -- the
    two tables don't share a column name for "which platform reported
    this," so a single generic implementation would need per-model special
    casing anyway.
    """
    keyed = WorkItemOutcomeEvent.connection_key == connection.connection_key if connection.connection_key else None
    platform_fallback = (
        (WorkItemOutcomeEvent.workspace_id == connection.workspace_id)
        & (func.lower(WorkItemOutcomeEvent.source_system) == connection.platform.lower())
        & (WorkItemOutcomeEvent.connection_key.is_(None))
    )
    if keyed is not None:
        return or_(keyed, platform_fallback)
    return platform_fallback


def compute_connection_status(db: Session, connection: IntegrationConnection) -> dict:
    scope = connection_scope(connection)
    row = db.query(
        func.max(TokenTransaction.timestamp),
        func.count(TokenTransaction.id),
        func.count(func.distinct(TokenTransaction.agent_id)),
    ).filter(scope).first()
    last_event_at, request_count, agents_observed = row or (None, 0, 0)
    request_count = int(request_count or 0)
    agents_observed = int(agents_observed or 0)

    if not last_event_at:
        activity_status = "no_events_yet"
    elif datetime.utcnow() - last_event_at > STALE_AFTER:
        activity_status = "stale"
    else:
        activity_status = "receiving"

    return {
        "last_event_at": last_event_at,
        "request_count": request_count,
        "agents_observed": agents_observed,
        "activity_status": activity_status,
    }
