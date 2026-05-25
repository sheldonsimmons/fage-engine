"""
api/routes_routing_config.py — Routing Rules configuration API

GET    /api/routing-config                    — current threshold + keywords
PATCH  /api/routing-config/threshold          — update token threshold
POST   /api/routing-config/keywords           — add a complexity keyword
DELETE /api/routing-config/keywords/{keyword} — remove a keyword
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

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


class ThresholdRequest(BaseModel):
    threshold: int


class KeywordRequest(BaseModel):
    keyword: str


def _out(cfg) -> RoutingConfigOut:
    return RoutingConfigOut(
        complexity_token_threshold=cfg.complexity_token_threshold,
        complexity_keywords=cfg.complexity_keywords,
        protected_keywords=sorted(PROTECTED_KEYWORDS),
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
