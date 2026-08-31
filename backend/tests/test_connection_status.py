"""
core/connection_status.py — live-computed activity status for a
Universal Connection. Locks in the connection_key-first, source_platform-
fallback identity resolution before the creation/verification endpoints
rely on it.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import IntegrationConnection, RegisteredAgent, TokenTransaction
from core.connection_status import compute_connection_status


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_no_events_yet_when_nothing_matches():
    db = _session()
    conn = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool", connection_key="conn_abc")
    db.add(conn); db.commit()

    status = compute_connection_status(db, conn)
    assert status["activity_status"] == "no_events_yet"
    assert status["request_count"] == 0
    assert status["last_event_at"] is None


def test_matches_by_exact_connection_key():
    db = _session()
    conn = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool", connection_key="conn_abc")
    db.add(conn); db.commit()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_abc",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    status = compute_connection_status(db, conn)
    assert status["activity_status"] == "receiving"
    assert status["request_count"] == 1


def test_ignores_events_keyed_to_a_different_connection_even_with_same_platform_string():
    db = _session()
    conn_a = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool A", connection_key="conn_a")
    conn_b = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool B", connection_key="conn_b")
    db.add_all([conn_a, conn_b]); db.commit()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_b",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    status_a = compute_connection_status(db, conn_a)
    status_b = compute_connection_status(db, conn_b)
    assert status_a["request_count"] == 0
    assert status_b["request_count"] == 1


def test_falls_back_to_source_platform_match_for_unkeyed_connections():
    # Pre-existing (Salesforce/ServiceNow) connections predate connection_key
    # and are never backfilled with one -- status must still resolve via
    # workspace_id + source_platform, matching only rows that ALSO have no
    # connection_key of their own (never claimed by a different connection).
    db = _session()
    conn = IntegrationConnection(workspace_id="WS-1", platform="salesforce", display_name="Salesforce", connection_key=None)
    db.add(conn); db.commit()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", source_platform="Salesforce", connection_key=None,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    status = compute_connection_status(db, conn)
    assert status["request_count"] == 1
    assert status["activity_status"] == "receiving"


def test_stale_when_last_event_older_than_24_hours():
    db = _session()
    conn = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool", connection_key="conn_abc")
    db.add(conn); db.commit()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_abc",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0,
        timestamp=datetime.utcnow() - timedelta(hours=30),
    ))
    db.commit()

    status = compute_connection_status(db, conn)
    assert status["activity_status"] == "stale"


def test_agents_observed_counts_distinct_agents():
    db = _session()
    conn = IntegrationConnection(workspace_id="WS-1", platform="Acme Tool", display_name="Acme Tool", connection_key="conn_abc")
    db.add(conn); db.commit()
    agent_a = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write")
    agent_b = RegisteredAgent(name="Agent B", department="WS-1:Sales", permissions="read,write")
    db.add_all([agent_a, agent_b]); db.flush()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_abc", agent_id=agent_a.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_abc", agent_id=agent_b.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", connection_key="conn_abc", agent_id=agent_a.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now,
    ))
    db.commit()

    status = compute_connection_status(db, conn)
    assert status["agents_observed"] == 2
    assert status["request_count"] == 3
