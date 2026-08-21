import asyncio
import json

import api.routes_connections as connection_routes
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_connections import (
    AiEntryPointSelectionUpdate,
    ConnectionCreate,
    MappingUpdate,
    PackageRelationshipApproval,
    _build_context_snapshot,
    _merge_salesforce_package_connection,
    _merge_salesforce_org_connection,
    _new_context_changes,
    _new_salesforce_workspace,
    _populate_salesforce_costpilot_credential,
    _salesforce_package_install_error,
    _salesforce_oauth_error_reason,
    _servicenow_auth_base,
    approve_mapping,
    approve_salesforce_package_relationships,
    activate_salesforce_package_connection,
    create_connection,
    discover_salesforce_ai_entry_points,
    install_salesforce_package,
    list_connections,
    recommend_business_mapping,
    recommend_child_relationships,
    save_salesforce_ai_entry_points,
    salesforce_package_status,
    verify_salesforce_package_request,
)
from fastapi import HTTPException
from database.db import Base
from database.models import AuditEvent, IntegrationConnection


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_salesforce_field_metadata_produces_explainable_recommendations():
    fields = [
        {"name": "Id", "label": "Record ID", "type": "id"},
        {"name": "Name", "label": "Project Name", "type": "string"},
        {"name": "OwnerId", "label": "Project Owner", "type": "reference"},
        {"name": "Account__c", "label": "Customer Account", "type": "reference"},
        {"name": "Status__c", "label": "Status", "type": "picklist"},
        {"name": "Description__c", "label": "Project Description", "type": "textarea"},
    ]
    mapping = recommend_business_mapping(fields)
    assert mapping["work_id"]["field"] == "Id"
    assert mapping["work_name"]["field"] == "Name"
    assert mapping["owner"]["field"] == "OwnerId"
    assert mapping["customer"]["field"] == "Account__c"
    assert mapping["status"]["field"] == "Status__c"
    assert mapping["content"]["field"] == "Description__c"
    assert all(value["confidence"] in {"high", "medium"} for value in mapping.values())


def test_salesforce_package_compile_failure_never_tells_customer_to_fix_code():
    message = _salesforce_package_install_error({
        "Errors": [{"message": "Apex compile failure in ExperienceControllerTest"}],
    })
    assert "CostPilot did not change that code" in message
    assert "no CostPilot user should edit unrelated code" in message


def test_salesforce_package_install_error_handles_bare_rest_error_array():
    # Salesforce's standard REST/Tooling error shape for validation failures
    # is a bare JSON array of error records, not an {"Errors": [...]} object --
    # this crashed in production with AttributeError: 'list' object has no
    # attribute 'get' before payload-shape detection was added.
    message = _salesforce_package_install_error([
        {"message": "Invalid SubscriberPackageVersionKey", "errorCode": "MALFORMED_ID"},
    ])
    assert "Invalid SubscriberPackageVersionKey" in message


def test_salesforce_package_install_error_handles_bare_string_list():
    message = _salesforce_package_install_error(["Something went wrong"])
    assert "Something went wrong" in message


def test_salesforce_missing_external_app_has_dedicated_customer_reason():
    assert _salesforce_oauth_error_reason(
        "OAUTH_EC_APP_NOT_FOUND",
        "External client app is not installed in this org",
    ) == "package_required"
    assert _salesforce_oauth_error_reason("access_denied", "User denied access") == "oauth_denied"


