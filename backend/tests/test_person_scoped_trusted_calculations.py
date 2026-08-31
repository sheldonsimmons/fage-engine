"""
compute_realized_savings (api/routes_reports.py), compute_potential_savings,
compute_outcome_coverage, compute_cost_per_outcome (core/metrics_query.py)
each already accepted an agent_id scope for the Agent Intelligence Profile.
These tests lock in the new person_external_id scope added for the Person
Intelligence Profile (AI Activity Explorer Phase 1) -- same trusted
calculations, one more optional filter, no new source of truth.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome, WorkUser
from api.routes_reports import compute_realized_savings
from core.metrics_query import compute_cost_per_outcome, compute_outcome_coverage, compute_potential_savings


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_realized_savings_scoped_to_person_excludes_other_people():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.flush()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id,
        model_tier="Scout", input_tokens=1000, output_tokens=500, tokens_saved=200,
        was_pruned=True, cost_usd=1.0, timestamp=now,
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", actor_external_id="USER-OTHER",
        model_tier="Scout", input_tokens=1000, output_tokens=500, tokens_saved=999,
        was_pruned=True, cost_usd=1.0, timestamp=now,
    ))
    db.commit()

    result = compute_realized_savings(db, "WS-1", days=30, person_external_id="USER-DANA")
    assert result["total_saved_usd"] > 0
    # Only Dana's 200 pruned tokens should count, not the other person's 999.
    unscoped = compute_realized_savings(db, "WS-1", days=30)
    assert result["total_saved_usd"] < unscoped["total_saved_usd"]


def test_potential_savings_scoped_to_person():
    from database.models import KnownModel

    db = _session()
    db.add(KnownModel(
        model_id="scout-model", display_name="Scout", provider="Anthropic", provider_group="anthropic", tier=1,
        cost_input_per_1m=1.0, cost_output_per_1m=1.0, is_active=True,
    ))
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.flush()
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id,
        model_tier="Advisor", routing_reason="ROUTINE",
        input_tokens=100000, output_tokens=50000, cost_usd=10.0, timestamp=now,
    ))
    db.commit()

    result = compute_potential_savings(db, "WS-1", person_external_id="USER-DANA")
    assert result["potential_savings_usd"] > 0
    assert result["candidate_request_count"] == 1


def test_outcome_coverage_scoped_to_person():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    db.add(dana); db.flush()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-1", name="Acme Deal",
    )
    db.add(item); db.flush()
    db.add(WorkItemOutcome(
        work_item_id=item.id, workspace_id="WS-1", source_system="salesforce",
        source_object="Opportunity", external_id="OPP-1",
        outcome_success=True, is_closed=True, outcome_value=100.0,
        last_synced_at=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id, work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=1.0, timestamp=datetime.utcnow(),
    ))
    db.commit()

    result = compute_outcome_coverage(db, "WS-1", person_external_id="USER-DANA")
    assert result["work_items_touched"] == 1
    assert result["outcomes_with_known_data"] == 1
    assert result["outcome_coverage_pct"] == 100.0


def test_cost_per_outcome_scoped_to_person_isolates_from_other_people():
    db = _session()
    dana = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-DANA", name="Dana")
    elena = WorkUser(workspace_id="WS-1", source_platform="Salesforce", external_id="USER-ELENA", name="Elena")
    db.add_all([dana, elena]); db.flush()
    account = WorkAccount(workspace_id="WS-1", name="Acme", external_id="ACC-1")
    db.add(account); db.flush()
    item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-1", name="Acme Deal",
    )
    db.add(item); db.flush()
    db.add(WorkItemOutcome(
        work_item_id=item.id, workspace_id="WS-1", source_system="salesforce",
        source_object="Opportunity", external_id="OPP-1",
        outcome_success=True, is_closed=True, outcome_value=100.0,
        last_synced_at=datetime.utcnow(),
    ))
    now = datetime.utcnow()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=dana.id, work_item_id=item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=10.0, timestamp=now,
    ))
    # Elena's spend is on a DIFFERENT, outcome-less work item -- must not
    # leak into Dana's cost-per-outcome numerator or denominator.
    other_item = WorkItem(
        workspace_id="WS-1", account_id=account.id, context_type="opportunity",
        external_id="OPP-2", name="Other Deal",
    )
    db.add(other_item); db.flush()
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", work_user_id=elena.id, work_item_id=other_item.id,
        model_tier="Scout", input_tokens=100, output_tokens=50, cost_usd=999.0, timestamp=now,
    ))
    db.commit()

    result = compute_cost_per_outcome(db, "WS-1", person_external_id="USER-DANA")
    assert result["successful_outcomes"] == 1
    assert result["ai_spend_on_successful_outcomes_usd"] == 10.0
    assert result["cost_per_successful_outcome_usd"] == 10.0
