"""
Salesforce access tokens are short-lived (~2 hours) and nothing previously
refreshed them -- every connection went silently stale until someone
manually re-authorized it in the browser (found and fixed live in
production on 2026-08-09). These tests cover the fix: _salesforce_get
retries once via the stored refresh_token on a 401, and sync_outcomes only
advances last_success_at on an actual success (it previously did so
unconditionally, which made a fully-failed sync look healthy).
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
from api.routes_connections import _encrypt, _decrypt, _salesforce_get, sync_outcomes


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _make_connection(db, workspace_id="WS-REFRESH"):
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        platform="salesforce",
        display_name="Salesforce (refresh test)",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        access_token_encrypted=_encrypt("expired-access-token"),
        refresh_token_encrypted=_encrypt("valid-refresh-token"),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


class _FakeAsyncClient:
    """Records every request and returns the next canned response in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.requests.append(("GET", url, headers))
        return self._responses.pop(0)

    async def post(self, url, data=None):
        self.requests.append(("POST", url, data))
        return self._responses.pop(0)


def test_salesforce_get_refreshes_expired_token_and_retries_once(monkeypatch):
    db = _session()
    connection = _make_connection(db)

    responses = [
        httpx.Response(401, json={"error": "INVALID_SESSION_ID"}),
        httpx.Response(200, json={
            "access_token": "fresh-access-token",
            "refresh_token": "rotated-refresh-token",
        }),
        httpx.Response(200, json={"sobjects": []}),
    ]
    fake_client = _FakeAsyncClient(responses)
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "fake-client-id")

    result = asyncio.run(_salesforce_get(connection, "sobjects", db=db))

    assert result == {"sobjects": []}
    # First GET (401) -> POST refresh -> retried GET, in that order.
    assert [r[0] for r in fake_client.requests] == ["GET", "POST", "GET"]
    assert _decrypt(connection.access_token_encrypted) == "fresh-access-token"
    # Rotation is enabled on the Connected App -- the new refresh_token must
    # be stored too, or the *next* refresh would fail with the old one.
    assert _decrypt(connection.refresh_token_encrypted) == "rotated-refresh-token"


def test_salesforce_get_without_db_does_not_attempt_refresh(monkeypatch):
    """Callers that don't pass db (most existing call sites) keep the old
    behavior exactly -- fail on 401, no surprise retry/side effect."""
    db = _session()
    connection = _make_connection(db)
    fake_client = _FakeAsyncClient([httpx.Response(401, json={})])
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: fake_client)

    with pytest.raises(Exception):
        asyncio.run(_salesforce_get(connection, "sobjects"))
    assert len(fake_client.requests) == 1


def test_sync_outcomes_does_not_advance_last_success_at_on_failure(monkeypatch):
    db = _session()
    connection = _make_connection(db)
    work_item = WorkItem(
        external_id="PROJECT-REFRESH-TEST",
        name="Refresh Test Opportunity",
        context_type="opportunity",
        context_template="salesforce_opportunity",
        source_platform="Salesforce",
        source_record_type="Opportunity",
        source_record_id="006TESTREFRESH0001",
        workspace_id=connection.workspace_id,
    )
    db.add(work_item)
    db.commit()

    async def always_fails(_item, _query, db=None):
        return [], "Salesforce metadata request failed (401)"

    monkeypatch.setattr("api.routes_connections._salesforce_try_query", always_fails)

    assert connection.last_success_at is None
    result = asyncio.run(sync_outcomes(connection.id, db=db))

    assert result["errors"]
    assert connection.last_success_at is None, (
        "last_success_at must not advance when every batch failed -- doing "
        "so previously hid a fully-broken connection behind a healthy-"
        "looking timestamp."
    )
