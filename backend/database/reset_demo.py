"""Database operations for scoped CostPilot data resets."""

from datetime import datetime

from sqlalchemy import or_

from .models import (
    AuditEvent,
    AuditReviewState,
    DepartmentBudget,
    IntegrationConnection,
    OrganizationalUnit,
    RegisteredAgent,
    TokenTransaction,
    WorkAccount,
    WorkItem,
    WorkItemAgent,
    WorkItemSourceLink,
    WorkItemUser,
    WorkUser,
)


def reset_demo_records(db, *, reset_at=None):
    """Clear generated activity while preserving configured business contexts."""
    reset_at = reset_at or datetime.utcnow()

    # Bulk deletes do not run SQLAlchemy relationship cascades. Remove project
    # assignments explicitly before their registered agents to satisfy the
    # database foreign-key constraint while keeping the work items themselves.
    tx_count = db.query(TokenTransaction).delete(synchronize_session=False)
    audit_count = db.query(AuditEvent).delete(synchronize_session=False)
    assignment_count = db.query(WorkItemAgent).delete(synchronize_session=False)
    agent_count = db.query(RegisteredAgent).delete(synchronize_session=False)
    review_state_count = db.query(AuditReviewState).delete(synchronize_session=False)

    for budget in db.query(DepartmentBudget).all():
        budget.current_spend_usd = 0.0
        budget.throttled = False
        budget.override_granted = False
        budget.period_start = reset_at

    return {
        "transactions_cleared": tx_count,
        "audit_events_cleared": audit_count,
        "agent_assignments_cleared": assignment_count,
        "agents_cleared": agent_count,
        "audit_review_states_cleared": review_state_count,
    }


def _workspace_query(query, model, workspace_id):
    """Apply an exact workspace filter when one was supplied."""
    if workspace_id:
        return query.filter(model.workspace_id == workspace_id)
    return query


def _reset_budget_state(db, departments, reset_at):
    query = db.query(DepartmentBudget)
    if departments:
        query = query.filter(DepartmentBudget.department.in_(departments))
    for budget in query.all():
        budget.current_spend_usd = 0.0
        budget.throttled = False
        budget.override_granted = False
        budget.period_start = reset_at


def _delete_orphan_agents(db, candidate_ids=None):
    query = db.query(RegisteredAgent)
    if candidate_ids is not None:
        if not candidate_ids:
            return 0
        query = query.filter(RegisteredAgent.id.in_(candidate_ids))

    deleted = 0
    for agent in query.all():
        has_usage = (
            db.query(TokenTransaction.id).filter(TokenTransaction.agent_id == agent.id).first()
            or db.query(AuditEvent.id).filter(AuditEvent.agent_id == agent.id).first()
            or db.query(WorkItemAgent.id).filter(WorkItemAgent.agent_id == agent.id).first()
        )
        if not has_usage:
            db.delete(agent)
            deleted += 1
    return deleted


