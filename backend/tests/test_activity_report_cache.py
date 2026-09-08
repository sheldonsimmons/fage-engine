"""
tests/test_activity_report_cache.py — project_activity_reporting() is the
single most expensive call in the app (a full multi-join, multi-dimension
aggregation scan). Found via real logged Ask CostPilot latencies: deterministic
answers took 6-22s each, dominated by 1-3 calls into this function per
question. Added a short-TTL cache so near-simultaneous calls for the same
workspace/period/filters share one scan instead of each re-running it --
this proves the cache actually collapses repeat calls, and that two calls
for genuinely different periods (as comparison/change_drivers intents make)
are NOT incorrectly collapsed together.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from api.routes_work_items import project_activity_reporting
from database.db import Base
from database.models import TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _count_selects(db, fn):
    queries = []

    def _log(conn, cursor, statement, *_a, **_kw):
        if statement.strip().upper().startswith("SELECT"):
            queries.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _log)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _log)
    return result, queries


def test_two_calls_for_the_same_period_share_one_scan():
    db = _session()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="Support", model_tier="Scout", input_tokens=10, output_tokens=10,
        cost_usd=1.5, timestamp=now - timedelta(hours=1), is_simulation=False,
        usage_source="estimated", routing_reason="ROUTINE", workspace_id="WS-1",
    ))
    db.commit()

    date_from = now - timedelta(days=1)
    date_to = now

    _, first_queries = _count_selects(db, lambda: project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=1,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=500, db=db,
    ))
    _, second_queries = _count_selects(db, lambda: project_activity_reporting(
        workspace_id="WS-1", date_from=date_from, date_to=date_to, days=1,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=500, db=db,
    ))

    assert len(first_queries) > 0
    assert len(second_queries) == 0, (
        f"expected the second identical call to hit the cache and issue zero queries, got {len(second_queries)}"
    )


def test_calls_for_different_periods_are_not_collapsed():
    db = _session()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="Support", model_tier="Scout", input_tokens=10, output_tokens=10,
        cost_usd=1.5, timestamp=now - timedelta(days=40), is_simulation=False,
        usage_source="estimated", routing_reason="ROUTINE", workspace_id="WS-1",
    ))
    db.add(TokenTransaction(
        department="Support", model_tier="Scout", input_tokens=20, output_tokens=20,
        cost_usd=3.0, timestamp=now - timedelta(hours=1), is_simulation=False,
        usage_source="estimated", routing_reason="ROUTINE", workspace_id="WS-1",
    ))
    db.commit()

    current_period = project_activity_reporting(
        workspace_id="WS-1", date_from=now - timedelta(days=1), date_to=now, days=1,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=500, db=db,
    )
    prior_period = project_activity_reporting(
        workspace_id="WS-1", date_from=now - timedelta(days=41), date_to=now - timedelta(days=39), days=2,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=500, db=db,
    )

    assert round(current_period["summary"]["spend_usd"], 2) == 3.0
    assert round(prior_period["summary"]["spend_usd"], 2) == 1.5
