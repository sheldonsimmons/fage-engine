from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from api.routes_work_items import organizational_usage_reporting
from database.db import Base
from database.models import AuditEvent, OrganizationalUnit, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_universal_request_snapshots_independent_organizational_dimensions():
    db = _session()
    request = RouteRequest(**{
        "synthetic_simulation": True,
        "source": {
            "platform": "Salesforce",
            "workspace_id": "ORG-1",
            "agent_name": "Renewal AI",
            "department": "Sales",
            "agent_department": "Revenue Operations",
        },
        "actor": {
            "external_id": "USER-1",
            "name": "David Chen",
            "department": "Enterprise Sales",
        },
        "work": {
            "external_id": "006-1",
            "type": "Opportunity",
            "name": "Acme Renewal",
            "department": "Strategic Accounts",
            "sync_if_missing": True,
        },
        "request": {
            "content": "Summarize renewal risks and recommend the next action for the account team.",
            "task": "Summarize",
        },
    })

    route_payload(request, db)

    tx = db.query(TokenTransaction).one()
    assert tx.workspace_id == "ORG-1"
    assert tx.actor_org_unit_name == "Enterprise Sales"
    assert tx.agent_org_unit_name == "Revenue Operations"
    assert tx.work_org_unit_name == "Strategic Accounts"
    assert tx.charged_org_unit_name == "Strategic Accounts"
    assert tx.department == "Strategic Accounts"
    assert tx.attribution_source == "work_record"
    assert tx.attribution_confidence == "high"

    audit = db.query(AuditEvent).order_by(AuditEvent.id.desc()).first()
    assert audit.charged_org_unit_name == "Strategic Accounts"
    assert audit.workspace_id == "ORG-1"
    assert db.query(OrganizationalUnit).count() == 3


def test_organizational_report_does_not_sum_dimensions_into_company_total():
    db = _session()
    request = RouteRequest(**{
        "synthetic_simulation": True,
        "source": {
            "platform": "ServiceNow",
            "workspace_id": "INSTANCE-1",
            "agent_name": "Change AI",
            "department": "Operations",
        },
        "actor": {"external_id": "USER-2", "name": "Alex Morgan"},
        "work": {
            "external_id": "CHG-1",
            "type": "change_request",
            "name": "CHG0001",
            "sync_if_missing": True,
        },
        "request": {
            "content": "Summarize this change request and list the recommended next step.",
        },
    })
    route_payload(request, db)

    report = organizational_usage_reporting(
        workspace_id="INSTANCE-1",
        date_from=None,
        date_to=None,
        days=30,
        charged_unit=None,
        db=db,
    )

    assert report["company"]["request_count"] == 1
    assert report["organizational_units"][0]["request_count"] == 1
    assert report["users"][0]["request_count"] == 1
    assert report["agents"][0]["request_count"] == 1
    assert report["work_items"][0]["request_count"] == 1
