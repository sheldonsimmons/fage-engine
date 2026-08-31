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

from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
    compare_to: Optional[str] = None
    sort: Optional[str] = None
    limit: int = 20


@router.post("/query")
def metrics_query(body: MetricsQueryRequest, db: Session = Depends(get_db)):
    # Same period resolution ask_costpilot_tools.py's run_query_metrics
    # already uses -- period_key wins when given; otherwise a rolling
    # `days`-day window ending now (never "no time filter at all", the
    # same bug already caught and fixed in the get_usage_report
    # migration this session).
    if body.period_key in (None, "none"):
        period = resolve_primary_period(period_key="none", days=max(1, min(int(body.days or 30), 365)))
    else:
        period = resolve_primary_period(period_key=body.period_key, days=max(1, min(int(body.days or 30), 365)))
    timeframe = {"start": period.start.isoformat(), "end": period.end.isoformat()}

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
