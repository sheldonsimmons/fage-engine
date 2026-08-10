"""
project_activity_reporting()'s filters used to all be applied in Python
after loading every workspace+date-scoped row into memory -- the actual
cause of real production timeouts/memory spikes once row counts grew.
project_id/user_external_id/agent_id/account_id/source_platform/
record_type/model_tier now have exact SQL equivalents pushed down before
the row load. This locks in that each one still filters correctly, that
the actor_external_id fallback for user_external_id still works, and that
filter_options still reflects the FULL scoped set (not the filtered one)
so a user can still broaden/pivot filters -- the one behavior that must
NOT change from pushing filters into the base query.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    RegisteredAgent, TokenTransaction, WorkAccount, WorkItem, WorkUser,
)
from api.routes_work_items import project_activity_reporting


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    account_a = WorkAccount(external_id="ACC-A", name="Acme Corp", workspace_id="WS-1")
    account_b = WorkAccount(external_id="ACC-B", name="Beta Inc", workspace_id="WS-1")
    db.add_all([account_a, account_b]); db.flush()

    item_a = WorkItem(external_id="ITEM-A", name="Acme Deal", account_id=account_a.id, workspace_id="WS-1")
    item_b = WorkItem(external_id="ITEM-B", name="Beta Deal", account_id=account_b.id, workspace_id="WS-1")
    db.add_all([item_a, item_b]); db.flush()

    user_a = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-A", name="Dana A")
    db.add(user_a); db.flush()

    agent_a = RegisteredAgent(name="Agent A", department="WS-1:Sales", permissions="read,write")
    agent_b = RegisteredAgent(name="Agent B", department="WS-1:Support", permissions="read,write")
    db.add_all([agent_a, agent_b]); db.flush()

    now = datetime.utcnow()
    # Row 1: linked to a real WorkUser, agent A, item A, account A, Salesforce/Opportunity/Scout.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item_a.id,
        work_user_id=user_a.id, agent_id=agent_a.id,
        source_platform="Salesforce", origin_record_type="Opportunity",
        model_tier="Scout", model_name="claude-3-5-haiku",
        input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=now,
    ))
    # Row 2: no WorkUser row, only actor_external_id -- exercises the
    # user_external_id fallback. Agent B, item B, account B, ServiceNow/Case/Advisor.
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", work_item_id=item_b.id,
        agent_id=agent_b.id, actor_external_id="ACTOR-ONLY",
        source_platform="ServiceNow", origin_record_type="Case",
        model_tier="Advisor", model_name="gpt-4.1",
        input_tokens=200, output_tokens=100, cost_usd=2.0, timestamp=now,
    ))
    db.commit()
    return item_a, item_b, user_a, agent_a, agent_b, account_a, account_b


def _report(db, **filters):
    return project_activity_reporting(
        workspace_id="WS-1",
        date_from=datetime.utcnow() - timedelta(days=1),
        date_to=datetime.utcnow() + timedelta(days=1),
        days=30, db=db, **filters,
    )


def test_project_id_filter():
    db = _session()
    item_a, *_ = _seed(db)
    report = _report(db, project_id=item_a.external_id, user_external_id=None,
                      agent_id=None, account_id=None, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["project_external_id"] == item_a.external_id


def test_user_external_id_filter_matches_real_workuser():
    db = _session()
    _, _, user_a, *_ = _seed(db)
    report = _report(db, project_id=None, user_external_id=user_a.external_id,
                      agent_id=None, account_id=None, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["user_external_id"] == user_a.external_id


def test_user_external_id_filter_matches_actor_fallback_when_no_workuser_row():
    db = _session()
    _seed(db)
    report = _report(db, project_id=None, user_external_id="ACTOR-ONLY",
                      agent_id=None, account_id=None, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["user_external_id"] == "ACTOR-ONLY"


def test_agent_id_filter():
    db = _session()
    _, _, _, agent_a, agent_b, *_ = _seed(db)
    report = _report(db, project_id=None, user_external_id=None,
                      agent_id=agent_b.id, account_id=None, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["agent_id"] == agent_b.id


def test_account_id_filter():
    db = _session()
    *_, account_a, account_b = _seed(db)
    report = _report(db, project_id=None, user_external_id=None,
                      agent_id=None, account_id=account_b.external_id, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["account_external_id"] == account_b.external_id


def test_source_platform_and_record_type_and_model_tier_filters():
    db = _session()
    _seed(db)
    report = _report(db, project_id=None, user_external_id=None, agent_id=None,
                      account_id=None, source_platform="servicenow", record_type="Case",
                      model_tier="Advisor", charged_unit=None, business_purpose=None,
                      provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    assert report["activities"][0]["source_platform"] == "ServiceNow"


def test_filter_options_show_full_set_even_when_narrowly_filtered():
    # The one behavior that must NOT change: filter_options reflects the
    # full workspace+date scoped set so a user can still broaden/pivot,
    # not just what matched the currently-applied filter.
    db = _session()
    item_a, item_b, *_ = _seed(db)
    report = _report(db, project_id=item_a.external_id, user_external_id=None,
                      agent_id=None, account_id=None, source_platform=None,
                      record_type=None, model_tier=None, charged_unit=None,
                      business_purpose=None, provider=None, activity_limit=50)
    assert report["summary"]["request_count"] == 1
    # Both projects' names must still be discoverable in the full option
    # set, even though only one project matched the applied filter.
    all_labels = {row["label"] for row in report["filter_options"]["projects"]}
    assert "Acme Deal" in all_labels
    assert "Beta Deal" in all_labels
