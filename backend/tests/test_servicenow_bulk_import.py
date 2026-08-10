"""
Mirrors test_salesforce_bulk_import.py, against ServiceNow's incident
table -- this is the actual proof that the connector abstraction
(_import_work_items in api/routes_connections.py) generalizes to a second
platform with a genuinely different pagination model (sysparm_offset, not
a nextRecordsUrl cursor) and a genuinely different record shape
(sysparm_display_value=all's {"display_value", "value"} wrapper, not
SOQL's flat fields).
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
from api.routes_connections import _encrypt, _servicenow_query_all, import_work_items
from core.outcome_adapters.servicenow_incident import map_servicenow_incident_to_work_item_fields


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_connection(db, workspace_id="WS-SN-IMPORT"):
    connection = IntegrationConnection(
        workspace_id=workspace_id, platform="servicenow", display_name="ServiceNow",
        status="connected", instance_url="https://acmedev.service-now.com",
        access_token_encrypted=_encrypt("fake-token"),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def _field(display, value=None):
    return {"display_value": display, "value": value if value is not None else display}


def _fake_incident(sys_id, number, short_description, company_sys_id, company_name, state="In Progress"):
    return {
        "sys_id": _field(sys_id),
        "number": _field(number),
        "short_description": _field(short_description),
        "state": _field(state, str(hash(state) % 100)),
        "close_code": _field(""),
        "resolved_at": _field(""),
        "closed_at": _field(""),
        "priority": _field("3 - Moderate"),
        "assigned_to": _field("Jamie Lee"),
        "company": _field(company_name, company_sys_id),
        "sys_updated_on": _field("2026-01-01 00:00:00"),
    }


def test_maps_incident_relationship_query_fields():
    record = _fake_incident("INC001SYSID000001", "INC0010001", "Login failure", "ACME001SYSID", "Acme Corp")
    fields = map_servicenow_incident_to_work_item_fields(record)
    assert fields["name"] == "Incident INC0010001: Login failure"
    assert fields["source_record_id"] == "INC001SYSID000001"
    # The real sys_id, not the display label, is the identity.
    assert fields["account_external_id"] == "ACME001SYSID"
    assert fields["account_name"] == "Acme Corp"


def test_query_all_pages_via_offset_not_cursor(monkeypatch):
    """ServiceNow's Table API has no cursor field -- pagination must stop
    only when a page comes back shorter than the requested page_size."""
    db = _session()
    connection = _make_connection(db)

    calls = []

    async def fake_table_get(_item, _table, *, query, fields, limit, offset=0, db=None, display_value="true"):
        calls.append(offset)
        if offset == 0:
            return [_fake_incident(f"SYSID{i}", f"INC00{i}", "desc", "ACME", "Acme") for i in range(2)]
        return [_fake_incident("SYSID2", "INC002", "desc", "ACME", "Acme")]

    monkeypatch.setattr("api.routes_connections._servicenow_table_get", fake_table_get)

    records, error = asyncio.run(_servicenow_query_all(
        connection, "incident", query="", fields="sys_id", db=db, page_size=2,
    ))
    assert error is None
    assert len(records) == 3
    assert calls == [0, 2]


def test_import_creates_work_items_accounts_and_outcomes(monkeypatch):
    db = _session()
    connection = _make_connection(db)

    fake_records = [
        _fake_incident("INCIMPORT001", "INC0001", "Cannot log in", "ACME001", "Acme Corp", state="Resolved"),
        _fake_incident("INCIMPORT002", "INC0002", "Slow VPN", "BETA001", "Beta Inc", state="New"),
    ]

    async def fake_query_all(_item, _table, *, query, fields, db=None, page_size=500, max_records=50_000):
        return fake_records, None

    monkeypatch.setattr("api.routes_connections._servicenow_query_all", fake_query_all)

    result = asyncio.run(import_work_items(connection.id, object_type="incident", db=db))
    assert result["discovered"] == 2
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []

    accounts = db.query(WorkAccount).filter_by(workspace_id="WS-SN-IMPORT").all()
    assert {a.name for a in accounts} == {"Acme Corp", "Beta Inc"}

    work_items = db.query(WorkItem).filter_by(workspace_id="WS-SN-IMPORT").all()
    assert len(work_items) == 2
    resolved_item = next(w for w in work_items if w.name == "Incident INC0001: Cannot log in")
    assert resolved_item.context_type == "case"
    assert resolved_item.source_record_id == "INCIMPORT001"
    assert resolved_item.external_id == "SN-INCIDENT-INCIMPORT001"

    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=resolved_item.id).first()
    assert outcome.outcome_status == "Resolved"
    assert outcome.is_closed is True
    # No dollar-value/success signal on an incident -- honest None, not a guess.
    assert outcome.outcome_value is None
    assert outcome.outcome_success is None

    link = db.query(WorkItemSourceLink).filter_by(source_record_id="INCIMPORT001").first()
    assert link.work_item_id == resolved_item.id
    assert link.source_platform == "ServiceNow"

    # Re-running with the SAME data must not duplicate anything.
    result_again = asyncio.run(import_work_items(connection.id, object_type="incident", db=db))
    assert result_again["created"] == 0
    assert result_again["updated"] == 2
    assert db.query(WorkItem).filter_by(workspace_id="WS-SN-IMPORT").count() == 2


def test_dry_run_reports_counts_without_writing_anything(monkeypatch):
    db = _session()
    connection = _make_connection(db)
    fake_records = [_fake_incident("INCPREVIEW001", "INC9001", "Preview ticket", "PREV001", "Preview Co")]

    async def fake_query_all(_item, _table, *, query, fields, db=None, page_size=500, max_records=50_000):
        return fake_records, None

    monkeypatch.setattr("api.routes_connections._servicenow_query_all", fake_query_all)

    result = asyncio.run(import_work_items(connection.id, object_type="incident", dry_run=True, db=db))
    assert result["created"] == 1
    assert result["dry_run"] is True
    assert db.query(WorkItem).filter_by(workspace_id="WS-SN-IMPORT").count() == 0

    result_real = asyncio.run(import_work_items(connection.id, object_type="incident", db=db))
    assert result_real["created"] == 1
    assert db.query(WorkItem).filter_by(workspace_id="WS-SN-IMPORT").count() == 1


def test_import_self_heals_multiple_records_sharing_one_stale_work_item(monkeypatch):
    """Same corruption pattern proven for Salesforce in
    test_salesforce_bulk_import.py -- two different Incidents both
    pre-linked to one stale shared WorkItem must end up on distinct ones."""
    db = _session()
    connection = _make_connection(db)

    account = WorkAccount(external_id="ACMESHARED", name="Shared Co", workspace_id="WS-SN-IMPORT")
    db.add(account)
    db.flush()
    stale_shared_item = WorkItem(
        external_id="ACCOUNT-LEVEL-FALLBACK", name="Shared Co", account_id=account.id,
        context_type="account", source_platform="ServiceNow", source_record_type="company",
        source_record_id="ACMESHARED", workspace_id="WS-SN-IMPORT",
    )
    db.add(stale_shared_item)
    db.flush()
    db.add(WorkItemSourceLink(
        work_item_id=stale_shared_item.id, workspace_id="WS-SN-IMPORT", source_platform="ServiceNow",
        source_record_type="incident", source_record_id="INCSTALE001",
    ))
    db.add(WorkItemSourceLink(
        work_item_id=stale_shared_item.id, workspace_id="WS-SN-IMPORT", source_platform="ServiceNow",
        source_record_type="incident", source_record_id="INCSTALE002",
    ))
    db.commit()

    fake_records = [
        _fake_incident("INCSTALE001", "INC7001", "Ticket one", "ACMESHARED", "Shared Co"),
        _fake_incident("INCSTALE002", "INC7002", "Ticket two", "ACMESHARED", "Shared Co"),
    ]

    async def fake_query_all(_item, _table, *, query, fields, db=None, page_size=500, max_records=50_000):
        return fake_records, None

    monkeypatch.setattr("api.routes_connections._servicenow_query_all", fake_query_all)

    result = asyncio.run(import_work_items(connection.id, object_type="incident", db=db))
    assert result["errors"] == []
    assert result["healed"] == 1

    link1 = db.query(WorkItemSourceLink).filter_by(source_record_id="INCSTALE001").first()
    link2 = db.query(WorkItemSourceLink).filter_by(source_record_id="INCSTALE002").first()
    assert link1.work_item_id != link2.work_item_id


def test_import_rejects_object_type_not_valid_for_platform():
    db = _session()
    connection = _make_connection(db)
    with pytest.raises(Exception):
        asyncio.run(import_work_items(connection.id, object_type="Opportunity", db=db))


def test_salesforce_and_servicenow_imports_share_the_same_core_function(monkeypatch):
    """The actual proof the connector abstraction holds: both platforms'
    import_work_items branches must call the SAME shared _import_work_items
    core (not platform-specific copies of the create/update/heal logic),
    just with different fetch_records/mapping/identity parameters."""
    db = _session()
    sf_connection = IntegrationConnection(
        workspace_id="WS-SHARED-CORE", platform="salesforce", display_name="Salesforce",
        status="connected", instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("fake-token"),
    )
    sn_connection = _make_connection(db, workspace_id="WS-SHARED-CORE-SN")
    db.add(sf_connection)
    db.commit()
    db.refresh(sf_connection)

    calls = []
    real_import_work_items = None
    import api.routes_connections as routes_connections
    real_import_work_items = routes_connections._import_work_items

    async def spy(*args, **kwargs):
        calls.append(kwargs.get("source_platform"))
        return await real_import_work_items(*args, **kwargs)

    monkeypatch.setattr("api.routes_connections._import_work_items", spy)

    async def fake_salesforce_query_all(_item, _query, db=None, max_records=50_000):
        return [], None

    async def fake_servicenow_query_all(_item, _table, *, query, fields, db=None, page_size=500, max_records=50_000):
        return [], None

    monkeypatch.setattr("api.routes_connections._salesforce_query_all", fake_salesforce_query_all)
    monkeypatch.setattr("api.routes_connections._servicenow_query_all", fake_servicenow_query_all)

    asyncio.run(import_work_items(sf_connection.id, object_type="Opportunity", db=db))
    asyncio.run(import_work_items(sn_connection.id, object_type="incident", db=db))

    assert calls == ["Salesforce", "ServiceNow"]