def reset_workspace_records(db, *, scope, workspace_id=None, reset_at=None):
    """
    Reset one of three deliberately different data scopes.

    usage:
        Clears measured calls and their audit/risk history, while preserving
        users, agents, accounts, business records, mappings, and policy.
    simulator:
        Clears only simulated calls plus simulator-created identities and work.
    workspace:
        Clears all measured activity, business context, identities, agents, and
        external connections for one workspace. The trial/login identity and
        company policy configuration are intentionally preserved.
    """
    if scope not in {"usage", "simulator", "workspace"}:
        raise ValueError(f"Unsupported reset scope: {scope}")
    if scope == "workspace" and not workspace_id:
        raise ValueError("workspace_id is required for an entire workspace reset")

    reset_at = reset_at or datetime.utcnow()
    result = {
        "scope": scope,
        "workspace_id": workspace_id,
        "transactions_cleared": 0,
        "audit_events_cleared": 0,
        "audit_review_states_cleared": 0,
        "source_links_cleared": 0,
        "agent_assignments_cleared": 0,
        "user_assignments_cleared": 0,
        "work_items_cleared": 0,
        "accounts_cleared": 0,
        "users_cleared": 0,
        "agents_cleared": 0,
        "organizational_units_cleared": 0,
        "connections_cleared": 0,
    }

    tx_query = _workspace_query(db.query(TokenTransaction), TokenTransaction, workspace_id)
    audit_query = _workspace_query(db.query(AuditEvent), AuditEvent, workspace_id)

    if scope == "usage":
        departments = {row[0] for row in tx_query.with_entities(TokenTransaction.department).distinct()}
        result["transactions_cleared"] = tx_query.delete(synchronize_session=False)
        result["audit_events_cleared"] = audit_query.delete(synchronize_session=False)
        review_query = db.query(AuditReviewState)
        if workspace_id:
            review_query = review_query.filter(AuditReviewState.scope_key == workspace_id)
        result["audit_review_states_cleared"] = review_query.delete(synchronize_session=False)
        _reset_budget_state(db, departments, reset_at)
        return result

    if scope == "simulator":
        simulator_work = _workspace_query(db.query(WorkItem), WorkItem, workspace_id).filter(
            or_(
                WorkItem.workspace_id.like("SIM-%"),
                WorkItem.external_id.like("SIM-%"),
                WorkItem.source_platform.ilike("%simulator%"),
            )
        )
        simulator_accounts = _workspace_query(db.query(WorkAccount), WorkAccount, workspace_id).filter(
            or_(
                WorkAccount.workspace_id.like("SIM-%"),
                WorkAccount.external_id.like("SIM-%"),
            )
        )
        simulator_users = _workspace_query(db.query(WorkUser), WorkUser, workspace_id).filter(
            or_(
                WorkUser.workspace_id.like("SIM-%"),
                WorkUser.external_id.like("SIM-%"),
                WorkUser.source_platform.ilike("%simulator%"),
            )
        )
        work_ids = [row[0] for row in simulator_work.with_entities(WorkItem.id)]
        account_ids = [row[0] for row in simulator_accounts.with_entities(WorkAccount.id)]
        user_ids = [row[0] for row in simulator_users.with_entities(WorkUser.id)]

        sim_tx = tx_query.filter(
            or_(
                TokenTransaction.is_simulation.is_(True),
                TokenTransaction.work_item_id.in_(work_ids) if work_ids else False,
                TokenTransaction.work_user_id.in_(user_ids) if user_ids else False,
            )
        )
        sim_audit = audit_query.filter(
            or_(
                AuditEvent.is_simulation.is_(True),
                AuditEvent.work_item_id.in_(work_ids) if work_ids else False,
                AuditEvent.work_user_id.in_(user_ids) if user_ids else False,
            )
        )
        candidate_agent_ids = {
            row[0] for row in sim_tx.with_entities(TokenTransaction.agent_id).distinct() if row[0]
        } | {
            row[0] for row in sim_audit.with_entities(AuditEvent.agent_id).distinct() if row[0]
        }
        result["transactions_cleared"] = sim_tx.delete(synchronize_session=False)
        result["audit_events_cleared"] = sim_audit.delete(synchronize_session=False)
    else:
        work_ids = [
            row[0] for row in _workspace_query(
                db.query(WorkItem.id), WorkItem, workspace_id
            )
        ]
        account_ids = [
            row[0] for row in _workspace_query(
                db.query(WorkAccount.id), WorkAccount, workspace_id
            )
        ]
        user_ids = [
            row[0] for row in _workspace_query(
                db.query(WorkUser.id), WorkUser, workspace_id
            )
        ]
        candidate_agent_ids = {
            row[0] for row in tx_query.with_entities(TokenTransaction.agent_id).distinct() if row[0]
        } | {
            row[0] for row in audit_query.with_entities(AuditEvent.agent_id).distinct() if row[0]
        }
        departments = {row[0] for row in tx_query.with_entities(TokenTransaction.department).distinct()}
        result["transactions_cleared"] = tx_query.delete(synchronize_session=False)
        result["audit_events_cleared"] = audit_query.delete(synchronize_session=False)
        result["audit_review_states_cleared"] = (
            db.query(AuditReviewState)
            .filter(AuditReviewState.scope_key == workspace_id)
            .delete(synchronize_session=False)
        )
        _reset_budget_state(db, departments, reset_at)

    if work_ids:
        # Break self-references before bulk deletion.
        db.query(WorkItem).filter(
            WorkItem.merged_into_work_item_id.in_(work_ids)
        ).update({WorkItem.merged_into_work_item_id: None}, synchronize_session=False)
        result["source_links_cleared"] = db.query(WorkItemSourceLink).filter(
            WorkItemSourceLink.work_item_id.in_(work_ids)
        ).delete(synchronize_session=False)
        result["agent_assignments_cleared"] = db.query(WorkItemAgent).filter(
            WorkItemAgent.work_item_id.in_(work_ids)
        ).delete(synchronize_session=False)
        assignment_filter = WorkItemUser.work_item_id.in_(work_ids)
        if user_ids:
            assignment_filter = or_(
                assignment_filter,
                WorkItemUser.work_user_id.in_(user_ids),
            )
        result["user_assignments_cleared"] = db.query(WorkItemUser).filter(
            assignment_filter
        ).delete(synchronize_session=False)
        result["work_items_cleared"] = db.query(WorkItem).filter(
            WorkItem.id.in_(work_ids)
        ).delete(synchronize_session=False)
    elif user_ids:
        result["user_assignments_cleared"] = db.query(WorkItemUser).filter(
            WorkItemUser.work_user_id.in_(user_ids)
        ).delete(synchronize_session=False)

    if account_ids:
        removable_account_ids = [
            account_id for account_id in account_ids
            if not db.query(WorkItem.id).filter(WorkItem.account_id == account_id).first()
        ]
        if removable_account_ids:
            result["accounts_cleared"] = db.query(WorkAccount).filter(
                WorkAccount.id.in_(removable_account_ids)
            ).delete(synchronize_session=False)
    if user_ids:
        result["users_cleared"] = db.query(WorkUser).filter(
            WorkUser.id.in_(user_ids)
        ).delete(synchronize_session=False)

    if scope == "workspace":
        org_unit_ids = [
            row[0] for row in db.query(OrganizationalUnit.id).filter(
                OrganizationalUnit.workspace_id == workspace_id
            )
        ]
        if org_unit_ids:
            db.query(RegisteredAgent).filter(
                RegisteredAgent.owner_org_unit_id.in_(org_unit_ids)
            ).update({RegisteredAgent.owner_org_unit_id: None}, synchronize_session=False)
            db.query(OrganizationalUnit).filter(
                OrganizationalUnit.parent_id.in_(org_unit_ids)
            ).update({OrganizationalUnit.parent_id: None}, synchronize_session=False)
            result["organizational_units_cleared"] = db.query(OrganizationalUnit).filter(
                OrganizationalUnit.id.in_(org_unit_ids)
            ).delete(synchronize_session=False)
        result["connections_cleared"] = db.query(IntegrationConnection).filter(
            IntegrationConnection.workspace_id == workspace_id
        ).delete(synchronize_session=False)

    result["agents_cleared"] = _delete_orphan_agents(db, candidate_agent_ids)
    return result
