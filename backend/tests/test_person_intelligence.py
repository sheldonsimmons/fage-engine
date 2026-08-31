"""
core/person_intelligence.py — Person Intelligence Profile. New for the
AI Activity Explorer's Person drill-down (Phase 1 of the approved
reporting plan). Modeled directly on core/agent_intelligence.py's tests
(isolation between entities, real numbers, no invented scores).
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome, WorkUser
from core.person_intelligence import get_person_intelligence_profile


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_profile_returns_none_for_unknown_person():
    db = _session()
    assert get_person_intelligence_profile(db, "WS-1", "NOBODY") is None


def test_profile_resolves_from_synced_work_user():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana Ackerman", email="dana@acme.com")
    db.add(dana); db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "USER-DANA")
    assert profile is not None
    assert profile["person"]["name"] == "Dana Ackerman"
    assert profile["person"]["email"] == "dana@acme.com"
    assert profile["person"]["identity_kind"] == "work_user"


def test_profile_resolves_from_actor_only_identity_without_work_user():
    db = _session()
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", actor_external_id="ACTOR-1", actor_name="Legacy Actor",
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "ACTOR-1")
    assert profile is not None
    assert profile["person"]["name"] == "Legacy Actor"
    assert profile["person"]["identity_kind"] == "actor_only"


def test_profile_isolates_economics_between_people():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    elena = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-ELENA", name="Elena")
    db.add_all([dana, elena]); db.flush()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=5.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=elena.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=999.0, timestamp=now,
    ))
    db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "USER-DANA")
    assert profile["economics"]["ai_investment_usd"] == 5.0
    assert profile["economics"]["ai_requests"] == 1


def test_profile_lists_agents_used_and_model_usage():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.flush()
    agent = RegisteredAgent(name="Pipeline Coach", department="WS-1:Sales", permissions="read,write")
    db.add(agent); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id, agent_id=agent.id,
        model_name="claude-sonnet-4-6", model_tier="Advisor",
        input_tokens=100, output_tokens=50, cost_usd=3.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "USER-DANA")
    assert profile["agents_used"] == [{"agent_id": agent.id, "agent_name": "Pipeline Coach", "ai_spend_usd": 3.0, "request_count": 1}]
    assert profile["model_usage"][0]["model"] == "claude-sonnet-4-6"


def test_profile_lists_accounts_and_work_items():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.flush()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item = WorkItem(workspace_id="WS-1", account_id=account.id, context_type="opportunity", external_id="OPP-1", name="Acme Deal")
    db.add(item); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id, work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=4.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "USER-DANA")
    assert profile["accounts"][0]["account_name"] == "Acme"
    assert profile["work_items"][0]["name"] == "Acme Deal"


def _all_keys(obj, out=None):
    out = out if out is not None else set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _all_keys(item, out)
    return out


def test_profile_has_no_productivity_score_or_ranking_fields():
    """
    Explicit guardrail from the approved plan: no productivity scoring,
    no performance judgments, no employee ranking anywhere in this
    payload -- checked by field NAME, not prose (the association_note's
    disclaimer text legitimately mentions "performance score" precisely
    to disclaim it, which isn't the same as the payload carrying one).
    """
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.commit()

    profile = get_person_intelligence_profile(db, "WS-1", "USER-DANA")
    forbidden_terms = ("score", "rank", "performance", "productivity", "grade", "rating")
    keys = _all_keys(profile)
    for key in keys:
        key_lower = key.lower()
        for term in forbidden_terms:
            assert term not in key_lower, f"forbidden term '{term}' found in field name '{key}'"
