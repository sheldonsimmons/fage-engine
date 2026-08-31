"""
project_activity_reporting()'s request_count/token sums used to always
include VOICE_GUARD_PRUNE rows (prune-only log entries, not real AI
calls), while core/metrics_catalog.py's ai_requests/input_tokens/
output_tokens metrics (via core.metrics_query.IS_AI_CALL) always
excluded them -- a live, self-acknowledged inconsistency between Ask
CostPilot's two reporting paths (get_usage_report/get_change_drivers/
get_agent_adoption via project_activity_reporting, vs. query_metrics via
the registry). Fixed via an opt-in exclude_prune_only_rows parameter,
default False so every other caller (core/budget.py's live spend
recompute chief among them) keeps its exact current behavior.

These tests lock in both halves: the flag actually excludes prune rows
when passed, and the default (unset) behavior is unchanged.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction
from api.routes_work_items import project_activity_reporting


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", model_tier="Scout",
        input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now,
    ))
    # A real prune-only row -- always cost_usd=0.0 at the one insert site
    # (api/routes_voice.py), routing_reason="VOICE_GUARD_PRUNE".
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", model_tier="Scout",
        input_tokens=40, output_tokens=0, cost_usd=0.0, timestamp=now,
        routing_reason="VOICE_GUARD_PRUNE", was_pruned=True,
    ))
    db.commit()


def _range():
    now = datetime.utcnow()
    return now - timedelta(days=30), now + timedelta(days=1)


# Direct (non-FastAPI-routed) calls don't get Query()'s default coercion --
# every Query-defaulted param must be passed explicitly, same convention
# already established in test_activity_reporting_filters.py.
_NO_FILTERS = dict(
    project_id=None, user_external_id=None, agent_id=None, account_id=None,
    source_platform=None, record_type=None, model_tier=None, charged_unit=None,
    business_purpose=None, provider=None, activity_limit=50,
)


def test_default_behavior_still_includes_prune_rows():
    # Unchanged for every existing caller (core/budget.py etc.) that
    # doesn't explicitly ask for the new behavior.
    db = _session()
    _seed(db)
    date_from, date_to = _range()
    report = project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=30, db=db, **_NO_FILTERS,
    )
    assert report["summary"]["request_count"] == 2
    assert report["summary"]["input_tokens"] == 140


def test_exclude_prune_only_rows_matches_registry_convention():
    db = _session()
    _seed(db)
    date_from, date_to = _range()
    report = project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=30,
        exclude_prune_only_rows=True, db=db, **_NO_FILTERS,
    )
    assert report["summary"]["request_count"] == 1
    assert report["summary"]["input_tokens"] == 100


def test_spend_total_is_identical_either_way():
    # The prune row is always $0, so this was never actually a spend
    # discrepancy -- confirming that explicitly, not just assuming it.
    db = _session()
    _seed(db)
    date_from, date_to = _range()
    default_report = project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=30, db=db, **_NO_FILTERS,
    )
    excluded_report = project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=30,
        exclude_prune_only_rows=True, db=db, **_NO_FILTERS,
    )
    assert default_report["summary"]["spend_usd"] == excluded_report["summary"]["spend_usd"] == 1.0
