"""
core/keywords.py — Sensitive Term Library  [Step 9]

Checks incoming payloads against company-configured sensitive terms.
Matches trigger one of three actions:
  - flag      → mark as high-risk in audit log, routing continues normally
  - escalate  → force flagship model regardless of complexity score
  - block     → reject the request entirely

Default terms are seeded on first use if the table is empty.
"""

from sqlalchemy.orm import Session
from database.models import SensitiveTerm

# ── Default seed terms ────────────────────────────────────────────────────────

DEFAULT_TERMS = [
    # Legal
    {"term": "lawsuit",       "category": "legal",     "action": "escalate"},
    {"term": "litigation",    "category": "legal",     "action": "escalate"},
    {"term": "attorney",      "category": "legal",     "action": "escalate"},
    {"term": "subpoena",      "category": "legal",     "action": "block"},
    {"term": "liability",     "category": "legal",     "action": "escalate"},
    {"term": "settlement",    "category": "legal",     "action": "escalate"},
    # HIPAA / Health
    {"term": "hipaa",         "category": "hipaa",     "action": "block"},
    {"term": "phi",           "category": "hipaa",     "action": "block"},
    {"term": "diagnosis",     "category": "hipaa",     "action": "escalate"},
    {"term": "medical record","category": "hipaa",     "action": "escalate"},
    {"term": "patient",       "category": "hipaa",     "action": "flag"},
    # Financial
    {"term": "fraud",         "category": "financial", "action": "block"},
    {"term": "embezzlement",  "category": "financial", "action": "block"},
    {"term": "sec filing",    "category": "financial", "action": "escalate"},
    {"term": "audit",         "category": "financial", "action": "escalate"},
    # HR
    {"term": "termination",   "category": "hr",        "action": "escalate"},
    {"term": "harassment",    "category": "hr",        "action": "escalate"},
    {"term": "discrimination","category": "hr",        "action": "escalate"},
    {"term": "wrongful",      "category": "hr",        "action": "escalate"},
]


def seed_defaults(db: Session):
    """Seed default terms if the table is empty."""
    if db.query(SensitiveTerm).count() == 0:
        for t in DEFAULT_TERMS:
            db.add(SensitiveTerm(**t))
        db.commit()


def check_terms(db: Session, text: str, department: str = None) -> dict:
    """
    Scan text against all sensitive terms that apply to this department.
    Returns the highest-priority match and full list of all matches.

    Action priority: block > escalate > flag
    """
    seed_defaults(db)

    text_lower = text.lower()

    # Load terms: global (no dept) + dept-specific
    query = db.query(SensitiveTerm).filter(
        (SensitiveTerm.department == None) |
        (SensitiveTerm.department == department)
    )
    all_terms = query.all()

    matches = []
    for t in all_terms:
        if t.term.lower() in text_lower:
            matches.append({
                "id":         t.id,
                "term":       t.term,
                "category":   t.category,
                "action":     t.action,
                "department": t.department,
            })

    if not matches:
        return {"triggered": False, "action": None, "matches": []}

    # Determine highest-priority action
    priority = {"block": 3, "escalate": 2, "flag": 1}
    top = max(matches, key=lambda m: priority.get(m["action"], 0))

    return {
        "triggered": True,
        "action":    top["action"],
        "top_match": top,
        "matches":   matches,
    }


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def get_all_terms(db: Session):
    seed_defaults(db)
    return db.query(SensitiveTerm).order_by(SensitiveTerm.category, SensitiveTerm.term).all()


def add_term(db: Session, term: str, category: str, action: str, department: str = None) -> SensitiveTerm:
    existing = db.query(SensitiveTerm).filter(SensitiveTerm.term == term.lower()).first()
    if existing:
        raise ValueError(f"Term '{term}' already exists.")
    obj = SensitiveTerm(
        term=term.lower().strip(),
        category=category,
        action=action,
        department=department or None,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_term(db: Session, term_id: int) -> bool:
    obj = db.query(SensitiveTerm).filter(SensitiveTerm.id == term_id).first()
    if not obj:
        return False
    db.delete(obj)
    db.commit()
    return True


def update_term(db: Session, term_id: int, action: str = None, category: str = None) -> SensitiveTerm:
    obj = db.query(SensitiveTerm).filter(SensitiveTerm.id == term_id).first()
    if not obj:
        raise ValueError(f"Term ID {term_id} not found.")
    if action:
        obj.action = action
    if category:
        obj.category = category
    db.commit()
    db.refresh(obj)
    return obj
