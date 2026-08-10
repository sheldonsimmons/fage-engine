"""
core/outcome_adapters/servicenow_case.py — second ServiceNow outcome
adapter, proving servicenow_incident.py's pattern generalizes to a second
table on the same platform (Customer Service Management Case, not ITSM
Incident), same shape as salesforce_case.py alongside
salesforce_opportunity.py.

    ServiceNow sn_customerservice_case  --[this adapter]-->  CostPilot Canonical Outcome

Same honesty principle as the other Case/Incident adapters: no reliable
success/failure signal, so outcome_value and outcome_success stay unset.
"""

from datetime import datetime
from typing import Optional


SERVICENOW_CASE_TABLE = "sn_customerservice_case"

SERVICENOW_CASE_OUTCOME_FIELDS = (
    "sys_id", "number", "short_description", "state", "priority",
    "account", "assigned_to", "closed_at", "sys_updated_on",
)


def _value(record: dict, field: str) -> Optional[str]:
    """Unwrap a sysparm_display_value=all field to its raw/id value."""
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
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def map_servicenow_case_to_canonical_outcome(record: dict) -> dict:
    status = _display(record, "state")
    return {
        "outcome_status": status,
        "outcome_value": None,
        "outcome_date": _parse_servicenow_datetime(_value(record, "closed_at")),
        "outcome_success": None,
        "is_closed": (status in ("Resolved", "Closed", "Cancelled")) if status else None,
        "owner": _value(record, "assigned_to"),
        "source_system": "servicenow",
        "source_object": "sn_customerservice_case",
        "external_id": _value(record, "sys_id"),
        "source_modified_at": _parse_servicenow_datetime(_value(record, "sys_updated_on")),
    }


def build_all_cases_query() -> str:
    return "ORDERBYnumber"


def build_cases_incremental_query(since: datetime) -> str:
    since_literal = since.strftime("%Y-%m-%d %H:%M:%S")
    return f"sys_updated_on>{since_literal}^ORDERBYsys_updated_on"


def map_servicenow_case_to_work_item_fields(record: dict) -> dict:
    sys_id = _value(record, "sys_id")
    number = _value(record, "number")
    short_description = _value(record, "short_description")
    name = (
        f"Case {number}: {short_description}" if number and short_description
        else (short_description or number or sys_id)
    )
    return {
        "name": name,
        "source_record_id": sys_id,
        "source_record_type": "sn_customerservice_case",
        # CSM Case's `account` reference field -- its .value is the real
        # sys_id, never the display label, same principle as Incident's
        # `company` field.
        "account_external_id": _value(record, "account"),
        "account_name": _display(record, "account"),
    }
