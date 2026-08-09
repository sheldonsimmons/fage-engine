"""Universal connector contract and platform capability manifests."""

from fastapi import APIRouter, HTTPException


router = APIRouter()
CONTRACT_VERSION = "2026-07-26"

CONNECTOR_MANIFESTS = {
    "salesforce": {
        "key": "salesforce",
        "label": "Salesforce",
        "category": "business_platform",
        "authentication": {"type": "oauth_named_credential", "customer_label": "Connect Salesforce"},
        "installation": ["Agentforce action", "Invocable Apex", "Record-triggered Flow"],
        "modes": {"control": "available", "observe": "available"},
        "objects": ["CostPilot_Project__c", "Case", "Opportunity", "Lead", "Contact", "Account", "Task"],
        "recommended_work": [
            {"label": "Projects", "object": "CostPilot_Project__c", "context_type": "project"},
            {"label": "Cases", "object": "Case", "context_type": "case"},
            {"label": "Deals", "object": "Opportunity", "context_type": "opportunity"},
        ],
        "identity": {"tenant": "Organization ID", "user": "User ID", "record": "Salesforce record ID"},
        # What this platform's typical integration can actually tell us in
        # mode="observe" -- the capability-registry piece: Ask CostPilot
        # and the reporting layer should trust "reported" fields as real
        # facts and treat "estimated" fields as CostPilot's own best guess,
        # never assume every connector knows everything a customer's own
        # code integration (see "custom" below) would.
        "reports": {
            "model_name": "reported", "input_tokens": "reported",
            "output_tokens": "reported", "cost_usd": "estimated",
        },
    },
    "servicenow": {
        "key": "servicenow",
        "label": "ServiceNow",
        "category": "business_platform",
        "authentication": {"type": "oauth_rest_message", "customer_label": "Connect ServiceNow"},
        "installation": ["Flow Designer action", "REST Message"],
        "modes": {"control": "available", "observe": "available"},
        "objects": ["sn_customerservice_case", "incident", "pm_project", "sc_request", "problem", "change_request", "task"],
        "recommended_work": [
            {"label": "Cases", "object": "sn_customerservice_case", "context_type": "case"},
            {"label": "Incidents", "object": "incident", "context_type": "case"},
            {"label": "Projects", "object": "pm_project", "context_type": "project"},
        ],
        "identity": {"tenant": "Instance name", "user": "User sys_id", "record": "Record sys_id"},
        "reports": {
            "model_name": "reported", "input_tokens": "reported",
            "output_tokens": "reported", "cost_usd": "estimated",
        },
    },
    "hubspot": {
        "key": "hubspot",
        "label": "HubSpot",
        "category": "business_platform",
        "authentication": {"type": "oauth_private_app", "customer_label": "Connect HubSpot"},
        "installation": ["Workflow custom code action", "Webhook"],
        "modes": {"control": "available", "observe": "available"},
        "objects": ["contacts", "deals", "tickets", "companies", "tasks"],
        "recommended_work": [
            {"label": "Deals", "object": "deals", "context_type": "opportunity"},
            {"label": "Tickets", "object": "tickets", "context_type": "ticket"},
            {"label": "Customers", "object": "companies", "context_type": "customer"},
        ],
        "identity": {"tenant": "Portal ID", "user": "User ID when available", "record": "Object ID"},
        "reports": {
            "model_name": "reported", "input_tokens": "reported",
            "output_tokens": "reported", "cost_usd": "estimated",
        },
    },
    "custom": {
        "key": "custom",
        "label": "Custom API",
        "category": "code_api",
        "authentication": {"type": "api_key", "customer_label": "Create API credential"},
        "installation": ["REST API", "Python", "Node.js", "Java", "Ruby"],
        "modes": {"control": "available", "observe": "available"},
        "objects": [],
        "recommended_work": [],
        "identity": {"tenant": "Workspace ID", "user": "External user ID", "record": "External work ID"},
        # A system with its own AI provider account (its own OpenAI/Anthropic
        # key) always knows its own real cost -- unlike a business platform
        # whose native AI feature may not surface dollar cost to us at all.
        "reports": {
            "model_name": "reported", "input_tokens": "reported",
            "output_tokens": "reported", "cost_usd": "reported",
        },
    },
}


