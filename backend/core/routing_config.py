"""
core/routing_config.py — Routing Rules CRUD helpers

Provides get/set for the single RoutingConfig row that controls:
  - complexity_token_threshold  (int, 150–2000)
  - complexity_keywords         (list of strings)

The starter keywords are recommendations; administrators can remove any of them.
"""

import json
from datetime import datetime
from sqlalchemy.orm import Session
from database.models import RoutingConfig

# Kept for response compatibility with older frontends. No keywords are locked.
PROTECTED_KEYWORDS = frozenset()


def get_routing_config(db: Session) -> RoutingConfig:
    """Fetch the single config row. Creates with defaults if absent."""
    cfg = db.query(RoutingConfig).filter_by(id=1).first()
    if not cfg:
        from config import COMPLEXITY_TOKEN_THRESHOLD, COMPLEXITY_KEYWORDS
        cfg = RoutingConfig(
            id=1,
            complexity_token_threshold=COMPLEXITY_TOKEN_THRESHOLD,
            complexity_keywords_json=json.dumps(COMPLEXITY_KEYWORDS),
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def set_threshold(db: Session, threshold: int) -> RoutingConfig:
    """Update the token threshold. Valid range: 150–2000."""
    if not (150 <= threshold <= 2000):
        raise ValueError("Threshold must be between 150 and 2000.")
    cfg = get_routing_config(db)
    cfg.complexity_token_threshold = threshold
    cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    return cfg


def add_keyword(db: Session, keyword: str) -> RoutingConfig:
    """Add a keyword to the complexity list. No duplicates."""
    kw = keyword.strip().lower()
    if not kw:
        raise ValueError("Keyword cannot be empty.")
    cfg = get_routing_config(db)
    kws = cfg.complexity_keywords
    if kw in kws:
        raise ValueError(f"Keyword '{kw}' already exists.")
    kws.append(kw)
    cfg.complexity_keywords = kws
    cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    return cfg


def remove_keyword(db: Session, keyword: str) -> RoutingConfig:
    """Remove a configured complexity keyword."""
    kw = keyword.strip().lower()
    cfg = get_routing_config(db)
    kws = cfg.complexity_keywords
    if kw not in kws:
        return cfg
    kws.remove(kw)
    cfg.complexity_keywords = kws
    cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    return cfg