def test_salesforce_package_install_compiles_only_costpilot_apex(monkeypatch):
    db = _session()
    item = IntegrationConnection(
        workspace_id="ORG-A",
        platform="salesforce",
        display_name="Salesforce",
        status="connected",
        instance_url="https://example.my.salesforce.com",
        access_token_encrypted="encrypted",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    captured = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {"id": "0Hf000000000001AAA", "success": True}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(
        connection_routes,
        "_require_connected_salesforce",
        lambda _item: ("https://example.my.salesforce.com", "token"),
    )
    monkeypatch.setattr(connection_routes.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(install_salesforce_package(item.id, db=db))

    assert result["status"] == "in_progress"
    assert captured["json"]["ApexCompileType"] == "package"
    assert captured["json"]["SubscriberPackageVersionKey"] == "04tfj000000PZSPAA4"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_context_discovery_baseline_does_not_create_false_changes():
    snapshot = _build_context_snapshot(
        [{"name": "Account", "label": "Account", "custom": False}],
        "Account",
        [],
    )
    assert _new_context_changes(snapshot, snapshot) == []


def test_context_discovery_detects_new_objects_and_parent_relationships():
    previous = _build_context_snapshot(
        [{"name": "Account", "label": "Account", "custom": False}],
        "Account",
        [],
    )
    current = _build_context_snapshot(
        [
            {"name": "Account", "label": "Account", "custom": False},
            {"name": "Delivery__c", "label": "Delivery", "custom": True},
        ],
        "Account",
        [{
            "object": "Delivery__c",
            "label": "Deliveries",
            "parent_field": "Account__c",
            "relationship_name": "Deliveries__r",
            "recommended_behavior": "track_and_rollup",
        }],
    )
    changes = _new_context_changes(previous, current)
    assert {change["kind"] for change in changes} == {"object_added", "relationship_added"}
    assert all(change["status"] == "pending" for change in changes)
    assert len({change["id"] for change in changes}) == 2


def test_connection_registry_is_workspace_scoped_and_never_returns_tokens():
    db = _session()
    item = create_connection(
        ConnectionCreate(
            workspace_id="ORG-A",
            platform="salesforce",
            display_name="Salesforce Production",
        ),
        db=db,
    )
    assert item["status"] == "draft"
    assert item["configured"] is False
    assert "access_token" not in item

    other = create_connection(
        ConnectionCreate(
            workspace_id="ORG-B",
            platform="salesforce",
            display_name="Salesforce Production",
        ),
        db=db,
    )
    assert other["id"] != item["id"]
    assert len(list_connections("ORG-A", db=db)["connections"]) == 1
    assert len(list_connections("ORG-B", db=db)["connections"]) == 1


def test_salesforce_package_setup_creates_and_reuses_a_workspace():
    db = _session()
    identity = {
        "email": "admin@acme.example",
        "display_name": "Acme Admin",
        "organization_id": "00D000000000001AAA",
    }

    first = _new_salesforce_workspace(identity, db)
    db.commit()
    second = _new_salesforce_workspace(identity, db)

    assert first.id == second.id
    assert first.workspace_id == second.workspace_id
    assert first.secret_key.startswith("sk-cp-")
    assert first.platform == "salesforce"


def test_salesforce_package_reconnect_merges_the_pending_connection():
    db = _session()
    display_name = "Salesforce package setup 00D000000000001AAA"
    existing = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name=display_name,
        status="connected",
        access_token_encrypted="old-access",
        refresh_token_encrypted="old-refresh",
    )
    pending = IntegrationConnection(
        workspace_id="pending-reconnect",
        platform="salesforce",
        display_name=display_name,
        status="authorizing",
        auth_base_url="https://login.salesforce.com",
        instance_url="https://acme.my.salesforce.com",
        external_tenant_id="00D000000000001AAA",
        access_token_encrypted="new-access",
        refresh_token_encrypted="new-refresh",
        mapping_json='{"package_setup": true}',
    )
    db.add_all([existing, pending])
    db.commit()

    merged = _merge_salesforce_package_connection(pending, "WORKSPACE-A", db)
    merged.status = "connected"
    db.commit()

    rows = db.query(IntegrationConnection).all()
    assert len(rows) == 1
    assert merged.id == existing.id
    assert merged.workspace_id == "WORKSPACE-A"
    assert merged.access_token_encrypted == "new-access"
    assert merged.refresh_token_encrypted == "new-refresh"
    assert merged.instance_url == "https://acme.my.salesforce.com"


def test_salesforce_org_reconnect_reuses_the_active_connection():
    db = _session()
    active = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce original",
        status="active",
        instance_url="https://acme.my.salesforce.com",
        external_tenant_id="00D000000000001AAA",
        access_token_encrypted="old-token",
        mapping_json=json.dumps({"package_setup": {"active": True}}),
    )
    pending = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce reconnect",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        external_tenant_id="00D000000000001AAA",
        access_token_encrypted="fresh-token",
        mapping_json=json.dumps({"salesforce_identity": {"username": "admin@acme.example"}}),
    )
    db.add_all([active, pending])
    db.commit()

    canonical = _merge_salesforce_org_connection(pending, db)
    db.commit()

    assert canonical.id == active.id
    assert canonical.access_token_encrypted == "fresh-token"
    assert json.loads(canonical.mapping_json)["package_setup"]["active"] is True
    assert pending.status == "superseded"
    assert pending.access_token_encrypted is None
    assert json.loads(pending.mapping_json)["superseded_by_connection_id"] == active.id


