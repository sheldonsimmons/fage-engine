"""
tests/test_agent_adoption_lifetime_ledger.py — the agent_adoption/inactive
intent's "lifetime usage" check used to query AuditEvent, while the
in-period count it's compared against comes from TokenTransaction (via
project_activity_reporting's agent_breakdown). Those two tables aren't
guaranteed to agree -- reproduced live: 8 agents in the historical demo
workspace each showed hundreds of in-period requests right next to
"Never used / 0 lifetime requests", because that workspace's bulk-seeded
TokenTransaction history was never matched 1:1 by AuditEvent rows for the
same agent ids. Fixed by reading lifetime usage from the same
TokenTransaction ledger the in-period count already uses.
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_efficiency import AskCostPilotRequest, ask_costpilot
from core.ask_costpilot_contracts import ask_interpretation_label
from database.db import Base
from database.models import AuditEvent, RegisteredAgent, TokenTransaction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_agent_with_real_transactions_but_no_audit_events_is_not_reported_as_never_used():
    db = _session()
    workspace_id = "WS-HIST"
    agent = RegisteredAgent(
        name="Pipeline Coach Agent", department=f"{workspace_id}:Sales", status="active", permissions="read,write",
    )
    db.add(agent)
    db.commit()

    now = datetime.utcnow()
    # 20 real TokenTransaction rows for this agent, spread across the last
    # 10 days -- well within the default 30-day window -- but ZERO
    # AuditEvent rows, reproducing the historical-demo-workspace shape
    # confirmed live (bulk-seeded ledger data with no matching audit log).
    for i in range(20):
        db.add(TokenTransaction(
            agent_id=agent.id, department=f"{workspace_id}:Sales", model_tier="Scout",
            input_tokens=10, output_tokens=10, cost_usd=0.05,
            timestamp=now - timedelta(days=i % 10), workspace_id=workspace_id,
            is_simulation=True, usage_source="estimated", routing_reason="ROUTINE",
        ))
    db.commit()
    assert db.query(AuditEvent).filter(AuditEvent.agent_id == agent.id).count() == 0

    result = ask_costpilot(
        AskCostPilotRequest(question="Which agents have not been used in this period?", workspace_id=workspace_id),
        db=db,
    )

    evidence_by_name = {item["label"]: item for item in result["evidence"]}
    assert "Pipeline Coach Agent" not in evidence_by_name, (
        "agent with 20 real TokenTransaction rows was still reported as unused"
    )


def test_agent_adoption_interpreted_as_label_does_not_duplicate_agent():
    label = ask_interpretation_label({"intent": "agent_adoption", "entity": "agent", "metric": "request_count"})
    assert label == "Agent adoption using request count"
    assert "Agent agent" not in label
