from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_connections import (
    ConnectionCreate,
    MappingUpdate,
    approve_mapping,
    create_connection,
    list_connections,
    recommend_business_mapping,
)
from fastapi import HTTPException
from database.db import Base


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
            },
        ),
        db=db,
    )
    assert updated["status"] == "active"
    assert updated["selected_object"] == "CostPilot_Project__c"
    assert updated["mapping"]["content"] == "Description__c"


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