def test_salesforce_package_status_prefers_a_working_connection():
    db = _session()
    display_name = "Salesforce package setup 00D000000000001AAA"
    db.add_all([
        IntegrationConnection(
            workspace_id="WORKSPACE-A",
            platform="salesforce",
            display_name=display_name,
            status="connected",
        ),
        IntegrationConnection(
            workspace_id="pending-reconnect",
            platform="salesforce",
            display_name=display_name,
            status="authorizing",
        ),
    ])
    db.commit()

    status = asyncio.run(
        salesforce_package_status("00D000000000001AAA", db=db)
    )

    assert status["connected"] is True
    assert status["status"] == "connected"
    assert status["workspace_id"] == "WORKSPACE-A"
    assert status["connection_id"] is not None


def test_salesforce_package_saves_selected_agents_and_flows():
    db = _session()
    item = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce package setup 00D000000000001AAA",
        status="connected",
        mapping_json='{"package_setup": true}',
    )
    db.add(item)
    db.commit()

    result = save_salesforce_ai_entry_points(
        item.id,
        AiEntryPointSelectionUpdate(
            entries=[
                {
                    "kind": "agent",
                    "id": "0Xx000000000001",
                    "name": "Sales_Assistant",
                    "label": "Sales Assistant",
                },
                {
                    "kind": "flow",
                    "id": "300000000000001",
                    "name": "Draft_Follow_Up",
                    "label": "Draft Follow Up",
                },
            ]
        ),
        db=db,
    )

    db.refresh(item)
    mapping = json.loads(item.mapping_json)
    assert result["count"] == 2
    assert mapping["package_setup"] is True
    assert [entry["kind"] for entry in mapping["selected_ai_entry_points"]] == [
        "agent",
        "flow",
    ]


def test_salesforce_entry_point_save_is_idempotent_and_deduplicates_agents():
    db = _session()
    item = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce Production",
        status="connected",
        mapping_json=json.dumps({
            "package_setup": {"verification": {"verified": True}},
            "selected_ai_entry_points": [{
                "kind": "agent", "id": "agent-1", "name": "Quoting_Agent",
                "label": "CostPilot Agent", "activation_status": "action_required",
            }],
            "entry_points_selected_at": "2026-08-03T22:50:05.158597",
        }),
    )
    db.add(item)
    db.commit()

    result = save_salesforce_ai_entry_points(
        item.id,
        AiEntryPointSelectionUpdate(entries=[
            {"kind": "agent", "id": "agent-1", "name": "Quoting_Agent", "label": "CostPilot Agent"},
            {"kind": "agent", "id": "manual:Quoting_Agent", "name": "Quoting_Agent", "label": "Quoting Agent"},
            {"kind": "agent", "id": "", "name": "Quoting_Agent", "label": "Quoting Agent"},
        ]),
        db=db,
    )

    db.refresh(item)
    mapping = json.loads(item.mapping_json)
    assert result["count"] == 1
    assert result["selected"][0]["id"] == "agent-1"
    assert mapping["entry_points_selected_at"] == "2026-08-03T22:50:05.158597"
    assert mapping["package_setup"]["verification"]["verified"] is True

    save_salesforce_ai_entry_points(
        item.id,
        AiEntryPointSelectionUpdate(entries=[
            {"kind": "flow", "id": "flow-2", "name": "Quote_Flow", "label": "Quote Flow"},
        ]),
        db=db,
    )
    db.refresh(item)
    changed = json.loads(item.mapping_json)
    assert changed["package_setup"]["verification"]["verified"] is False
    assert changed["entry_points_selected_at"] != "2026-08-03T22:50:05.158597"


