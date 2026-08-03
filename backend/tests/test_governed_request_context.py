from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_work_items import project_activity_reporting
from api.routes_router import RouteRequest, route_payload
from api.routes_efficiency import AskCostPilotRequest, ask_costpilot
from core.governed_requests import new_governed_request_id
from database.db import Base
from database.models import AuditEvent, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_governed_request_ids_are_opaque_and_unique():
    first = new_governed_request_id()
    second = new_governed_request_id()

    assert first.startswith("cp_req_")
    assert len(first) == 39
    assert first != second


def test_reporting_joins_financial_and_audit_evidence_by_request_id():
    db = _session()
    request_id = new_governed_request_id()
    db.add(TokenTransaction(
        governed_request_id=request_id,
        department="Support",
        source_platform="Salesforce",
        model_tier="analyst",
        model_name="claude-sonnet-4-6",
        requested_model_name="claude-opus-4-6",
        requested_model_tier="strategist",
        resolved_model_tier="analyst",
        routing_policy_version="costpilot-routing-v1",
        execution_status="succeeded",
        provider_status_code=200,
        input_tokens=120,
        output_tokens=30,
        usage_source="provider_reported",
        cost_usd=0.0042,
        routing_reason="ROUTINE",
        tokens_saved=40,
        timestamp=datetime.utcnow(),
    ))
    db.add(AuditEvent(
        governed_request_id=request_id,
        event_type="ROUTING",
        department="Support",
        model_tier="analyst",
        requested_model_name="claude-opus-4-6",
        requested_model_tier="strategist",
        selected_model_name="claude-sonnet-4-6",
        selected_model_tier="analyst",
        routing_policy_version="costpilot-routing-v1",
        routing_reason_code="ROUTINE",
        execution_status="succeeded",
        rationale="Routine support request safely routed to the Analyst tier.",
        decision_outcome="Analyst model used",
        risk_level="low",
        timestamp=datetime.utcnow(),
    ))
    db.commit()

    report = project_activity_reporting(
        workspace_id=None,
        date_from=None,
        date_to=None,
        days=30,
        project_id=None,
        user_external_id=None,
        agent_id=None,
        account_id=None,
        source_platform=None,
        record_type=None,
        model_tier=None,
        charged_unit=None,
        business_purpose=None,
        activity_limit=100,
        db=db,
    )

    assert report["evidence_quality"]["request_identity_count"] == 1
    assert report["evidence_quality"]["correlated_request_count"] == 1
    activity = report["activities"][0]
    assert activity["governed_request_id"] == request_id
    assert activity["requested_model_name"] == "claude-opus-4-6"
    assert activity["model_name"] == "claude-sonnet-4-6"
    assert activity["audit_rationale"].startswith("Routine support request")
    assert activity["decision_outcome"] == "Analyst model used"
    assert activity["risk_level"] == "low"


def test_primary_router_persists_one_request_identity_across_cost_and_audit():
    db = _session()
    response = route_payload(RouteRequest(
        text="Summarize this routine support request.",
        department="Support",
        agent_name="Support Summary Agent",
        source_platform="Salesforce",
        synthetic_simulation=True,
    ), db)

    tx = db.query(TokenTransaction).one()
    audit = db.query(AuditEvent).filter(
        AuditEvent.governed_request_id == response.governed_request_id
    ).one()
    assert response.governed_request_id.startswith("cp_req_")
    assert tx.governed_request_id == response.governed_request_id
    assert audit.governed_request_id == response.governed_request_id
    assert tx.routing_policy_version == "costpilot-routing-v1"
    assert audit.routing_policy_version == "costpilot-routing-v1"
    assert tx.execution_status == "succeeded"
    assert audit.execution_status == "succeeded"

    answer = ask_costpilot(AskCostPilotRequest(
        question="Why did CostPilot route this request to this tier?",
        governed_request_id=response.governed_request_id,
    ), db)
    assert answer["intent"] == "decision"
    assert answer["entity"] == "request"
    assert answer["data_provenance"]["scope"] == "governed_request"
    assert answer["filters"]["governed_request_id"] == response.governed_request_id
    assert answer["evidence"][0]["governed_request_id"] == response.governed_request_id
    assert answer["contract_status"] == "passed"


def test_request_decision_question_without_selected_evidence_fails_closed():
    db = _session()
    answer = ask_costpilot(AskCostPilotRequest(
        question="Why did CostPilot route this request to Strategist?",
    ), db)

    assert answer["intent"] == "clarification"
    assert answer["contract_status"] == "failed"
    assert answer["evidence"] == []
    assert "did not display" in answer["answer"]
