"""
database/backfill_work_accounts.py — one-time backfill of WorkItem.account_id
for rows created via /api/route before the UniversalWorkAccount schema field
existed (see api/routes_router.py _resolve_work_item). Those rows encode the
account name as a "{account} — {name}" prefix on WorkItem.name because there
was nowhere else to send it — this script parses that prefix, get-or-creates
the matching WorkAccount using the same canonical id scheme the live resolver
now uses, and links it.

Safe to re-run: only touches WorkItem rows where account_id IS NULL and name
matches the "X — Y" convention; WorkAccount rows are get-or-created by their
canonical external_id.

Usage:
    cd backend
    python database/backfill_work_accounts.py [--dry-run]
"""

import argparse
import re

from database.db import SessionLocal
from database.models import WorkAccount, WorkItem

SEPARATOR = " — "


def _canonical_account_external_id(workspace_id: str, platform: str, account_name: str) -> str:
    raw = f"{workspace_id}:{platform}:ACCOUNT:{account_name}"
    return re.sub(r"[^A-Za-z0-9._:-]+", "-", raw).strip("-")[:240]


def backfill(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        orphans = (
            db.query(WorkItem)
            .filter(WorkItem.account_id.is_(None), WorkItem.name.like(f"%{SEPARATOR}%"))
            .all()
        )
        print(f"Found {len(orphans)} orphaned work items with a parseable account prefix.")

        linked = 0
        created_accounts = 0
        account_cache: dict[str, WorkAccount] = {}

        for item in orphans:
            account_name = item.name.split(SEPARATOR, 1)[0].strip()
            if not account_name:
                continue

            workspace_id = item.workspace_id or "default"
            platform = item.source_platform or "Custom"
            external_id = _canonical_account_external_id(workspace_id, platform, account_name)

            account = account_cache.get(external_id)
            if account is None:
                account = db.query(WorkAccount).filter(WorkAccount.external_id == external_id).first()
                if not account:
                    account = WorkAccount(
                        external_id=external_id,
                        name=account_name,
                        department=item.department,
                        status="active",
                        workspace_id=workspace_id,
                    )
                    db.add(account)
                    if not dry_run:
                        db.commit()
                        db.refresh(account)
                    created_accounts += 1
                account_cache[external_id] = account

            print(f"  {item.external_id!r}: {item.name!r} -> account {account_name!r}")
            if not dry_run:
                item.account_id = account.id
            linked += 1

        if dry_run:
            db.rollback()
            print(f"[dry-run] Would link {linked} work items to {created_accounts} new accounts.")
        else:
            db.commit()
            print(f"Linked {linked} work items ({created_accounts} new accounts created).")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
