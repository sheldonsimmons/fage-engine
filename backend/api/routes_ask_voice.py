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

_MONEY_RE = re.compile(r"\$(\d[\d,]*)(?:\.(\d+))?")
_PERCENT_RE = re.compile(r"(\d+\.\d{2,})%")
_MONTHS = {
    "Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April",
    "Jun": "June", "Jul": "July", "Aug": "August", "Sept": "September",
    "Sep": "September", "Oct": "October", "Nov": "November", "Dec": "December",
}
# Longest-first so "Sept" matches before the "Sep" prefix would.
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?(?=\s*\d)"
)


_TABLE_SEP_RE = re.compile(r"^\|?[\s:|-]+\|?\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+", re.MULTILINE)


def _markdown_table_to_speech(text: str) -> str:
    """
    A markdown table renders fine on screen (the frontend's markdown
    renderer turns it into a real <table>), but /speak receives the same
    raw "| Engineering | $3.02 | 60.5% | $1.98 |" text -- TTS reads the
    cell values but the pipe/dash-heavy header and separator rows read as
    noise, so a listener hears numbers with no idea which column each one
    is. Converts each data row into "Engineering: Cap $5.00, Spent
    $3.02, ..." using the header row as labels, before any further
    speech normalization runs.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1].strip())
        ):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            row_sentences = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if cells and cells[0]:
                    parts = [
                        f"{headers[j]} {cells[j]}"
                        for j in range(1, min(len(cells), len(headers)))
                        if cells[j]
                    ]
                    row_sentences.append(
                        f"{cells[0]}: {', '.join(parts)}." if parts else f"{cells[0]}."
                    )
                i += 1
            out.append(" ".join(row_sentences))
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
)
_SCALES = ("", "thousand", "million", "billion", "trillion")


def _three_digits_to_words(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        tens_word = _TENS[n // 10]
        n %= 10
        words.append(f"{tens_word}-{_ONES[n]}" if n else tens_word)
    elif n > 0:
        words.append(_ONES[n])
    return " ".join(words)


def _int_to_words(n: int) -> str:
    """
    Spell out an integer for TTS. A long run of digits (e.g. "14202000") is
    exactly what a numeric-fidelity guardrail needs on screen, but is what
    most TTS voices read back digit-by-digit or in mangled pairs instead of
    as a number -- reproduced live: "$14,202,000.00" and "$22,847,000.00"
    were both heard as disconnected digit fragments, not "fourteen million
    two hundred two thousand" / "twenty-two million eight hundred forty-
    seven thousand". Self-contained rather than a new dependency -- bounded
    problem, and every other TTS reshaping in this module is already
    hand-rolled the same way (see _MONTHS, _money_to_words itself).
    """
    if n == 0:
        return "zero"
    groups = []
    working = n
    while working > 0:
        groups.append(working % 1000)
        working //= 1000
    parts = []
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if group == 0:
            continue
        scale = _SCALES[index]
        group_words = _three_digits_to_words(group)
        parts.append(f"{group_words} {scale}".strip())
    return " ".join(parts)


def _money_to_words(match: re.Match) -> str:
    dollars = int(match.group(1).replace(",", ""))
    cents = round(float("0." + match.group(2)) * 100) if match.group(2) else 0
    if dollars == 0 and cents:
        return f"{cents} cent" + ("" if cents == 1 else "s")
    dollar_part = f"{_int_to_words(dollars)} dollar" + ("" if dollars == 1 else "s")
    if not cents:
        return dollar_part
    cent_part = f"{cents} cent" + ("" if cents == 1 else "s")
    return f"{dollar_part} and {cent_part}"


def _speech_friendly(text: str) -> str:
    """
    Reshape money, percentages, and month abbreviations into what TTS
    actually pronounces naturally. Whisper's own numbers -- and every
    answer-construction call site in routes_efficiency.py -- carry full
    float precision and calendar-standard abbreviations (e.g. "$3.0243",
    "Aug 9"), which is exactly right for the on-screen answer's numeric-
    fidelity guardrail, but read aloud as "three point zero two four
    three dollars" and "Aug" (as a mangled short word) instead of "three
    dollars and two cents" and "August". This only reshapes punctuation
    and abbreviations for pronunciation -- it never changes a figure's
    value or a date's meaning -- so it doesn't touch the guardrail-
    validated text itself, only the audio narrated from it.
    """
    text = _markdown_table_to_speech(text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _BULLET_RE.sub("", text)
    text = _MONEY_RE.sub(_money_to_words, text)
    text = _PERCENT_RE.sub(lambda m: f"{float(m.group(1)):.1f}%", text)
    text = _MONTH_RE.sub(lambda m: _MONTHS[m.group(1)], text)
    return text


class TranscribeResponse(BaseModel):
    transcript: str
    confidence: Optional[float] = None
    pii_redacted: bool = False


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    # "onyx" -- OpenAI tts-1's clearly male-register voice -- to match the
    # avatar (a male portrait). "alloy" (the previous default) reads
    # female/neutral, confirmed live as a mismatch once the avatar shipped.
    voice: str = "onyx"


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
            # Without this, Whisper auto-detects the spoken language --
            # confirmed live 2026-09-19: a real English question got
            # mis-detected, and everything downstream (transcript, the
            # LLM's answer, the spoken TTS reply) followed that instead
            # of staying English. Every other surface in this app (UI
            # copy, CostPilot's own answers, prompts) is English-only, so
            # there's no case where a different detected language would
            # be correct -- pinning it removes the misdetection entirely
            # rather than only reducing how often it happens.
            language="en",
            # Whisper's prompt param biases word choice toward vocabulary it
            # contains -- it does not add facts or context, just spelling
            # preference among acoustically similar candidates. Without this,
            # "spend" (a CostPilot-specific noun with no strong acoustic
            # anchor) loses to generic-sounding neighbors: confirmed live,
            # "Marcus Reed's spend" was transcribed as "Marcus's reed spin"
            # and, separately, "spend" alone as "spin" -- both silently
            # misinterpreted downstream rather than caught as unrecognized.
            prompt=(
                "CostPilot governs enterprise AI spend, tokens, requests, "
                "budgets, departments, agents, models, and accounts. Common "
                "terms: spend, cost, budget cap, tokens, governed requests, "
                "agent adoption, department, Salesforce, ServiceNow, "
                "Scout, Analyst, Advisor, Strategist, Flagship."
            ),
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

    # Whisper hallucinates plausible-sounding but fake text from silence
    # or ambient noise instead of returning nothing -- confirmed live
    # 2026-09-11 via the mobile voice page's new auto-listen flow (mic
    # starts recording right after CostPilot finishes speaking, before
    # the user necessarily starts talking): "Thank you for watching!" and
    # "Learn more at www.salesforce.com" both got transcribed, then
    # answered as if they were real questions. Both are well-documented
    # classic Whisper hallucination artifacts (YouTube-outro/sponsor-
    # plug phrases from its training data), not something the user said.
    # no_speech_prob (Whisper's own per-segment "this segment probably
    # has no real speech in it" estimate) is the purpose-built signal for
    # exactly this, not a repurposed one like avg_logprob below -- a
    # high no_speech_prob is treated the same as an empty transcript,
    # before this ever reaches Ask CostPilot as a "question."
    segments = getattr(result, "segments", None) or []
    no_speech_probs = [
        seg.no_speech_prob for seg in segments
        if getattr(seg, "no_speech_prob", None) is not None
    ]
    if no_speech_probs and (sum(no_speech_probs) / len(no_speech_probs)) > 0.6:
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
            voice=body.voice or "onyx",
            input=text,
            response_format="mp3",
        )
    except Exception as e:
        logger.warning("ASK_VOICE: speech synthesis failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not generate speech for that answer.")

    audio_bytes = response.read() if hasattr(response, "read") else response.content
    return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")
