"""Workspace historical coverage contracts for executive analytics."""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_

from database.models import (
    AuditEvent,
    DepartmentBudget,
    IntegrationConnection,
    TokenTransaction,
    TrialAccount,
    WorkspaceAnalyticsSettings,
    WorkItem,
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def workspace_analytics_settings(db, workspace_id: Optional[str]) -> Optional[WorkspaceAnalyticsSettings]:
    if db is None or not workspace_id:
        return None
    return (
        db.query(WorkspaceAnalyticsSettings)
        .filter(WorkspaceAnalyticsSettings.workspace_id == workspace_id)
        .first()
    )


def workspace_collection_profile(db, workspace_id: Optional[str]) -> dict:
    """Return observed facts and conservative verified collection boundaries."""
    if db is None or not workspace_id:
        return {
            "workspace_id": workspace_id,
            "settings_configured": False,
            "collection_started_at": None,
            "collection_start_source": "not_available",
            "latest_complete_at": None,
            "earliest_observed_at": None,
            "latest_observed_at": None,
            "integration_count": 0,
            "sources": [],
        }

    settings = workspace_analytics_settings(db, workspace_id)
    trial = db.query(TrialAccount).filter(TrialAccount.workspace_id == workspace_id).first()
    if settings and settings.collection_started_at:
        collection_started_at = settings.collection_started_at
        collection_start_source = "workspace_analytics_settings"
    elif trial and trial.created_at:
        # No CostPilot collection can predate creation of its tenant workspace.
        collection_started_at = trial.created_at
        collection_start_source = "workspace_created_at"
    else:
        collection_started_at = None
        collection_start_source = "not_available"

    tx_scope = db.query(TokenTransaction).outerjoin(
        WorkItem, TokenTransaction.work_item_id == WorkItem.id
    ).filter(or_(
        TokenTransaction.workspace_id == workspace_id,
        TokenTransaction.workspace_id.is_(None) & (WorkItem.workspace_id == workspace_id),
    ))
    earliest_observed_at, latest_observed_at = tx_scope.with_entities(
        func.min(TokenTransaction.timestamp),
        func.max(TokenTransaction.timestamp),
    ).one()
    connections = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.workspace_id == workspace_id,
            IntegrationConnection.status.in_(("connected", "active")),
        )
        .all()
    )
    source_rows = tx_scope.with_entities(
        TokenTransaction.source_platform,
        func.count(TokenTransaction.id),
        func.min(TokenTransaction.timestamp),
        func.max(TokenTransaction.timestamp),
    ).group_by(TokenTransaction.source_platform).all()
    connections_by_platform = {}
    for connection in connections:
        key = str(connection.platform or "unknown").lower()
        existing = connections_by_platform.get(key)
        if not existing or (connection.last_success_at or datetime.min) > (
            existing.last_success_at or datetime.min
        ):
            connections_by_platform[key] = connection
    sources = []
    for platform, request_count, first_seen, last_seen in source_rows:
        key = str(platform or "unknown").lower()
        connection = connections_by_platform.get(key)
        sources.append({
            "platform": platform or "Unknown",
            "request_count": int(request_count or 0),
            "earliest_observed_at": _iso(first_seen),
            "latest_observed_at": _iso(last_seen),
            "connection_status": connection.status if connection else None,
            "connection_last_success_at": _iso(connection.last_success_at) if connection else None,
            "connection_has_error": bool(connection and connection.last_error),
        })
    return {
        "workspace_id": workspace_id,
        "settings_configured": bool(settings),
        "timezone_name": settings.timezone_name if settings else None,
        "week_starts_on": settings.week_starts_on if settings else None,
        "fiscal_year_start_month": settings.fiscal_year_start_month if settings else None,
        "default_window_days": settings.default_window_days if settings else None,
        "collection_started_at": _iso(collection_started_at),
        "collection_start_source": collection_start_source,
        "latest_complete_at": _iso(settings.latest_complete_at if settings else None),
        "earliest_observed_at": _iso(earliest_observed_at),
        "latest_observed_at": _iso(latest_observed_at),
        "integration_count": len(connections),
        "sources": sources,
    }


