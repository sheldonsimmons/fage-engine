from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base
from database.models import (
    AuditEvent,
    AuditReviewState,
    DepartmentBudget,
    IntegrationConnection,
    RegisteredAgent,
    TokenTransaction,
    WorkAccount,
    WorkItem,
    WorkItemAgent,
    WorkItemUser,
    WorkUser,
)
from database.reset_demo import reset_demo_records, reset_workspace_records


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


def test_usage_reset_preserves_business_context_people_and_agents():
    db = _session()
    reset_at = datetime(2026, 7, 29, 13, 0, 0)
    try:
        account = WorkAccount(
            external_id="ACC-001",
            name="Acme Corporation",
            workspace_id="workspace-a",
        )
        user = WorkUser(
            workspace_id="workspace-a",
            source_platform="Salesforce",
            external_id="USER-001",
            name="Alex Morgan",
        )
        agent = RegisteredAgent(
            name="Account Assistant",
            department="Sales",
            permissions="read,write",
        )
        budget = DepartmentBudget(
            department="Sales",
            monthly_cap_usd=100.0,
            current_spend_usd=82.0,
            throttled=True,
            override_granted=True,
        )
        db.add_all([account, user, agent, budget])
        db.flush()
        work_item = WorkItem(
            external_id="OPP-001",
            name="Acme Renewal",
            account_id=account.id,
            department="Sales",
            workspace_id="workspace-a",
            context_type="account",
        )
        db.add(work_item)
        db.flush()
        db.add_all(
            [
                WorkItemAgent(work_item_id=work_item.id, agent_id=agent.id),
                WorkItemUser(work_item_id=work_item.id, work_user_id=user.id),
                TokenTransaction(
                    department="Sales",
                    workspace_id="workspace-a",
                    agent_id=agent.id,
                    work_item_id=work_item.id,
                    work_user_id=user.id,
                    model_tier="Scout",
                    input_tokens=100,
                    output_tokens=25,
                    cost_usd=0.01,
                ),
                AuditEvent(
                    event_type="ROUTING",
                    workspace_id="workspace-a",
                    agent_id=agent.id,
                    work_item_id=work_item.id,
                    work_user_id=user.id,
                    department="Sales",
                ),
                AuditReviewState(scope_key="workspace-a", reviewed_through_id=1),
            ]
        )
        db.commit()

        result = reset_workspace_records(
            db,
            scope="usage",
            workspace_id="workspace-a",
            reset_at=reset_at,
        )
        db.commit()

        assert result["transactions_cleared"] == 1
        assert result["audit_events_cleared"] == 1
        assert db.query(TokenTransaction).count() == 0
        assert db.query(AuditEvent).count() == 0
        assert db.query(AuditReviewState).count() == 0
        assert db.query(WorkAccount).count() == 1
        assert db.query(WorkItem).count() == 1
        assert db.query(WorkUser).count() == 1
        assert db.query(RegisteredAgent).count() == 1
        assert db.query(WorkItemAgent).count() == 1
        assert db.query(WorkItemUser).count() == 1

        refreshed_budget = db.query(DepartmentBudget).one()
        assert refreshed_budget.current_spend_usd == 0.0
        assert refreshed_budget.throttled is False
        assert refreshed_budget.override_granted is False
        assert refreshed_budget.period_start == reset_at
    finally:
        db.close()


def test_simulator_reset_removes_only_simulator_activity_and_entities():
    db = _session()
    try:
        live_account = WorkAccount(
            external_id="ACC-LIVE",
            name="Live Account",
            workspace_id="workspace-a",
        )
        sim_account = WorkAccount(
            external_id="SIM-ACC-001",
            name="Simulator Account",
            workspace_id="SIM-ENTERPRISE",
        )
        live_user = WorkUser(
            workspace_id="workspace-a",
            source_platform="Salesforce",
            external_id="USER-LIVE",
            name="Live User",
        )
        sim_user = WorkUser(
            workspace_id="SIM-ENTERPRISE",
            source_platform="Traffic Simulator",
            external_id="SIM-USER-001",
            name="Simulator User",
        )
        live_agent = RegisteredAgent(
            name="Live Agent",
            department="Sales",
            permissions="read,write",
        )
        sim_agent = RegisteredAgent(
            name="Simulator Agent",
            department="Operations",
            permissions="read,write",
        )
        db.add_all(
            [live_account, sim_account, live_user, sim_user, live_agent, sim_agent]
        )
        db.flush()
        live_work = WorkItem(
            external_id="WORK-LIVE",
            name="Live Work",
            account_id=live_account.id,
            workspace_id="workspace-a",
            department="Sales",
        )
        sim_work = WorkItem(
            external_id="SIM-WORK-001",
            name="Simulator Work",
            account_id=sim_account.id,
            workspace_id="SIM-ENTERPRISE",
            source_platform="Traffic Simulator",
            department="Operations",
        )
        db.add_all([live_work, sim_work])
        db.flush()
        db.add_all(
            [
                WorkItemAgent(work_item_id=live_work.id, agent_id=live_agent.id),
                WorkItemAgent(work_item_id=sim_work.id, agent_id=sim_agent.id),
                WorkItemUser(work_item_id=live_work.id, work_user_id=live_user.id),
                WorkItemUser(work_item_id=sim_work.id, work_user_id=sim_user.id),
                TokenTransaction(
                    department="Sales",
                    workspace_id="workspace-a",
                    agent_id=live_agent.id,
                    work_item_id=live_work.id,
                    work_user_id=live_user.id,
                    model_tier="Scout",
                    input_tokens=100,
                    output_tokens=25,
                    cost_usd=0.01,
                    is_simulation=False,
                ),
                TokenTransaction(
                    department="Operations",
                    workspace_id="SIM-ENTERPRISE",
                    agent_id=sim_agent.id,
                    work_item_id=sim_work.id,
                    work_user_id=sim_user.id,
                    model_tier="Scout",
                    input_tokens=200,
                    output_tokens=50,
                    cost_usd=0.02,
                    is_simulation=True,
                ),
                AuditEvent(
                    event_type="ROUTING",
                    workspace_id="workspace-a",
                    agent_id=live_agent.id,
                    work_item_id=live_work.id,
                    work_user_id=live_user.id,
                    department="Sales",
                    is_simulation=False,
                ),
                AuditEvent(
                    event_type="ROUTING",
                    workspace_id="SIM-ENTERPRISE",
                    agent_id=sim_agent.id,
                    work_item_id=sim_work.id,
                    work_user_id=sim_user.id,
                    department="Operations",
                    is_simulation=True,
                ),
            ]
        )
        db.commit()

        result = reset_workspace_records(db, scope="simulator")
        db.commit()

        assert result["transactions_cleared"] == 1
        assert result["audit_events_cleared"] == 1
        assert db.query(TokenTransaction).count() == 1
        assert db.query(AuditEvent).count() == 1
        assert [row.external_id for row in db.query(WorkItem).all()] == ["WORK-LIVE"]
        assert [row.external_id for row in db.query(WorkAccount).all()] == ["ACC-LIVE"]
        assert [row.external_id for row in db.query(WorkUser).all()] == ["USER-LIVE"]
        assert [row.name for row in db.query(RegisteredAgent).all()] == ["Live Agent"]
    finally:
        db.close()


