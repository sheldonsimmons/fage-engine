import asyncio
from datetime import datetime, timedelta
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_agentforce import (
    AgentforceGovernRequest,
    _apply_approved_relationship_mapping,
    _approved_relationship,
    _resolve_or_create_project,
)
from api.routes_trial import (
    BusinessContextSetupRequest,
    _trial_status_payload,
    save_business_context,
)
from api.routes_work_items import (
    _work_item_json,
    business_context_reporting,
    list_context_templates,
    project_activity_reporting,
)
from core.business_context import get_context_template, normalize_context_type
from database.db import Base
from database.models import (
    TokenTransaction,
    TrialAccount,
    IntegrationConnection,
    RegisteredAgent,
    WorkAccount,
    WorkItem,
    WorkItemSourceLink,
    WorkUser,
)


SALESFORCE_ACCOUNT_MAPPING = {
    "parent_object": "Account",
    "preserve_origin_record": True,
    "unmapped_behavior": "separate",
    "children": [
        {
            "child_object": "Contact",
            "parent_field": "AccountId",
            "behavior": "track_and_rollup",
        },
        {
            "child_object": "Opportunity",
            "parent_field": "AccountId",
            "behavior": "track_and_rollup",
        },
        {
            "child_object": "Case",
            "parent_field": "AccountId",
            "behavior": "track_and_rollup",
        },
    ],
}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_standard_salesforce_relationship_options_are_resolved_case_insensitively():
    for source_type in ("Contact", "opportunity", "CASE"):
        relationship = _approved_relationship(SALESFORCE_ACCOUNT_MAPPING, source_type)
        assert relationship is not None
        assert relationship["parent_field"] == "AccountId"

    separate_mapping = {
        "children": [
            {
                "child_object": "Contact",
                "parent_field": "AccountId",
                "behavior": "separate",
            }
        ]
    }
    assert _approved_relationship(separate_mapping, "Contact")["behavior"] == "separate"


def test_approved_contact_mapping_rolls_up_to_account_and_preserves_origin(monkeypatch):
    db = _session()
    db.add(
        IntegrationConnection(
            workspace_id="WORKSPACE-RELATIONSHIPS",
            platform="salesforce",
            display_name="Salesforce",
            status="connected",
            mapping_json=json.dumps(SALESFORCE_ACCOUNT_MAPPING),
        )
    )
    db.commit()

    requested_paths = []

    async def fake_salesforce_get(_connection, path):
        requested_paths.append(path)
        if path.startswith("sobjects/Contact/"):
            return {"AccountId": "001-ACME"}
        if path == "sobjects/Account/describe":
            return {"fields": [{"name": "Name", "nameField": True}]}
        if path.startswith("sobjects/Account/"):
            return {"Name": "Acme Corporation"}
        raise AssertionError(f"Unexpected Salesforce path: {path}")

    monkeypatch.setattr("api.routes_connections._salesforce_get", fake_salesforce_get)
    body = AgentforceGovernRequest(
        record_id="003-CONTACT-1",
        task_description="Draft a follow-up message",
        project_name="Maria Lopez",
        source_type="Contact",
        source_record_type="Contact",
        department="Sales",
    )

    resolved = asyncio.run(
        _apply_approved_relationship_mapping(db, "WORKSPACE-RELATIONSHIPS", body)
    )

    assert resolved is True
    assert body.project_external_id == "001-ACME"
    assert body.project_name == "Acme Corporation"
    assert body.customer_external_id == "001-ACME"
    assert body.customer_name == "Acme Corporation"
    assert body.source_record_name == "Maria Lopez"
    assert body.context_type == "account"
    assert requested_paths == [
        "sobjects/Contact/003-CONTACT-1?fields=AccountId",
        "sobjects/Account/describe",
        "sobjects/Account/001-ACME?fields=Name",
    ]

    project = _resolve_or_create_project(
        db,
        "WORKSPACE-RELATIONSHIPS",
        body,
        force_canonical_parent=True,
    )
    source_link = db.query(WorkItemSourceLink).one()

    assert project.name == "Acme Corporation"
    assert project.context_type == "account"
    assert source_link.work_item_id == project.id
    assert source_link.source_record_type == "Contact"
    assert source_link.source_record_id == "003-CONTACT-1"
    assert source_link.source_record_name == "Maria Lopez"


def test_separate_relationship_behavior_keeps_the_origin_as_its_own_work(monkeypatch):
    db = _session()
    mapping = dict(SALESFORCE_ACCOUNT_MAPPING)
    mapping["children"] = [
        {
            "child_object": "Case",
            "parent_field": "AccountId",
            "behavior": "separate",
        }
    ]
    db.add(
        IntegrationConnection(
            workspace_id="WORKSPACE-SEPARATE",
            platform="salesforce",
            display_name="Salesforce",
            status="connected",
            mapping_json=json.dumps(mapping),
        )
    )
    db.commit()

    async def should_not_query_salesforce(_connection, path):
        raise AssertionError(f"Separate behavior must not query Salesforce: {path}")

    monkeypatch.setattr(
        "api.routes_connections._salesforce_get",
        should_not_query_salesforce,
    )
    body = AgentforceGovernRequest(
        record_id="500-CASE-1",
        task_description="Summarize the case",
        project_name="Case 000123",
        source_type="Case",
        source_record_type="Case",
    )

    resolved = asyncio.run(
        _apply_approved_relationship_mapping(db, "WORKSPACE-SEPARATE", body)
    )

    assert resolved is False
    assert body.project_external_id is None
    assert body.project_name == "Case 000123"


