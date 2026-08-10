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


def test_reimport_refreshes_an_existing_account_name(monkeypatch):
    # An account rename in Salesforce previously never reached CostPilot
    # once the WorkAccount row already existed -- the import path only
    # ever set `name` on creation, never on a later sync of the same
    # account_external_id. account_name is available on every
    # Opportunity/Case record via the joined Account.Name field, so a
    # re-import is exactly where this should get picked up.
    db = _session()
    connection = _make_connection(db)

    record_v1 = {
        "Id": "006RENAME001", "Name": "Renamed Co Deal", "AccountId": "001RENAME",
        "Account": {"Name": "GenePoint"}, "StageName": "Proposal", "IsClosed": False,
        "IsWon": False, "Amount": 5000.0, "CloseDate": None,
        "OwnerId": "005R", "LastModifiedDate": "2026-01-01T00:00:00.000+0000",
    }
    record_v2 = {**record_v1, "Account": {"Name": "GenePoint Renewables"}}

    call_count = {"n": 0}

    async def fake_query_all(_item, _query, db=None, max_records=50_000):
        call_count["n"] += 1
        return [record_v1 if call_count["n"] == 1 else record_v2], None

    monkeypatch.setattr("api.routes_connections._salesforce_query_all", fake_query_all)

    asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    account = db.query(WorkAccount).filter_by(workspace_id="WS-IMPORT", external_id="001RENAME").first()
    assert account.name == "GenePoint"

    # Simulate this account having gone through CostPilot's own merge
    # feature -- sync must never clobber merge state, only the name.
    account.status = "merged"
    account.merged_into_work_account_id = 999
    db.commit()

    asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    db.refresh(account)
    assert account.name == "GenePoint Renewables"
    assert account.status == "merged"
    assert account.merged_into_work_account_id == 999
    # Still exactly one account row -- a rename must not create a duplicate.
    assert db.query(WorkAccount).filter_by(workspace_id="WS-IMPORT").count() == 1


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


def test_import_self_heals_multiple_records_sharing_one_stale_work_item(monkeypatch):
    """
    Found live in production: before bulk import existed, Agentforce's
    reactive fallback could point several genuinely different
    Opportunities' WorkItemSourceLink rows at the SAME account-level
    WorkItem (it picked "the account's oldest work item" whenever no link
    existed yet). Bulk import must not reuse that shared link for more
    than one record -- doing so either silently merges unrelated deals or
    crashes on WorkItemOutcome's one-row-per-work-item constraint, which
    is exactly what happened before this fix.
    """
    db = _session()
    connection = _make_connection(db)

    account = WorkAccount(external_id="001SHARED", name="Shared Co", workspace_id="WS-IMPORT")
    db.add(account)
    db.flush()
    stale_shared_item = WorkItem(
        external_id="ACCOUNT-LEVEL-FALLBACK", name="Shared Co", account_id=account.id,
        context_type="account", source_platform="Salesforce", source_record_type="Account",
        source_record_id="001SHARED", workspace_id="WS-IMPORT",
    )
    db.add(stale_shared_item)
    db.flush()
    # Two DIFFERENT real Opportunities both incorrectly linked to the same
    # fallback WorkItem -- the corruption pattern found live.
    db.add(WorkItemSourceLink(
        work_item_id=stale_shared_item.id, workspace_id="WS-IMPORT", source_platform="Salesforce",
        source_record_type="Opportunity", source_record_id="006STALE001",
    ))
    db.add(WorkItemSourceLink(
        work_item_id=stale_shared_item.id, workspace_id="WS-IMPORT", source_platform="Salesforce",
        source_record_type="Opportunity", source_record_id="006STALE002",
    ))
    db.commit()

    fake_records = [
        {
            "Id": "006STALE001", "Name": "Deal One", "AccountId": "001SHARED",
            "Account": {"Name": "Shared Co"}, "StageName": "Closed Won", "IsClosed": True,
            "IsWon": True, "Amount": 10000.0, "CloseDate": "2026-01-01",
            "OwnerId": "005X", "LastModifiedDate": "2026-01-01T00:00:00.000+0000",
        },
        {
            "Id": "006STALE002", "Name": "Deal Two", "AccountId": "001SHARED",
            "Account": {"Name": "Shared Co"}, "StageName": "Closed Lost", "IsClosed": True,
            "IsWon": False, "Amount": 20000.0, "CloseDate": "2026-01-02",
            "OwnerId": "005X", "LastModifiedDate": "2026-01-02T00:00:00.000+0000",
        },
    ]

    async def fake_query_all(_item, _query, db=None, max_records=50_000):
        return fake_records, None

    monkeypatch.setattr("api.routes_connections._salesforce_query_all", fake_query_all)

    result = asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))
    assert result["errors"] == []
    assert result["healed"] == 1  # the second record needed its own WorkItem

    link1 = db.query(WorkItemSourceLink).filter_by(source_record_id="006STALE001").first()
    link2 = db.query(WorkItemSourceLink).filter_by(source_record_id="006STALE002").first()
    assert link1.work_item_id != link2.work_item_id, "the two deals must end up on distinct WorkItems"

    outcome1 = db.query(WorkItemOutcome).filter_by(work_item_id=link1.work_item_id).first()
    outcome2 = db.query(WorkItemOutcome).filter_by(work_item_id=link2.work_item_id).first()
    assert outcome1.outcome_value == 10000.0
    assert outcome2.outcome_value == 20000.0
    assert outcome1.outcome_success is True
    assert outcome2.outcome_success is False


def test_import_rejects_invalid_object_type():
    db = _session()
    connection = _make_connection(db)
    with pytest.raises(Exception):
        asyncio.run(import_work_items(connection.id, object_type="NotARealType", db=db))
