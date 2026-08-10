"""
core/outcome_adapters/servicenow_incident.py — first non-Salesforce outcome
adapter, proving salesforce_opportunity.py/salesforce_case.py's pattern
generalizes to a different platform AND a different domain (support, not
sales):

    ServiceNow Incident  --[this adapter]-->  CostPilot Canonical Outcome

An Incident has no dollar-value or won/lost concept -- like Salesforce
Case, outcome_value and outcome_success are deliberately left unset rather
than guessing from close_code (a "Closed" incident isn't necessarily a
satisfied customer; forcing a binary success signal onto it would be the
kind of overclaim CostPilot is built to avoid). Only outcome_status and
is_closed are populated, mirroring salesforce_case.py's honesty principle.

Queries here return a ServiceNow encoded query string (sysparm_query), not
SOQL -- see api/routes_connections.py's _servicenow_query_all, which pages
via sysparm_offset/sysparm_limit instead of Salesforce's nextRecordsUrl
cursor. Bulk fetches use sysparm_display_value=all (not the "true" used by
metadata discovery), so every field arrives as
{"display_value": <readable label>, "value": <raw/sys_id>} -- reference
fields like `company` must use .value for identity, never .display_value,
per the "never use display names as identity" rule established for
Salesforce/WorkItemSourceLink resolution.
"""

from datetime import datetime
from typing import Optional


SERVICENOW_INCIDENT_TABLE = "incident"

# Minimum field set -- same "don't expand without a reason" principle as
# SALESFORCE_OPPORTUNITY_OUTCOME_FIELDS.
SERVICENOW_INCIDENT_OUTCOME_FIELDS = (
    "sys_id", "number", "short_description", "state", "close_code",
    "resolved_at", "closed_at", "priority", "assigned_to", "company",
    "sys_updated_on",
)


def _value(record: dict, field: str) -> Optional[str]:
    """Unwrap a sysparm_display_value=all field to its raw/id value.
    Falls back to a plain scalar if the caller passed a non-dict-shaped
    record (e.g. in unit tests that build fixtures by hand)."""
    raw = record.get(field)
    if isinstance(raw, dict):
        return raw.get("value") or None
    return raw or None


def _display(record: dict, field: str) -> Optional[str]:
    """Unwrap a sysparm_display_value=all field to its human-readable
    label -- used only for outcome_status/name, never for identity."""
    raw = record.get(field)
    if isinstance(raw, dict):
        return raw.get("display_value") or raw.get("value") or None
    return raw or None


def _parse_servicenow_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # ServiceNow's raw (.value) datetime format is always
        # "YYYY-MM-DD HH:MM:SS" UTC, regardless of display timezone.
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def map_servicenow_incident_to_canonical_outcome(record: dict) -> dict:
    status = _display(record, "state")
    return {
        "outcome_status": status,
        # No standard dollar-value field on an Incident -- leaving this
        # None rather than inventing one, same as Case.
        "outcome_value": None,
        "outcome_date": (
            _parse_servicenow_datetime(_value(record, "resolved_at"))
            or _parse_servicenow_datetime(_value(record, "closed_at"))
        ),
        # close_code (e.g. "Solved (Work Around)", "Not Solvable", "Duplicate")
        # is not a clean success/failure signal -- CostPilot says "unknown"
        # rather than guess, same principle as Salesforce Case.
        "outcome_success": None,
        "is_closed": (status in ("Resolved", "Closed", "Cancelled")) if status else None,
        "owner": _value(record, "assigned_to"),
        "source_system": "servicenow",
        "source_object": "incident",
        "external_id": _value(record, "sys_id"),
        "source_modified_at": _parse_servicenow_datetime(_value(record, "sys_updated_on")),
    }


def build_all_incidents_query() -> str:
    """Encoded query for every Incident in the instance -- bulk import,
    not a bounded batch."""
    return "ORDERBYnumber"


def build_incidents_incremental_query(since: datetime) -> str:
    """Encoded query for Incidents modified since the last sync checkpoint."""
    since_literal = since.strftime("%Y-%m-%d %H:%M:%S")
    return f"sys_updated_on>{since_literal}^ORDERBYsys_updated_on"


def map_servicenow_incident_to_work_item_fields(record: dict) -> dict:
    """The work-item-identity half of a bulk-imported record, kept
    separate from the outcome-facts half, same split as the Salesforce
    adapters."""
    sys_id = _value(record, "sys_id")
    number = _value(record, "number")
    short_description = _value(record, "short_description")
    name = (
        f"Incident {number}: {short_description}" if number and short_description
        else (short_description or number or sys_id)
    )
    return {
        "name": name,
        "source_record_id": sys_id,
        "source_record_type": "incident",
        # .value on the `company` reference field is its real sys_id --
        # the stable identifier -- never the display label.
        "account_external_id": _value(record, "company"),
        "account_name": _display(record, "company"),
    }
