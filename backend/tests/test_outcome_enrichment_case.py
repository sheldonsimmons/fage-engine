"""
Second outcome adapter (Salesforce Case) -- proves the pattern in
core/outcome_adapters/ generalizes beyond Opportunity rather than being a
one-off special case, per the CostPilot Universal design's instruction to
prove the pattern before building more outcome integrations.

Case deliberately never gets a dollar value or a won/lost claim -- those
concepts don't exist for a support case the way they do for a deal, and
inventing them would be exactly the kind of overclaim CostPilot is built
to avoid.
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
    WorkItem,
    WorkItemOutcome,
    WorkItemOutcomeEvent,
)
from api.routes_connections import sync_outcomes, _encrypt
from core.outcome_adapters.salesforce_case import (
    build_case_query,
    map_salesforce_case_to_canonical_outcome,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_case_query_rejects_non_salesforce_ids():
    with pytest.raises(ValueError):
        build_case_query(["'; DROP TABLE Case; --"])


def test_maps_salesforce_case_record_without_inventing_value_or_success():
    record = {
        "Id": "500TEST123456789",
        "Status": "Closed",
        "IsClosed": True,
        "ClosedDate": "2026-08-01",
        "OwnerId": "005OWNER123456789",
        "AccountId": "001ACME123456789",
        "LastModifiedDate": "2026-08-01T10:00:00.000+0000",
    }
    canonical = map_salesforce_case_to_canonical_outcome(record)
    assert canonical["outcome_status"] == "Closed"
    assert canonical["is_closed"] is True
    assert canonical["outcome_date"] == datetime(2026, 8, 1)
    assert canonical["outcome_value"] is None
    assert canonical["outcome_success"] is None
    assert canonical["source_object"] == "Case"


def test_sync_outcomes_handles_both_opportunity_and_case_work_items(monkeypatch):
    db = _session()
    connection = IntegrationConnection(
        workspace_id="WS-CASE",
        platform="salesforce",
        display_name="Salesforce (case test)",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("fake-token"),
    )
    db.add(connection)

    opp_item = WorkItem(
        external_id="PROJECT-OPP", name="Test Opportunity",
        context_type="opportunity", context_template="salesforce_opportunity",
        source_platform="Salesforce", source_record_type="Opportunity",
        source_record_id="006TESTOPP0000001", workspace_id="WS-CASE",
    )
    case_item = WorkItem(
        external_id="PROJECT-CASE", name="Test Support Case",
        context_type="case", context_template="salesforce_case",
        source_platform="Salesforce", source_record_type="Case",
        source_record_id="500TESTCASE000001", workspace_id="WS-CASE",
    )
    db.add_all([opp_item, case_item])
    db.commit()
    db.refresh(connection)

    async def fake_salesforce_try_query(_item, query):
        if "FROM Opportunity" in query:
            return ([{
                "Id": "006TESTOPP0000001", "StageName": "Closed Won", "IsClosed": True,
                "IsWon": True, "Amount": 75000.0, "CloseDate": "2026-08-05",
                "OwnerId": "005X", "AccountId": "001X",
                "LastModifiedDate": "2026-08-05T09:00:00.000+0000",
            }], None)
        if "FROM Case" in query:
            return ([{
                "Id": "500TESTCASE000001", "Status": "Closed", "IsClosed": True,
                "ClosedDate": "2026-08-06", "OwnerId": "005Y", "AccountId": "001X",
                "LastModifiedDate": "2026-08-06T09:00:00.000+0000",
            }], None)
        raise AssertionError(f"Unexpected query: {query}")

    monkeypatch.setattr(
        "api.routes_connections._salesforce_try_query",
        lambda item, query, db=None: fake_salesforce_try_query(item, query),
    )

    result = asyncio.run(sync_outcomes(connection.id, db=db))
    assert result["checked"] == 2
    assert result["updated"] == 2
    assert result["errors"] == []

    opp_outcome = db.query(WorkItemOutcome).filter_by(work_item_id=opp_item.id).first()
    case_outcome = db.query(WorkItemOutcome).filter_by(work_item_id=case_item.id).first()

    assert opp_outcome.outcome_value == 75000.0
    assert opp_outcome.outcome_success is True

    assert case_outcome.outcome_status == "Closed"
    assert case_outcome.is_closed is True
    assert case_outcome.outcome_value is None
    assert case_outcome.outcome_success is None

    history = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=case_item.id).all()
    assert len(history) == 1
