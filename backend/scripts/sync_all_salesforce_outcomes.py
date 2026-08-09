"""
scripts/sync_all_salesforce_outcomes.py — run the Salesforce Opportunity
outcome sync (see api/routes_connections.py sync_outcomes) for every real,
working Salesforce connection across every workspace.

This exists so outcome data can be kept fresh automatically instead of
requiring someone to remember to call POST /connections/{id}/sync-outcomes
by hand. Intentionally NOT a new in-app scheduler (no APScheduler/Celery
added) -- per the original outcome-enrichment design, the smallest viable
approach is an external trigger hitting existing code, e.g. Heroku
Scheduler running this script on an interval.

A workspace can accumulate multiple IntegrationConnection rows for the
same platform over time (re-auth attempts, package-install retries, a
connection that errored and was replaced). Only one -- the most recently
*working* one -- should actually be synced per workspace; the others are
either duplicates or dead ends. "Working" is judged the same way the
Business Profile UI's Connected Systems card judges it: a real
last_success_at, and not "error"/"superseded".

Usage:
    cd backend
    python scripts/sync_all_salesforce_outcomes.py [--dry-run]
"""
import argparse
import asyncio
import logging
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _select_connections_to_sync(db):
    from database.models import IntegrationConnection

    candidates = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.access_token_encrypted.isnot(None),
            IntegrationConnection.instance_url.isnot(None),
            IntegrationConnection.status.notin_(["error", "superseded"]),
        )
        .all()
    )
    by_workspace = {}
    for item in candidates:
        existing = by_workspace.get(item.workspace_id)
        if existing is None:
            by_workspace[item.workspace_id] = item
            continue
        # Prefer whichever has actually succeeded more recently; a
        # connection that has never succeeded loses to one that has.
        item_success = item.last_success_at
        existing_success = existing.last_success_at
        if item_success and (not existing_success or item_success > existing_success):
            by_workspace[item.workspace_id] = item
    return list(by_workspace.values())


async def main(dry_run: bool):
    from database.db import SessionLocal
    from api.routes_connections import _sync_salesforce_opportunity_outcomes

    db = SessionLocal()
    try:
        connections = _select_connections_to_sync(db)
        if not connections:
            logger.info("No working Salesforce connections found to sync.")
            return
        logger.info("Found %d workspace(s) with a Salesforce connection to sync.", len(connections))
        for item in connections:
            if dry_run:
                logger.info(
                    "[dry-run] Would sync workspace=%s connection_id=%s instance_url=%s",
                    item.workspace_id, item.id, item.instance_url,
                )
                continue
            try:
                result = await _sync_salesforce_opportunity_outcomes(db, item)
                if result["errors"]:
                    logger.warning(
                        "workspace=%s connection_id=%s sync had errors: %s",
                        item.workspace_id, item.id, result["errors"],
                    )
                else:
                    item.last_success_at = datetime.utcnow()
                    db.commit()
                logger.info(
                    "workspace=%s connection_id=%s checked=%s updated=%s unchanged=%s errors=%d",
                    item.workspace_id, item.id, result["checked"], result["updated"],
                    result["unchanged"], len(result["errors"]),
                )
            except Exception:
                logger.exception(
                    "workspace=%s connection_id=%s sync raised an exception, continuing to next connection",
                    item.workspace_id, item.id,
                )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
