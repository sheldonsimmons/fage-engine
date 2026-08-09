"""
database/backfill_business_purpose.py — one-time backfill of
TokenTransaction.business_purpose for rows written before that column
existed (see database/models.py TokenTransaction.business_purpose and
api/routes_work_items.py classify_business_purpose_fields()).

Pure read of existing origin_record_type/origin_record_name/work item/agent
data, nothing is moved, renamed, or deleted. Safe to re-run: only fills rows
where business_purpose is currently NULL.

Usage:
    cd backend
    python database/backfill_business_purpose.py [--dry-run]
"""

import argparse

from database.db import SessionLocal
from database.migrate import create_tables, run_migrations
from database.models import RegisteredAgent, TokenTransaction, WorkItem
from api.routes_work_items import classify_business_purpose_fields


def backfill(db, dry_run=False):
    rows = (
        db.query(TokenTransaction, WorkItem, RegisteredAgent)
        .outerjoin(WorkItem, TokenTransaction.work_item_id == WorkItem.id)
        .outerjoin(RegisteredAgent, TokenTransaction.agent_id == RegisteredAgent.id)
        .filter(TokenTransaction.business_purpose.is_(None))
        .all()
    )
    updated = 0
    for tx, work_item, agent in rows:
        tx.business_purpose = classify_business_purpose_fields(
            tx.origin_record_type,
            tx.origin_record_name,
            work_item.context_type if work_item else None,
            work_item.source_record_type if work_item else None,
            agent.name if agent else None,
        )
        updated += 1
    print(f"{'Would update' if dry_run else 'Updated'} {updated} token_transactions rows.")
    if not dry_run:
        db.commit()
    else:
        db.rollback()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    create_tables()
    run_migrations()
    db = SessionLocal()
    try:
        backfill(db, dry_run=args.dry_run)
    finally:
        db.close()