def _ready_salesforce_connection(db):
    item = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce package setup 00D000000000001AAA",
        status="connected",
        instance_url="https://acme.my.salesforce.com",
        external_tenant_id="00D000000000001AAA",
        mapping_json=json.dumps({
            "package_setup": {},
            "selected_ai_entry_points": [{
                "kind": "agent", "id": "agent-1", "name": "Sales Agent", "label": "Sales Agent",
            }],
        }),
    )
    db.add(item)
    db.commit()
    approve_salesforce_package_relationships(
        item.id,
        PackageRelationshipApproval(
            parent_object="Account",
            children=[
                {
                    "object_name": "Account",
                    "parent_field": "ParentId",
                    "behavior": "track_and_rollup",
                },
                {
                    "object_name": "Case",
                    "parent_field": "AccountId",
                    "behavior": "track_and_rollup",
                },
            ],
        ),
        db=db,
    )
    return item


def test_salesforce_package_requires_parent_and_related_requests_on_same_work():
    db = _session()
    item = _ready_salesforce_connection(db)
    db.add(AuditEvent(
        event_type="ROUTING",
        department="Sales",
        workspace_id="WORKSPACE-A",
        actor_source_platform="salesforce",
        origin_record_type="Account",
        origin_record_name="University of Arizona",
        work_item_id=41,
        is_simulation=False,
    ))
    db.commit()

    parent_only = verify_salesforce_package_request(item.id, db=db)
    assert parent_only["verification"]["parent_verified"] is True
    assert parent_only["verification"]["child_verified"] is False
    assert parent_only["ready_to_activate"] is False
    try:
        activate_salesforce_package_connection(item.id, db=db)
    except HTTPException as error:
        assert error.status_code == 409
        assert "related record" in error.detail
    else:
        raise AssertionError("Activation should require a related-record request")

    db.add(AuditEvent(
        event_type="ROUTING",
        department="Support",
        workspace_id="WORKSPACE-A",
        actor_source_platform="salesforce",
        origin_record_type="Case",
        origin_record_name="Repeated motor breakdown",
        work_item_id=41,
        is_simulation=False,
    ))
    db.commit()

    verified = verify_salesforce_package_request(item.id, db=db)
    assert verified["verification"]["verified"] is True
    assert verified["checklist"]["parent_request_verified"] is True
    assert verified["checklist"]["child_request_verified"] is True
    assert verified["ready_to_activate"] is True
    active = activate_salesforce_package_connection(item.id, db=db)
    assert active["active"] is True
    assert active["connection_status"] == "active"


def test_salesforce_package_does_not_accept_unrelated_child_rollup():
    db = _session()
    item = _ready_salesforce_connection(db)
    db.add_all([
        AuditEvent(
            event_type="ROUTING", department="Sales", workspace_id="WORKSPACE-A",
            actor_source_platform="salesforce", origin_record_type="Account",
            origin_record_name="University of Arizona", work_item_id=41, is_simulation=False,
        ),
        AuditEvent(
            event_type="ROUTING", department="Support", workspace_id="WORKSPACE-A",
            actor_source_platform="salesforce", origin_record_type="Case",
            origin_record_name="Unrelated case", work_item_id=99, is_simulation=False,
        ),
    ])
    db.commit()

    result = verify_salesforce_package_request(item.id, db=db)
    assert result["verification"]["parent_verified"] is True
    assert result["verification"]["child_verified"] is False
    assert result["ready_to_activate"] is False


def test_salesforce_activation_supersedes_only_same_org_duplicates():
    db = _session()
    item = _ready_salesforce_connection(db)
    mapping = json.loads(item.mapping_json)
    mapping["package_setup"]["verification"] = {
        "verified": True,
        "parent_verified": True,
        "child_verified": True,
    }
    item.mapping_json = json.dumps(mapping)
    duplicate = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Older same org",
        status="mapping",
        external_tenant_id="00D000000000001AAA",
        access_token_encrypted="duplicate-token",
        mapping_json="{}",
    )
    other_org = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Different org",
        status="connected",
        external_tenant_id="00D000000000002AAA",
        access_token_encrypted="other-token",
        mapping_json="{}",
    )
    db.add_all([duplicate, other_org])
    db.commit()

    result = activate_salesforce_package_connection(item.id, db=db)
    db.refresh(duplicate)
    db.refresh(other_org)

    assert result["active"] is True
    assert duplicate.status == "superseded"
    assert duplicate.access_token_encrypted is None
    assert other_org.status == "connected"
    visible_ids = {row["id"] for row in list_connections("WORKSPACE-A", db=db)["connections"]}
    assert duplicate.id not in visible_ids
    assert item.id in visible_ids
    assert other_org.id in visible_ids


