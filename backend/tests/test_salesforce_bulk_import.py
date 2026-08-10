"""
Bulk discovery/import of Salesforce Opportunities and Cases as CostPilot
WorkItems -- the piece that was missing before every WorkItem had to be
created by hand, one API call at a time. Deliberately tests that a second
import run is idempotent (updates, never duplicates) and that outcome data
is seeded in the same pass as the WorkItem itself.
"""
import asyncio
import os

os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import (
    IntegrationConnection, WorkAccount, WorkItem, WorkItemOutcome,
    WorkItemOutcomeEvent, WorkItemSourceLink,
)
from api.routes_connections import (
    _encrypt, _salesforce_query_all, import_work_items,
)
from core.outcome_adapters.salesforce_opportunity import (
    build_all_opportunities_query,
    map_salesforce_opportunity_to_work_item_fields,
)
from core.outcome_adapters.salesforce_case import (
    build_all_cases_query,
    map_salesforce_case_to_work_item_fields,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_connection(db, workspace_id="WS-IMPORT"):
    connection = IntegrationConnection(
        workspace_id=workspace_id, platform="salesforce", display_name="Salesforce",
        status="connected", instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("fake-token"),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def test_maps_opportunity_relationship_query_fields():
    record = {
        "Id": "006IMPORT000001", "Name": "Acme Renewal",
        "AccountId": "001ACME000001", "Account": {"Name": "Acme Corp"},
        "StageName": "Closed Won", "IsClosed": True, "IsWon": True,
        "Amount": 42000.0, "CloseDate": "2026-01-01",
        "OwnerId": "005X", "LastModifiedDate": "2026-01-01T00:00:00.000+0000",
    }
    fields = map_salesforce_opportunity_to_work_item_fields(record)
    assert fields["name"] == "Acme Renewal"
    assert fields["source_record_id"] == "006IMPORT000001"
    assert fields["account_external_id"] == "001ACME000001"
    assert fields["account_name"] == "Acme Corp"


def test_maps_opportunity_without_account_relationship_falls_back_to_id():
    record = {"Id": "006X", "Name": "No Account Deal", "AccountId": "001Y", "Account": None}
    fields = map_salesforce_opportunity_to_work_item_fields(record)
    assert fields["account_name"] == "001Y"


def test_maps_case_uses_case_number_and_subject():
    record = {
        "Id": "500IMPORT001", "CaseNumber": "00001234", "Subject": "Cannot log in",
        "AccountId": "001ACME000001", "Account": {"Name": "Acme Corp"},
        "Status": "New", "IsClosed": False,
    }
    fields = map_salesforce_case_to_work_item_fields(record)
    assert fields["name"] == "Case 00001234: Cannot log in"
    assert fields["source_record_id"] == "500IMPORT001"


def test_query_all_follows_pagination(monkeypatch):
    """Salesforce caps query results at 2000/page -- bulk import needs to
    follow nextRecordsUrl to actually get every record, not just the first
    page."""
    db = _session()
    connection = _make_connection(db)

    class _FakeAsyncClient:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            self.calls.append(url)
            if "nextpage" not in url:
                return httpx.Response(200, json={
                    "records": [{"Id": "006A"}, {"Id": "006B"}],
                    "done": False,
                    "nextRecordsUrl": "/services/data/v65.0/query/nextpage-abc",
                })
            return httpx.Response(200, json={"records": [{"Id": "006C"}], "done": True})

    fake_client = _FakeAsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)

    records, error = asyncio.run(_salesforce_query_all(connection, "SELECT Id FROM Opportunity", db=db))
    assert error is None
    assert [r["Id"] for r in records] == ["006A", "006B", "006C"]
    assert len(fake_client.calls) == 2


def test_import_creates_work_items_accounts_and_outcomes(monkeypatch):
    db = _session()
    connection = _make_connection(db)

    fake_records = [
        {
            "Id": "006IMPORT000001", "Name": "Acme Renewal", "AccountId": "001ACME000001",
            "Account": {"Name": "Acme Corp"}, "StageName": "Closed Won", "IsClosed": True,
            "IsWon": True, "Amount": 42000.0, "CloseDate": "2026-01-01",
            "OwnerId": "005X", "LastModifiedDate": "2026-01-01T00:00:00.000+0000",
        },
        {
            "Id": "006IMPORT000002", "Name": "Beta Expansion", "AccountId": "001BETA000001",
            "Account": {"Name": "Beta Inc"}, "StageName": "Negotiation", "IsClosed": False,
            "IsWon": False, "Amount": 15000.0, "CloseDate": None,
            "OwnerId": "005Y", "LastModifiedDate": "2026-01-02T00:00:00.000+0000",
        },
    ]

    async def fake_query_all(_item, _query, db=None, max_records=50_000):
        return fake_records, None

    monkeypatch.setattr("api.routes_connections._salesforce_query_all", fake_query_all)

    result = asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    assert result["discovered"] == 2
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []

    accounts = db.query(WorkAccount).filter_by(workspace_id="WS-IMPORT").all()
    assert {a.name for a in accounts} == {"Acme Corp", "Beta Inc"}

    work_items = db.query(WorkItem).filter_by(workspace_id="WS-IMPORT").all()
    assert len(work_items) == 2
    acme_item = next(w for w in work_items if w.name == "Acme Renewal")
    assert acme_item.context_type == "opportunity"
    assert acme_item.source_record_id == "006IMPORT000001"

    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=acme_item.id).first()
    assert outcome.outcome_status == "Closed Won"
    assert outcome.outcome_value == 42000.0
    assert outcome.outcome_success is True

    link = db.query(WorkItemSourceLink).filter_by(source_record_id="006IMPORT000001").first()
    assert link.work_item_id == acme_item.id

    history = db.query(WorkItemOutcomeEvent).filter_by(work_item_id=acme_item.id).all()
    assert len(history) == 1

    # Re-running the import with the SAME data must not duplicate anything.
    result_again = asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    assert result_again["created"] == 0
    assert result_again["updated"] == 2
    assert db.query(WorkItem).filter_by(workspace_id="WS-IMPORT").count() == 2
    assert db.query(WorkAccount).filter_by(workspace_id="WS-IMPORT").count() == 2
    # Nothing about the outcome changed, so no new history row either.
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=acme_item.id).count() == 1


