"""
POST /api/work-items/accounts/{id}/merge and /restore-merge -- merges two
WorkAccount rows that represent the same real-world company but ended up
as separate rows (e.g. two connected Salesforce orgs both seeded with
Salesforce's own canned demo data). Unlike the WorkItem-level merge, this
only has to repoint WorkItem.account_id: TokenTransaction, WorkItemOutcome,
and everything else hang off WorkItem, not WorkAccount, so they come along
automatically without being touched directly.
"""
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem, WorkItemOutcome
from api.routes_work_items import (
    MergeAccountsIn,
    list_accounts,
    merge_work_accounts,
    restore_merged_work_account,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed_pair(db):
    keeper = WorkAccount(external_id="ACC-KEEP", name="Dickenson plc", workspace_id="WS-1")
    dupe = WorkAccount(external_id="ACC-DUPE", name="Dickenson plc", workspace_id="WS-1")
    db.add_all([keeper, dupe])
    db.flush()

    keeper_item = WorkItem(
        external_id="WI-KEEP", name="Keeper project", account_id=keeper.id, workspace_id="WS-1",
    )
    dupe_item = WorkItem(
        external_id="WI-DUPE", name="Dupe project", account_id=dupe.id, workspace_id="WS-1",
    )
    db.add_all([keeper_item, dupe_item])
    db.flush()

    db.add(WorkItemOutcome(
        work_item_id=dupe_item.id, workspace_id="WS-1", outcome_status="Closed Won",
        outcome_value=50000.0, outcome_success=True, is_closed=True,
        source_system="salesforce", source_object="Opportunity", external_id="006DUPE",
        last_synced_at=datetime.utcnow(),
    ))
    db.add(TokenTransaction(
        department="WS-1:Sales", workspace_id="WS-1", source_platform="Salesforce",
        work_item_id=dupe_item.id, model_tier="Scout", model_name="claude-3-5-haiku",
        input_tokens=100, output_tokens=50, cost_usd=0.05, timestamp=datetime.utcnow(),
    ))
    db.commit()
    return keeper, dupe, keeper_item, dupe_item


def test_merge_moves_work_items_and_their_data_to_target():
    db = _session()
    keeper, dupe, keeper_item, dupe_item = _seed_pair(db)

    result = merge_work_accounts("ACC-DUPE", MergeAccountsIn(target_identifier="ACC-KEEP"), db)

    assert result["merged"] == "ACC-DUPE"
    assert result["into"] == "ACC-KEEP"

    db.refresh(dupe_item)
    assert dupe_item.account_id == keeper.id

    db.refresh(dupe)
    assert dupe.status == "merged"
    assert dupe.merged_into_work_account_id == keeper.id

    # The outcome and transaction stayed attached to their WorkItem, which
    # simply now belongs to a different account.
    outcome = db.query(WorkItemOutcome).filter_by(work_item_id=dupe_item.id).first()
    tx = db.query(TokenTransaction).filter_by(work_item_id=dupe_item.id).first()
    assert outcome is not None
    assert tx is not None


def test_merged_account_excluded_from_list_accounts():
    db = _session()
    _seed_pair(db)
    merge_work_accounts("ACC-DUPE", MergeAccountsIn(target_identifier="ACC-KEEP"), db)

    accounts = list_accounts(workspace_id="WS-1", db=db)
    external_ids = {a["external_id"] for a in accounts}
    assert external_ids == {"ACC-KEEP"}


def test_merge_rejects_same_account():
    db = _session()
    _seed_pair(db)
    with pytest.raises(HTTPException) as exc:
        merge_work_accounts("ACC-KEEP", MergeAccountsIn(target_identifier="ACC-KEEP"), db)
    assert exc.value.status_code == 400


def test_merge_rejects_cross_workspace():
    db = _session()
    keeper, dupe, _, _ = _seed_pair(db)
    dupe.workspace_id = "WS-2"
    db.commit()
    with pytest.raises(HTTPException) as exc:
        merge_work_accounts("ACC-DUPE", MergeAccountsIn(target_identifier="ACC-KEEP"), db)
    assert exc.value.status_code == 409


def test_restore_merge_reactivates_source_account():
    db = _session()
    _seed_pair(db)
    merge_work_accounts("ACC-DUPE", MergeAccountsIn(target_identifier="ACC-KEEP"), db)

    restored = restore_merged_work_account("ACC-DUPE", db)
    assert restored["status"] == "active"
    assert restored["merged_into_work_account_id"] is None


def test_restore_merge_rejects_already_active_account():
    db = _session()
    _seed_pair(db)
    with pytest.raises(HTTPException) as exc:
        restore_merged_work_account("ACC-KEEP", db)
    assert exc.value.status_code == 409
