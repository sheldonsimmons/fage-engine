"""
GET /api/work-items/accounts/{identifier}/profile -- the account-level
rollup that the Business Profile UI needs and that did not exist before:
AI investment/activity/savings and business outcomes (won/lost/open,
pipeline value, closed-won value) across every work item belonging to one
account. Entirely SQL-side aggregation, no Python row loop.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome
from api.routes_work_items import account_profile


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    account = WorkAccount(external_id="ACCOUNT-ACME", name="Acme Corp", workspace_id="WS-1")
    db.add(account)
    db.flush()

    won_item = WorkItem(
        external_id="PROJECT-ACME-WON", name="Acme Expansion", account_id=account.id,
        context_type="opportunity", source_platform="Salesforce", workspace_id="WS-1",
    )
    lost_item = WorkItem(
        external_id="PROJECT-ACME-LOST", name="Acme Renewal (lost)", account_id=account.id,
        context_type="opportunity", source_platform="Salesforce", workspace_id="WS-1",
    )
    open_item = WorkItem(
        external_id="PROJECT-ACME-OPEN", name="Acme Upsell", account_id=account.id,
        context_type="opportunity", source_platform="Salesforce", workspace_id="WS-1",
    )
    db.add_all([won_item, lost_item, open_item])
    db.flush()

    db.add(WorkItemOutcome(
        work_item_id=won_item.id, workspace_id="WS-1", outcome_status="Closed Won",
        outcome_value=600000.0, outcome_success=True, is_closed=True,
        source_system="salesforce", source_object="Opportunity", external_id="006WON",
        last_synced_at=datetime.utcnow(),
    ))
    db.add(WorkItemOutcome(
        work_item_id=lost_item.id, workspace_id="WS-1", outcome_status="Closed Lost",
        outcome_value=150000.0, outcome_success=False, is_closed=True,
        source_system="salesforce", source_object="Opportunity", external_id="006LOST",
        last_synced_at=datetime.utcnow(),
    ))
    db.add(WorkItemOutcome(
        work_item_id=open_item.id, workspace_id="WS-1", outcome_status="Negotiation",
        outcome_value=300000.0, outcome_success=None, is_closed=False,
        source_system="salesforce", source_object="Opportunity", external_id="006OPEN",
        last_synced_at=datetime.utcnow(),
    ))

    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", source_platform="Salesforce",
        work_item_id=won_item.id, model_tier="Scout", model_name="claude-3-5-haiku",
        input_tokens=800, output_tokens=200, tokens_saved=100, cost_usd=0.42,
        was_pruned=True, business_purpose="Sales & Revenue", timestamp=now,
    ))
    db.add(TokenTransaction(
        department="WS-1:Support", workspace_id="WS-1", source_platform="Salesforce",
        work_item_id=lost_item.id, model_tier="Scout", model_name="claude-3-5-haiku",
        input_tokens=400, output_tokens=100, tokens_saved=50, cost_usd=0.10,
        was_pruned=True, business_purpose="Customer & Employee Support", timestamp=now,
    ))
    db.commit()
    return account


def test_account_profile_rolls_up_spend_and_outcomes_across_work_items():
    db = _session()
    account = _seed(db)

    profile = account_profile(
        identifier="ACCOUNT-ACME", workspace_id="WS-1",
        date_from=datetime.utcnow() - timedelta(days=1),
        date_to=datetime.utcnow() + timedelta(days=1),
        days=90, db=db,
    )

    assert profile["account"]["name"] == "Acme Corp"
    assert profile["work_item_count"] == 3
    assert round(profile["kpis"]["ai_investment_usd"], 2) == 0.52
    assert profile["kpis"]["ai_activity_count"] == 2
    assert profile["kpis"]["tokens_saved"] == 150

    outcomes = profile["outcomes"]
    assert outcomes["won_count"] == 1
    assert outcomes["lost_count"] == 1
    assert outcomes["open_count"] == 1
    assert outcomes["pipeline_value_usd"] == 300000.0
    assert outcomes["closed_won_value_usd"] == 600000.0

    purposes = {row["business_purpose"] for row in profile["business_function_breakdown"]}
    assert purposes == {"Sales & Revenue", "Customer & Employee Support"}


def test_account_profile_reports_prior_period_spend_for_comparison():
    db = _session()
    account = WorkAccount(external_id="ACCOUNT-TREND", name="Trend Co", workspace_id="WS-1")
    db.add(account); db.flush()
    item = WorkItem(external_id="PROJ-TREND", name="Trend Deal", account_id=account.id, workspace_id="WS-1")
    db.add(item); db.flush()

    now = datetime.utcnow()
    # Current period is [now-7d, now+1d) -- an 8-day span -- so the prior
    # period of equal length is [now-15d, now-7d). Place the prior
    # transaction well inside that window.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item.id,
        model_tier="Scout", model_name="claude", input_tokens=100, output_tokens=50,
        tokens_saved=0, cost_usd=10.0, timestamp=now - timedelta(days=10),
    ))
    # Current period.
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_item_id=item.id,
        model_tier="Scout", model_name="claude", input_tokens=100, output_tokens=50,
        tokens_saved=0, cost_usd=15.0, timestamp=now - timedelta(days=1),
    ))
    db.commit()

    profile = account_profile(
        identifier="ACCOUNT-TREND", workspace_id="WS-1",
        date_from=now - timedelta(days=7), date_to=now + timedelta(days=1),
        days=7, db=db,
    )
    assert profile["kpis"]["ai_investment_usd"] == 15.0
    # Prior period is the same length (8 days), ending where the current
    # period starts -- the 20-days-ago transaction falls inside it.
    assert profile["prior_period"]["ai_investment_usd"] == 10.0


def test_account_profile_handles_account_with_no_work_items():
    db = _session()
    db.add(WorkAccount(external_id="ACCOUNT-EMPTY", name="Empty Co", workspace_id="WS-1"))
    db.commit()

    profile = account_profile(identifier="ACCOUNT-EMPTY", workspace_id="WS-1", date_from=None, date_to=None, days=90, db=db)

    assert profile["work_item_count"] == 0
    assert profile["kpis"]["ai_investment_usd"] == 0.0
    assert profile["outcomes"]["won_count"] == 0


def test_account_profile_404s_for_unknown_account():
    db = _session()
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        account_profile(identifier="NOPE", workspace_id="WS-1", date_from=None, date_to=None, days=90, db=db)
    assert exc_info.value.status_code == 404
