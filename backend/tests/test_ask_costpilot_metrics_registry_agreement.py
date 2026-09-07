"""
tests/test_ask_costpilot_metrics_registry_agreement.py — locks in that
Ask CostPilot's deterministic answer path (api/routes_efficiency.py,
which calls project_activity_reporting() from api/routes_work_items.py)
agrees with the canonical metrics registry on the core spend figure.

Investigating a full migration of routes_efficiency.py onto
run_metrics_query() (this session's metrics-unification effort's last
step) surfaced something more significant than the earlier Reports/
Business Profile/WorkItem Profile cases: project_activity_reporting()
isn't a one-off duplicate calculation -- it's a large (13-parameter),
heavily-shared reporting primitive that core/budget.py's live spend
recompute, trial reporting, and core/insights.py ALSO depend on, older
than and structurally different from the registry (it returns named-
entity fuzzy matching and multi-dimensional breakdowns the registry
doesn't produce). Replacing Ask CostPilot's call to it would mean
rearchitecting the primary retrieval layer several other systems share,
not swapping a formula -- a much larger, separate initiative than
anything else in this session's fix, and one that would touch Budget's
spend calculation as a side effect (the exact thing flagged elsewhere
as needing its own dedicated, careful pass given its prior counter-
drift incident, not a drive-by).

What IS safe and valuable here, same pattern as Reports/Business
Profile: verify the two independent code paths already agree on the
core number, since all 4 of routes_efficiency.py's project_activity_
reporting() call sites already opt into exclude_prune_only_rows=True
(the same VOICE_GUARD_PRUNE-row exclusion the registry's ai_spend
metric applies), confirmed via api/routes_work_items.py's own
docstring, which documents this flag as the fix for a real, previously
-existing disagreement between the two paths on request counts.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_work_items import project_activity_reporting
from core.metrics_query import run_metrics_query
from database.db import Base
from database.models import TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_project_activity_reporting_spend_matches_the_registry():
    db = _session()
    now = datetime.utcnow()
    db.add_all([
        TokenTransaction(
            workspace_id="WS-ASK", department="Sales", model_tier="Advisor",
            input_tokens=100, output_tokens=40, cost_usd=1.20,
            timestamp=now - timedelta(hours=2), is_simulation=False,
            usage_source="estimated", routing_reason="COMPLEX",
        ),
        TokenTransaction(
            workspace_id="WS-ASK", department="Support", model_tier="Scout",
            input_tokens=30, output_tokens=10, cost_usd=0.03,
            timestamp=now - timedelta(hours=1), is_simulation=False,
            usage_source="estimated", routing_reason="ROUTINE",
        ),
        # A VOICE_GUARD_PRUNE row -- always cost_usd=0.0, excluded from
        # the registry's ai_spend by IS_AI_CALL; must not change either
        # total if exclude_prune_only_rows=True is doing its job.
        TokenTransaction(
            workspace_id="WS-ASK", department="Support", model_tier="Scout",
            input_tokens=5, output_tokens=0, cost_usd=0.0,
            timestamp=now - timedelta(minutes=30), is_simulation=False,
            usage_source="estimated", routing_reason="VOICE_GUARD_PRUNE",
        ),
    ])
    db.commit()

    report = project_activity_reporting(
        workspace_id="WS-ASK", date_from=None, date_to=None, days=1,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None,
        activity_limit=500, exclude_prune_only_rows=True, db=db,
    )
    report_spend = round(report["summary"]["spend_usd"], 6)

    registry = run_metrics_query(
        db, "WS-ASK", metrics=["ai_spend"],
        timeframe={"start": now - timedelta(days=1), "end": now + timedelta(minutes=1)},
    )
    registry_spend = round(registry.rows[0]["ai_spend"], 6) if registry.rows else 0.0

    assert report_spend == registry_spend
    assert report_spend == 1.23  # 1.20 + 0.03, sanity check on the real number
