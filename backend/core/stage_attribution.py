"""
core/stage_attribution.py — point-in-time business-stage attribution.

Extracted from api/routes_work_items.py's account_profile() (Phase A2/A4 of
the ROI/Business Impact investigation) so the same point-in-time attribution
logic is one reusable function instead of embedded in a single route
handler. This is the "AI Investment by Opportunity Stage" computation --
which stage was active when each AI request happened, using
WorkItemOutcomeEvent's already-populated change history (see that model's
docstring -- built for exactly this).

Generalized beyond context_type == "opportunity": the event-walk and
retroactive-attribution logic below has no Salesforce- or Opportunity-
specific assumption in it -- it only reads WorkItemOutcome/WorkItemOutcomeEvent
generically. What IS still Salesforce/HubSpot-Opportunity-specific is the
*picklist zero-fill* (there is no discovery-backed picklist source for any
other object today) -- callers for other context_types simply get the
existing observed-only fallback, which was already the documented behavior
whenever a picklist wasn't available.
"""

import json
from datetime import datetime

from sqlalchemy import case, func, or_

from database.models import IntegrationConnection, TokenTransaction, WorkItem, WorkItemOutcomeEvent


def compute_stage_breakdown(
    db,
    account,
    work_item_ids: list,
    tx_base,
    context_types: tuple = ("opportunity",),
    picklist_object_name: str = "Opportunity",
) -> list:
    """
    Returns the stage_breakdown list exactly as account_profile() used to
    build it inline -- same shape, same zero-fill/platform-grouping/sort
    behavior -- for whichever WorkItems match `context_types` (by
    WorkItem.context_type or the real source_record_type, matching both
    since the live Agentforce ingestion path hardcodes context_type to a
    generic "project" bucket regardless of the record's actual Salesforce
    object type; source_record_type is reliably accurate either way).

    `picklist_object_name` is the IntegrationConnection.selected_object to
    look up a real admin-defined picklist for (only Salesforce Opportunity
    discovery captures one today) -- other context_types fall back to the
    observed-only stage list, same as an Opportunity would with no
    discovery-backed picklist yet.
    """
    matched_types = tuple(t.lower() for t in context_types)
    item_ids = [
        row[0] for row in db.query(WorkItem.id)
        .filter(
            WorkItem.id.in_(work_item_ids),
            or_(
                WorkItem.context_type.in_(matched_types),
                func.lower(WorkItem.source_record_type).in_(matched_types),
            ),
        )
        .all()
    ]
    if not item_ids:
        return []

    # Lowercased and stripped so this matches present_platforms/
    # PLATFORM_ORDER/PLATFORM_LABELS exactly -- otherwise the same
    # platform (e.g. "HubSpot" from real data vs "hubspot" from the
    # fallback picklist) gets treated as two different platforms.
    platform_by_item: dict[int, str] = {
        row[0]: (row[1] or "").strip().lower()
        for row in db.query(WorkItem.id, WorkItem.source_platform).filter(WorkItem.id.in_(item_ids)).all()
    }
    stage_platform: dict[str, str] = {}
    events_by_item: dict[int, list] = {}
    for wi_id, status, recorded_at in (
        db.query(WorkItemOutcomeEvent.work_item_id, WorkItemOutcomeEvent.outcome_status, WorkItemOutcomeEvent.recorded_at)
        .filter(WorkItemOutcomeEvent.work_item_id.in_(item_ids))
        .order_by(WorkItemOutcomeEvent.work_item_id, WorkItemOutcomeEvent.recorded_at)
        .all()
    ):
        events_by_item.setdefault(wi_id, []).append((recorded_at, status))

    # Only for items with zero outcome-sync history at all -- activity that
    # predates the *first* known event on an otherwise-tracked item is
    # attributed to that earliest stage instead (see the merge loop below),
    # not lumped in here.
    NO_STAGE_YET = "Before tracking began"
    stage_totals: dict[str, dict] = {}
    stage_first_seen: dict[str, datetime] = {}

    item_txs = (
        tx_base
        .filter(TokenTransaction.work_item_id.in_(item_ids))
        .with_entities(TokenTransaction.work_item_id, TokenTransaction.timestamp, TokenTransaction.cost_usd)
        .order_by(TokenTransaction.work_item_id, TokenTransaction.timestamp)
        .all()
    )
    event_cursor: dict[int, int] = {}
    for wi_id, ts, cost in item_txs:
        events = events_by_item.get(wi_id, [])
        idx = event_cursor.get(wi_id, 0)
        while idx < len(events) and events[idx][0] <= ts:
            idx += 1
        event_cursor[wi_id] = idx
        if idx > 0:
            stage = events[idx - 1][1]
        elif events:
            # No event recorded before this transaction, but the item does
            # have known history -- CostPilot just hadn't synced yet at
            # that moment, not that there was no real stage. The stage
            # didn't change because we weren't watching, so attribute
            # retroactively to the earliest stage we've ever observed
            # rather than an "unknown" bucket.
            stage = events[0][1]
        else:
            stage = NO_STAGE_YET
        stage = stage or NO_STAGE_YET

        bucket = stage_totals.setdefault(stage, {"spend_usd": 0.0, "request_count": 0})
        bucket["spend_usd"] += float(cost or 0.0)
        bucket["request_count"] += 1
        if stage not in stage_platform and platform_by_item.get(wi_id):
            stage_platform[stage] = platform_by_item[wi_id]
        if stage != NO_STAGE_YET:
            first_seen = next((e[0] for e in events if e[1] == stage), ts)
            if stage not in stage_first_seen or first_seen < stage_first_seen[stage]:
                stage_first_seen[stage] = first_seen

    # Show every stage the org actually defines -- not just the ones
    # CostPilot happened to observe activity or a stage-change on -- when
    # the picklist is known. Captured from the object's admin-defined
    # picklist field, already fetched during object discovery (see
    # routes_connections.py's discover_object()); existing connections need
    # to re-run discovery once to populate this. Falls back to observed-only
    # stages (today's behavior) when it isn't available yet.
    picklist_stages: list[str] = []
    connection = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.workspace_id == account.workspace_id,
            IntegrationConnection.platform == "salesforce",
            IntegrationConnection.selected_object == picklist_object_name,
        )
        .order_by(IntegrationConnection.last_success_at.desc())
        .first()
    )
    if connection and connection.discovery_json:
        try:
            discovery = json.loads(connection.discovery_json)
        except (TypeError, ValueError):
            discovery = {}
        stage_field = next(
            (f for f in discovery.get("fields") or [] if f.get("name") == "StageName"),
            None,
        )
        if stage_field and stage_field.get("picklist_values"):
            picklist_stages = [s for s in stage_field["picklist_values"] if s]
            for stage in picklist_stages:
                stage_platform.setdefault(stage, "salesforce")

    # Simulated/demo workspaces (and any platform without a live
    # connection's cached picklist -- only Salesforce discovery captures one
    # today) have no IntegrationConnection to source a real picklist from.
    # Fall back to each recognized platform's own standard stage list,
    # appended for whichever platforms actually appear among this account's
    # matched work items, so the zero-fill still shows a complete,
    # correctly-ordered funnel. Only meaningful for opportunity-shaped work
    # (Salesforce/HubSpot both use a stage-name picklist) -- other
    # context_types simply get no fallback and stay observed-only, which is
    # the correct, honest behavior rather than fabricating a picklist.
    FALLBACK_STAGE_PICKLISTS = {
        "salesforce": [
            "Prospecting", "Qualification", "Needs Analysis", "Value Proposition",
            "Id. Decision Makers", "Perception Analysis", "Proposal/Price Quote",
            "Negotiation/Review", "Closed Won", "Closed Lost",
        ],
        "hubspot": [
            "appointmentscheduled", "qualifiedtobuy", "presentationscheduled",
            "contractsent", "closedwon", "closedlost",
        ],
    }
    present_platforms = {
        (row[0] or "").strip().lower()
        for row in db.query(WorkItem.source_platform).filter(WorkItem.id.in_(item_ids)).distinct().all()
    }
    known_stages = set(picklist_stages)
    if matched_types == ("opportunity",):
        for platform in present_platforms:
            fallback = FALLBACK_STAGE_PICKLISTS.get(platform)
            if not fallback:
                continue
            new_stages = [s for s in fallback if s not in known_stages]
            if new_stages:
                picklist_stages = picklist_stages + new_stages
                known_stages.update(new_stages)
                for stage in new_stages:
                    stage_platform.setdefault(stage, platform)

    for stage in picklist_stages:
        if stage not in stage_totals:
            stage_totals[stage] = {"spend_usd": 0.0, "request_count": 0}

    # Group stages by which platform they belong to (Salesforce and HubSpot
    # use entirely different vocabularies, so mixing them into one flat
    # numbered list read as one confusing pipeline instead of two real,
    # separate ones). A fixed order keeps this deterministic rather than
    # depending on set-iteration order.
    PLATFORM_ORDER = ["salesforce", "hubspot"]
    PLATFORM_LABELS = {"salesforce": "Salesforce", "hubspot": "HubSpot"}

    def _platform_rank(platform_key: str):
        if platform_key in PLATFORM_ORDER:
            return (0, PLATFORM_ORDER.index(platform_key))
        if platform_key:
            return (1, platform_key)
        return (2, "")  # no known platform (e.g. NO_STAGE_YET) sorts last

    def _final_sort_key(name: str):
        platform_key = stage_platform.get(name, "")
        if name in picklist_stages:
            # Picklist order first and foremost -- an org's defined stage
            # sequence beats a first-observed-timestamp guess.
            within = (0, picklist_stages.index(name))
        elif name == NO_STAGE_YET:
            within = (1, 0)
        else:
            # Observed but outside the known picklist (a stale value no
            # longer active) sorts after the real picklist, chronologically.
            within = (2, stage_first_seen.get(name, datetime.max).timestamp())
        return (_platform_rank(platform_key), within)

    return [
        {
            "stage": stage,
            "platform": stage_platform.get(stage) or None,
            "platform_label": PLATFORM_LABELS.get(stage_platform.get(stage), (stage_platform.get(stage) or "").title() or None),
            "spend_usd": round(totals["spend_usd"], 6),
            "request_count": totals["request_count"],
        }
        for stage, totals in sorted(stage_totals.items(), key=lambda kv: _final_sort_key(kv[0]))
    ]
