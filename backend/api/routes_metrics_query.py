"""
api/routes_metrics_query.py — public HTTP surface for core.metrics_query's
run_metrics_query(), the general metric x dimension x filter x comparison
engine (core/metrics_catalog.py's registry).

Until now this was only reachable internally: Ask CostPilot's agent-loop
tool (api/ask_costpilot_tools.py's query_metrics) and a couple of
routes_dashboard.py call sites. The AI Activity Explorer's View By /
Break Down By pivot (Reporting Tableau-style Explorer, Phase 1) is the
first FRONTEND caller, so it needs a real endpoint -- this is a thin,
validated pass-through, not a new calculation. Same metrics-registry-
first architecture the whole Explorer plan is built on: this endpoint
must stay the only new data path the Explorer adds, never a reason to
reach back to project_activity_reporting().
"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.auth import check_membership
from database.db import get_db
from core.analytics_periods import resolve_primary_period
from core.metrics_query import run_metrics_query

router = APIRouter()


class MetricsQueryRequest(BaseModel):
    workspace_id: Optional[str] = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    days: int = 30
    period_key: str = "none"
    # An exact range (e.g. from an Ask CostPilot drill-through, or the
    # Explorer's own custom date picker) used to get silently discarded in
    # favor of a rolling `days`-back-from-now window -- see metrics_query()
    # below for what that caused.
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    compare_to: Optional[str] = None
    sort: Optional[str] = None
    limit: int = 20


@router.post("/query")
def metrics_query(
    body: MetricsQueryRequest, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
):
    # Security architecture assessment, Finding 9: same soft-mode-gated
    # membership check as routes_dashboard.py's _check_reporting_access --
    # a no-op today, real once AUTH_ENFORCEMENT_ENABLED is on, and only
    # when body.workspace_id is actually supplied (this endpoint's
    # workspace_id lives on the request body, not a query/path param, so
    # this calls check_membership() directly rather than the
    # require_membership() dependency -- same reasoning as
    # create_universal_connection in routes_connections_universal.py).
    if body.workspace_id:
        check_membership(db, authorization, body.workspace_id, "view_reports")

    # Same period resolution ask_costpilot_tools.py's run_query_metrics
    # already uses -- period_key wins when given; otherwise a rolling
    # `days`-day window ending now (never "no time filter at all", the
    # same bug already caught and fixed in the get_usage_report
    # migration this session).
    #
    # An explicit date_from/date_to (period_key left at its "none"
    # default) takes priority over both: confirmed live during a
    # Dreamforce demo-path rehearsal that an Ask CostPilot drill-through
    # into this page landed on an exact historical range (e.g. Sep 1-12)
    # while this endpoint silently reinterpreted it as "the last ~10 days
    # ending right now" -- the one request that actually fell on Sep 1
    # dropped out of a window starting Sep 2, so the Explorer showed "No
    # AI activity" for a department the rest of the same page had just
    # shown real spend for.
    if body.period_key in (None, "none") and body.date_from and body.date_to:
        period = resolve_primary_period(period_key=None, days=30, date_from=body.date_from, date_to=body.date_to)
    elif body.period_key in (None, "none"):
        period = resolve_primary_period(period_key="none", days=max(1, min(int(body.days or 30), 365)))
    else:
        period = resolve_primary_period(period_key=body.period_key, days=max(1, min(int(body.days or 30), 365)))
    # Real datetime objects, not .isoformat() strings -- run_metrics_query
    # passes these straight into a SQLAlchemy timestamp filter, and an ISO
    # string compared against SQLite's own datetime text format (space
    # separator, not "T") silently matches nothing despite being
    # lexicographically close. Postgres happens to parse the "T" format
    # correctly via its own implicit text->timestamp cast (why this never
    # showed up against production), but it's fragile and breaks under
    # SQLite -- found while building the Universal Connection feature's
    # verification endpoint, which hit the same pattern and failed its own
    # test suite immediately.
    timeframe = {"start": period.start, "end": period.end}

    # Strip empty-string filter values -- the frontend's <select> "All X"
    # option sends "" for "not filtering," which must mean None here, not
    # a literal empty-string filter value.
    clean_filters = {k: v for k, v in (body.filters or {}).items() if v not in (None, "")}

    result = run_metrics_query(
        db, body.workspace_id, metrics=body.metrics, dimensions=body.dimensions,
        filters=clean_filters, timeframe=timeframe, compare_to=body.compare_to,
        sort=body.sort, limit=body.limit,
    )
    return {
        "rows": result.rows,
        "metrics": result.metrics,
        "dimensions": result.dimensions,
        "metric_definitions": result.metric_definitions,
        "scope": result.scope,
        "filters_applied": result.filters_applied,
        "timeframe": result.timeframe,
        "comparison": result.comparison,
        "errors": result.errors,
        "unsupported_metrics": result.unsupported_metrics,
        "freshness": result.freshness,
    }
