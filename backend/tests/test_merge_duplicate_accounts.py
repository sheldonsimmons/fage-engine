"""
database/merge_duplicate_accounts.py -- finds exact-name duplicate
WorkAccount rows within a workspace and merges each group down to the
account with the most real activity (TokenTransaction count, spend as
tiebreaker), using the merge_work_accounts primitive.
"""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import TokenTransaction, WorkAccount, WorkItem
from database.merge_duplicate_accounts import apply_merges, find_duplicate_groups


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _account_with_activity(db, external_id, name, workspace_id, tx_count):
    account = WorkAccount(external_id=external_id, name=name, workspace_id=workspace_id)
    db.add(account)
    db.flush()
    item = WorkItem(external_id=f"WI-{external_id}", name=name, account_id=account.id, workspace_id=workspace_id)
    db.add(item)
    db.flush()
    for i in range(tx_count):
        db.add(TokenTransaction(
            department=f"{workspace_id}:Sales", workspace_id=workspace_id, source_platform="Salesforce",
            work_item_id=item.id, model_tier="Scout", model_name="claude-3-5-haiku",
            input_tokens=10, output_tokens=5, cost_usd=1.0, timestamp=datetime.utcnow(),
        ))
    db.commit()
    return account


def test_find_duplicate_groups_matches_exact_name_only():
    db = _session()
    _account_with_activity(db, "A1", "Dickenson plc", "WS-1", 0)
    _account_with_activity(db, "A2", "Dickenson plc", "WS-1", 0)
    _account_with_activity(db, "A3", "GenePoint", "WS-1", 0)

    groups = find_duplicate_groups(db, "WS-1")
    assert len(groups) == 1
    assert {a.external_id for a in groups[0]} == {"A1", "A2"}


def test_apply_merges_keeps_the_more_active_account():
    db = _session()
    _account_with_activity(db, "QUIET", "Dickenson plc", "WS-1", 1)
    _account_with_activity(db, "ACTIVE", "Dickenson plc", "WS-1", 5)

    apply_merges(db, "WS-1", dry_run=False)

    quiet = db.query(WorkAccount).filter_by(external_id="QUIET").first()
    active = db.query(WorkAccount).filter_by(external_id="ACTIVE").first()
    assert quiet.status == "merged"
    assert quiet.merged_into_work_account_id == active.id
    assert active.status == "active"


def test_apply_merges_dry_run_makes_no_changes():
    db = _session()
    _account_with_activity(db, "QUIET", "Dickenson plc", "WS-1", 1)
    _account_with_activity(db, "ACTIVE", "Dickenson plc", "WS-1", 5)

    apply_merges(db, "WS-1", dry_run=True)

    quiet = db.query(WorkAccount).filter_by(external_id="QUIET").first()
    active = db.query(WorkAccount).filter_by(external_id="ACTIVE").first()
    assert quiet.status == "active"
    assert active.status == "active"


def test_apply_merges_no_op_when_no_duplicates():
    db = _session()
    _account_with_activity(db, "A1", "Dickenson plc", "WS-1", 0)
    _account_with_activity(db, "A2", "GenePoint", "WS-1", 0)

    apply_merges(db, "WS-1", dry_run=False)

    a1 = db.query(WorkAccount).filter_by(external_id="A1").first()
    a2 = db.query(WorkAccount).filter_by(external_id="A2").first()
    assert a1.status == "active"
    assert a2.status == "active"
