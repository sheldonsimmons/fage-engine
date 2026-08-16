"""
tests/test_get_account_outcomes.py — correctness check for
api.ask_costpilot_tools.run_get_account_outcomes, the Ask CostPilot agent
tool that surfaces Opportunity won/lost/open, pipeline value, and AI spend
tied to those outcomes. Before this tool existed, no agent tool touched
WorkItemOutcome at all, so named-account and won/lost questions were
unanswerable regardless of entity_name.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.ask_costpilot_tools import run_get_account_outcomes
from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _account(db, *, name, external_id, workspace_id="WS1"):
    acct = WorkAccount(name=name, external_id=external_id, workspace_id=workspace_id)
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct


def _work_item(db, *, external_id, account=None, workspace_id="WS1", context_type="opportunity"):
    wi = WorkItem(
        external_id=external_id, workspace_id=workspace_id, name=external_id,
        context_type=context_type, account_id=account.id if account else None,
    )
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _outcome(db, work_item, *, workspace_id="WS1", outcome_success=None, is_closed=False, outcome_value=0.0):
    db.add(WorkItemOutcome(
        work_item_id=work_item.id, workspace_id=workspace_id, source_system="salesforce",
        source_object="Opportunity", external_id=work_item.external_id,
        outcome_success=outcome_success, is_closed=is_closed, outcome_value=outcome_value,
        last_synced_at=datetime.utcnow(),
    ))


def _tx(db, work_item, *, cost_usd, tokens=100, workspace_id="WS1"):
    db.add(TokenTransaction(
        department="Sales", model_tier="Scout", input_tokens=tokens, output_tokens=0,
        cost_usd=cost_usd, timestamp=datetime.utcnow(), workspace_id=workspace_id,
        is_simulation=False, usage_source="estimated", routing_reason="ROUTINE",
        work_item_id=work_item.id,
    ))


def test_unknown_account_name_reports_not_found():
    db = _session()
    result = run_get_account_outcomes(db, "WS1", "Nonexistent Co")
    assert result["found"] is False
    assert "ambiguous" not in result or not result["ambiguous"]


def test_ambiguous_account_name_lists_candidates():
    db = _session()
    _account(db, name="Acme Corp", external_id="acct-1")
    _account(db, name="Acme Industries", external_id="acct-2")
    db.commit()

    result = run_get_account_outcomes(db, "WS1", "Acme")
    assert result["found"] is False
    assert result["ambiguous"] is True
    assert len(result["candidates"]) == 2


def test_named_account_scopes_outcomes_to_that_account_only():
    db = _session()
    acme = _account(db, name="Acme", external_id="acct-1")
    other = _account(db, name="Globex", external_id="acct-2")
    acme_won = _work_item(db, external_id="opp-acme-won", account=acme)
    other_won = _work_item(db, external_id="opp-globex-won", account=other)
    _outcome(db, acme_won, outcome_success=True, is_closed=True, outcome_value=10000.0)
    _outcome(db, other_won, outcome_success=True, is_closed=True, outcome_value=999999.0)
    db.commit()

    result = run_get_account_outcomes(db, "WS1", "Acme")
    assert result["found"] is True
    assert result["scope"] == "account"
    assert result["opportunities_won"] == 1
    assert result["closed_won_value_usd"] == 10000.0


def test_empty_entity_name_returns_company_wide_scope():
    db = _session()
    won = _work_item(db, external_id="opp-1")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=5000.0)
    db.commit()

    result = run_get_account_outcomes(db, "WS1", "")
    assert result["found"] is True
    assert result["scope"] == "workspace"
    assert result["opportunities_won"] == 1


def test_ai_spend_split_between_won_and_lost():
    db = _session()
    won = _work_item(db, external_id="opp-won")
    lost = _work_item(db, external_id="opp-lost")
    _outcome(db, won, outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, lost, outcome_success=False, is_closed=True, outcome_value=500.0)
    _tx(db, won, cost_usd=3.0)
    _tx(db, lost, cost_usd=7.0)
    db.commit()

    result = run_get_account_outcomes(db, "WS1", "")
    assert result["ai_spend_on_won_opportunities_usd"] == 3.0
    assert result["ai_spend_on_lost_opportunities_usd"] == 7.0


def test_workspace_scoping_excludes_other_workspaces():
    db = _session()
    mine = _work_item(db, external_id="opp-mine", workspace_id="WS-A")
    theirs = _work_item(db, external_id="opp-theirs", workspace_id="WS-B")
    _outcome(db, mine, workspace_id="WS-A", outcome_success=True, is_closed=True, outcome_value=1000.0)
    _outcome(db, theirs, workspace_id="WS-B", outcome_success=True, is_closed=True, outcome_value=999999.0)
    db.commit()

    result = run_get_account_outcomes(db, "WS-A", "")
    assert result["opportunities_won"] == 1
    assert result["closed_won_value_usd"] == 1000.0
