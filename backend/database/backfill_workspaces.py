"""
database/backfill_workspaces.py — one-time backfill of the workspaces table.

Populates the new `workspaces` registry (see database/models.py Workspace)
from the workspace identifiers already scattered across the database — a
pure read of existing data, nothing is moved, renamed, or deleted. Also
backfills DepartmentBudget.workspace_id, which never had it, from the
existing "workspace_id:DeptName" prefix convention on `department`.

Safe to re-run: workspace rows are upserted by workspace_id, and the
DepartmentBudget backfill only fills rows where workspace_id is currently
NULL.

Usage:
    cd backend
    python database/backfill_workspaces.py [--dry-run]
"""

import argparse
import json
from datetime import datetime

from sqlalchemy import func

from database.db import SessionLocal
from database.migrate import create_tables, run_migrations
from database.models import DepartmentBudget, TokenTransaction, TrialAccount, Workspace

# Human-readable labels and types for known non-customer workspace_ids that
# only ever appear as scattered activity rows (no TrialAccount owner).
KNOWN_WORKSPACES = {
    "SIM-HISTORICAL-2Y": {
        "name": "Historical Demo",
        "workspace_type": "demo",
        "source": "historical_backfill",
    },
    "SIM-MANUFACTURING": {
        "name": "Manufacturing Demo Scenario",
        "workspace_type": "simulation",
        "source": "manual_seed",
    },
    "SIM-ENTERPRISE-SAAS": {
        "name": "Enterprise SaaS Demo Scenario",
        "workspace_type": "simulation",
        "source": "manual_seed",
    },
    "SIM-PROFESSIONAL-SERVICES": {
        "name": "Professional Services Demo Scenario",
        "workspace_type": "simulation",
        "source": "manual_seed",
    },
    "SIM-RETAIL-SERVICES": {
        "name": "Retail Services Demo Scenario",
        "workspace_type": "simulation",
        "source": "manual_seed",
    },
}
DEFAULT_WORKSPACE_ID = "default"


def _department_prefix(department: str) -> str:
    text = (department or "").strip()
    return text.split(":", 1)[0] if ":" in text else DEFAULT_WORKSPACE_ID


def discover_workspace_ids(db) -> set[str]:
    ids = set()
    for (wid,) in db.query(TrialAccount.workspace_id).all():
        if wid:
            ids.add(wid)
    for (wid,) in db.query(TokenTransaction.workspace_id).distinct().all():
        if wid:
            ids.add(wid)
    for (department,) in db.query(DepartmentBudget.department).all():
        ids.add(_department_prefix(department))
    return ids


def backfill_workspaces(db, dry_run: bool = False) -> dict:
    create_tables()
    run_migrations()

    discovered = discover_workspace_ids(db)
    existing = {w.workspace_id: w for w in db.query(Workspace).all()}
    trial_by_workspace = {t.workspace_id: t for t in db.query(TrialAccount).all()}

    created, updated = [], []
    for workspace_id in sorted(discovered):
        trial = trial_by_workspace.get(workspace_id)
        known = KNOWN_WORKSPACES.get(workspace_id)

        last_activity_at = db.query(func.max(TokenTransaction.timestamp)).filter(
            TokenTransaction.workspace_id == workspace_id
        ).scalar()

        if trial:
            name = trial.company or trial.name or workspace_id
            workspace_type = "production"
            source = "trial_signup"
            owner_trial_account_id = trial.id
        elif known:
            name = known["name"]
            workspace_type = known["workspace_type"]
            source = known["source"]
            owner_trial_account_id = None
        elif workspace_id == DEFAULT_WORKSPACE_ID:
            name = "Default (legacy)"
            workspace_type = "legacy"
            source = "manual_seed"
            owner_trial_account_id = None
        else:
            # Unrecognized workspace_id found only as scattered activity —
            # flag it clearly rather than silently guessing it's real.
            name = f"Unknown ({workspace_id})"
            workspace_type = "simulation"
            source = "manual_seed"
            owner_trial_account_id = None

        row = existing.get(workspace_id)
        if row is None:
            row = Workspace(workspace_id=workspace_id)
            db.add(row)
            created.append(workspace_id)
        else:
            updated.append(workspace_id)
        row.name = name
        row.workspace_type = workspace_type
        row.source = source
        row.owner_trial_account_id = owner_trial_account_id
        row.last_activity_at = last_activity_at
        row.is_active = True

    # Backfill DepartmentBudget.workspace_id from the department prefix —
    # only rows that don't already have it set (safe to re-run).
    budget_rows_updated = 0
    for budget in db.query(DepartmentBudget).filter(DepartmentBudget.workspace_id.is_(None)).all():
        prefix = _department_prefix(budget.department)
        budget.workspace_id = prefix if prefix != DEFAULT_WORKSPACE_ID else DEFAULT_WORKSPACE_ID
        budget_rows_updated += 1

    result = {
        "discovered_workspace_ids": len(discovered),
        "workspaces_created": created,
        "workspaces_updated": updated,
        "department_budgets_backfilled": budget_rows_updated,
    }
    if dry_run:
        db.rollback()
        result["status"] = "dry_run"
    else:
        db.commit()
        result["status"] = "committed"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = backfill_workspaces(db, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
