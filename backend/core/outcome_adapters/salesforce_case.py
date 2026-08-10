"""
core/outcome_adapters/salesforce_case.py — second outcome adapter, proving
the pattern from salesforce_opportunity.py generalizes beyond one object
type:

    Salesforce Case  --[this adapter]-->  CostPilot Canonical Outcome

A Case has no dollar-value or won/lost concept the way an Opportunity
does -- forcing one onto it (e.g. treating "Closed" as "success") would be
exactly the kind of overclaim CostPilot is built to avoid. So
outcome_value and outcome_success are deliberately left None here; only
outcome_status and is_closed are populated, because those are the only
facts Salesforce's standard Case fields actually support without
guessing.
"""

from datetime import datetime
from typing import Optional


SALESFORCE_CASE_OUTCOME_FIELDS = (
    "Id", "Status", "IsClosed", "ClosedDate", "OwnerId", "AccountId", "LastModifiedDate",
)


def _parse_salesforce_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if len(value) <= 10:
            return datetime.strptime(value, "%Y-%m-%d")
        return datetime.fromisoformat(value.replace("+0000", "+00:00"))
    except ValueError:
        return None


def map_salesforce_case_to_canonical_outcome(record: dict) -> dict:
    return {
        "outcome_status": record.get("Status"),
        # No standard dollar-value field on a Case -- leaving this None
        # rather than inventing one is the honest choice.
        "outcome_value": None,
        "outcome_date": _parse_salesforce_datetime(record.get("ClosedDate")),
        # Salesforce's standard Case object has no reliable "resolved
        # successfully vs. unresolved" signal the way Opportunity has
        # IsWon -- a closed case isn't necessarily a satisfied customer.
        # Leaving this unset (not inferring it from IsClosed) is
        # deliberate: CostPilot should say "unknown" rather than guess.
        "outcome_success": None,
        "is_closed": bool(record.get("IsClosed")) if record.get("IsClosed") is not None else None,
        "owner": record.get("OwnerId"),
        "source_system": "salesforce",
        "source_object": "Case",
        "external_id": record["Id"],
        "source_modified_at": _parse_salesforce_datetime(record.get("LastModifiedDate")),
    }


def build_case_query(case_ids: list[str]) -> str:
    safe_ids = [cid for cid in case_ids if cid.isalnum() and 15 <= len(cid) <= 18]
    if len(safe_ids) != len(case_ids):
        raise ValueError("One or more case ids are not valid Salesforce record ids")
    fields = ", ".join(SALESFORCE_CASE_OUTCOME_FIELDS)
    id_list = ", ".join(f"'{cid}'" for cid in safe_ids)
    return f"SELECT {fields} FROM Case WHERE Id IN ({id_list})"


# ── Bulk discovery/import -- same shape as salesforce_opportunity.py's,
# proving the pattern generalizes to a second object type, not just a
# second platform ──

def build_all_cases_query() -> str:
    fields = ", ".join(SALESFORCE_CASE_OUTCOME_FIELDS) + ", CaseNumber, Subject, Account.Name"
    return f"SELECT {fields} FROM Case"


def map_salesforce_case_to_work_item_fields(record: dict) -> dict:
    account = record.get("Account") or {}
    case_number = record.get("CaseNumber")
    subject = record.get("Subject")
    name = f"Case {case_number}: {subject}" if case_number and subject else (subject or case_number or record["Id"])
    return {
        "name": name,
        "source_record_id": record["Id"],
        "source_record_type": "Case",
        "account_external_id": record.get("AccountId"),
        "account_name": account.get("Name") or record.get("AccountId"),
    }
