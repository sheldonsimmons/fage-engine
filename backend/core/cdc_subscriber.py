"""
core/cdc_subscriber.py -- real-time Salesforce outcome sync via Change
Data Capture (CDC), instead of waiting for the ~10-minute background
scheduler (core/../scripts/sync_all_salesforce_outcomes.py, still kept
running underneath as a safety net for anything missed here).

Salesforce CDC publishes an event on the org's Streaming API bus
(Bayeux/CometD protocol) the moment a tracked object changes -- this
module holds one persistent long-polling connection per Salesforce
connection, and on each Opportunity/Case change event, calls the exact
same outcome-sync code the scheduler and the manual "Sync Now" button
already use (api/routes_connections.py's
SALESFORCE_CDC_ENTITY_SYNC_FUNCS), scoped to just the record(s) that
changed via only_record_ids. This module owns the "when to sync"
question; it deliberately knows nothing about how outcome mapping works.

Requires Change Data Capture to be enabled for Opportunity/Case in the
target org (Setup -> Change Data Capture) -- a declarative, per-org
config step, not something this code can turn on for you.
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Bayeux long-poll connect blocks server-side for a while; give it room
# before treating it as a network failure rather than a normal empty poll.
_CONNECT_TIMEOUT = httpx.Timeout(15.0, read=120.0)
_HANDSHAKE_TIMEOUT = httpx.Timeout(20.0)

# How often the supervisor (run_cdc_subscriber.py) re-checks the DB for
# newly-added or removed Salesforce connections to subscribe/unsubscribe.
CONNECTION_RESCAN_SECONDS = 300

# Backoff after a connect/handshake failure, capped -- avoids hammering
# Salesforce (or our own error logs) if an org's connection is broken.
_MIN_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 120

CDC_CHANNELS = {
    "Opportunity": "/data/OpportunityChangeEvent",
    "Case": "/data/CaseChangeEvent",
}


class _BayeuxSession:
    """One CometD handshake + subscriptions + long-poll loop for a single
    Salesforce org. Not reused across orgs -- each org gets its own
    clientId and its own long-lived HTTP connection."""

    def __init__(self, instance_url: str, api_version: str, get_token, on_refresh_needed):
        self._base = f"{instance_url.rstrip('/')}/cometd/{api_version}"
        self._get_token = get_token
        self._on_refresh_needed = on_refresh_needed
        self._client_id: Optional[str] = None

    async def _post(self, body: list[dict], *, timeout: httpx.Timeout) -> list[dict]:
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._base,
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        if response.status_code == 401:
            refreshed = await self._on_refresh_needed()
            if not refreshed:
                raise PermissionError("Salesforce authorization failed and could not be refreshed")
            token = await self._get_token()
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self._base,
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
        response.raise_for_status()
        return response.json()

    async def handshake(self) -> None:
        result = await self._post(
            [{
                "channel": "/meta/handshake",
                "version": "1.0",
                "minimumVersion": "1.0",
                "supportedConnectionTypes": ["long-polling"],
            }],
            timeout=_HANDSHAKE_TIMEOUT,
        )
        message = result[0]
        if not message.get("successful"):
            raise ConnectionError(f"Bayeux handshake failed: {message.get('error')}")
        self._client_id = message["clientId"]

    async def subscribe(self, channel: str) -> None:
        result = await self._post(
            [{"channel": "/meta/subscribe", "clientId": self._client_id, "subscription": channel}],
            timeout=_HANDSHAKE_TIMEOUT,
        )
        message = result[0]
        if not message.get("successful"):
            raise ConnectionError(f"Subscribe to {channel} failed: {message.get('error')}")

    async def connect(self) -> list[dict]:
        """One long-poll cycle. Returns event messages (may be empty)."""
        result = await self._post(
            [{"channel": "/meta/connect", "clientId": self._client_id, "connectionType": "long-polling"}],
            timeout=_CONNECT_TIMEOUT,
        )
        events = []
        for message in result:
            channel = message.get("channel", "")
            if message.get("channel") == "/meta/connect" and not message.get("successful"):
                advice = message.get("advice", {})
                if advice.get("reconnect") == "handshake":
                    raise ConnectionError("Salesforce requested re-handshake")
                raise ConnectionError(f"Connect failed: {message.get('error')}")
            if channel.startswith("/data/") and "data" in message:
                events.append(message["data"])
        return events


def parse_change_event(event_data: dict) -> Optional[tuple[str, list[str]]]:
    """Extract (entityName, recordIds) from one CDC event payload, or
    None if it isn't a change we act on (e.g. a DELETE, or a CREATE for a
    record CostPilot has never imported -- only_record_ids sync on an
    untracked id is a harmless no-op, so CREATE is passed through too)."""
    payload = event_data.get("payload") or {}
    header = payload.get("ChangeEventHeader") or {}
    entity_name = header.get("entityName")
    record_ids = header.get("recordIds") or []
    change_type = header.get("changeType")
    if not entity_name or not record_ids or change_type == "DELETE":
        return None
    return entity_name, record_ids


async def subscribe_connection(
    connection_id: int,
    *,
    instance_url: str,
    api_version: str,
    get_token,
    on_refresh_needed,
    on_change,
    stop_event: asyncio.Event,
) -> None:
    """
    Runs until stop_event is set. get_token()/on_refresh_needed() hide
    token storage/decryption/refresh (see run_cdc_subscriber.py) so this
    function only deals with the Bayeux protocol. on_change(entity_name,
    record_ids) is called for every relevant event -- it owns actually
    writing outcome data.
    """
    backoff = _MIN_BACKOFF_SECONDS
    while not stop_event.is_set():
        session = _BayeuxSession(instance_url, api_version, get_token, on_refresh_needed)
        try:
            await session.handshake()
            for channel in CDC_CHANNELS.values():
                await session.subscribe(channel)
            logger.info("cdc connection_id=%s subscribed to %s", connection_id, list(CDC_CHANNELS.values()))
            backoff = _MIN_BACKOFF_SECONDS
            while not stop_event.is_set():
                events = await session.connect()
                for event_data in events:
                    parsed = parse_change_event(event_data)
                    if not parsed:
                        continue
                    entity_name, record_ids = parsed
                    try:
                        await on_change(entity_name, record_ids)
                    except Exception:
                        logger.exception(
                            "cdc connection_id=%s failed handling change for %s %s",
                            connection_id, entity_name, record_ids,
                        )
        except asyncio.CancelledError:
            raise
        except PermissionError as exc:
            logger.error("cdc connection_id=%s auth failure, backing off: %s", connection_id, exc)
        except Exception as exc:
            logger.warning("cdc connection_id=%s stream error, reconnecting: %s", connection_id, exc)

        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
