"""
api/routes_routing_config.py — Routing Rules configuration API

GET    /api/routing-config                        — current threshold + keywords + tier names
PATCH  /api/routing-config/threshold              — update token threshold
POST   /api/routing-config/keywords               — add a complexity keyword
DELETE /api/routing-config/keywords/{keyword}     — remove a keyword
PATCH  /api/routing-config/tier-names             — update custom tier display names
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from database.db import get_db
from core.routing_config import (
    get_routing_config, set_threshold, add_keyword, remove_keyword,
    PROTECTED_KEYWORDS,
)

router = APIRouter()


class RoutingConfigOut(BaseModel):
    complexity_token_threshold: int
    complexity_keywords:        list
    protected_keywords:         list
    tier_names:                 dict   # {"1":"Scout","2":"Analyst","3":"Advisor","4":"Strategist"}


class ThresholdRequest(BaseModel):
    threshold: int


class KeywordRequest(BaseModel):
    keyword: str


class TierNamesRequest(BaseModel):
    tier_1: Optional[str] = None
    tier_2: Optional[str] = None
    tier_3: Optional[str] = None
    tier_4: Optional[str] = None


def _out(cfg) -> RoutingConfigOut:
    return RoutingConfigOut(
        complexity_token_threshold=cfg.complexity_token_threshold,
        complexity_keywords=cfg.complexity_keywords,
        protected_keywords=sorted(PROTECTED_KEYWORDS),
        tier_names=cfg.tier_names,
    )


@router.get("")
def get_config(db: Session = Depends(get_db)):
    return _out(get_routing_config(db))


@router.patch("/threshold")
def update_threshold(body: ThresholdRequest, db: Session = Depends(get_db)):
    try:
        return _out(set_threshold(db, body.threshold))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/keywords", status_code=201)
def create_keyword(body: KeywordRequest, db: Session = Depends(get_db)):
    try:
        return _out(add_keyword(db, body.keyword))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/keywords/{keyword}")
def delete_keyword(keyword: str, db: Session = Depends(get_db)):
    try:
        return _out(remove_keyword(db, keyword))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/tier-names")
def update_tier_names(body: TierNamesRequest, db: Session = Depends(get_db)):
    """Update custom display names for tiers 1–4. Empty/null values revert to defaults."""
    cfg = get_routing_config(db)
    current = cfg.tier_names
    updates = {
        "1": body.tier_1 or current["1"],
        "2": body.tier_2 or current["2"],
        "3": body.tier_3 or current["3"],
        "4": body.tier_4 or current["4"],
    }
    # Validate: names must be non-empty strings between 1-30 chars
    for k, v in updates.items():
        v = v.strip()
        if not v:
            raise HTTPException(status_code=400, detail=f"Tier {k} name cannot be empty.")
        if len(v) > 30:
            raise HTTPException(status_code=400, detail=f"Tier {k} name must be 30 characters or fewer.")
        updates[k] = v
    cfg.tier_names = updates
    from datetime import datetime
    cfg.updated_at = datetime.utcnow()
    db.commit()
    return _out(cfg)
