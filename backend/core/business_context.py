"""Universal business-context definitions shared by every integration.

Projects remain the first supported context type. Platform templates translate
native records into this contract so callers do not need to perform field
mapping on every AI request.
"""

from dataclasses import dataclass
from typing import Optional


VALID_CONTEXT_TYPES = {
    "project",
    "matter",
    "engagement",
    "case",
    "ticket",
    "claim",
    "opportunity",
    "environment",
    "application",
}


@dataclass(frozen=True)
class BusinessContextTemplate:
    key: str
    name: str
    source_platform: str
    context_type: str
    work_label: str
    customer_label: str
    source_record_types: tuple[str, ...]
    description: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "source_platform": self.source_platform,
            "context_type": self.context_type,
            "work_label": self.work_label,
            "customer_label": self.customer_label,
            "source_record_types": list(self.source_record_types),
            "description": self.description,
        }


BUSINESS_CONTEXT_TEMPLATES = {
    "salesforce_project": BusinessContextTemplate(
        key="salesforce_project",
        name="Salesforce Project",
        source_platform="Salesforce",
        context_type="project",
        work_label="Project",
        customer_label="Account",
        source_record_types=("CostPilot_Project__c",),
        description=(
            "Automatically connects Agentforce activity to the Salesforce "
            "project, account, user, agent, budget, and governance decision."
        ),
    ),
    "servicenow_case": BusinessContextTemplate(
        key="servicenow_case",
        name="ServiceNow Case",
        source_platform="ServiceNow",
        context_type="case",
        work_label="Case",
        customer_label="Account",
        source_record_types=("sn_customerservice_case", "incident"),
        description=(
            "Connects AI activity to a ServiceNow case or incident and its "
            "account, assignee, service agent, and operational controls."
        ),
    ),
}


def get_context_template(key: Optional[str]) -> Optional[BusinessContextTemplate]:
    return BUSINESS_CONTEXT_TEMPLATES.get((key or "").strip().lower())


def normalize_context_type(
    value: Optional[str],
    *,
    template_key: Optional[str] = None,
    default: str = "project",
) -> str:
    template = get_context_template(template_key)
    normalized = (value or (template.context_type if template else default)).strip().lower()
    if normalized not in VALID_CONTEXT_TYPES:
        raise ValueError(
            f"context_type must be one of: {', '.join(sorted(VALID_CONTEXT_TYPES))}"
        )
    return normalized


def business_context_json(item) -> dict:
    """Return the universal contract without changing legacy project fields."""
    template = get_context_template(getattr(item, "context_template", None))
    account = getattr(item, "account", None)
    return {
        "id": item.external_id,
        "type": getattr(item, "context_type", None) or "project",
        "name": item.name,
        "template": getattr(item, "context_template", None),
        "work_label": template.work_label if template else "Project",
        "customer": (
            {
                "id": account.external_id,
                "name": account.name,
            }
            if account
            else None
        ),
        "source": {
            "platform": item.source_platform,
            "record_type": getattr(item, "source_record_type", None),
            "record_id": getattr(item, "source_record_id", None),
        },
        "owner": item.owner,
        "department": item.department,
        "status": item.status,
        "monthly_ai_budget": item.monthly_ai_budget,
    }
