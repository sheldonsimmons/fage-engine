"""
core/metrics_query.py's DIMENSIONS registry was missing "person" and
"provider" -- 2 of the 7 breakdowns Ask CostPilot's get_usage_report
tool needs, blocking that tool's migration onto the registry
(Recommendation #4 of the Ask CostPilot architecture audit). These
tests lock in both new dimensions before that migration relies on them.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import RegisteredAgent, TokenTransaction, WorkUser
from core.metrics_query import run_metrics_query


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_person_dimension_groups_by_linked_workuser():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana A")
    db.add(dana); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["person"], timeframe={})
    assert not result.errors
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["person"] == "Dana A"
    assert result.rows[0]["ai_spend"] == 1.0


def test_person_dimension_falls_back_to_actor_fields_without_workuser():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", work_user_id=None,
        actor_external_id="ACTOR-ONLY", actor_name="Actor Only",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=2.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend"], dimensions=["person"], timeframe={})
    assert not result.errors
    assert result.rows[0]["dimensions"]["person"] == "Actor Only"
    assert result.rows[0]["ai_spend"] == 2.0


def test_provider_dimension_resolves_from_model_name_via_prefix_heuristic():
    db = _session()
    agent = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write")
    db.add(agent); db.flush()
    now = datetime.utcnow()
    db.add_all([
        TokenTransaction(department="WS-1:Sales", workspace_id="WS-1", agent_id=agent.id,
                          model_name="claude-3-5-haiku", model_tier="Scout",
                          input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now),
        TokenTransaction(department="WS-1:Sales", workspace_id="WS-1", agent_id=agent.id,
                          model_name="gpt-4.1", model_tier="Advisor",
                          input_tokens=200, output_tokens=100, cost_usd=3.0, timestamp=now),
        # A second Anthropic model -- must be RE-AGGREGATED into the same
        # "Anthropic" bucket as claude-3-5-haiku above, not left as two
        # separate provider rows.
        TokenTransaction(department="WS-1:Sales", workspace_id="WS-1", agent_id=agent.id,
                          model_name="claude-opus-4-6", model_tier="Strategist",
                          input_tokens=50, output_tokens=25, cost_usd=5.0, timestamp=now),
    ])
    db.commit()

    result = run_metrics_query(db, "WS-1", metrics=["ai_spend", "ai_requests"], dimensions=["provider"], timeframe={})
    assert not result.errors
    by_provider = {r["dimensions"]["provider"]: r for r in result.rows}
    assert set(by_provider) == {"Anthropic", "OpenAI"}
    assert by_provider["Anthropic"]["ai_spend"] == 6.0   # 1.0 + 5.0, two Claude models merged
    assert by_provider["Anthropic"]["ai_requests"] == 2
    assert by_provider["OpenAI"]["ai_spend"] == 3.0
    assert by_provider["OpenAI"]["ai_requests"] == 1


def test_provider_dimension_respects_filters_and_timeframe():
    db = _session()
    agent = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write")
    db.add(agent); db.flush()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", agent_id=agent.id,
        model_name="gpt-4.1", model_tier="Advisor",
        input_tokens=200, output_tokens=100, cost_usd=3.0, timestamp=now,
    ))
    db.commit()

    result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["provider"],
        filters={"department": "Sales"}, timeframe={},
    )
    assert len(result.rows) == 1
    assert result.rows[0]["dimensions"]["provider"] == "OpenAI"

    # Wrong department -> no rows, not an error.
    empty_result = run_metrics_query(
        db, "WS-1", metrics=["ai_spend"], dimensions=["provider"],
        filters={"department": "Engineering"}, timeframe={},
    )
    assert empty_result.rows == []
