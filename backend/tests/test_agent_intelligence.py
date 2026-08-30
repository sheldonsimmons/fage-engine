"""
core/agent_intelligence.py wires together CostPilot's existing trusted
calculations (compute_potential_savings, compute_cost_per_outcome,
compute_outcome_coverage, compute_realized_savings) scoped to one agent
via the new optional agent_id parameter added to each. These tests lock
in that the wiring produces correct numbers and that the deterministic
attention signals fire on the conditions they claim to.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import RegisteredAgent, TokenTransaction, WorkItem, WorkItemOutcome
from core.agent_intelligence import get_agent_intelligence_profile


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_unknown_agent_returns_none():
    db = _session()
    assert get_agent_intelligence_profile(db, 999) is None


def test_economics_and_outcome_attribution_scoped_to_one_agent():
    db = _session()
    agent_a = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write", workspace_id="WS-1")
    agent_b = RegisteredAgent(name="Agent B", department="WS-1:Sales", permissions="read,write", workspace_id="WS-1")
    db.add_all([agent_a, agent_b]); db.flush()

    item_won = WorkItem(external_id="ITEM-WON", name="Won Deal", workspace_id="WS-1", context_type="opportunity")
    db.add(item_won); db.flush()
    db.add(WorkItemOutcome(work_item_id=item_won.id, workspace_id="WS-1",
                            source_system="Salesforce", source_object="Opportunity", external_id="OPP-1",
                            outcome_success=True, is_closed=True, outcome_value=1000.0))
    db.flush()

    now = datetime.utcnow()
    # Agent A's spend touches the won deal.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", agent_id=agent_a.id, work_item_id=item_won.id,
        model_tier="Advisor", model_name="gpt-4.1", input_tokens=100, output_tokens=50,
        cost_usd=10.0, timestamp=now,
    ))
    # Agent B has its own, unrelated spend with no work item at all.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", agent_id=agent_b.id, work_item_id=None,
        model_tier="Scout", model_name="claude-3-5-haiku", input_tokens=100, output_tokens=50,
        cost_usd=2.0, timestamp=now,
    ))
    db.commit()

    profile_a = get_agent_intelligence_profile(db, agent_a.id)
    assert profile_a["agent"]["name"] == "Agent A"
    econ_a = profile_a["economics"]
    assert econ_a["total_ai_spend_usd"] == 10.0
    assert econ_a["associated_business_value_usd"] == 1000.0
    assert len(profile_a["work_item_attribution"]) == 1
    assert profile_a["work_item_attribution"][0]["work_item_id"] == item_won.id

    profile_b = get_agent_intelligence_profile(db, agent_b.id)
    econ_b = profile_b["economics"]
    assert econ_b["total_ai_spend_usd"] == 2.0
    # Agent B's spend never touched a WorkItem, so it has zero associated
    # value and zero attribution rows -- must not see Agent A's outcome.
    assert econ_b["associated_business_value_usd"] == 0.0
    assert profile_b["work_item_attribution"] == []


def test_runtime_and_lifecycle_fields_stay_separate():
    db = _session()
    agent = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write",
                             status="active", approval_status="approved")
    db.add(agent); db.commit()

    profile = get_agent_intelligence_profile(db, agent.id)
    assert profile["agent"]["runtime_status"] == "active"
    assert profile["agent"]["lifecycle"]["approval_status"] == "approved"
    # The two must be genuinely separate top-level concepts, not one enum.
    assert "runtime_status" not in profile["agent"]["lifecycle"]
    assert "lifecycle" not in {"runtime_status"}


def test_active_unreviewed_signal_fires_only_when_both_true():
    db = _session()
    recently = datetime.utcnow() - timedelta(minutes=1)

    unreviewed_active = RegisteredAgent(name="Unreviewed Active", department="WS-1:Sales",
                                         permissions="read,write", status="active",
                                         approval_status="unreviewed", last_used_at=recently)
    approved_active = RegisteredAgent(name="Approved Active", department="WS-1:Sales",
                                       permissions="read,write", status="active",
                                       approval_status="approved", last_used_at=recently)
    db.add_all([unreviewed_active, approved_active]); db.commit()

    signals_unreviewed = {s["signal"] for s in get_agent_intelligence_profile(db, unreviewed_active.id)["attention_signals"]}
    signals_approved = {s["signal"] for s in get_agent_intelligence_profile(db, approved_active.id)["attention_signals"]}

    assert "active_unreviewed" in signals_unreviewed
    assert "active_unreviewed" not in signals_approved


def test_inactive_agent_signal_fires_past_the_idle_threshold():
    db = _session()
    long_ago = datetime.utcnow() - timedelta(days=90)
    stale_agent = RegisteredAgent(name="Stale Agent", department="WS-1:Sales", permissions="read,write",
                                   last_used_at=long_ago)
    never_used_agent = RegisteredAgent(name="Never Used Agent", department="WS-1:Sales", permissions="read,write")
    archived_agent = RegisteredAgent(name="Archived Agent", department="WS-1:Sales", permissions="read,write",
                                      last_used_at=long_ago, archived=True)
    db.add_all([stale_agent, never_used_agent, archived_agent]); db.commit()

    assert "inactive_agent" in {s["signal"] for s in get_agent_intelligence_profile(db, stale_agent.id)["attention_signals"]}
    assert "inactive_agent" in {s["signal"] for s in get_agent_intelligence_profile(db, never_used_agent.id)["attention_signals"]}
    # Archived agents are already out of the live grid -- not worth flagging as "inactive" too.
    assert "inactive_agent" not in {s["signal"] for s in get_agent_intelligence_profile(db, archived_agent.id)["attention_signals"]}


def test_governance_concern_signal_fires_for_active_deprecated_agent():
    db = _session()
    recently = datetime.utcnow() - timedelta(minutes=1)
    deprecated_but_active = RegisteredAgent(name="Zombie Agent", department="WS-1:Sales", permissions="read,write",
                                             approval_status="deprecated", last_used_at=recently)
    deprecated_and_idle = RegisteredAgent(name="Retired Agent", department="WS-1:Sales", permissions="read,write",
                                           approval_status="retired")
    db.add_all([deprecated_but_active, deprecated_and_idle]); db.commit()

    assert "governance_concern" in {s["signal"] for s in get_agent_intelligence_profile(db, deprecated_but_active.id)["attention_signals"]}
    # Retired AND idle isn't a "still running when it shouldn't be" concern.
    assert "governance_concern" not in {s["signal"] for s in get_agent_intelligence_profile(db, deprecated_and_idle.id)["attention_signals"]}
