"""
End-to-end proof of AI Event -> Work Item -> Salesforce Outcome -> Ask
CostPilot, per the CostPilot Universal outcome-enrichment design doc's
section 14 scenario: an Opportunity accumulates AI activity, then Salesforce
later reports Closed Won, and the report layer can answer questions that
combine both.
"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    IntegrationConnection,
    TokenTransaction,
    WorkItem,
    WorkItemOutcome,
    WorkItemOutcomeEvent,
)
from api.routes_connections import sync_outcomes
from api.routes_work_items import project_activity_reporting
from core.outcome_adapters.salesforce_opportunity import (
    build_opportunity_query,
    map_salesforce_opportunity_to_canonical_outcome,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_connection(db, workspace_id="WS-ACME"):
    from api.routes_connections import _encrypt

    connection = IntegrationConnection(
        workspace_id=workspace_id,
        platform="salesforce",
        display_name="Salesforce",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("fake-access-token"),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _make_opportunity_work_item(db, workspace_id="WS-ACME"):
    work_item = WorkItem(
        external_id="PROJECT-ACME-EXPANSION",
        name="Acme Expansion",
        context_type="opportunity",
        context_template="salesforce_opportunity",
        source_platform="Salesforce",
        source_record_type="Opportunity",
        source_record_id="006TEST123456789",
        workspace_id=workspace_id,
        status="active",
    )
    db.add(work_item)
    db.commit()
    db.refresh(work_item)
    return work_item


def _seed_ai_activity(db, work_item, n=83, total_cost=196.0, tokens_saved_total=None):
    now = datetime.utcnow()
    per_call_cost = round(total_cost / n, 6)
    for i in range(n):
        db.add(TokenTransaction(
            department="WS-ACME:Sales",
            workspace_id=work_item.workspace_id,
            source_platform="Salesforce",
            work_item_id=work_item.id,
            origin_record_id=work_item.source_record_id,
            origin_record_type="Opportunity",
            model_tier="Scout",
            model_name="claude-3-5-haiku",
            input_tokens=150,
            output_tokens=60,
            tokens_saved=25,
            cost_usd=per_call_cost,
            timestamp=now - timedelta(hours=i),
        ))
    db.commit()


def test_opportunity_query_rejects_non_salesforce_ids():
    with pytest.raises(ValueError):
        build_opportunity_query(["'; DROP TABLE Opportunity; --"])


def test_maps_salesforce_opportunity_record_to_canonical_outcome():
    record = {
        "Id": "006TEST123456789",
        "StageName": "Closed Won",
        "IsClosed": True,
        "IsWon": True,
        "Amount": 600000.0,
        "CloseDate": "2026-08-15",
        "OwnerId": "005OWNER123456789",
        "AccountId": "001ACME123456789",
        "LastModifiedDate": "2026-08-15T14:32:00.000+0000",
    }
    canonical = map_salesforce_opportunity_to_canonical_outcome(record)
    assert canonical["outcome_status"] == "Closed Won"
    assert canonical["outcome_value"] == 600000.0
    assert canonical["outcome_success"] is True
    assert canonical["is_closed"] is True
    assert canonical["outcome_date"] == datetime(2026, 8, 15)
    assert canonical["source_system"] == "salesforce"
    assert canonical["source_object"] == "Opportunity"
    assert canonical["external_id"] == "006TEST123456789"


def test_full_scenario_ai_event_to_work_item_to_outcome_to_reporting(monkeypatch):
    db = _session()
    connection = _make_connection(db)
    work_item = _make_opportunity_work_item(db, workspace_id=connection.workspace_id)
    _seed_ai_activity(db, work_item, n=83, total_cost=196.0)

    async def fake_salesforce_try_query(_item, _query, db=None):
        return (
            [{
                "Id": work_item.source_record_id,
                "StageName": "Closed Won",
                "IsClosed": True,
                "IsWon": True,
                "Amount": 600000.0,
                "CloseDate": "2026-08-15",
                "OwnerId": "005OWNER123456789",
                "AccountId": "001ACME123456789",
                "LastModifiedDate": "2026-08-15T14:32:00.000+0000",
            }],
            None,
        )

    monkeypatch.setattr(
        "api.routes_connections._salesforce_try_query", fake_salesforce_try_query
    )

    result = asyncio.run(sync_outcomes(connection.id, db=db))
    assert result["updated"] == 1
    assert result["errors"] == []

    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert outcome is not None
    assert outcome.outcome_status == "Closed Won"
    assert outcome.outcome_value == 600000.0
    assert outcome.outcome_success is True
    assert outcome.retrieval_method == "sync"

    history = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).all()
    assert len(history) == 1

    # Re-syncing an unchanged record should not create duplicate history.
    result_again = asyncio.run(sync_outcomes(connection.id, db=db))
    assert result_again["unchanged"] == 1
    assert result_again["updated"] == 0
    history_after = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).all()
    assert len(history_after) == 1

    # "How much AI was used on the Acme opportunity, and what did it cost?"
    report = project_activity_reporting(
        workspace_id=connection.workspace_id,
        date_from=datetime.utcnow() - timedelta(days=7),
        date_to=datetime.utcnow() + timedelta(days=1),
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, provider=None, activity_limit=500, days=30,
        db=db,
    )
    assert report["summary"]["request_count"] == 83
    assert round(report["summary"]["spend_usd"], 2) == 196.0

    # project_activity_reporting joins WorkItemOutcome at read time (never
    # copies it onto TokenTransaction) so the AI-spend breakdown and the
    # business outcome show up together without CostPilot ever asserting
    # one caused the other -- that restraint lives in Ask CostPilot's
    # narration guardrail (_ask_narration_causal_claims), not here.
    [project_row] = [
        row for row in report["project_breakdown"]
        if row["id"] == work_item.external_id
    ]
    assert round(project_row["spend_usd"], 2) == 196.0
    assert project_row["outcome_status"] == "Closed Won"
    assert project_row["outcome_value"] == 600000.0
    assert project_row["outcome_success"] is True
    assert project_row["outcome_freshness"] == "current"
