"""
tests/test_data_coverage.py — correctness check for
core.data_coverage.get_data_coverage, the Ask CostPilot tool that reports
which source platforms are connected before a question gets answered as if
data from all of them were available.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.data_coverage import get_data_coverage
from database.db import Base
from database.models import IntegrationConnection


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _connection(db, *, platform, workspace_id="WS1", status="active", display_name=None,
                 last_outcome_sync_at=None, tracked_objects_json=None, selected_object=None):
    conn = IntegrationConnection(
        workspace_id=workspace_id, platform=platform, display_name=display_name or platform,
        status=status, last_outcome_sync_at=last_outcome_sync_at,
        tracked_objects_json=tracked_objects_json, selected_object=selected_object,
    )
    db.add(conn)
    db.commit()
    return conn


def test_no_connections_reports_nothing_connected():
    db = _session()
    result = get_data_coverage(db, "WS1")
    assert result.connected_platforms == []
    assert set(result.not_connected_platforms) == {"salesforce", "servicenow", "hubspot"}


def test_connected_platform_reported_correctly():
    db = _session()
    _connection(db, platform="salesforce", status="active", selected_object="Opportunity")
    db.commit()

    result = get_data_coverage(db, "WS1")
    assert "salesforce" in result.connected_platforms
    assert "hubspot" in result.not_connected_platforms
    assert "servicenow" in result.not_connected_platforms
    sf = next(p for p in result.platforms if p["platform"] == "salesforce")
    assert sf["connected"] is True
    assert "Opportunity" in sf["tracked_objects"]


def test_draft_connection_not_counted_as_connected():
    db = _session()
    _connection(db, platform="hubspot", status="draft")
    db.commit()

    result = get_data_coverage(db, "WS1")
    assert "hubspot" in result.not_connected_platforms


def test_stale_sync_flagged():
    db = _session()
    _connection(
        db, platform="salesforce", status="active",
        last_outcome_sync_at=datetime.utcnow() - timedelta(days=3),
    )
    db.commit()

    result = get_data_coverage(db, "WS1")
    sf = next(p for p in result.platforms if p["platform"] == "salesforce")
    assert sf["stale"] is True


def test_recent_sync_not_flagged_stale():
    db = _session()
    _connection(
        db, platform="salesforce", status="active",
        last_outcome_sync_at=datetime.utcnow() - timedelta(minutes=7),
    )
    db.commit()

    result = get_data_coverage(db, "WS1")
    sf = next(p for p in result.platforms if p["platform"] == "salesforce")
    assert sf["stale"] is False


def test_workspace_scoping_excludes_other_workspaces():
    db = _session()
    _connection(db, platform="salesforce", workspace_id="WS-A", status="active")
    db.commit()

    result = get_data_coverage(db, "WS-B")
    assert "salesforce" in result.not_connected_platforms
