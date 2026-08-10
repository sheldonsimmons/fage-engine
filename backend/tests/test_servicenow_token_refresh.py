"""
Mirrors test_salesforce_token_refresh.py: ServiceNow access tokens are
also short-lived and, before this fix, nothing refreshed them --
_servicenow_table_get now retries once via the stored refresh_token on a
401, same shape as _salesforce_get, and sync_outcomes only advances
last_success_at on an actual success.
"""
import asyncio
import os

os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import IntegrationConnection, WorkItem
from api.routes_connections import _encrypt, _decrypt, _servicenow_table_get, sync_outcomes


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_connection(db, workspace_id="WS-SN-REFRESH"):
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        platform="servicenow",
        display_name="ServiceNow (refresh test)",
        status="connected",
        instance_url="https://acmedev.service-now.com",
        access_token_encrypted=_encrypt("expired-access-token"),
        refresh_token_encrypted=_encrypt("valid-refresh-token"),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.requests.append(("GET", url, params, headers))
        return self._responses.pop(0)

    async def post(self, url, data=None, headers=None):
        self.requests.append(("POST", url, data, headers))
        return self._responses.pop(0)


def test_servicenow_table_get_refreshes_expired_token_and_retries_once(monkeypatch):
    db = _session()
    connection = _make_connection(db)

    responses = [
        httpx.Response(401, json={"error": "Invalid token"}),
        httpx.Response(200, json={
            "access_token": "fresh-access-token",
            "refresh_token": "rotated-refresh-token",
        }),
        httpx.Response(200, json={"result": []}),
    ]
    fake_client = _FakeAsyncClient(responses)
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)
    monkeypatch.setenv("SERVICENOW_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("SERVICENOW_CLIENT_SECRET", "fake-client-secret")

    result = asyncio.run(_servicenow_table_get(
        connection, "incident", query="", fields="sys_id", db=db,
    ))

    assert result == []
    assert [r[0] for r in fake_client.requests] == ["GET", "POST", "GET"]
    assert _decrypt(connection.access_token_encrypted) == "fresh-access-token"
    assert _decrypt(connection.refresh_token_encrypted) == "rotated-refresh-token"


def test_servicenow_table_get_without_db_does_not_attempt_refresh(monkeypatch):
    db = _session()
    connection = _make_connection(db)
    fake_client = _FakeAsyncClient([httpx.Response(401, json={})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)

    with pytest.raises(Exception):
        asyncio.run(_servicenow_table_get(connection, "incident", query="", fields="sys_id"))
    assert len(fake_client.requests) == 1


def test_sync_outcomes_does_not_advance_last_success_at_on_failure_servicenow(monkeypatch):
    db = _session()
    connection = _make_connection(db)
    work_item = WorkItem(
        external_id="SN-INCIDENT-TESTREFRESH0001",
        name="Refresh Test Incident",
        context_type="case",
        context_template="servicenow_case",
        source_platform="ServiceNow",
        source_record_type="incident",
        source_record_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        workspace_id=connection.workspace_id,
    )
    db.add(work_item)
    db.commit()

    async def always_fails(_item, _table, *, sys_ids, fields, db=None):
        return [], "ServiceNow metadata request failed (401)"

    monkeypatch.setattr("api.routes_connections._servicenow_try_query", always_fails)

    assert connection.last_success_at is None
    result = asyncio.run(sync_outcomes(connection.id, db=db))

    assert result["errors"]
    assert connection.last_success_at is None, (
        "last_success_at must not advance when every batch failed."
    )
