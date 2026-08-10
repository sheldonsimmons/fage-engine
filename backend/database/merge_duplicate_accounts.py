"""
database/merge_duplicate_accounts.py — finds WorkAccount rows in a workspace
that share an exact name (e.g. two Salesforce dev orgs both seeded with
Salesforce's own canned demo dataset -- "Dickenson plc," "GenePoint," etc.
-- connected to the same CostPilot workspace) and merges each duplicate
group down to one account, using the account-merge primitive in
api/routes_work_items.py::merge_work_accounts.

The account with more real activity in it (most linked WorkItems, spend as
tiebreaker) is kept; the rest are merged into it. Merging only repoints
WorkItem.account_id, so every downstream table (TokenTransaction,
WorkItemOutcome, source links, ...) automatically stays attached to its
WorkItem and comes along for free -- see merge_work_accounts for why that's
safe.

Exact name match only -- no fuzzy/similarity matching. Safe to re-run:
already-merged accounts (status="merged") are excluded from grouping.

Usage:
    cd backend
    python database/merge_duplicate_accounts.py --workspace-id 4BE43240A6674314 [--dry-run]
"""

import argparse
from collections import defaultdict

from sqlalchemy import func

from database.db import SessionLocal
from database.migrate import create_tables, run_migrations
from database.models import TokenTransaction, WorkAccount, WorkItem


def _activity_score(db, account_id: int) -> tuple:
    row = (
        db.query(
            func.count(func.distinct(TokenTransaction.id)),
            func.coalesce(func.sum(TokenTransaction.cost_usd), 0.0),
        )
        .join(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .filter(WorkItem.account_id == account_id)
        .first()
    )
    tx_count, spend = row
    return (tx_count or 0, float(spend or 0.0))


def find_duplicate_groups(db, workspace_id: str) -> list:
    accounts = (
        db.query(WorkAccount)
        .filter(WorkAccount.workspace_id == workspace_id)
        .filter(WorkAccount.status != "merged")
        .all()
    )
    by_name = defaultdict(list)
    for account in accounts:
        by_name[account.name].append(account)
    return [group for group in by_name.values() if len(group) > 1]


def plan_merges(db, workspace_id: str) -> list:
    """Returns a list of (keeper, [accounts to merge into keeper]) tuples."""
    plans = []
    for group in find_duplicate_groups(db, workspace_id):
        scored = sorted(
            group, key=lambda a: _activity_score(db, a.id), reverse=True
        )
        keeper, rest = scored[0], scored[1:]
        plans.append((keeper, rest))
    return plans


def apply_merges(db, workspace_id: str, dry_run: bool = False):
    from api.routes_work_items import merge_work_accounts, MergeAccountsIn

    plans = plan_merges(db, workspace_id)
    if not plans:
        print(f"No duplicate account names found in workspace {workspace_id}.")
        return

    for keeper, rest in plans:
        keeper_score = _activity_score(db, keeper.id)
        print(f"\n{keeper.name!r}: keeping {keeper.external_id} (activity={keeper_score})")
        for account in rest:
            score = _activity_score(db, account.id)
            action = "Would merge" if dry_run else "Merging"
            print(f"  {action} {account.external_id} (activity={score}) -> {keeper.external_id}")
            if not dry_run:
                merge_work_accounts(
                    identifier=account.external_id,
                    body=MergeAccountsIn(target_identifier=keeper.external_id),
                    db=db,
                )

    if dry_run:
        db.rollback()
        print("\nDry run only -- no changes committed.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    create_tables()
    run_migrations()
    db = SessionLocal()
    try:
        apply_merges(db, args.workspace_id, dry_run=args.dry_run)
    finally:
        db.close()
