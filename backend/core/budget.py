"""
core/budget.py — Departmental Budget Allocator & Throttle Engine  [Step 4]

Responsibilities:
  - Return real-time budget status for all departments
  - Record spend from each token transaction
  - Auto-throttle a department when it hits 100% of its monthly cap
  - Allow a supervisor to update a cap or grant a throttle override
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import or_
from sqlalchemy.orm import Session
from database.models import DepartmentBudget, RegisteredAgent
from config import DEFAULT_BUDGET_CAPS, THROTTLE_TRIGGER_PERCENT, WARN_TRIGGER_PERCENT


def clean_budget_department_name(department: str) -> str:
    """
    Return the human-facing department label used by the dashboard.

    Some trial/workspace budget rows are stored as "workspace-id:Department".
    The UI groups those rows under "Department"; backend budget snapshots need
    to use the same grouping so override/throttle state matches what users see.
    """
    text = str(department or "").strip()
    prefix, sep, label = text.partition(":")
    if sep and label.strip() and len(prefix) >= 12 and prefix.replace("-", "").isalnum():
        return label.strip()
    return text


def related_budget_rows(db: Session, department: str) -> list[DepartmentBudget]:
    """Return budget rows that belong to the same displayed department."""
    # A workspace-qualified department is a tenant-scoped routing key. Never
    # combine it with the global department or another workspace's budget.
    # Unqualified names may still be grouped for the all-company dashboard.
    qualified = clean_budget_department_name(department) != str(department or "").strip()
    if qualified:
        exact = db.query(DepartmentBudget).filter_by(department=department).first()
        return [exact] if exact else []

    target = clean_budget_department_name(department).lower()
    if not target:
        return []

    rows = [
        row for row in db.query(DepartmentBudget).all()
        if clean_budget_department_name(row.department).lower() == target
    ]
    if rows:
        return rows

    exact = db.query(DepartmentBudget).filter_by(department=department).first()
    return [exact] if exact else []


def _default_cap_for(department: str) -> float:
    """Return a sensible cap for departments discovered from AgentLake."""
    if department in DEFAULT_BUDGET_CAPS:
        return DEFAULT_BUDGET_CAPS[department]
    if ":" in str(department):
        label = str(department).split(":", 1)[1].strip()
        if label in DEFAULT_BUDGET_CAPS:
            return DEFAULT_BUDGET_CAPS[label]
    return 1.0


def ensure_agent_departments_have_budgets(db: Session) -> bool:
    """Create missing budget rows for departments that have active agents."""
    existing = {row[0] for row in db.query(DepartmentBudget.department).all()}
    agent_departments = {
        row[0]
        for row in db.query(RegisteredAgent.department)
        .filter(
            RegisteredAgent.department.isnot(None),
            RegisteredAgent.department != "",
            or_(RegisteredAgent.archived == False, RegisteredAgent.archived.is_(None)),
        )
        .distinct()
        .all()
    }

    missing = sorted(dept for dept in agent_departments if dept not in existing)
    for dept in missing:
        prefix, sep, _label = str(dept).partition(":")
        db.add(DepartmentBudget(
            department=dept,
            workspace_id=prefix if sep else "default",
            monthly_cap_usd=_default_cap_for(dept),
            current_spend_usd=0.0,
            period_start=datetime.utcnow(),
            throttled=False,
            override_granted=False,
        ))
    return bool(missing)


def workspace_type_for(db: Session, workspace_id: str | None) -> str:
    """Defaults to 'production' — the safe assumption for any workspace not
    yet registered in the workspaces table, so current_spend_usd (the
    live-tracked counter) stays the default source of truth unless a
    workspace is explicitly known to be demo/simulation/legacy data."""
    from database.models import Workspace

    row = db.query(Workspace).filter(Workspace.workspace_id == (workspace_id or "default")).first()
    return row.workspace_type if row else "production"


def recomputed_department_spend(db: Session, workspace_id: str | None) -> dict[str, float]:
    """
    Month-to-date spend per department, recomputed from the actual
    transaction ledger. Only meaningful for workspaces whose data was
    bulk-backfilled or simulated rather than written through live request
    routing — current_spend_usd never gets touched by a backfill, so it
    reads $0.00 forever for those workspaces even when real activity
    exists. Never used for production workspaces, where current_spend_usd
    is the live, authoritative, admin-matching number.
    """
    from datetime import datetime as _datetime
    from api.routes_work_items import project_activity_reporting

    month_start = _datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    report = project_activity_reporting(
        workspace_id=workspace_id, date_from=month_start, date_to=_datetime.utcnow(), days=31,
        project_id=None, user_external_id=None, agent_id=None, account_id=None,
        source_platform=None, record_type=None, model_tier=None, charged_unit=None,
        business_purpose=None, activity_limit=2000, db=db,
    )
    spend_by_department: dict[str, float] = {}
    for row in report.get("organizational_unit_breakdown") or []:
        raw_department = str(row.get("id") or row.get("label") or "").strip()
        if workspace_id and raw_department.startswith(f"{workspace_id}:"):
            raw_department = raw_department[len(workspace_id) + 1:]
        elif ":" in raw_department:
            raw_department = raw_department.rsplit(":", 1)[-1]
        key = raw_department.casefold()
        if key:
            spend_by_department[key] = spend_by_department.get(key, 0.0) + float(row.get("spend_usd") or 0)
    return spend_by_department


def get_all_budgets(db: Session, workspace_id: str | None = None) -> list:
    """
    Return budget status for every department in one workspace, enriched
    with usage metrics. Without workspace scoping this returned every
    workspace's rows together — the Admin > Budgets page then summed
    same-named departments across all of them (see displayBudgetRows in
    frontend/js/budget.js), silently blending unrelated accounts' spend
    into one misleading total. workspace_id=None means the legacy
    unscoped "default" bucket specifically, matching every other budget
    lookup in this app (Ask CostPilot, the dashboard) — never "every
    workspace pooled together."
    """
    dirty = ensure_agent_departments_have_budgets(db)
    if dirty:
        db.flush()
    budgets = db.query(DepartmentBudget).filter(
        DepartmentBudget.workspace_id == (workspace_id or "default")
    ).all()
    for b in budgets:
        dirty = reconcile_throttle_state(b) or dirty
    if dirty:
        db.commit()

    if workspace_type_for(db, workspace_id) == "production":
        return [_enrich(b) for b in budgets]

    # Non-production workspace (demo/simulation/legacy): current_spend_usd
    # never gets touched by backfilled/simulated data, so it would show
    # $0.00 forever even with real recorded activity. Recompute for display.
    spend_by_department = recomputed_department_spend(db, workspace_id)
    return [
        _enrich(b, spend_override=spend_by_department.get(
            (b.department or "").split(":")[-1].casefold(), 0.0
        ))
        for b in budgets
    ]


def get_budget(db: Session, department: str):
    """Return budget status for a single department, or None if not found."""
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if b and reconcile_throttle_state(b):
        db.commit()
    return _enrich(b) if b else None


def set_cap(db: Session, department: str, new_cap: float) -> dict:
    """
    Update a department's monthly cap.
    Creates the department if it doesn't exist yet (supports onboarding flow).
    If the new cap puts the department back under threshold, clear the throttle.
    """
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        b = DepartmentBudget(
            department=department,
            monthly_cap_usd=round(new_cap, 2),
            current_spend_usd=0.0,
            period_start=datetime.utcnow(),
            throttled=False,
            override_granted=False,
        )
        db.add(b)
        db.commit()
        return _enrich(b)

    b.monthly_cap_usd = round(new_cap, 2)

    # Clear throttle if new cap gives them headroom
    usage_pct = (b.current_spend_usd / b.monthly_cap_usd) * 100
    if usage_pct < THROTTLE_TRIGGER_PERCENT:
        b.throttled = False

    db.commit()
    return _enrich(b)


def grant_override(db: Session, department: str) -> dict:
    """
    Grant a supervisor override — clears the throttle flag and marks override_granted.
    The department can use flagship models again until the next cap breach.
    """
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")

    b.throttled        = False
    b.override_granted = True
    db.commit()
    return _enrich(b)


def revoke_override(db: Session, department: str) -> dict:
    """Revoke a previously granted override and re-evaluate throttle state."""
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")

    b.override_granted = False
    usage_pct = (b.current_spend_usd / b.monthly_cap_usd) * 100
    if usage_pct >= THROTTLE_TRIGGER_PERCENT:
        b.throttled = True

    db.commit()
    return _enrich(b)


def set_throttle_tier(db: Session, department: str, tier: int) -> dict:
    """
    Set the model tier ceiling a department is capped to when throttled.
    1=Scout, 2=Analyst, 3=Advisor, 4=Strategist.
    """
    if tier not in (1, 2, 3, 4):
        raise ValueError("throttle_tier must be 1, 2, 3, or 4.")
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")
    b.throttle_tier = tier
    db.commit()
    return _enrich(b)


def set_raw_logging(db: Session, department: str, enabled: bool, retention_days: int) -> dict:
    """
    Enable or disable raw payload logging for a department.
    retention_days: 30 | 90 | 180 | 365 | 0 = indefinite
    Raw payloads are only stored when pruning actually removed tokens.
    """
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")
    b.raw_payload_logging_enabled = enabled
    b.raw_retention_days          = retention_days
    db.commit()
    return _enrich(b)


def reset_period(db: Session, department: str) -> dict:
    """Reset a department's spend to zero (simulates a new billing month)."""
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")

    b.current_spend_usd = 0.0
    b.throttled         = False
    b.override_granted  = False
    b.period_start      = datetime.utcnow()
    db.commit()
    return _enrich(b)


