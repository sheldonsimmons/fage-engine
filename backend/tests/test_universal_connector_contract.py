from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_integrations import (
    CONTRACT_VERSION,
    get_connector_contract,
    get_connector_manifest,
    list_connector_manifests,
)
from api.routes_router import (
    RouteRequest,
    _normalize_universal_request,
    _resolve_work_item,
    route_payload,
)
from database.db import Base
from database.models import WorkItem


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _universal_payload(platform: str, workspace_id: str, record_id: str):
    return {
        "contract_version": CONTRACT_VERSION,
        "mode": "control",
        "source": {
            "platform": platform,
            "workspace_id": workspace_id,
            "agent_name": "Renewal Assistant",
            "department": "Sales",
        },
        "actor": {
            "external_id": "USER-101",
            "name": "David Chen",
            "email": "david@example.com",
        },
        "work": {
            "external_id": record_id,
            "type": "Opportunity",
            "name": "Acme Renewal",
            "sync_if_missing": True,
        },
        "request": {
            "task": "Summarize renewal risks",
            "content": "Customer renewal context",
            "payload_type": "text",
            "auto_prune": True,
        },
    }


def test_connector_catalog_exposes_one_versioned_contract():
    catalog = list_connector_manifests()
    assert catalog["contract_version"] == CONTRACT_VERSION
    assert {item["key"] for item in catalog["connectors"]} == {
        "salesforce",
        "servicenow",
        "hubspot",
        "custom",
    }
    assert all(item["modes"]["control"] == "available" for item in catalog["connectors"])
    assert get_connector_manifest("Salesforce")["authentication"]["type"] == "oauth_named_credential"
    assert get_connector_contract()["backward_compatible"] is True


def test_universal_platforms_normalize_to_identical_routing_fields():
    normalized = []
    for platform in ("Salesforce", "ServiceNow", "HubSpot"):
        req = RouteRequest(**_universal_payload(platform, f"{platform}-TENANT", "WORK-101"))
        normalized.append(_normalize_universal_request(req))

    for req, platform in zip(normalized, ("Salesforce", "ServiceNow", "HubSpot")):
        assert req.text == "Customer renewal context"
        assert req.department == "Sales"
        assert req.agent_name == "Renewal Assistant"
        assert req.source_platform == platform
        assert req.actor_external_id == "USER-101"
        assert req.actor_name == "David Chen"
        assert req.actor_workspace_id == f"{platform}-TENANT"
        assert req.payload_type == "text"
        assert req.auto_prune is True


def test_universal_work_records_are_tenant_scoped_and_synced():
    db = _session()
    first = RouteRequest(**_universal_payload("Salesforce", "ORG-A", "006-SAME"))
    second = RouteRequest(**_universal_payload("Salesforce", "ORG-B", "006-SAME"))
    _normalize_universal_request(first)
    _normalize_universal_request(second)

    item_a = _resolve_work_item(db, first, "Sales")
    item_b = _resolve_work_item(db, second, "Sales")

    assert item_a.id != item_b.id
    assert item_a.source_record_id == item_b.source_record_id == "006-SAME"
    assert item_a.workspace_id == "ORG-A"
    assert item_b.workspace_id == "ORG-B"
    assert item_a.name == item_b.name == "Acme Renewal"
    assert db.query(WorkItem).count() == 2

    same_item = _resolve_work_item(db, first, "Sales")
    assert same_item.id == item_a.id
    assert db.query(WorkItem).count() == 2


def test_work_source_identity_is_stable_across_agent_platforms_and_reruns():
    db = _session()
    first = _universal_payload("Salesforce", "SIM-WORKSPACE", "NORTHSTAR-AUDIT")
    second = _universal_payload("Microsoft Teams", "SIM-WORKSPACE", "NORTHSTAR-AUDIT")
    first["work"]["source_platform"] = "CostPilot Simulator"
    second["work"]["source_platform"] = "CostPilot Simulator"

    first_request = RouteRequest(**first)
    second_request = RouteRequest(**second)
    _normalize_universal_request(first_request)
    _normalize_universal_request(second_request)

    item_a = _resolve_work_item(db, first_request, "Legal")
    item_b = _resolve_work_item(db, second_request, "Legal")
    rerun_item = _resolve_work_item(db, first_request, "Legal")

    assert item_a.id == item_b.id == rerun_item.id
    assert item_a.source_platform == "CostPilot Simulator"
    assert item_a.source_record_id == "NORTHSTAR-AUDIT"
    assert db.query(WorkItem).count() == 1


def test_platform_native_record_type_is_preserved_with_safe_custom_category():
    db = _session()
    request = RouteRequest(
        **_universal_payload("ServiceNow", "dev412335", "SN-CHANGE-1")
    )
    request.work_context.type = "change_request"
    request.work_context.name = "CHG0001001"
    _normalize_universal_request(request)

    item = _resolve_work_item(db, request, "Operations")

    assert item.context_type == "custom"
    assert item.source_record_type == "change_request"
    assert item.source_record_id == "SN-CHANGE-1"
    assert item.name == "CHG0001001"


def test_legacy_flat_request_remains_backward_compatible():
    req = RouteRequest(
        text="Existing integration payload",
        department="Finance",
        agent_name="Existing Agent",
        source_platform="Custom",
    )
    normalized = _normalize_universal_request(req)
    assert normalized.text == "Existing integration payload"
    assert normalized.department == "Finance"
    assert normalized.agent_name == "Existing Agent"
    assert normalized.source_platform == "Custom"
    assert normalized.work_context is None


