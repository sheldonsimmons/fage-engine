"""
api/routes_voice.py — FAGE Voice Guard endpoints

POST /api/voice/transcript  — Process a full post-call transcript
POST /api/voice/stream      — Process a real-time chunk (Phase 2)
GET  /api/voice/stats       — Dashboard KPIs for Voice Guard panel
GET  /api/voice/events      — Recent redaction event log
"""

import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import VoiceEvent
from core.voice_guard import process_transcript, presidio_available

router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────

class TranscriptRequest(BaseModel):
    transcript: str
    call_id:    Optional[str] = None
    platform:   Optional[str] = None      # Genesys | AWS Connect | Salesforce Voice | etc.
    department: Optional[str] = None


class TranscriptResponse(BaseModel):
    call_id:            Optional[str]
    clean_transcript:   str
    redactions_count:   int
    pii_types_found:    list
    detection_method:   str
    confidence_score:   float
    flagged_for_review: bool
    processing_ms:      int
    warnings:           list
    status:             str   # clean | redacted | flagged


# ── POST /api/voice/transcript ────────────────────────────────────────────────

@router.post("/transcript", response_model=TranscriptResponse)
def process_voice_transcript(req: TranscriptRequest, db: Session = Depends(get_db)):
    """
    Process a full post-call transcript through the Voice Guard pipeline.

    Strips PII (SSN, credit card, routing number, DOB, phone) using:
    - Spoken-word number normalization
    - Trigger phrase detection + digit vacuum state machine
    - Direct regex pattern matching (for formatted numbers without trigger)

    Returns the clean transcript and a full audit record.
    The raw transcript is NOT stored if PII was found.
    """
    result = process_transcript(req.transcript)

    # Determine status
    redactions_count = len(result.redactions)
    if result.flagged_for_review:
        status = "flagged"
    elif redactions_count > 0 or result.pii_types_found:
        status = "redacted"
    else:
        status = "clean"

    # Persist to DB
    event = VoiceEvent(
        call_id=req.call_id,
        platform=req.platform or "unknown",
        department=req.department or "unknown",
        # Only store raw transcript if no PII was found (never store PII)
        raw_transcript=req.transcript if not result.redactions else None,
        clean_transcript=result.clean_transcript,
        redactions_count=redactions_count,
        pii_types_found=json.dumps(result.pii_types_found),
        detection_method=result.detection_method,
        confidence_score=result.confidence_score,
        flagged_for_review=result.flagged_for_review,
        processing_ms=result.processing_ms,
    )
    db.add(event)
    db.commit()

    return TranscriptResponse(
        call_id=req.call_id,
        clean_transcript=result.clean_transcript,
        redactions_count=redactions_count,
        pii_types_found=result.pii_types_found,
        detection_method=result.detection_method,
        confidence_score=result.confidence_score,
        flagged_for_review=result.flagged_for_review,
        processing_ms=result.processing_ms,
        warnings=result.warnings,
        status=status,
    )


# ── GET /api/voice/stats ──────────────────────────────────────────────────────

@router.get("/stats")
def get_voice_stats(db: Session = Depends(get_db)):
    """Dashboard KPI data for the Voice Guard panel."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    calls_today = db.query(func.count(VoiceEvent.id)).filter(
        VoiceEvent.timestamp >= today_start
    ).scalar() or 0

    calls_month = db.query(func.count(VoiceEvent.id)).filter(
        VoiceEvent.timestamp >= month_start
    ).scalar() or 0

    calls_total = db.query(func.count(VoiceEvent.id)).scalar() or 0

    redactions_today = db.query(func.sum(VoiceEvent.redactions_count)).filter(
        VoiceEvent.timestamp >= today_start
    ).scalar() or 0

    redactions_total = db.query(func.sum(VoiceEvent.redactions_count)).scalar() or 0

    flagged_total = db.query(func.count(VoiceEvent.id)).filter(
        VoiceEvent.flagged_for_review == True
    ).scalar() or 0

    avg_confidence = db.query(func.avg(VoiceEvent.confidence_score)).filter(
        VoiceEvent.redactions_count > 0
    ).scalar() or 0.0

    # PII type breakdown (parse JSON strings)
    all_events_with_pii = db.query(VoiceEvent.pii_types_found).filter(
        VoiceEvent.redactions_count > 0
    ).all()
    pii_breakdown: dict = {}
    for row in all_events_with_pii:
        if row[0]:
            try:
                types = json.loads(row[0])
                for t in types:
                    pii_breakdown[t] = pii_breakdown.get(t, 0) + 1
            except Exception:
                pass

    # Platform breakdown
    platform_rows = db.query(
        VoiceEvent.platform, func.count(VoiceEvent.id)
    ).group_by(VoiceEvent.platform).all()
    platform_breakdown = {p: c for p, c in platform_rows}

    return {
        "calls_today":        calls_today,
        "calls_month":        calls_month,
        "calls_total":        calls_total,
        "redactions_today":   redactions_today,
        "redactions_total":   redactions_total,
        "flagged_total":      flagged_total,
        "avg_confidence":     round(float(avg_confidence), 3),
        "pii_breakdown":      pii_breakdown,
        "platform_breakdown": platform_breakdown,
        "presidio_active":    presidio_available(),
    }


# ── GET /api/voice/events ─────────────────────────────────────────────────────

@router.get("/events")
def get_voice_events(limit: int = 20, db: Session = Depends(get_db)):
    """Recent Voice Guard redaction events for the audit log."""
    events = db.query(VoiceEvent).order_by(
        VoiceEvent.timestamp.desc()
    ).limit(limit).all()

    return [
        {
            "id":               e.id,
            "timestamp":        e.timestamp.isoformat() if e.timestamp else None,
            "call_id":          e.call_id,
            "platform":         e.platform,
            "department":       e.department,
            "clean_transcript": e.clean_transcript,
            "redactions_count": e.redactions_count,
            "pii_types_found":  json.loads(e.pii_types_found) if e.pii_types_found else [],
            "detection_method": e.detection_method,
            "confidence_score": e.confidence_score,
            "flagged_for_review": e.flagged_for_review,
            "processing_ms":    e.processing_ms,
        }
        for e in events
    ]