@router.get("/connectors")
def list_connector_manifests():
    return {"contract_version": CONTRACT_VERSION, "connectors": list(CONNECTOR_MANIFESTS.values())}


@router.get("/connectors/{connector_key}")
def get_connector_manifest(connector_key: str):
    manifest = CONNECTOR_MANIFESTS.get(connector_key.strip().lower())
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Connector '{connector_key}' was not found")
    return {"contract_version": CONTRACT_VERSION, **manifest}


@router.get("/contract")
def get_connector_contract():
    return {
        "contract_version": CONTRACT_VERSION,
        "endpoint": "/api/route",
        "backward_compatible": True,
        "modes": {
            "control": "CostPilot prunes, selects a model, places the call itself, and records the result.",
            "observe": (
                "Report a call your own system already made -- your own model choice, your own "
                "provider key. No prompt is sent or pruned; you're telling CostPilot what happened, "
                "not asking it to happen."
            ),
        },
        "control": {
            "required": ["source.platform", "source.workspace_id", "request.content"],
            "optional": [
                "source.agent_name",
                "source.department",
                "source.agent_department",
                "source.charged_department",
                "actor",
                "actor.department",
                "work",
                "work.department",
                "request.task",
            ],
            "example": {
                "contract_version": CONTRACT_VERSION,
                "mode": "control",
                "source": {
                    "platform": "salesforce",
                    "workspace_id": "00D000000000001",
                    "agent_name": "Renewal Assistant",
                    "department": "Sales",
                    "agent_department": "Revenue Operations",
                },
                "actor": {
                    "external_id": "005000000000001",
                    "name": "David Chen",
                    "email": "david@example.com",
                    "department": "Enterprise Sales",
                },
                "work": {
                    "external_id": "006000000000001",
                    "type": "Opportunity",
                    "name": "Acme Renewal",
                    "department": "Strategic Accounts",
                    "sync_if_missing": True,
                },
                "request": {
                    "task": "Summarize renewal risks",
                    "content": "Customer renewal context...",
                    "payload_type": "text",
                    "auto_prune": True,
                },
            },
        },
        "observe": {
            "required": ["source.platform", "source.workspace_id", "usage.model_name",
                         "usage.input_tokens", "usage.output_tokens"],
            "optional": [
                "source.agent_name",
                "source.department",
                "actor",
                "work",
                "usage.cost_usd",
                "usage.occurred_at",
            ],
            "example": {
                "contract_version": CONTRACT_VERSION,
                "mode": "observe",
                "source": {"platform": "Acme Support Tool", "workspace_id": "acme-prod"},
                "actor": {
                    "external_id": "emp-4471",
                    "name": "Jamie Lee",
                    "department": "Customer Support",
                },
                "work": {
                    "external_id": "TICKET-8842",
                    "type": "ticket",
                    "name": "Refund request escalation",
                },
                "usage": {
                    "model_name": "gpt-4o-mini",
                    "input_tokens": 1200,
                    "output_tokens": 340,
                    "cost_usd": 0.0021,
                },
            },
        },
        # Backward-compatible top-level aliases for the control-mode fields,
        # since existing connectors (Salesforce, ServiceNow, HubSpot) were
        # built against this contract before "observe" existed.
        "required": ["source.platform", "source.workspace_id", "request.content"],
        "optional": [
            "source.agent_name",
            "source.department",
            "source.agent_department",
            "source.charged_department",
            "actor",
            "actor.department",
            "work",
            "work.department",
            "request.task",
        ],
        "example": {
            "contract_version": CONTRACT_VERSION,
            "mode": "control",
            "source": {
                "platform": "salesforce",
                "workspace_id": "00D000000000001",
                "agent_name": "Renewal Assistant",
                "department": "Sales",
                "agent_department": "Revenue Operations",
            },
            "actor": {
                "external_id": "005000000000001",
                "name": "David Chen",
                "email": "david@example.com",
                "department": "Enterprise Sales",
            },
            "work": {
                "external_id": "006000000000001",
                "type": "Opportunity",
                "name": "Acme Renewal",
                "department": "Strategic Accounts",
                "sync_if_missing": True,
            },
            "request": {
                "task": "Summarize renewal risks",
                "content": "Customer renewal context...",
                "payload_type": "text",
                "auto_prune": True,
            },
        },
    }
