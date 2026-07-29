"""Database operations for the demo-data factory reset."""

from datetime import datetime

from .models import (
    AuditEvent,
    AuditReviewState,
    DepartmentBudget,
    RegisteredAgent,
    TokenTransaction,
    WorkItemAgent,
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
