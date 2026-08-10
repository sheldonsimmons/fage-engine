"""
resolve_account_through_merge -- new activity that references an account's
old (now-merged-away) external_id must land on the surviving account, not
silently start a second, invisible pocket of data on the archived one.

Account merging (routes_work_items.py::merge_work_accounts) only repoints
WorkItems that exist at merge time. It can't do anything about a live-write
path that later looks the old account back up by external_id -- which
genuinely happens: two connected Salesforce orgs can each have their own
real "GenePoint" account, merged together in CostPilot, but Agentforce
calls (or the universal /api/route ingestion path) made from either org
still reference their own org's original external_id.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import WorkAccount, WorkItem
from api.routes_work_items import (
    resolve_account_through_merge,
    merge_work_accounts,
    MergeAccountsIn,
)
from api.routes_agentforce import _resolve_customer, AgentforceGovernRequest


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_resolve_account_through_merge_follows_redirect():
    db = _session()
    target = WorkAccount(external_id="CANON", name="GenePoint", workspace_id="WS-1")
    source = WorkAccount(external_id="OLD", name="GenePoint", workspace_id="WS-1")
    db.add_all([target, source]); db.flush()
    source.status = "merged"
    source.merged_into_work_account_id = target.id
    db.commit()

    resolved = resolve_account_through_merge(db, source)
    assert resolved.id == target.id


def test_resolve_account_through_merge_is_noop_for_active_account():
    db = _session()
    account = WorkAccount(external_id="ACTIVE", name="Acme", workspace_id="WS-1")
    db.add(account); db.commit()
    assert resolve_account_through_merge(db, account).id == account.id


def test_resolve_account_through_merge_handles_none():
    db = _session()
    assert resolve_account_through_merge(db, None) is None


def test_agentforce_customer_lookup_redirects_through_a_merged_account():
    # Reproduces the exact bug: a real merge happened (two orgs' GenePoint
    # accounts consolidated), then a new Agentforce call on the account
    # record used the OLD (now-merged-away) external_id -- it must attach
    # to the surviving account, not the archived one.
    db = _session()
    canonical = WorkAccount(external_id="001CANON", name="GenePoint", workspace_id="WS-1")
    archived = WorkAccount(external_id="001OLD", name="GenePoint", workspace_id="WS-1")
    db.add_all([canonical, archived]); db.flush()
    archived.status = "merged"
    archived.merged_into_work_account_id = canonical.id
    db.commit()

    body = AgentforceGovernRequest(
        record_id="001OLD-TASK-1",
        task_description="Summarize recent activity",
        customer_external_id="001OLD",
        customer_name="GenePoint",
    )
    resolved = _resolve_customer(db, "WS-1", body)
    assert resolved.id == canonical.id
    assert resolved.status == "active"


def test_merge_is_safe_to_rerun_after_new_activity_lands_on_the_old_side():
    # Belt-and-suspenders: even if some other path ever bypasses the
    # redirect, re-running the merge tool sweeps up anything that slipped
    # through -- it's idempotent by design (see merge_work_accounts).
    db = _session()
    canonical = WorkAccount(external_id="001CANON2", name="GenePoint", workspace_id="WS-1")
    archived = WorkAccount(external_id="001OLD2", name="GenePoint", workspace_id="WS-1")
    db.add_all([canonical, archived]); db.flush()
    archived.status = "merged"
    archived.merged_into_work_account_id = canonical.id
    db.flush()

    stray_item = WorkItem(external_id="STRAY-1", name="Stray", account_id=archived.id, workspace_id="WS-1")
    db.add(stray_item); db.commit()

    merge_work_accounts("001OLD2", MergeAccountsIn(target_identifier="001CANON2"), db)
    db.refresh(stray_item)
    assert stray_item.account_id == canonical.id
