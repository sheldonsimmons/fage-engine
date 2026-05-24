"""
core/keywords.py — Sensitive Term Library  [Step 9]

Checks incoming payloads against company-configured sensitive terms.
Matches trigger one of three actions:
  - flag      → mark as high-risk in audit log, routing continues normally
  - escalate  → force flagship model regardless of complexity score
  - block     → reject the request entirely

Default terms are seeded on first use if the table is empty.
"""

import re
from sqlalchemy.orm import Session
from database.models import SensitiveTerm

# ── Default seed terms ────────────────────────────────────────────────────────

DEFAULT_TERMS = [
    # Legal
    {"term": "lawsuit",        "category": "legal",       "action": "escalate"},
    {"term": "litigation",     "category": "legal",       "action": "escalate"},
    {"term": "attorney",       "category": "legal",       "action": "escalate"},
    {"term": "subpoena",       "category": "legal",       "action": "block"},
    {"term": "liability",      "category": "legal",       "action": "escalate"},
    {"term": "settlement",     "category": "legal",       "action": "escalate"},
    # HIPAA / Health
    {"term": "hipaa",          "category": "hipaa",       "action": "block"},
    {"term": "phi",            "category": "hipaa",       "action": "block"},
    {"term": "diagnosis",      "category": "hipaa",       "action": "escalate"},
    {"term": "medical record", "category": "hipaa",       "action": "escalate"},
    {"term": "patient",        "category": "hipaa",       "action": "flag"},
    # Financial
    {"term": "fraud",          "category": "financial",   "action": "block"},
    {"term": "embezzlement",   "category": "financial",   "action": "block"},
    {"term": "sec filing",     "category": "financial",   "action": "escalate"},
    {"term": "audit",          "category": "financial",   "action": "escalate"},
    # HR
    {"term": "termination",    "category": "hr",          "action": "escalate"},
    {"term": "harassment",     "category": "hr",          "action": "escalate"},
    {"term": "discrimination", "category": "hr",          "action": "escalate"},
    {"term": "wrongful",       "category": "hr",          "action": "escalate"},
    # PII / Confidential (keyword triggers)
    {"term": "social security", "category": "pii",        "action": "block"},
    {"term": "date of birth",   "category": "pii",        "action": "escalate"},
    {"term": "bank account",    "category": "pii",        "action": "block"},
    {"term": "routing number",  "category": "pii",        "action": "block"},
    {"term": "credit card",     "category": "pii",        "action": "block"},
    {"term": "cvv",             "category": "pii",        "action": "block"},
    {"term": "passport number", "category": "pii",        "action": "block"},
    {"term": "drivers license", "category": "pii",        "action": "escalate"},
    {"term": "confidential",    "category": "pii",        "action": "flag"},
    {"term": "proprietary",     "category": "pii",        "action": "flag"},
    {"term": "do not share",    "category": "pii",        "action": "escalate"},
    {"term": "nda",             "category": "pii",        "action": "escalate"},
]

# ── PII regex patterns (detect actual numbers, not just keywords) ─────────────

PII_PATTERNS = [
    {
        "name":     "Credit Card Number",
        "category": "pii",
        "action":   "block",
        # Visa, MC, Amex, Discover — with or without dashes/spaces
        "pattern":  re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|"          # Visa
            r"5[1-5][0-9]{14}|"                         # Mastercard
            r"3[47][0-9]{13}|"                          # Amex
            r"6(?:011|5[0-9]{2})[0-9]{12}|"             # Discover
            r"(?:\d{4}[- ]){3}\d{4})\b"                 # Generic 16-digit with separators
        ),
    },
    {
        "name":     "SSN",
        "category": "pii",
        "action":   "block",
        "pattern":  re.compile(r"\b\d{3}[-]\d{2}[-]\d{4}\b"),
    },
    {
        "name":     "US Phone Number",
        "category": "pii",
        "action":   "flag",
        "pattern":  re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    },
    {
        "name":     "Email Address",
        "category": "pii",
        "action":   "flag",
        "pattern":  re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    },
]


def seed_defaults(db: Session):
    """Seed default terms if the table is empty."""
    if db.query(SensitiveTerm).count() == 0:
        for t in DEFAULT_TERMS:
            db.add(SensitiveTerm(**t))
        db.commit()


def check_terms(db: Session, text: str, department: str = None,
                skip_pii: bool = False) -> dict:
    """
    Scan text against all sensitive terms and PII patterns.
    Returns the highest-priority match and full list of all matches.

    Action priority: block > escalate > flag

    skip_pii: when True, skip category='pii' terms and PII regex patterns.
    Use this when the request was already processed by Voice Guard — the
    actual PII numbers are gone; only conversation context words remain.
    """
    seed_defaults(db)

    text_lower = text.lower()
    priority   = {"block": 3, "escalate": 2, "flag": 1}
    matches    = []

    # ── Keyword matches ───────────────────────────────────────────────────────
    query = db.query(SensitiveTerm).filter(
        (SensitiveTerm.department == None) |
        (SensitiveTerm.department == department)
    )
    for t in query.all():
        if skip_pii and t.category == "pii":
            continue   # Voice Guard already handled PII — don't block on context words
        if t.term.lower() in text_lower:
            matches.append({
                "id":         t.id,
                "term":       t.term,
                "category":   t.category,
                "action":     t.action,
                "department": t.department,
            })

    # ── PII regex pattern matches ─────────────────────────────────────────────
    # Skip regex scan when Voice Guard already ran — [REDACTED-X] tags won't match anyway
    if not skip_pii:
        for p in PII_PATTERNS:
            hit = p["pattern"].search(text)
            if hit:
                matched_val = hit.group(0)
                redacted    = matched_val[:4] + "*" * max(0, len(matched_val) - 4)
                matches.append({
                    "id":         None,
                    "term":       f"{p['name']} detected ({redacted})",
                    "category":   p["category"],
                    "action":     p["action"],
                    "department": None,
                })

    if not matches:
        return {"triggered": False, "action": None, "matches": []}

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
