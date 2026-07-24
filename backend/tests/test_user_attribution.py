from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, _resolve_work_user
from api.routes_work_items import _project_user_rows
from database.db import Base
from database.models import TokenTransaction, WorkItem, WorkItemUser, WorkUser


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_project_user_is_created_and_usage_is_reported():
    db = _session()
    project = WorkItem(
        external_id="CP-SF-POC-001",
        name="CostPilot Salesforce POC",
        status="active",
        source_platform="Salesforce",
        workspace_id="WORKSPACE-1",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    request = RouteRequest(
        text="Summarize this project",
        work_item_id=project.external_id,
        source_platform="Salesforce Agentforce",
        actor_external_id="005-SHELDON",
        actor_name="Sheldon Simmons",
        actor_email="sheldon@example.com",
        actor_source_platform="Salesforce",
        actor_workspace_id="WORKSPACE-1",
        actor_role="Project Manager",
        actor_status="active",
        actor_can_use_ai=True,
        enforce_project_membership=True,
    )
    user = _resolve_work_user(db, request, project)
    membership = db.query(WorkItemUser).one()

    assert user.external_id == "005-SHELDON"
    assert membership.work_user_id == user.id
    assert membership.role == "Project Manager"
    assert membership.can_use_ai is True

    db.add(
        TokenTransaction(
            department="WORKSPACE-1:Sales",
            source_platform="Salesforce Agentforce",
            work_item_id=project.id,
            work_user_id=user.id,
            actor_external_id=user.external_id,
            actor_name=user.name,
            actor_email=user.email,
            actor_source_platform=user.source_platform,
            model_tier="Scout",
            input_tokens=420,
            output_tokens=80,
            cost_usd=0.04,
        )
    )
    db.commit()

    rows = _project_user_rows(project, db)
    assert len(rows) == 1
    assert rows[0]["name"] == "Sheldon Simmons"
    assert rows[0]["call_count"] == 1
    assert rows[0]["input_tokens"] == 420
    assert rows[0]["output_tokens"] == 80
    assert rows[0]["total_tokens"] == 500
    assert rows[0]["spend_usd"] == 0.04


def test_membership_can_block_ai_without_losing_identity():
    db = _session()
    project = WorkItem(
        external_id="PROJECT-BLOCKED-USER",
        name="Restricted Project",
        status="active",
        workspace_id="WORKSPACE-1",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    request = RouteRequest(
        text="Generate a report",
        actor_external_id="005-RESTRICTED",
        actor_name="Restricted User",
        actor_source_platform="Salesforce",
        actor_workspace_id="WORKSPACE-1",
        actor_status="active",
        actor_can_use_ai=False,
        enforce_project_membership=True,
    )
    try:
        _resolve_work_user(db, request, project)
        assert False, "Expected user membership to block AI"
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "not allowed to use AI" in str(exc.detail)

    assert db.query(WorkUser).count() == 1
    membership = db.query(WorkItemUser).one()
    assert membership.can_use_ai is False
    assert membership.status == "active"


def test_requests_without_user_identity_remain_backward_compatible():
    db = _session()
    request = RouteRequest(text="Existing API request")
    assert _resolve_work_user(db, request, None) is None
    assert db.query(WorkUser).count() == 0