def test_salesforce_ai_entry_points_use_current_agentforce_metadata(monkeypatch):
    db = _session()
    item = IntegrationConnection(
        workspace_id="WORKSPACE-A",
        platform="salesforce",
        display_name="Salesforce Production",
        status="connected",
        mapping_json="{}",
    )
    db.add(item)
    db.commit()
    queries = []

    async def fake_query(_item, query, *, tooling=False):
        queries.append((query, tooling))
        if "GenAiPlannerDefinition" in query:
            return [
                {
                    "Id": "16j000000000001",
                    "DeveloperName": "CostPilot_Test_Agent",
                    "MasterLabel": "CostPilot Test Agent",
                    "PlannerType": "AiCopilot__ReAct",
                }
            ], None
        if "FlowDefinition" in query:
            return [
                {
                    "Id": "300000000000001",
                    "DeveloperName": "Draft_Follow_Up",
                    "MasterLabel": None,
                    "ActiveVersionId": "301000000000001",
                }
            ], None
        raise AssertionError(f"Unexpected query: {query}")

    monkeypatch.setattr("api.routes_connections._salesforce_try_query", fake_query)
    result = asyncio.run(discover_salesforce_ai_entry_points(item.id, db=db))

    assert result["agents"][0]["label"] == "CostPilot Test Agent"
    assert result["agents"][0]["planner_type"] == "AiCopilot__ReAct"
    assert result["flows"][0]["label"] == "Draft_Follow_Up"
    assert result["flows"][0]["status"] == "active"
    assert result["warnings"] == []
    assert all(tooling is True for _, tooling in queries)
    assert "GenAiPlannerDefinition" in queries[0][0]
    assert "FlowDefinition" in queries[1][0]


def test_salesforce_package_setup_populates_the_packaged_named_principal(monkeypatch):
    captured = {}

    class Response:
        status_code = 201

    class Client:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, headers, json):
            captured.update(endpoint=endpoint, headers=headers, json=json)
            return Response()

    monkeypatch.setattr("api.routes_connections.httpx.AsyncClient", Client)
    ok, error = asyncio.run(
        _populate_salesforce_costpilot_credential(
            instance_url="https://acme.my.salesforce.com",
            access_token="salesforce-access-token",
            secret_key="sk-cp-private",
        )
    )

    assert ok is True
    assert error == ""
    assert captured["json"]["externalCredential"] == "CostPilotExternal"
    assert captured["json"]["principalName"] == "CostPilotKey"
    assert captured["json"]["credentials"] == {
        "CostPilotKey": {
            "value": "sk-cp-private",
            "encrypted": True,
        }
    }


def test_salesforce_package_setup_replaces_an_existing_named_principal(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, endpoint, headers, json):
            calls.append(("POST", endpoint, headers, json))
            return Response(409)

        async def put(self, endpoint, headers, json):
            calls.append(("PUT", endpoint, headers, json))
            return Response(200)

    monkeypatch.setattr("api.routes_connections.httpx.AsyncClient", Client)
    ok, error = asyncio.run(
        _populate_salesforce_costpilot_credential(
            instance_url="https://acme.my.salesforce.com",
            access_token="salesforce-access-token",
            secret_key="sk-cp-replacement",
        )
    )

    assert ok is True
    assert error == ""
    assert [call[0] for call in calls] == ["POST", "PUT"]
    assert calls[-1][3]["credentials"]["CostPilotKey"] == {
        "value": "sk-cp-replacement",
        "encrypted": True,
    }


