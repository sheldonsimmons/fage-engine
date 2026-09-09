"""
tests/test_work_items_workspace_scoping.py — security architecture
assessment, Finding 8: resolve_work_item()/resolve_work_account() (the
shared resolver behind ~15 routes -- read, update, archive, merge, and
agent/user assignment) used to take a bare identifier with no workspace
check at all. A caller who knew/guessed another tenant's work_item_id or
external_id could read its full financial profile, edit it, reassign its
agents/users, or archive it.

workspace_id is OPTIONAL, matching Findings 5/6's pattern: defaults to
None so nothing already calling these resolvers without it breaks; when
supplied, a cross-tenant match resolves to None (treated as not-found),
never leaking which identifier belongs to a different tenant.

Note: workspace_id was deliberately placed AFTER db (not before) in every
retrofitted route signature in routes_work_items.py -- test_account_merge.py
and test_account_merge_redirect.py call merge_work_accounts()/
restore_merged_work_account() with positional arguments including a bare
`db`, and inserting a new parameter before it would have silently rebound
`db` to the wrong argument. Caught and fixed during this same change --
see that regression in the commit history for this file.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import WorkAccount, WorkItem
from api.routes_work_items import resolve_work_item, resolve_work_account
from main import app


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal()


def _work_item(db, external_id="WI-1", workspace_id="WS-A", name="Acme Renewal"):
    wi = WorkItem(external_id=external_id, name=name, workspace_id=workspace_id, context_type="opportunity")
    db.add(wi)
    db.commit()
    db.refresh(wi)
    return wi


def _work_account(db, external_id="ACC-1", workspace_id="WS-A", name="Acme Corp"):
    a = WorkAccount(external_id=external_id, name=name, workspace_id=workspace_id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ── resolve_work_item() directly ────────────────────────────────────────────

def test_resolve_work_item_without_workspace_id_still_resolves():
    db = _session()
    wi = _work_item(db, workspace_id="WS-A")
    assert resolve_work_item(db, "WI-1") is not None


def test_resolve_work_item_matching_workspace_resolves():
    db = _session()
    _work_item(db, workspace_id="WS-A")
    result = resolve_work_item(db, "WI-1", workspace_id="WS-A")
    assert result is not None


def test_resolve_work_item_cross_tenant_returns_none():
    db = _session()
    _work_item(db, workspace_id="WS-A")
    result = resolve_work_item(db, "WI-1", workspace_id="WS-B")
    assert result is None


def test_resolve_work_item_by_numeric_id_cross_tenant_returns_none():
    db = _session()
    wi = _work_item(db, workspace_id="WS-A")
    result = resolve_work_item(db, str(wi.id), workspace_id="WS-B")
    assert result is None


def test_resolve_work_account_cross_tenant_returns_none():
    db = _session()
    _work_account(db, workspace_id="WS-A")
    result = resolve_work_account(db, "ACC-1", workspace_id="WS-B")
    assert result is None


def test_resolve_work_account_matching_workspace_resolves():
    db = _session()
    _work_account(db, workspace_id="WS-A")
    result = resolve_work_account(db, "ACC-1", workspace_id="WS-A")
    assert result is not None


# ── representative endpoints, through the real HTTP layer ──────────────────

def test_get_work_item_endpoint_cross_tenant_404s():
    client, db = _client()
    _work_item(db, workspace_id="WS-A")

    ok = client.get("/api/work-items/WI-1", params={"workspace_id": "WS-A"})
    assert ok.status_code == 200

    denied = client.get("/api/work-items/WI-1", params={"workspace_id": "WS-B"})
    assert denied.status_code == 404

    unscoped = client.get("/api/work-items/WI-1")
    assert unscoped.status_code == 200  # today's default behavior, unchanged


def test_update_work_item_cross_tenant_denied_and_unmutated():
    client, db = _client()
    _work_item(db, workspace_id="WS-A", name="Original Name")

    resp = client.patch(
        "/api/work-items/WI-1", params={"workspace_id": "WS-B"}, json={"name": "Hijacked"},
    )
    assert resp.status_code == 404

    still = db.query(WorkItem).filter_by(external_id="WI-1").first()
    assert still.name == "Original Name"


def test_archive_work_item_cross_tenant_denied():
    client, db = _client()
    wi = _work_item(db, workspace_id="WS-A")

    resp = client.post(f"/api/work-items/{wi.external_id}/archive", params={"workspace_id": "WS-B"})
    assert resp.status_code == 404

    db.refresh(wi)
    assert wi.status != "archived"