def test_salesforce_template_resolves_universal_business_context():
    db = _session()
    body = AgentforceGovernRequest(
        record_id="a0Hfj00003mqqB7EAI",
        task_description="Summarize the project",
        project_external_id="CP-SF-POC-001",
        project_name="CostPilot Salesforce POC",
        project_owner="Sheldon Simmons",
        department="Sales",
        monthly_ai_budget=10,
        customer_external_id="001fj00001ACMEAAA",
        customer_name="Acme Corporation",
    )

    project = _resolve_or_create_project(db, "WORKSPACE-1", body)
    payload = _work_item_json(project, db)

    assert project.context_type == "project"
    assert project.context_template == "salesforce_project"
    assert project.source_record_type == "CostPilot_Project__c"
    assert project.source_record_id == "a0Hfj00003mqqB7EAI"
    assert db.query(WorkAccount).one().name == "Acme Corporation"
    assert payload["business_context"] == {
        "id": "CP-SF-POC-001",
        "type": "project",
        "name": "CostPilot Salesforce POC",
        "template": "salesforce_project",
        "work_label": "Project",
        "customer": {
            "id": "001fj00001ACMEAAA",
            "name": "Acme Corporation",
        },
        "source": {
            "platform": "Salesforce",
            "record_type": "CostPilot_Project__c",
            "record_id": "a0Hfj00003mqqB7EAI",
        },
        "owner": "Sheldon Simmons",
        "department": "Sales",
        "status": "active",
        "monthly_ai_budget": 10,
    }


def test_existing_project_is_upgraded_without_replacing_its_identity():
    db = _session()
    project = WorkItem(
        external_id="EXISTING-1",
        name="Existing Project",
        status="active",
    )
    db.add(project)
    db.commit()
    original_id = project.id

    resolved = _resolve_or_create_project(
        db,
        "WORKSPACE-1",
        AgentforceGovernRequest(
            record_id="a0H000000000001AAA",
            task_description="Review the project",
            project_external_id="EXISTING-1",
        ),
    )

    assert resolved.id == original_id
    assert resolved.context_template == "salesforce_project"
    assert resolved.source_record_id == "a0H000000000001AAA"


def test_template_catalog_uses_one_universal_contract():
    templates = list_context_templates()
    keys = {template["key"] for template in templates}

    assert {"universal_context", "salesforce_project", "servicenow_case"}.issubset(keys)
    assert get_context_template("salesforce_project").context_type == "project"
    assert normalize_context_type(None, template_key="servicenow_case") == "case"
    assert normalize_context_type("custom", template_key="universal_context") == "custom"
    assert normalize_context_type("account", template_key="salesforce_project") == "account"


def test_business_context_reporting_counts_transactions_once_and_keeps_origin():
    db = _session()
    parent = WorkItem(
        external_id="ACME",
        name="Acme Corporation",
        context_type="account",
        status="active",
        source_platform="Salesforce",
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)
    db.add(WorkItemSourceLink(
        work_item_id=parent.id,
        source_platform="Salesforce",
        source_record_type="Opportunity",
        source_record_id="006-OPP-1",
        source_record_name="Acme Expansion",
    ))
    db.add_all([
        TokenTransaction(
            department="Sales",
            work_item_id=parent.id,
            source_platform="Salesforce",
            origin_record_id="006-OPP-1",
            origin_record_type="Opportunity",
            origin_record_name="Acme Expansion",
            model_tier="Scout",
            input_tokens=100,
            output_tokens=50,
            tokens_saved=20,
            cost_usd=0.10,
        ),
        TokenTransaction(
            department="Sales",
            work_item_id=parent.id,
            source_platform="Salesforce",
            origin_record_id="001-ACME",
            origin_record_type="Account",
            origin_record_name="Acme Corporation",
            model_tier="Scout",
            input_tokens=200,
            output_tokens=75,
            tokens_saved=30,
            cost_usd=0.20,
        ),
        TokenTransaction(
            department="Direct",
            source_platform="Direct API",
            model_tier="Scout",
            input_tokens=50,
            output_tokens=25,
            tokens_saved=5,
            cost_usd=0.05,
        ),
    ])
    db.commit()

    report = business_context_reporting(workspace_id=None, days=30, limit=10, db=db)

    assert report["company_totals"]["request_count"] == 3
    assert report["company_totals"]["spend_usd"] == 0.35
    assert report["parents"][0]["request_count"] == 2
    assert report["parents"][0]["spend_usd"] == 0.30
    assert sum(child["request_count"] for child in report["parents"][0]["children"]) == 2
    assert report["attribution"]["unattributed_request_count"] == 1
    assert report["attribution"]["coverage_pct"] == 66.7


