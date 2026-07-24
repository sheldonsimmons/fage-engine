from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_agentforce import AgentforceGovernRequest, _resolve_or_create_project
from api.routes_work_items import _work_item_json, list_context_templates
from core.business_context import get_context_template, normalize_context_type
from database.db import Base
from database.models import WorkAccount, WorkItem


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


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

    assert {"salesforce_project", "servicenow_case"}.issubset(keys)
    assert get_context_template("salesforce_project").context_type == "project"
    assert normalize_context_type(None, template_key="servicenow_case") == "case"
