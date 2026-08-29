"""
scripts/run_cdc_subscriber.py -- long-running worker process (Heroku
`worker` dyno) that keeps one Salesforce Change Data Capture subscription
open per connected org, so an Opportunity/Case stage change is reflected
in CostPilot within seconds instead of waiting for the ~10-minute
scheduled sync (scripts/sync_all_salesforce_outcomes.py, left running
unchanged as a safety net for anything this misses -- e.g. while this
worker is redeploying).

Not a request-driven script -- runs forever. Rescans the DB every
CONNECTION_RESCAN_SECONDS for connections that started/stopped being
eligible (new org connected, one disconnected) and starts/stops that
org's subscription accordingly, without restarting the ones already
running.

Usage:
    cd backend
    python scripts/run_cdc_subscriber.py
"""
import asyncio
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _select_salesforce_connections(db):
    from scripts.sync_all_salesforce_outcomes import _select_connections_to_sync
    return [c for c in _select_connections_to_sync(db) if c.platform == "salesforce"]


class _RunningSubscription:
    def __init__(self, task: asyncio.Task, stop_event: asyncio.Event):
        self.task = task
        self.stop_event = stop_event


async def _start_subscription(connection_id: int) -> _RunningSubscription:
    from core.cdc_subscriber import subscribe_connection
    from api.routes_connections import (
        _decrypt, _salesforce_refresh_access_token, SALESFORCE_CDC_ENTITY_SYNC_FUNCS,
        SALESFORCE_API_VERSION,
    )
    from database.db import SessionLocal
    from database.models import IntegrationConnection

    db = SessionLocal()
    try:
        item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
        if item is None or not item.instance_url:
            raise ValueError(f"connection {connection_id} no longer usable")
        instance_url = item.instance_url
    finally:
        db.close()

    async def get_token() -> str:
        db = SessionLocal()
        try:
            item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
            return _decrypt(item.access_token_encrypted)
        finally:
            db.close()

    async def on_refresh_needed() -> bool:
        db = SessionLocal()
        try:
            item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
            if item is None:
                return False
            return await _salesforce_refresh_access_token(db, item)
        finally:
            db.close()

    async def on_change(entity_name: str, record_ids: list[str]) -> None:
        sync_func = SALESFORCE_CDC_ENTITY_SYNC_FUNCS.get(entity_name)
        if sync_func is None:
            return
        db = SessionLocal()
        try:
            item = db.query(IntegrationConnection).filter(IntegrationConnection.id == connection_id).first()
            if item is None:
                return
            result = await sync_func(db, item, only_record_ids=record_ids)
            if result["updated"]:
                logger.info(
                    "cdc connection_id=%s %s change -> updated=%s record_ids=%s",
                    connection_id, entity_name, result["updated"], record_ids,
                )
        finally:
            db.close()

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        subscribe_connection(
            connection_id,
            instance_url=instance_url,
            api_version=SALESFORCE_API_VERSION,
            get_token=get_token,
            on_refresh_needed=on_refresh_needed,
            on_change=on_change,
            stop_event=stop_event,
        ),
        name=f"cdc-{connection_id}",
    )
    return _RunningSubscription(task, stop_event)


async def main():
    from database.db import SessionLocal
    from core.cdc_subscriber import CONNECTION_RESCAN_SECONDS

    running: dict[int, _RunningSubscription] = {}
    try:
        while True:
            db = SessionLocal()
            try:
                connections = _select_salesforce_connections(db)
            finally:
                db.close()
            desired_ids = {c.id for c in connections}

            for connection_id in list(running.keys()):
                if connection_id not in desired_ids:
                    logger.info("cdc connection_id=%s no longer eligible, stopping", connection_id)
                    running[connection_id].stop_event.set()
                    del running[connection_id]

            for connection_id in desired_ids:
                if connection_id in running:
                    continue
                try:
                    running[connection_id] = await _start_subscription(connection_id)
                    logger.info("cdc connection_id=%s subscription started", connection_id)
                except Exception:
                    logger.exception("cdc connection_id=%s failed to start subscription", connection_id)

            await asyncio.sleep(CONNECTION_RESCAN_SECONDS)
    finally:
        for sub in running.values():
            sub.stop_event.set()
        await asyncio.gather(*(sub.task for sub in running.values()), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