def test_import_records_history_when_a_reimport_finds_a_real_change(monkeypatch):
    db = _session()
    connection = _make_connection(db)

    record_v1 = {
        "Id": "006CHANGE001", "Name": "Changing Deal", "AccountId": "001C",
        "Account": {"Name": "Change Co"}, "StageName": "Proposal", "IsClosed": False,
        "IsWon": False, "Amount": 10000.0, "CloseDate": None,
        "OwnerId": "005Z", "LastModifiedDate": "2026-01-01T00:00:00.000+0000",
    }
    record_v2 = {**record_v1, "StageName": "Closed Won", "IsClosed": True, "IsWon": True, "CloseDate": "2026-02-01"}

    call_count = {"n": 0}

    async def fake_query_all(_item, _query, db=None, max_records=50_000):
        call_count["n"] += 1
        return [record_v1 if call_count["n"] == 1 else record_v2], None

    monkeypatch.setattr("api.routes_connections._salesforce_query_all", fake_query_all)

    asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    work_item = db.query(WorkItem).filter_by(workspace_id="WS-IMPORT").first()
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).count() == 1

    asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=work_item.id).first()
    assert outcome.outcome_status == "Closed Won"
    assert db.query(WorkItemOutcomeEvent).filter_by(work_item_id=work_item.id).count() == 2


def test_import_rejects_invalid_object_type():
    db = _session()
    connection = _make_connection(db)
    with pytest.raises(Exception):
        asyncio.run(import_work_items(connection.id, object_type="NotARealType", db=db))
