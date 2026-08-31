"""
core/metrics_query.py's DIMENSIONS/METRICS registry was missing
tokens_saved_count, simulation_count, and live_count -- the 3 fields
get_usage_report's breakdown rows expose alongside spend/tokens, blocking
that tool's migration onto the registry (Ask CostPilot architecture audit
Recommendation #4). These tests lock in all three, including the
is_simulator_traffic fallback heuristic (no is_simulation flag but also
no real identity) matching project_activity_reporting()'s is_sim_condition.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import RegisteredAgent, TokenTransaction
from core.metrics_query import run_metrics_query


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_tokens_saved_count_sums_raw_token_count():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", tokens_saved=250,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["tokens_saved_count"], timeframe={})
    assert not result.errors
    assert result.rows[0]["tokens_saved_count"] == 250


def test_simulation_count_flags_explicit_is_simulation():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", is_simulation=True,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", is_simulation=False,
        actor_external_id="ACTOR-1", actor_name="Real Person",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_requests", "simulation_count", "live_count"], timeframe={})
    assert not result.errors
    row = result.rows[0]
    assert row["ai_requests"] == 2
    assert row["simulation_count"] == 1
    assert row["live_count"] == 1


def test_simulation_count_falls_back_to_no_identity_heuristic():
    db = _session()
    agent = RegisteredAgent(name="Sim Agent", department="WS-1:Sales", permissions="read,write")
    db.add(agent); db.flush()
    # No is_simulation flag set, no WorkItem/WorkUser/actor identity at all
    # -- attributed only to an agent -- matches the legacy simulator
    # fallback heuristic.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", agent_id=agent.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_requests", "simulation_count", "live_count"], timeframe={})
    assert not result.errors
    row = result.rows[0]
    assert row["simulation_count"] == 1
    assert row["live_count"] == 0


def test_people_touched_counts_distinct_identities_excluding_unknown():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        actor_external_id="ACTOR-1", actor_name="Real Person",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        actor_external_id="ACTOR-1", actor_name="Real Person",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["people_touched"], timeframe={})
    assert not result.errors
    assert result.rows[0]["people_touched"] == 1
