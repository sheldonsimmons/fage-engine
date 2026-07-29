from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base
from database.models import (
    AuditEvent,
    AuditReviewState,
    DepartmentBudget,
    RegisteredAgent,
    TokenTransaction,
    WorkItem,
    WorkItemAgent,
)
from database.reset_demo import reset_demo_records


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_reset_clears_agent_assignments_and_activity_but_preserves_work():
    db = _session()
    reset_at = datetime(2026, 7, 29, 12, 0, 0)
    try:
        agent = RegisteredAgent(
            name="Sales Assistant",
            department="Sales",
            permissions="read,write",
        )
        work_item = WorkItem(
            external_id="OPP-001",
            name="Acme Renewal",
            department="Sales",
        )
        budget = DepartmentBudget(
            department="Sales",
            monthly_cap_usd=100.0,
            current_spend_usd=83.0,
            throttled=True,
            override_granted=True,
        )
        db.add_all([agent, work_item, budget])
        db.flush()
        db.add_all(
            [
                WorkItemAgent(work_item_id=work_item.id, agent_id=agent.id),
                TokenTransaction(
                    department="Sales",
                    agent_id=agent.id,
                    work_item_id=work_item.id,
                    model_tier="Scout",
                    input_tokens=100,
                    output_tokens=25,
                    cost_usd=0.01,
                ),
                AuditEvent(
                    event_type="ROUTING",
                    agent_id=agent.id,
                    work_item_id=work_item.id,
                    department="Sales",
                ),
                AuditReviewState(scope_key="global", reviewed_through_id=1),
            ]
        )
        db.commit()

        result = reset_demo_records(db, reset_at=reset_at)
        db.commit()

        assert result["agent_assignments_cleared"] == 1
        assert db.query(TokenTransaction).count() == 0
        assert db.query(AuditEvent).count() == 0
        assert db.query(AuditReviewState).count() == 0
        assert db.query(WorkItemAgent).count() == 0
        assert db.query(RegisteredAgent).count() == 0
        assert db.query(WorkItem).count() == 1

        refreshed_budget = db.query(DepartmentBudget).one()
        assert refreshed_budget.monthly_cap_usd == 100.0
        assert refreshed_budget.current_spend_usd == 0.0
        assert refreshed_budget.throttled is False
        assert refreshed_budget.override_granted is False
        assert refreshed_budget.period_start == reset_at
    finally:
        db.close()