def archive_department(db: Session, department: str, archived: bool = True) -> dict:
    """Soft-hide or restore a department budget while preserving historical records."""
    b = db.query(DepartmentBudget).filter_by(department=department).first()
    if not b:
        raise ValueError(f"Department '{department}' not found.")

    b.archived = archived
    db.commit()
    return _enrich(b)


def reconcile_throttle_state(b: DepartmentBudget) -> bool:
    """
    Clear stale throttle flags when a department is back under its cap.

    This can happen after a cap increase: the previous cap may have triggered
    throttle, but the new cap gives the department headroom again.
    """
    if not b or not b.throttled or not b.monthly_cap_usd:
        return False
    if b.current_spend_usd < b.monthly_cap_usd:
        b.throttled = False
        return True
    return False


def effective_budget_context(db: Session, department: str) -> dict | None:
    """
    Summarize the effective budget state for a displayed department group.

    This keeps audit snapshots and routing controls aligned with the dashboard
    when the same department has multiple underlying rows.
    """
    rows = related_budget_rows(db, department)
    if not rows:
        return None

    spend = round(sum((row.current_spend_usd or 0.0) for row in rows), 4)
    cap = max((row.monthly_cap_usd or 0.0) for row in rows) or 0.0
    used_pct = round((spend / cap) * 100, 1) if cap else 0.0
    override_granted = any(bool(row.override_granted) for row in rows)
    throttled = any(bool(row.throttled) for row in rows) and not override_granted
    throttle_tiers = [getattr(row, "throttle_tier", 1) or 1 for row in rows]
    retention_days = max((getattr(row, "raw_retention_days", 30) or 30) for row in rows)

    return {
        "department": clean_budget_department_name(department),
        "budget_cap_usd": cap,
        "budget_spent_usd": spend,
        "budget_used_pct": used_pct,
        "throttled": throttled,
        "override_granted": override_granted,
        "throttle_tier": min(throttle_tiers) if throttle_tiers else 1,
        "raw_payload_logging_enabled": any(
            bool(getattr(row, "raw_payload_logging_enabled", False)) for row in rows
        ),
        "raw_retention_days": retention_days,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _enrich(b: DepartmentBudget, spend_override: float | None = None) -> dict:
    """
    Add computed fields to a raw DepartmentBudget row. spend_override lets
    callers substitute a recomputed spend figure for display (see
    recomputed_department_spend below) without ever mutating the ORM
    object — current_spend_usd is the live-tracked field real throttling
    enforcement reads, and must never be overwritten by a display-only
    recompute.
    """
    spend = b.current_spend_usd if spend_override is None else spend_override
    used_pct   = round((spend / b.monthly_cap_usd) * 100, 1) if b.monthly_cap_usd else 0
    remaining  = round(b.monthly_cap_usd - spend, 4)

    if b.throttled:
        state = "throttled"
    elif used_pct >= WARN_TRIGGER_PERCENT:
        state = "warning"
    else:
        state = "healthy"

    throttle_tier = getattr(b, "throttle_tier", 1) or 1
    tier_names    = {1: "Scout", 2: "Analyst", 3: "Advisor", 4: "Strategist"}

    return {
        "department":                  b.department,
        "monthly_cap_usd":             b.monthly_cap_usd,
        "current_spend_usd":           round(spend, 4),
        "remaining_usd":               remaining,
        "used_pct":                    used_pct,
        "throttled":                   b.throttled,
        "override_granted":            b.override_granted,
        "period_start":                b.period_start.isoformat() if b.period_start else None,
        "state":                       state,
        "throttle_tier":               throttle_tier,
        "throttle_tier_name":          tier_names.get(throttle_tier, "Scout"),
        "raw_payload_logging_enabled": getattr(b, "raw_payload_logging_enabled", False) or False,
        "raw_retention_days":          getattr(b, "raw_retention_days", 30) or 30,
        "archived":                    getattr(b, "archived", False) or False,
    }
