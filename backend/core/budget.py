"""
core/budget.py — Departmental Budget Allocator & Throttle Engine  [Step 4]

Responsibilities:
  - Return real-time budget status for all departments
  - Record spend from each token transaction
  - Auto-throttle a department when it hits 100% of its monthly cap
  - Allow a supervisor to update a cap or grant a throttle override
"""

from datetime import datetime
from sqlalchemy.orm import Session
from database.models import DepartmentBudget
from config import THROTTLE_TRIGGER_PERCENT, WARN_TRIGGER_PERCENT


def get_all_budgets(db: Session) -> list:
    """Return budget status for every department, enriched with usage metrics."""
    budgets = db.query(DepartmentBudget).all()
    return [_enrich(b) for b in budgets]


def get_budget(db: Session, department: str):
    """Return budget status for a single department, or None if not found."""
    b = db.query(DepartmentBudget).filter_by(department=department).first()
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


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _enrich(b: DepartmentBudget) -> dict:
    """Add computed fields to a raw DepartmentBudget row."""
    used_pct   = round((b.current_spend_usd / b.monthly_cap_usd) * 100, 1) if b.monthly_cap_usd else 0
    remaining  = round(b.monthly_cap_usd - b.current_spend_usd, 4)

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
        "current_spend_usd":           round(b.current_spend_usd, 4),
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
    }
