"""
scripts/cleanup_duplicate_outcome_events.py — one-time cleanup for the
WorkItemOutcomeEvent pileup caused by the naive/timezone-aware datetime
comparison bug fixed in core/outcome_adapters/salesforce_case.py and
salesforce_opportunity.py (see that commit for the root cause). Every
~10-minute scheduled sync wrote a new, content-identical event for any
Case/Opportunity whose ClosedDate/CloseDate happened to be a full
timestamp rather than a bare date -- one production work item had 1594
rows for a value that never actually changed.

This does NOT touch the fix itself (already deployed) -- it only removes
the junk history the bug already wrote, so WorkItemOutcomeEvent reflects
real changes going forward instead of thousands of no-op duplicates.

Approach: per work item, walk its events in chronological order and
delete any event whose (outcome_status, outcome_value, outcome_date,
outcome_success, is_closed) is identical to the most recent KEPT event
before it. This collapses consecutive duplicate runs while preserving
genuine history -- a value that changed A -> B -> A over time still keeps
all three events, since each differs from its immediate predecessor.

Usage:
    cd backend
    python scripts/cleanup_duplicate_outcome_events.py [--dry-run]
"""
import argparse
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _content_key(event):
    return (
        event.outcome_status, event.outcome_value, event.outcome_date,
        event.outcome_success, event.is_closed,
    )


def main(dry_run: bool):
    from sqlalchemy import func
    from database.db import SessionLocal
    from database.models import WorkItemOutcomeEvent

    db = SessionLocal()
    try:
        work_item_ids = [
            wi_id for (wi_id,) in
            db.query(WorkItemOutcomeEvent.work_item_id).distinct().all()
        ]
        logger.info("Checking %d work item(s) with outcome event history.", len(work_item_ids))

        total_deleted = 0
        total_kept = 0
        affected_items = 0
        for wi_id in work_item_ids:
            events = (
                db.query(WorkItemOutcomeEvent)
                .filter(WorkItemOutcomeEvent.work_item_id == wi_id)
                .order_by(WorkItemOutcomeEvent.recorded_at.asc(), WorkItemOutcomeEvent.id.asc())
                .all()
            )
            if not events:
                continue

            to_delete_ids = []
            last_kept_key = None
            for event in events:
                key = _content_key(event)
                if last_kept_key is not None and key == last_kept_key:
                    to_delete_ids.append(event.id)
                else:
                    last_kept_key = key

            if not to_delete_ids:
                continue

            affected_items += 1
            total_deleted += len(to_delete_ids)
            total_kept += len(events) - len(to_delete_ids)
            logger.info(
                "work_item_id=%s: %d event(s) total, %d duplicate(s) %s",
                wi_id, len(events), len(to_delete_ids),
                "would be deleted (dry-run)" if dry_run else "deleted",
            )
            if not dry_run:
                (
                    db.query(WorkItemOutcomeEvent)
                    .filter(WorkItemOutcomeEvent.id.in_(to_delete_ids))
                    .delete(synchronize_session=False)
                )

        if not dry_run:
            db.commit()

        logger.info(
            "Done. %d work item(s) had duplicates. %d duplicate row(s) %s, %d row(s) kept.",
            affected_items, total_deleted,
            "would be deleted (dry-run)" if dry_run else "deleted", total_kept,
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