def _parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def period_coverage(period, request_count: int, profile: dict) -> dict:
    """Classify one period without treating absence of rows as verified zero."""
    collection_start = _parse(profile.get("collection_started_at"))
    latest_complete = _parse(profile.get("latest_complete_at"))
    coverage_percent = None
    if collection_start and latest_complete:
        covered_start = max(period.start, collection_start)
        covered_end = min(period.end, latest_complete)
        covered_seconds = max(0.0, (covered_end - covered_start).total_seconds())
        period_seconds = max(1.0, (period.end - period.start).total_seconds())
        coverage_percent = round(min(100.0, covered_seconds / period_seconds * 100), 1)
    if collection_start and period.end <= collection_start:
        status = "unavailable_before_collection"
        verified_zero = False
        coverage_percent = 0.0
        limitation = (
            f"CostPilot collection began on {collection_start.date().isoformat()}, "
            "after this period ended."
        )
    elif collection_start and period.start < collection_start < period.end:
        status = "partial_collection_window"
        verified_zero = False
        limitation = (
            f"CostPilot collection began on {collection_start.date().isoformat()}, "
            "during this period."
        )
    elif request_count:
        status = "activity_observed"
        verified_zero = False
        limitation = None if latest_complete and period.end <= latest_complete else (
            "Activity is recorded, but ingestion completeness is not independently verified."
        )
    elif collection_start and latest_complete and period.start >= collection_start and period.end <= latest_complete:
        status = "verified_zero_activity"
        verified_zero = True
        limitation = "The verified collection window covers this period and no matching activity was recorded."
    else:
        status = "unverified_no_activity"
        verified_zero = False
        limitation = "No activity was recorded, but complete collection coverage is not verified."
    return {
        "status": status,
        "request_count": int(request_count),
        "verified_zero": verified_zero,
        "coverage_percent": coverage_percent,
        "limitation": limitation,
    }


def comparison_data_coverage(plan, primary_summary: dict, comparison_summary: dict, profile: dict) -> dict:
    """Combine period coverage and traffic-scope compatibility."""
    from core.analytics_periods import comparison_coverage

    result = comparison_coverage(primary_summary, comparison_summary)
    primary = period_coverage(
        plan.primary, int(primary_summary.get("request_count") or 0), profile
    )
    comparison = period_coverage(
        plan.comparison, int(comparison_summary.get("request_count") or 0), profile
    )
    result.update({
        "primary_period": primary,
        "comparison_period": comparison,
        "collection_profile": profile,
    })
    unavailable = {
        "unavailable_before_collection", "partial_collection_window", "unverified_no_activity",
    }
    if primary["status"] in unavailable or comparison["status"] in unavailable:
        result["comparable"] = False
        if comparison["status"] == "unavailable_before_collection":
            result["status"] = "comparison_history_unavailable"
            result["limitation"] = comparison["limitation"]
        elif primary["status"] == "unavailable_before_collection":
            result["status"] = "primary_history_unavailable"
            result["limitation"] = primary["limitation"]
        else:
            result["status"] = "collection_coverage_incomplete"
            result["limitation"] = primary["limitation"] or comparison["limitation"]
    elif result.get("primary_traffic_scope") != result.get("comparison_traffic_scope"):
        result["comparable"] = False
    else:
        result["comparable"] = True
        result["status"] = "verified_comparable_periods"
        result["limitation"] = None
    return result


DEFAULT_WORKSPACE_ID = "default"


def _workspace_budget_filter(workspace_id: Optional[str]):
    """
    DepartmentBudget.workspace_id (backfilled by database/backfill_workspaces.py)
    is the real column now — no workspace given means the legacy unscoped
    "default" bucket specifically, never every workspace pooled together.
    """
    return DepartmentBudget.workspace_id == (workspace_id or DEFAULT_WORKSPACE_ID)