def test_project_activity_report_links_person_agent_account_project_and_cost():
    db = _session()
    account = WorkAccount(
        external_id="001-ACME",
        name="Acme Corporation",
        workspace_id="WORKSPACE-1",
    )
    db.add(account)
    db.flush()
    project = WorkItem(
        external_id="PROJECT-ACME",
        name="Acme Expansion",
        account_id=account.id,
        context_type="project",
        status="active",
        source_platform="Salesforce",
        workspace_id="WORKSPACE-1",
    )
    person = WorkUser(
        workspace_id="WORKSPACE-1",
        source_platform="Salesforce",
        external_id="005-MARIA",
        name="Maria Lopez",
        email="maria@example.com",
    )
    agent = RegisteredAgent(
        name="Sales Follow-up Agent",
        department="Sales",
        source_platform="Salesforce Agentforce",
        permissions="read,write",
    )
    db.add_all([project, person, agent])
    db.flush()
    db.add(TokenTransaction(
        department="WORKSPACE-1:Sales",
        source_platform="Salesforce Agentforce",
        work_item_id=project.id,
        work_user_id=person.id,
        agent_id=agent.id,
        origin_record_id="006-OPP",
        origin_record_type="Opportunity",
        origin_record_name="Acme Renewal",
        model_tier="Scout",
        model_name="gpt-4.1-mini",
        input_tokens=800,
        output_tokens=200,
        tokens_saved=125,
        cost_usd=0.04,
    ))
    db.commit()

    report = project_activity_reporting(
        workspace_id="WORKSPACE-1",
        date_from=datetime.utcnow() - timedelta(days=1),
        date_to=datetime.utcnow() + timedelta(days=1),
        days=30,
        project_id="PROJECT-ACME",
        user_external_id="005-MARIA",
        agent_id=agent.id,
        account_id="001-ACME",
        source_platform="Salesforce Agentforce",
        record_type="Opportunity",
        activity_limit=100,
        db=db,
    )

    assert report["summary"] == {
        "request_count": 1,
        "input_tokens": 800,
        "output_tokens": 200,
        "total_tokens": 1000,
        "tokens_saved": 125,
        "spend_usd": 0.04,
        "people_count": 1,
        "agent_count": 1,
        "project_count": 1,
    }
    activity = report["activities"][0]
    assert activity["user_name"] == "Maria Lopez"
    assert activity["agent_name"] == "Sales Follow-up Agent"
    assert activity["account_name"] == "Acme Corporation"
    assert activity["project_name"] == "Acme Expansion"
    assert activity["source_record_name"] == "Acme Renewal"


def test_workspace_saves_business_context_template():
    db = _session()
    account = TrialAccount(
        email="context@example.com",
        name="Context Tester",
        api_key_enc="",
        provider="anthropic",
        workspace_id="WORKSPACE-CONTEXT",
        secret_key="secret",
        trial_end=datetime.utcnow() + timedelta(days=30),
        is_active=True,
    )
    db.add(account)
    db.commit()

    result = save_business_context(
        BusinessContextSetupRequest(
            workspace_id=account.workspace_id,
            secret_key="secret",
            platform="salesforce",
            template="salesforce_project",
            work_type="matter",
            work_label="Matter",
            customer_label="Client",
            measures=["cost", "tokens", "cost", "not-supported"],
        ),
        db,
    )

    stored = json.loads(account.business_context_config_json)
    assert result["saved"] is True
    assert stored["work_type"] == "matter"
    assert stored["customer_label"] == "Client"
    assert stored["measures"] == ["cost", "tokens"]
    assert _trial_status_payload(account, db)["business_context"] == stored


def test_workspace_saves_fully_custom_business_language():
    db = _session()
    account = TrialAccount(
        email="custom-context@example.com",
        name="Custom Context Tester",
        api_key_enc="",
        provider="anthropic",
        workspace_id="WORKSPACE-CUSTOM-CONTEXT",
        secret_key="secret",
        trial_end=datetime.utcnow() + timedelta(days=30),
        is_active=True,
    )
    db.add(account)
    db.commit()

    result = save_business_context(
        BusinessContextSetupRequest(
            workspace_id=account.workspace_id,
            secret_key="secret",
            platform="custom",
            platform_label="Monday.com",
            template="universal_context",
            work_type="custom",
            work_label="Campaign",
            customer_label="Brand",
            measures=["cost", "risk"],
        ),
        db,
    )

    stored = json.loads(account.business_context_config_json)
    assert result["saved"] is True
    assert stored["platform_label"] == "Monday.com"
    assert stored["work_type"] == "custom"
    assert stored["work_label"] == "Campaign"
    assert stored["customer_label"] == "Brand"
