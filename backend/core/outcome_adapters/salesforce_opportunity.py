"""
core/outcome_adapters/salesforce_opportunity.py — the one outcome adapter
built so far, proving the pattern described in the CostPilot Universal
outcome-enrichment design:

    Salesforce Opportunity  --[this adapter]-->  CostPilot Canonical Outcome

Salesforce remains the system of record. This module only maps its fields
onto CostPilot's universal outcome concepts (OUTCOME_STATUS, OUTCOME_VALUE,
OUTCOME_DATE, OUTCOME_SUCCESS) -- it never writes back to Salesforce, and it
pulls the minimum field set, not a full object copy.

Adding another platform/object later means adding a sibling adapter with
the same shape, not touching the analytics layer that consumes
WorkItemOutcome.
"""

from datetime import datetime
from typing import Optional


# Minimum field set (see design doc section 10 -- do not expand without a
# reason; every extra field is another thing to justify to security/procurement).
SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS = (
    "Id", "StageName", "IsClosed", "IsWon", "Amount",
    "CloseDate", "OwnerId", "AccountId", "LastModifiedDate",
)


def _parse_salesforce_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Salesforce returns CloseDate as "YYYY-MM-DD" and LastModifiedDate
        # as full ISO-8601 with a trailing "+0000" offset (not "Z").
        if len(value) <= 10:
            return datetime.strptime(value, "%Y-%m-%d")
        return datetime.fromisoformat(value.replace("+0000", "+00:00"))
    except ValueError:
        return None


def map_salesforce_opportunity_to_canonical_outcome(record: dict) -> dict:
    """
    Map one Salesforce Opportunity record (as returned by a SOQL query over
    SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS) to CostPilot's canonical outcome
    shape. A customer-specific field-mapping override (e.g. ARR__c instead
    of Amount) is applied by the caller before this function sees the
    record -- this function only knows the canonical field names.
    """
    is_won = record.get("IsWon")
    return {
        "outcome_status": record.get("StageName"),
        "outcome_value": (
            float(record["Amount"]) if record.get("Amount") is not None else None
        ),
        "outcome_date": _parse_salesforce_datetime(record.get("CloseDate")),
        "outcome_success": bool(is_won) if is_won is not None else None,
        "is_closed": bool(record.get("IsClosed")) if record.get("IsClosed") is not None else None,
        "owner": record.get("OwnerId"),
        "source_system": "salesforce",
        "source_object": "Opportunity",
        "external_id": record["Id"],
        "source_modified_at": _parse_salesforce_datetime(record.get("LastModifiedDate")),
    }


def build_opportunity_query(opportunity_ids: list[str]) -> str:
    """SOQL for a bounded batch of known Opportunity ids (on-demand / targeted sync)."""
    # Salesforce record ids are alphanumeric (15 or 18 chars) -- reject
    # anything else rather than interpolate untrusted strings into SOQL.
    safe_ids = [oid for oid in opportunity_ids if oid.isalnum() and 15 <= len(oid) <= 18]
    if len(safe_ids) != len(opportunity_ids):
        raise ValueError("One or more opportunity ids are not valid Salesforce record ids")
    fields = ", ".join(SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS)
    id_list = ", ".join(f"'{oid}'" for oid in safe_ids)
    return f"SELECT {fields} FROM Opportunity WHERE Id IN ({id_list})"


def build_opportunity_incremental_query(since: datetime) -> str:
    """SOQL for all Opportunities modified since the last successful sync checkpoint."""
    fields = ", ".join(SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS)
    since_literal = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"SELECT {fields} FROM Opportunity WHERE LastModifiedDate > {since_literal}"


# ── Bulk discovery/import (proves the "Connect -> Discover -> Import" shape
# from the universal Business Context design; Salesforce is the first
# adapter, not a special case -- see api/routes_connections.py's
# _import_salesforce_work_items, which knows nothing Salesforce-specific) ──

def build_all_opportunities_query() -> str:
    """SOQL for every Opportunity in the org -- used for the one-time bulk
    import, not the bounded batch queries above. Includes Account.Name via
    a relationship query so the importer doesn't need a second round-trip
    just to label the account it's creating/matching."""
    fields = ", ".join(SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS) + ", Name, Account.Name"
    return f"SELECT {fields} FROM Opportunity"


def map_salesforce_opportunity_to_work_item_fields(record: dict) -> dict:
    """The work-item-identity half of a bulk-imported record -- name,
    account linkage -- kept separate from map_salesforce_opportunity_to_
    canonical_outcome's outcome-facts half, since a WorkItem and its
    Outcome are different concepts even when built from the same record."""
    account = record.get("Account") or {}
    return {
        "name": record.get("Name") or record["Id"],
        "source_record_id": record["Id"],
        "source_record_type": "Opportunity",
        "account_external_id": record.get("AccountId"),
        "account_name": account.get("Name") or record.get("AccountId"),
    }