def workspace_attention_signals(db, workspace_id: Optional[str], limit: int = 5) -> list[dict]:
    """
    Cheap, workspace-scoped signals worth flagging unprompted — the backend
    equivalent of the dashboard's client-side "Needs attention" panel
    (frontend/index.html homeAttentionItems), so Ask CostPilot can surface
    the same thresholds without recomputing a full report.

    Each signal: {type, severity, title, detail, department}.
    Thresholds mirror the dashboard: budget >=80% warning, >=100% critical.
    """
    if db is None:
        return []

    from database.models import AuditReviewState

    signals: list[dict] = []

    # ── Budget caps ──────────────────────────────────────────────────────
    budget_query = db.query(DepartmentBudget).filter(
        or_(DepartmentBudget.archived == False, DepartmentBudget.archived.is_(None)),  # noqa: E712
        _workspace_budget_filter(workspace_id),
    )
    for budget in budget_query.all():
        cap = float(budget.monthly_cap_usd or 0)
        if cap <= 0:
            continue
        spend = float(budget.current_spend_usd or 0)
        pct = round(spend / cap * 100, 1)
        label = (budget.department or "Unassigned").split(":")[-1]
        if pct >= 100:
            signals.append({
                "type": "budget_exceeded", "severity": "critical", "department": label,
                "title": f"{label} is over its monthly AI budget",
                "detail": f"${spend:,.2f} spent against a ${cap:,.2f} cap ({pct:.0f}%).",
            })
        elif pct >= 80:
            signals.append({
                "type": "budget_near_cap", "severity": "warning", "department": label,
                "title": f"{label} is approaching its monthly AI budget",
                "detail": f"${spend:,.2f} spent against a ${cap:,.2f} cap ({pct:.0f}%).",
            })

    # ── Unreviewed blocked requests ─────────────────────────────────────
    scope_key = f"workspace:{workspace_id}" if workspace_id else "global"
    state = db.query(AuditReviewState).filter_by(scope_key=scope_key).first()
    reviewed_through_id = int(state.reviewed_through_id or 0) if state else 0
    blocked_query = db.query(func.count(AuditEvent.id)).filter(
        AuditEvent.decision_outcome.ilike("%blocked%"),
        AuditEvent.id > reviewed_through_id,
        AuditEvent.workspace_id == (workspace_id or DEFAULT_WORKSPACE_ID),
    )
    unreviewed_blocked = blocked_query.scalar() or 0
    if unreviewed_blocked:
        signals.append({
            "type": "blocked_unreviewed", "severity": "warning", "department": None,
            "title": f"{unreviewed_blocked} blocked request{'s' if unreviewed_blocked != 1 else ''} awaiting review",
            "detail": "Governance blocked these requests and they haven't been acknowledged yet.",
        })

    # ── Recent collisions ────────────────────────────────────────────────
    cutoff = datetime.utcnow() - timedelta(days=7)
    collision_query = db.query(func.count(AuditEvent.id)).filter(
        AuditEvent.timestamp >= cutoff,
        AuditEvent.event_type.in_(("LOCK", "COLLISION_LOCK", "COLLISION_QUEUE", "COLLISION_SKIP")),
        AuditEvent.workspace_id == (workspace_id or DEFAULT_WORKSPACE_ID),
    )
    collision_count = collision_query.scalar() or 0
    if collision_count:
        signals.append({
            "type": "collisions", "severity": "info", "department": None,
            "title": f"{collision_count} agent collision{'s' if collision_count != 1 else ''} in the last 7 days",
            "detail": "Two or more agents targeted the same record and were locked, queued, or skipped.",
        })

    return signals[:limit]


def any_workspace_over_budget(db) -> list[dict]:
    """
    Answer the plain question "is anything, anywhere, over budget" — scans
    every non-archived DepartmentBudget row across every *production*
    workspace (real customer accounts only — see database/models.py
    Workspace.workspace_type), ignoring whichever workspace happens to be
    selected in the UI. Demo/simulation/legacy workspaces are deliberately
    excluded: this check exists so a real over-budget account is never
    hidden just because a different workspace is on screen, not to
    surface old test data nobody's monitoring.

    Each result: {department, workspace_label, spend_usd, cap_usd, used_pct}.
    """
    if db is None:
        return []

    from database.models import Workspace

    production_workspaces = {
        w.workspace_id: w.name for w in
        db.query(Workspace).filter(Workspace.workspace_type == "production").all()
    }
    if not production_workspaces:
        return []

    rows = db.query(DepartmentBudget).filter(
        or_(DepartmentBudget.archived == False, DepartmentBudget.archived.is_(None)),  # noqa: E712
        DepartmentBudget.workspace_id.in_(production_workspaces.keys()),
    ).all()
    over = []
    for budget in rows:
        cap = float(budget.monthly_cap_usd or 0)
        if cap <= 0:
            continue
        spend = float(budget.current_spend_usd or 0)
        pct = round(spend / cap * 100, 1)
        if pct < 100:
            continue
        department = str(budget.department or "")
        workspace_label = production_workspaces.get(budget.workspace_id, budget.workspace_id or "Default")
        label = department.split(":")[-1] or "Unassigned"
        over.append({
            "department": label,
            "workspace_label": workspace_label,
            "spend_usd": round(spend, 2),
            "cap_usd": cap,
            "used_pct": pct,
        })
    over.sort(key=lambda row: -row["used_pct"])
    return over
