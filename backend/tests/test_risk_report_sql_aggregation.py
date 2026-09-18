"""
tests/test_risk_report_sql_aggregation.py — risk_report() (api/routes_reports.py)
had the same MAX_REPORT_ROWS/per-row-Python-loop OOM exposure as
compute_realized_savings (see test_realized_savings_sql_aggregation.py's
own docstring for the production incident this pattern caused).
Migrated onto SQL aggregation the same way, plus the recent-events table
is now its own bounded query instead of slicing an already-fully-loaded
result set.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import AuditEvent
from api.routes_reports import risk_report


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _event(db, *, risk_level, event_type, department, decision_outcome=None, rationale=None, days_ago=0):
    db.add(AuditEvent(
        event_type=event_type, department=department, risk_level=risk_level,
        decision_outcome=decision_outcome, rationale=rationale,
        timestamp=datetime.utcnow() - timedelta(days=days_ago),
        workspace_id="default",
    ))


def test_counts_breakdowns_and_recent_events_match_hand_computed_expectation():
    db = _session()
    _event(db, risk_level="critical", event_type="DECISION", department="Support",
           decision_outcome="Request Blocked — sensitive data", days_ago=0)
    _event(db, risk_level="high", event_type="LOCK", department="Support",
           rationale="Throttled due to budget cap", days_ago=0)
    _event(db, risk_level="medium", event_type="COLLISION_QUEUE", department="Sales", days_ago=1)
    _event(db, risk_level="low", event_type="ROUTING", department="Sales", days_ago=1)
    _event(db, risk_level="low", event_type="COLLISION_SKIP", department="Engineering",
           rationale="REQUEST BLOCKED — sensitive data detected", days_ago=2)
    db.commit()

    result = risk_report(days=30, workspace_id="default", date_from=None, date_to=None, db=db, authorization=None)

    assert result["total_events"] == 5
    assert result["critical"] == 1
    assert result["high"] == 1
    assert result["medium"] == 1
    assert result["low"] == 2
    # decision_outcome "Request Blocked..." (case-insensitive) + rationale
    # containing the fixed "REQUEST BLOCKED" literal -- 2 rows total.
    assert result["blocked"] == 2
    assert result["locks"] == 1
    assert result["collision_breakdown"] == {"lock": 1, "queue": 1, "skip": 1}
    assert result["collision_count"] == 3
    assert result["throttled"] == 1

    assert result["by_department"]["Support"]["total"] == 2
    assert result["by_department"]["Sales"]["total"] == 2
    assert result["by_department"]["Engineering"]["total"] == 1

    active_days = [d for d in result["timeline"] if d["total"] > 0]
    assert len(active_days) == 3

    assert len(result["recent_events"]) == 5
    assert result["truncated"] is False


def test_recent_events_bounded_query_matches_full_scan_ordering():
    """
    recent_events is now its own ORDER BY timestamp DESC LIMIT 500 query,
    independent of the aggregate counts above -- confirm it still returns
    events newest-first, same as the old "slice the first 500 of an
    already-loaded, already-DESC-ordered list" behavior.
    """
    db = _session()
    for i in range(5):
        _event(db, risk_level="low", event_type="ROUTING", department="Support", days_ago=i)
    db.commit()

    result = risk_report(days=30, workspace_id="default", date_from=None, date_to=None, db=db, authorization=None)
    timestamps = [e["timestamp"] for e in result["recent_events"]]
    assert timestamps == sorted(timestamps, reverse=True)
