"""
core/data_coverage.py — answers "what data does CostPilot actually have"
for a workspace, so Ask CostPilot can say "I can answer this for
Salesforce; HubSpot isn't connected" instead of guessing or silently
answering from whatever happens to be connected.

Reuses api.routes_connections.SUPPORTED_PLATFORMS as the single source of
truth for which platforms CostPilot has real discovery/outcome-sync
adapters for -- not redefined here, to avoid the exact drift problem that
comment already documents (this list used to be hardcoded in four places).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from database.models import IntegrationConnection

# A connector that hasn't run its outcome sync in longer than this is
# reported as stale rather than silently treated as current -- mirrors the
# precedent set by core/auditor.py's 30-day raw-payload retention window
# and metrics_query.py's freshness threshold, same "don't claim current
# without checking" principle applied to sync recency instead of retention.
STALE_SYNC_THRESHOLD = timedelta(hours=24)


@dataclass
class PlatformCoverage:
    platform: str
    connected: bool
    status: Optional[str] = None
    tracked_objects: list = field(default_factory=list)
    last_outcome_sync_at: Optional[str] = None
    stale: Optional[bool] = None


@dataclass
class CoverageResult:
    workspace_id: Optional[str]
    connected_platforms: list
    not_connected_platforms: list
    platforms: list  # list[PlatformCoverage] as dicts


def get_data_coverage(db: Session, workspace_id: Optional[str]) -> CoverageResult:
    from api.routes_connections import SUPPORTED_PLATFORMS
    import json

    query = db.query(IntegrationConnection)
    if workspace_id:
        query = query.filter(IntegrationConnection.workspace_id == workspace_id)
    connections = query.all()

    now = datetime.utcnow()
    by_platform: dict = {}
    for conn in connections:
        platform = (conn.platform or "").lower()
        tracked = []
        if conn.tracked_objects_json:
            try:
                tracked = json.loads(conn.tracked_objects_json) or []
            except (TypeError, ValueError):
                tracked = []
        if conn.selected_object and conn.selected_object not in tracked:
            tracked.append(conn.selected_object)

        stale = None
        last_sync_iso = None
        if conn.last_outcome_sync_at:
            last_sync_iso = conn.last_outcome_sync_at.isoformat()
            stale = (now - conn.last_outcome_sync_at) > STALE_SYNC_THRESHOLD

        # A workspace can have more than one connection per platform
        # (multiple orgs); a platform counts as "connected" if any of its
        # connections is active, and coverage reports the freshest sync.
        existing = by_platform.get(platform)
        is_active = conn.status == "active"
        if existing is None:
            by_platform[platform] = PlatformCoverage(
                platform=platform, connected=is_active, status=conn.status,
                tracked_objects=tracked, last_outcome_sync_at=last_sync_iso, stale=stale,
            )
        else:
            existing.connected = existing.connected or is_active
            existing.tracked_objects = sorted(set(existing.tracked_objects) | set(tracked))
            if conn.last_outcome_sync_at and (
                not existing.last_outcome_sync_at or last_sync_iso > existing.last_outcome_sync_at
            ):
                existing.last_outcome_sync_at = last_sync_iso
                existing.stale = stale

    # Report every platform CostPilot has real support for, connected or
    # not -- an unconnected platform must still show up as "not
    # connected", never be omitted as if the question about it didn't exist.
    all_platforms = []
    for platform in sorted(SUPPORTED_PLATFORMS):
        if platform in by_platform:
            all_platforms.append(by_platform[platform])
        else:
            all_platforms.append(PlatformCoverage(platform=platform, connected=False))

    connected = [p.platform for p in all_platforms if p.connected]
    not_connected = [p.platform for p in all_platforms if not p.connected]

    return CoverageResult(
        workspace_id=workspace_id,
        connected_platforms=connected,
        not_connected_platforms=not_connected,
        platforms=[vars(p) for p in all_platforms],
    )
