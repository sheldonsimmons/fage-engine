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


def test_observe_mode_is_explicitly_rejected_until_ingestion_exists():
    payload = _universal_payload("HubSpot", "PORTAL-1", "DEAL-1")
    payload["mode"] = "observe"
    req = RouteRequest(**payload)
    try:
        _normalize_universal_request(req)
        assert False, "Observe mode must not silently route a control request"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Observe-mode ingestion is not available yet" in str(exc.detail)