def test_universal_and_legacy_requests_use_the_same_routing_pipeline():
    db = _session()
    legacy = route_payload(
        RouteRequest(
            text="Summarize routine customer activity",
            department="Sales",
            auto_prune=True,
            is_test=True,
        ),
        db=db,
    )
    payload = _universal_payload("Salesforce", "ORG-PARITY", "WORK-PARITY")
    payload.pop("actor")
    payload.pop("work")
    payload["source"]["agent_name"] = None
    payload["request"]["content"] = "Summarize routine customer activity"
    universal_request = RouteRequest(**payload)
    universal_request.is_test = True
    universal = route_payload(universal_request, db=db)

    assert universal.model_tier == legacy.model_tier
    assert universal.model_name == legacy.model_name
    assert universal.routing_decision == legacy.routing_decision
    assert universal.input_tokens == legacy.input_tokens
    assert universal.output_tokens == legacy.output_tokens
    assert universal.cost_usd == legacy.cost_usd
    assert universal.was_pruned == legacy.was_pruned
    assert universal.tokens_saved_by_pruning == legacy.tokens_saved_by_pruning


def _observe_payload(platform: str, workspace_id: str, **usage_overrides):
    usage = {
        "model_name": "gpt-4o-mini",
        "input_tokens": 1200,
        "output_tokens": 340,
        "cost_usd": 0.0021,
    }
    usage.update(usage_overrides)
    return {
        "contract_version": CONTRACT_VERSION,
        "mode": "observe",
        "source": {"platform": platform, "workspace_id": workspace_id},
        "actor": {"external_id": "emp-4471", "name": "Jamie Lee", "department": "Customer Support"},
        "work": {
            "external_id": "TICKET-8842", "type": "ticket",
            "name": "Refund request escalation", "sync_if_missing": True,
        },
        "usage": usage,
    }


def test_observe_mode_requires_a_usage_block():
    payload = _observe_payload("Acme Support Tool", "acme-prod")
    payload.pop("usage")
    req = RouteRequest(**payload)
    try:
        _normalize_universal_request(req)
        assert False, "observe mode without a usage block must be rejected"
    except HTTPException as exc:
        assert exc.status_code == 422


def test_observe_mode_does_not_require_prompt_content():
    # Unlike control mode, observe mode has no prompt to prune -- there is
    # nothing analogous to request.content, and none should be required.
    req = RouteRequest(**_observe_payload("Acme Support Tool", "acme-prod"))
    normalized = _normalize_universal_request(req)
    assert not (normalized.text or "").strip()


def test_observe_mode_records_a_token_transaction_with_reported_cost():
    from database.models import TokenTransaction

    db = _session()
    req = RouteRequest(**_observe_payload("Acme Support Tool", "acme-prod"))
    response = route_payload(req, db=db)

    assert response.model_name == "gpt-4o-mini"
    assert response.input_tokens == 1200
    assert response.output_tokens == 340
    assert response.cost_usd == 0.0021
    assert response.routing_decision == "OBSERVED"

    tx = db.query(TokenTransaction).one()
    assert tx.model_name == "gpt-4o-mini"
    assert tx.usage_source == "provider_reported"
    assert tx.is_simulation is False
    assert tx.cost_usd == 0.0021
    assert tx.actor_name == "Jamie Lee"
    assert tx.department == "Customer Support"


def test_observe_mode_falls_back_to_registry_pricing_when_cost_omitted():
    from database.models import ModelRegistry, TokenTransaction

    db = _session()
    db.add(ModelRegistry(
        display_name="Test Model", model_id="test-model-x", provider="OpenAI",
        tier=1, cost_input_per_1m=3.0, cost_output_per_1m=15.0, is_enabled=True,
    ))
    db.commit()

    payload = _observe_payload("Acme Support Tool", "acme-prod", model_name="test-model-x")
    payload["usage"].pop("cost_usd")
    req = RouteRequest(**payload)
    response = route_payload(req, db=db)

    expected_cost = round(1200 * 3.0 / 1_000_000 + 340 * 15.0 / 1_000_000, 6)
    assert response.cost_usd == expected_cost
    assert db.query(TokenTransaction).one().cost_usd == expected_cost


def test_observe_mode_resolves_work_item_and_department_like_control_mode():
    from database.models import WorkItem

    db = _session()
    req = RouteRequest(**_observe_payload("Acme Support Tool", "acme-prod"))
    route_payload(req, db=db)

    item = db.query(WorkItem).filter(WorkItem.source_record_id == "TICKET-8842").first()
    assert item is not None
    assert item.name == "Refund request escalation"


def test_observe_mode_updates_department_budget_spend():
    from database.models import DepartmentBudget

    db = _session()
    db.add(DepartmentBudget(department="Customer Support", monthly_cap_usd=100.0, current_spend_usd=0.0))
    db.commit()

    req = RouteRequest(**_observe_payload("Acme Support Tool", "acme-prod"))
    route_payload(req, db=db)

    budget = db.query(DepartmentBudget).filter_by(department="Customer Support").first()
    assert budget.current_spend_usd == 0.0021


def test_connector_contract_documents_observe_mode_with_its_own_example():
    contract = get_connector_contract()
    assert "usage.model_name" in contract["observe"]["required"]
    assert contract["observe"]["example"]["mode"] == "observe"
    assert contract["observe"]["example"]["usage"]["model_name"]


def test_connector_manifests_report_observe_as_available():
    catalog = list_connector_manifests()
    assert all(item["modes"]["observe"] == "available" for item in catalog["connectors"])
    assert all("reports" in item for item in catalog["connectors"])