def test_parent_metadata_produces_useful_child_relationship_suggestions():
    relationships = [
        {
            "childSObject": "Opportunity",
            "field": "AccountId",
            "relationshipName": "Opportunities",
            "cascadeDelete": False,
        },
        {
            "childSObject": "Project_Task__c",
            "field": "Project__c",
            "relationshipName": "Project_Tasks__r",
            "cascadeDelete": True,
        },
        {
            "childSObject": "AccountHistory",
            "field": "AccountId",
            "relationshipName": "Histories",
        },
        {
            "childSObject": "Account",
            "field": "ParentId",
            "relationshipName": "ChildAccounts",
        },
        {
            "childSObject": "ContentDocumentLink",
            "field": "LinkedEntityId",
            "relationshipName": "ContentDocumentLinks",
        },
        {
            "childSObject": "ForecastingItem",
            "field": "AccountId",
            "relationshipName": "ForecastingItems",
        },
        {
            "childSObject": "Opportunity",
            "field": "PartnerAccountId",
            "relationshipName": "PartnerOpportunities",
        },
    ]
    suggestions = recommend_child_relationships(relationships, parent_object="Account")

    assert [item["object"] for item in suggestions] == ["Opportunity", "Project_Task__c", "ForecastingItem"]
    assert suggestions[0]["recommendation_reason"] == "Revenue and pipeline activity"
    assert suggestions[0]["parent_field"] == "AccountId"
    assert suggestions[0]["recommended_behavior"] == "track_and_rollup"
    assert suggestions[0]["recommended"] is True
    assert suggestions[1]["recommended"] is True
    assert suggestions[2]["recommended"] is False
    assert suggestions[2]["confidence"] == "low"


def test_approved_salesforce_mapping_remains_in_mapping_status():
    db = _session()
    item = create_connection(
        ConnectionCreate(
            workspace_id="ORG-A",
            platform="salesforce",
            display_name="Salesforce Sandbox",
        ),
        db=db,
    )
    updated = approve_mapping(
        item["id"],
        MappingUpdate(
            selected_object="CostPilot_Project__c",
            mapping={
                "work_id": "Id",
                "work_name": "Name",
                "owner": "OwnerId",
                "content": "Description__c",
                "children": [
                    {
                        "object": "Project_Task__c",
                        "label": "Project Task",
                        "parent_field": "Project__c",
                        "relationship_name": "Project_Tasks__r",
                        "behavior": "track_and_rollup",
                    }
                ],
                "unmapped_behavior": "separate",
            },
        ),
        db=db,
    )
    assert updated["status"] == "mapping"
    assert updated["selected_object"] == "CostPilot_Project__c"
    assert updated["mapping"]["content"] == "Description__c"
    assert updated["mapping"]["parent_object"] == "CostPilot_Project__c"
    assert updated["mapping"]["preserve_origin_record"] is True
    assert updated["mapping"]["children"][0]["object"] == "Project_Task__c"
    assert updated["mapping"]["children"][0]["behavior"] == "track_and_rollup"


def test_salesforce_connection_rejects_non_salesforce_authorization_hosts():
    db = _session()
    try:
        create_connection(
            ConnectionCreate(
                workspace_id="ORG-A",
                platform="salesforce",
                display_name="Unsafe Connection",
                auth_base_url="https://example.com",
            ),
            db=db,
        )
        assert False, "A non-Salesforce OAuth host must be rejected"
    except HTTPException as exc:
        assert exc.status_code == 400


def test_servicenow_connection_accepts_only_https_instance_domains():
    assert (
        _servicenow_auth_base("https://acme-dev.service-now.com/")
        == "https://acme-dev.service-now.com"
    )
    for unsafe_url in (
        "http://acme-dev.service-now.com",
        "https://service-now.com.example.net",
        "https://example.com",
    ):
        try:
            _servicenow_auth_base(unsafe_url)
            assert False, f"{unsafe_url} must not be accepted as a ServiceNow instance"
        except HTTPException as exc:
            assert exc.status_code == 400


def test_servicenow_connection_uses_the_shared_mapping_lifecycle():
    db = _session()
    item = create_connection(
        ConnectionCreate(
            workspace_id="SN-ACME",
            platform="servicenow",
            display_name="ServiceNow Development",
            auth_base_url="https://acme-dev.service-now.com",
        ),
        db=db,
    )
    assert item["platform"] == "servicenow"
    assert item["status"] == "draft"

    updated = approve_mapping(
        item["id"],
        MappingUpdate(
            selected_object="customer_account",
            mapping={
                "work_id": "sys_id",
                "work_name": "name",
                "owner": "assigned_to",
                "children": [
                    {
                        "object": "incident",
                        "label": "Incident",
                        "parent_field": "company",
                        "behavior": "track_and_rollup",
                    }
                ],
            },
        ),
        db=db,
    )
    assert updated["status"] == "active"
    assert updated["mapping"]["parent_object"] == "customer_account"
    assert updated["mapping"]["children"][0]["object"] == "incident"
