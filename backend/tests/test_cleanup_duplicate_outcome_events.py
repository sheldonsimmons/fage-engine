"""
tests/test_cleanup_duplicate_outcome_events.py — correctness check for
scripts/cleanup_duplicate_outcome_events.py, the one-time cleanup for the
WorkItemOutcomeEvent pileup left by the naive/aware datetime bug (see
core/outcome_adapters/salesforce_case.py's commit).
"""
import importlib.util
import os
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base
from database.models import WorkItem, WorkItemOutcomeEvent

_SPEC = importlib.util.spec_from_file_location(
    "cleanup_duplicate_outcome_events",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "cleanup_duplicate_outcome_events.py"),
)
_cleanup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_cleanup)


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _work_item(db, external_id="WI-1"):
    wi = WorkItem(external_id=external_id, name=external_id, workspace_id="WS1", context_type="case")
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _event(db, wi, *, status, is_closed, recorded_at, outcome_date=None):
    db.add(WorkItemOutcomeEvent(
        work_item_id=wi.id, workspace_id="WS1", outcome_status=status,
        outcome_value=None, outcome_date=outcome_date, outcome_success=None,
        is_closed=is_closed, retrieval_method="sync", recorded_at=recorded_at,
    ))


def test_consecutive_duplicates_collapsed_to_one(monkeypatch):
    db = _session()
    wi = _work_item(db)
    wi_id = wi.id
    now = datetime.utcnow()
    for i in range(5):
        _event(db, wi, status="Closed", is_closed=True, recorded_at=now + timedelta(minutes=i * 10))
    db.commit()

    monkeypatch.setattr("database.db.SessionLocal", lambda: db)
    _cleanup.main(dry_run=False)

    remaining = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=wi_id).all()
    assert len(remaining) == 1
    assert remaining[0].outcome_status == "Closed"


def test_genuine_changes_are_all_preserved(monkeypatch):
    db = _session()
    wi = _work_item(db)
    wi_id = wi.id
    now = datetime.utcnow()
    _event(db, wi, status="Open", is_closed=False, recorded_at=now)
    _event(db, wi, status="Open", is_closed=False, recorded_at=now + timedelta(minutes=10))
    _event(db, wi, status="Closed", is_closed=True, recorded_at=now + timedelta(minutes=20))
    _event(db, wi, status="Closed", is_closed=True, recorded_at=now + timedelta(minutes=30))
    db.commit()

    monkeypatch.setattr("database.db.SessionLocal", lambda: db)
    _cleanup.main(dry_run=False)

    remaining = (
        db.query(WorkItemOutcomeEvent).filter_by(work_item_id=wi_id)
        .order_by(WorkItemOutcomeEvent.recorded_at).all()
    )
    assert len(remaining) == 2
    assert remaining[0].outcome_status == "Open"
    assert remaining[1].outcome_status == "Closed"


def test_a_to_b_to_a_keeps_all_three():
    """Non-monotonic history (status flipped back) must not be collapsed
    across the intervening different value -- only CONSECUTIVE duplicates
    collapse."""
    db = _session()
    wi = _work_item(db)
    wi_id = wi.id
    now = datetime.utcnow()
    _event(db, wi, status="Open", is_closed=False, recorded_at=now)
    _event(db, wi, status="Closed", is_closed=True, recorded_at=now + timedelta(minutes=10))
    _event(db, wi, status="Open", is_closed=False, recorded_at=now + timedelta(minutes=20))
    db.commit()

    import database.db as db_module
    original = db_module.SessionLocal
    db_module.SessionLocal = lambda: db
    try:
        _cleanup.main(dry_run=False)
    finally:
        db_module.SessionLocal = original

    remaining = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=wi_id).all()
    assert len(remaining) == 3


def test_dry_run_deletes_nothing():
    db = _session()
    wi = _work_item(db)
    wi_id = wi.id
    now = datetime.utcnow()
    for i in range(3):
        _event(db, wi, status="Closed", is_closed=True, recorded_at=now + timedelta(minutes=i * 10))
    db.commit()

    import database.db as db_module
    original = db_module.SessionLocal
    db_module.SessionLocal = lambda: db
    try:
        _cleanup.main(dry_run=True)
    finally:
        db_module.SessionLocal = original

    remaining = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=wi_id).all()
    assert len(remaining) == 3
