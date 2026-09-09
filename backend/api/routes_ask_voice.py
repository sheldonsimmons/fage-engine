"""
api/routes_ask_voice.py — CostPilot Voice (Phase 1): speech-to-text and
text-to-speech for Ask CostPilot.

Not to be confused with Voice Guard (core/voice_guard.py,
api/routes_voice.py) -- that redacts PII from call-center transcripts a
platform like Genesys has already produced; this lets a user ask
CostPilot a question out loud. See the Voice feasibility assessment for
why the two are easy to conflate in conversation.

"Same intelligence, different interface": these two endpoints do ONLY
speech-to-text and text-to-speech. Neither one talks to Ask CostPilot's
answer engine, computes anything, or makes a claim about CostPilot data.
The frontend sends the transcript from /transcribe to the existing
POST /api/reports/bot-efficiency/ask exactly like a typed question
(tagged modality="voice" -- see AskCostPilotRequest.modality), and only
calls /speak on the final, already-validated answer text that comes
back. Ask CostPilot itself required zero changes for Voice to work.
"""

import io
import logging
import math
import os
import re
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.voice_guard import process_transcript

router = APIRouter()
logger = logging.getLogger("costpilot.ask_voice")

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25MB -- matches OpenAI's Whisper API limit
MAX_SPEECH_CHARS = 4000

_MONEY_RE = re.compile(r"\$(\d[\d,]*\.\d{3,})")
_PERCENT_RE = re.compile(r"(\d+\.\d{2,})%")


def _speech_friendly(text: str) -> str:
    """
    Round money/percentages to a natural spoken precision before TTS.
    Whisper's own numbers -- and every answer-construction call site in
    routes_efficiency.py -- carry full float precision (e.g. "$3.0243"),
    which is exactly right for the on-screen answer's numeric-fidelity
    guardrail but reads aloud as "three point zero two four three
    dollars" instead of "three dollars and two cents". This only reshapes
    punctuation for pronunciation -- it never changes a figure's value --
    so it doesn't touch the guardrail-validated text itself, only the
    audio narrated from it.
    """
    text = _MONEY_RE.sub(lambda m: f"${float(m.group(1).replace(',', '')):,.2f}", text)
    text = _PERCENT_RE.sub(lambda m: f"{float(m.group(1)):.1f}%", text)
    return text


class TranscribeResponse(BaseModel):
    transcript: str
    confidence: Optional[float] = None
    pii_redacted: bool = False


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = "alloy"


def _openai_client(timeout: float):
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("YOUR"):
        raise HTTPException(
            status_code=503,
            detail="Voice is not configured for this deployment (no OpenAI API key).",
        )
    return OpenAI(api_key=api_key, timeout=timeout)


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile = File(...)):
    """
    Speech-to-text only. Does not call Ask CostPilot -- the frontend
    sends the returned (PII-checked) transcript to the existing Ask
    endpoint itself as a follow-up call, so this endpoint's failure
    modes (bad audio, provider outage) never look like an Ask CostPilot
    failure to the guardrail layer.

    Non-streaming for Phase 1 (a full clip in, one transcript out) --
    the feasibility assessment recommends streaming STT as the highest-
    value latency improvement for a later pass, once the non-streaming
    path is proven correct end to end.
    """
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=422, detail="No audio received.")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="That clip is too long to transcribe.")

    client = _openai_client(timeout=20.0)
    try:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio.filename or "clip.webm", io.BytesIO(raw), audio.content_type or "audio/webm"),
            response_format="verbose_json",
        )
    except Exception as e:
        logger.warning("ASK_VOICE: transcription failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Could not transcribe that clip. Try again, or type your question instead.",
        )

    transcript = (getattr(result, "text", None) or "").strip()
    if not transcript:
        raise HTTPException(status_code=422, detail="Could not make out any speech in that clip.")

    # whisper-1's verbose_json response has no single top-level confidence
    # score. Per-segment avg_logprob (natural-log probability) is the
    # closest real signal it exposes -- averaging exp(avg_logprob) across
    # segments gives a rough 0-1 proxy. Deliberately not presented as a
    # precise confidence the way a purpose-built streaming STT provider
    # (Deepgram, per the feasibility assessment) would return -- this is
    # good enough to gate "should the UI ask the user to confirm the
    # transcript," not a claim of measured accuracy.
    confidence = None
    segments = getattr(result, "segments", None) or []
    logprobs = [
        seg.avg_logprob for seg in segments
        if getattr(seg, "avg_logprob", None) is not None
    ]
    if logprobs:
        confidence = round(sum(math.exp(lp) for lp in logprobs) / len(logprobs), 3)

    # Reuses Voice Guard's own PII detection wholesale (per the
    # feasibility assessment) -- cheap insurance against someone reading
    # a card number or SSN aloud into what's meant to be a business-
    # metrics question. Never call Ask CostPilot with the pre-redaction
    # text.
    guard_result = process_transcript(transcript)
    pii_redacted = bool(guard_result.redactions)
    clean_transcript = guard_result.clean_transcript if pii_redacted else transcript

    return TranscribeResponse(transcript=clean_transcript, confidence=confidence, pii_redacted=pii_redacted)


@router.post("/speak")
def speak(body: SpeakRequest):
    """
    Text-to-speech on already-validated answer text ONLY. Callers must
    pass Ask CostPilot's own final `answer` field here -- never raw,
    unvalidated model output -- so the numeric-fidelity and causal-
    language guardrails (which already ran before this text existed) are
    never bypassed by routing through this endpoint instead of the
    normal Ask response path.
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="No text to speak.")
    text = _speech_friendly(text)[:MAX_SPEECH_CHARS]

    client = _openai_client(timeout=15.0)
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice=body.voice or "alloy",
            input=text,
            response_format="mp3",
        )
    except Exception as e:
        logger.warning("ASK_VOICE: speech synthesis failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not generate speech for that answer.")

    audio_bytes = response.read() if hasattr(response, "read") else response.content
    return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")
