import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_connections import (
    AiEntryPointSelectionUpdate,
    ConnectionCreate,
    MappingUpdate,
    _merge_salesforce_package_connection,
    _new_salesforce_workspace,
    _populate_salesforce_costpilot_credential,
    _servicenow_auth_base,
    approve_mapping,
    create_connection,
    list_connections,
    recommend_business_mapping,
    recommend_child_relationships,
    save_salesforce_ai_entry_points,
    salesforce_package_status,
)
from fastapi import HTTPException
from database.db import Base
from database.models import IntegrationConnection


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
    ]
    suggestions = recommend_child_relationships(relationships)

    assert [item["object"] for item in suggestions] == ["Project_Task__c", "Opportunity"]
    assert suggestions[0]["parent_field"] == "Project__c"
    assert suggestions[0]["recommended_behavior"] == "track_and_rollup"
    assert all(item["confidence"] == "high" for item in suggestions)


def test_approved_mapping_is_persisted_as_active_connection():
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
    assert updated["status"] == "active"
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