def test_entire_workspace_reset_is_tenant_scoped():
    db = _session()
    try:
        target_account = WorkAccount(
            external_id="TARGET-ACCOUNT",
            name="Target Account",
            workspace_id="workspace-a",
        )
        other_account = WorkAccount(
            external_id="OTHER-ACCOUNT",
            name="Other Account",
            workspace_id="workspace-b",
        )
        target_user = WorkUser(
            workspace_id="workspace-a",
            source_platform="Salesforce",
            external_id="TARGET-USER",
            name="Target User",
        )
        other_user = WorkUser(
            workspace_id="workspace-b",
            source_platform="Salesforce",
            external_id="OTHER-USER",
            name="Other User",
        )
        target_agent = RegisteredAgent(
            name="Target Agent",
            department="Sales",
            permissions="read,write",
        )
        other_agent = RegisteredAgent(
            name="Other Agent",
            department="Support",
            permissions="read,write",
        )
        db.add_all(
            [
                target_account,
                other_account,
                target_user,
                other_user,
                target_agent,
                other_agent,
                IntegrationConnection(
                    workspace_id="workspace-a",
                    platform="Salesforce",
                    display_name="Target Salesforce",
                ),
                IntegrationConnection(
                    workspace_id="workspace-b",
                    platform="Salesforce",
                    display_name="Other Salesforce",
                ),
            ]
        )
        db.flush()
        target_work = WorkItem(
            external_id="TARGET-WORK",
            name="Target Work",
            account_id=target_account.id,
            workspace_id="workspace-a",
            department="Sales",
        )
        other_work = WorkItem(
            external_id="OTHER-WORK",
            name="Other Work",
            account_id=other_account.id,
            workspace_id="workspace-b",
            department="Support",
        )
        db.add_all([target_work, other_work])
        db.flush()
        db.add_all(
            [
                WorkItemAgent(work_item_id=target_work.id, agent_id=target_agent.id),
                WorkItemAgent(work_item_id=other_work.id, agent_id=other_agent.id),
                WorkItemUser(work_item_id=target_work.id, work_user_id=target_user.id),
                WorkItemUser(work_item_id=other_work.id, work_user_id=other_user.id),
                TokenTransaction(
                    department="Sales",
                    workspace_id="workspace-a",
                    agent_id=target_agent.id,
                    work_item_id=target_work.id,
                    work_user_id=target_user.id,
                    model_tier="Scout",
                    input_tokens=100,
                    output_tokens=25,
                    cost_usd=0.01,
                ),
                TokenTransaction(
                    department="Support",
                    workspace_id="workspace-b",
                    agent_id=other_agent.id,
                    work_item_id=other_work.id,
                    work_user_id=other_user.id,
                    model_tier="Scout",
                    input_tokens=100,
                    output_tokens=25,
                    cost_usd=0.01,
                ),
                AuditEvent(
                    event_type="ROUTING",
                    workspace_id="workspace-a",
                    agent_id=target_agent.id,
                    work_item_id=target_work.id,
                    work_user_id=target_user.id,
                    department="Sales",
                ),
                AuditEvent(
                    event_type="ROUTING",
                    workspace_id="workspace-b",
                    agent_id=other_agent.id,
                    work_item_id=other_work.id,
                    work_user_id=other_user.id,
                    department="Support",
                ),
            ]
        )
        db.commit()

        result = reset_workspace_records(
            db,
            scope="workspace",
            workspace_id="workspace-a",
        )
        db.commit()

        assert result["transactions_cleared"] == 1
        assert result["audit_events_cleared"] == 1
        assert [row.workspace_id for row in db.query(TokenTransaction).all()] == [
            "workspace-b"
        ]
        assert [row.external_id for row in db.query(WorkItem).all()] == ["OTHER-WORK"]
        assert [row.external_id for row in db.query(WorkAccount).all()] == [
            "OTHER-ACCOUNT"
        ]
        assert [row.external_id for row in db.query(WorkUser).all()] == ["OTHER-USER"]
        assert [row.name for row in db.query(RegisteredAgent).all()] == ["Other Agent"]
        assert [row.workspace_id for row in db.query(IntegrationConnection).all()] == [
            "workspace-b"
        ]
    finally:
        db.close()
