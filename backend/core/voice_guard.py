"""
core/voice_guard.py — FAGE Voice Guard Pipeline

Processes raw voice transcripts (from any ASR platform) and redacts PII
before the transcript is passed to FAGE's AI governance pipeline.

Pipeline:
  1. Normalization     — convert spoken numbers to digits, strip ASR artifacts
  2. Trigger Detection — look for PII context phrases ("social security", etc.)
  3. State Machine     — collect digits in a window after each trigger
  4. Presidio AI       — catch triggerless PII the rule engine misses
  5. Merge + Redact    — combine both layers, most restrictive wins
  6. Audit Record      — return structured result for logging
"""

import re
import time
import json
import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# ── Presidio AI layer (Phase 2) — graceful fallback if not installed ──────────

_presidio_ready = False
_analyzer = None

try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_analyzer import RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    # Use pattern-only recognizers — no spaCy NLP model required.
    # This keeps the Heroku slug small while still catching formatted PII
    # that has no trigger phrase (the gap the rule engine misses).

    registry = RecognizerRegistry()

    # SSN: 123-45-6789 or 123 45 6789
    registry.add_recognizer(PatternRecognizer(
        supported_entity="US_SSN",
        patterns=[
            Pattern("SSN_DASHES",  r"\b\d{3}-\d{2}-\d{4}\b",         0.95),
            Pattern("SSN_SPACES",  r"\b\d{3}\s\d{2}\s\d{4}\b",       0.90),
            Pattern("SSN_COMPACT", r"\b(?<!\d)\d{9}(?!\d)\b",         0.60),
        ]
    ))

    # Credit card: 16 digits with spaces or dashes
    registry.add_recognizer(PatternRecognizer(
        supported_entity="CREDIT_CARD",
        patterns=[
            Pattern("CC_DASHES", r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b", 0.95),
            Pattern("CC_COMPACT", r"\b\d{16}\b",                             0.70),
        ]
    ))

    # US phone: 555-867-5309 or (555) 867-5309
    registry.add_recognizer(PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        patterns=[
            Pattern("PHONE_DASHES",  r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",      0.85),
            Pattern("PHONE_PARENS",  r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}",          0.90),
        ]
    ))

    # Routing number: 9-digit ABA
    registry.add_recognizer(PatternRecognizer(
        supported_entity="US_BANK_NUMBER",
        patterns=[
            Pattern("ROUTING", r"\b0[0-9]{8}\b", 0.75),
        ]
    ))

    # Build engine with NO NLP backend (pattern-only mode)
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": []
    })

    try:
        nlp_engine = provider.create_engine()
    except Exception:
        nlp_engine = None

    _analyzer = AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=["en"],
    )
    _presidio_ready = True
    logger.info("Presidio pattern-only layer loaded successfully (no spaCy required)")
except Exception as e:
    logger.warning(f"Presidio not available — running rule engine only: {e}")


# PII type mapping: Presidio entity type → our redact label
_PRESIDIO_TYPE_MAP = {
    "US_SSN":           ("SSN",          0.92),
    "CREDIT_CARD":      ("CREDIT-CARD",  0.93),
    "PHONE_NUMBER":     ("PHONE",        0.85),
    "DATE_TIME":        ("DATE-OF-BIRTH",0.75),
    "US_BANK_NUMBER":   ("ROUTING-NUMBER",0.88),
    "US_PASSPORT":      ("PASSPORT",     0.88),
    "EMAIL_ADDRESS":    ("EMAIL",        0.90),
    "PERSON":           ("PERSON-NAME",  0.72),
    "LOCATION":         ("LOCATION",     0.70),
    "IP_ADDRESS":       ("IP-ADDRESS",   0.90),
    "MEDICAL_LICENSE":  ("MEDICAL-ID",   0.88),
    "NRP":              ("ID-NUMBER",    0.80),
}


# ── Spoken-word digit normalization ───────────────────────────────────────────

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}

_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

# Filler words and ASR artifacts to strip before digit collection
_FILLERS = {
    "uh", "um", "umm", "ummm", "ah", "ahh", "er", "hmm", "hm",
    "like", "so", "well", "okay", "ok", "let", "me", "see",
    "hold", "on", "just", "a", "second", "moment", "wait",
    "actually", "i", "think", "its", "it's", "that's", "thats",
    "and", "then", "is", "are", "the",
}

# ASR artifact patterns to clean before processing
_ASR_ARTIFACTS = re.compile(
    r'\[inaudible\]|\[crosstalk\]|\[noise\]|\[laughter\]|\[silence\]|\[pause\]',
    re.IGNORECASE
)


def normalize_spoken_numbers(text: str) -> str:
    """
    Convert spoken number words to digit strings.

    Examples:
      "one two three"           → "1 2 3"
      "forty five"              → "45"
      "six seven eight nine"    → "6 7 8 9"
      "twenty three forty five" → "23 45"
    """
    # Strip ASR artifacts first
    text = _ASR_ARTIFACTS.sub(" ", text)

    words = text.lower().split()
    result = []
    i = 0
    while i < len(words):
        word = words[i].strip(".,!?;:-")

        # Check compound: "twenty one", "forty five", etc.
        if word in _TENS:
            if i + 1 < len(words):
                next_word = words[i + 1].strip(".,!?;:-")
                if next_word in _ONES and _ONES[next_word] < 10:
                    result.append(str(_TENS[word] + _ONES[next_word]))
                    i += 2
                    continue
            result.append(str(_TENS[word]))
            i += 1
            continue

        if word in _ONES:
            result.append(str(_ONES[word]))
            i += 1
            continue

        # Keep original word
        result.append(words[i])
        i += 1

    return " ".join(result)


# ── PII trigger definitions ───────────────────────────────────────────────────

@dataclass
class PIIPattern:
    pii_type: str           # SSN | CREDIT_CARD | DATE_OF_BIRTH | ROUTING | PASSPORT | PHONE
    triggers: List[str]     # Phrases that activate the collection window
    digit_count: int        # Exact digits expected (0 = range)
    digit_min: int = 0      # Minimum digits (used when digit_count == 0)
    digit_max: int = 0      # Maximum digits (used when digit_count == 0)
    window_seconds: int = 15
    redact_label: str = ""

    def __post_init__(self):
        if not self.redact_label:
            self.redact_label = self.pii_type


PII_PATTERNS = [
    PIIPattern(
        pii_type="SSN",
        triggers=[
            "social security number", "social security", "ssn",
            "my social", "your social", "social is", "social number",
        ],
        digit_count=9,
        window_seconds=20,
        redact_label="SSN",
    ),
    PIIPattern(
        pii_type="CREDIT_CARD",
        triggers=[
            "credit card", "card number", "debit card", "card ending",
            "card is", "my card", "visa", "mastercard", "amex",
            "american express",
        ],
        digit_count=0, digit_min=13, digit_max=16,
        window_seconds=20,
        redact_label="CREDIT-CARD",
    ),
    PIIPattern(
        pii_type="ROUTING",
        triggers=[
            "routing number", "aba number", "bank routing",
            "routing is", "routing and account",
        ],
        digit_count=9,
        window_seconds=20,
        redact_label="ROUTING-NUMBER",
    ),
    PIIPattern(
        pii_type="DATE_OF_BIRTH",
        triggers=[
            "date of birth", "date of birth is", "dob", "birthday",
            "born on", "born in",
        ],
        digit_count=8,   # MMDDYYYY
        window_seconds=20,
        redact_label="DATE-OF-BIRTH",
    ),
    PIIPattern(
        pii_type="PHONE",
        triggers=[
            "phone number", "call me at", "my number is", "reach me at",
            "cell number", "mobile number",
        ],
        digit_count=10,
        window_seconds=15,
        redact_label="PHONE",
    ),
]

# Build a fast lookup: trigger phrase → PIIPattern
_TRIGGER_MAP: dict = {}
for _p in PII_PATTERNS:
    for _t in _p.triggers:
        _TRIGGER_MAP[_t.lower()] = _p


# ── Redaction result types ────────────────────────────────────────────────────

@dataclass
class RedactionSpan:
    start: int
    end: int
    pii_type: str
    digits_found: int
    detection_method: str   # "rule"
    confidence: float


@dataclass
class VoiceGuardResult:
    clean_transcript: str
    redactions: List[RedactionSpan]
    pii_types_found: List[str]
    detection_method: str       # rule | none
    confidence_score: float     # avg confidence across redactions
    flagged_for_review: bool    # True if any low-confidence or partial match
    processing_ms: int
    warnings: List[str] = field(default_factory=list)


# ── Core processing functions ─────────────────────────────────────────────────

def _find_trigger(text_lower: str, pos: int) -> Optional[Tuple[PIIPattern, int]]:
    """
    Scan from pos for any trigger phrase.
    Returns (pattern, end_position_of_trigger) or None.
    Checks longest triggers first to avoid partial matches.
    """
    sorted_triggers = sorted(_TRIGGER_MAP.keys(), key=len, reverse=True)
    for trigger in sorted_triggers:
        idx = text_lower.find(trigger, pos)
        if idx != -1:
            return _TRIGGER_MAP[trigger], idx + len(trigger)
    return None


def _vacuum_digits(text: str, start: int, window_chars: int = 300) -> Tuple[str, int, int]:
    """
    From start position, collect all digit characters within window_chars,
    ignoring filler words and whitespace. Return (digits_string, first_digit_pos, last_digit_pos).
    """
    segment = text[start:start + window_chars]
    first_pos = -1
    last_pos = -1
    digits = []

    for i, ch in enumerate(segment):
        if ch.isdigit():
            if first_pos == -1:
                first_pos = start + i
            last_pos = start + i
            digits.append(ch)

    return "".join(digits), (first_pos if first_pos != -1 else start), last_pos


def _score_confidence(digits_found: int, expected: int,
                      min_d: int, max_d: int) -> Tuple[float, bool]:
    """Return (confidence, flagged_for_review)."""
    if expected > 0:
        if digits_found == expected:
            return 0.95, False
        elif digits_found == expected + 1:   # extra digit spoken
            return 0.88, False
        elif digits_found >= expected - 1:   # one digit missing
            return 0.65, True               # flag — partial
        else:
            return 0.30, True
    else:
        # Range match (credit card: 13-16 digits)
        if min_d <= digits_found <= max_d:
            return 0.92, False
        elif digits_found >= min_d - 1:
            return 0.60, True
        else:
            return 0.25, True


# ── Direct-pattern fallback (no trigger required) ─────────────────────────────

# These fire even without a trigger phrase — catch "it's 123-45-6789" with no preamble
_DIRECT_PATTERNS = [
    (re.compile(r'\b(\d{3})[-\s](\d{2})[-\s](\d{4})\b'), "SSN", "SSN", 0.97),
    (re.compile(r'\b(\d{4}[\s-]){3}\d{4}\b'),             "CREDIT_CARD", "CREDIT-CARD", 0.93),
    (re.compile(r'\b(\d{3})[-.\s](\d{3})[-.\s](\d{4})\b'),"PHONE", "PHONE", 0.85),
]


def _apply_direct_patterns(text: str) -> List[RedactionSpan]:
    """Catch formatted PII that appears without a trigger phrase."""
    spans = []
    for pattern, pii_type, label, conf in _DIRECT_PATTERNS:
        for m in pattern.finditer(text):
            spans.append(RedactionSpan(
                start=m.start(), end=m.end(),
                pii_type=pii_type,
                digits_found=len(re.sub(r'\D', '', m.group())),
                detection_method="rule",
                confidence=conf,
            ))
    return spans


# ── Presidio AI layer ─────────────────────────────────────────────────────────

def _run_presidio(text: str) -> List[RedactionSpan]:
    """
    Run Presidio analyzer on the normalized transcript.
    Returns RedactionSpans for any PII found, with detection_method="ai".
    Silently returns empty list if Presidio is not available.
    """
    if not _presidio_ready or _analyzer is None:
        return []

    try:
        results = _analyzer.analyze(text=text, language="en")
        spans = []
        for r in results:
            if r.entity_type not in _PRESIDIO_TYPE_MAP:
                continue
            pii_type, base_conf = _PRESIDIO_TYPE_MAP[r.entity_type]
            # Use the higher of Presidio's score and our base confidence
            confidence = round(max(r.score, base_conf), 3)
            if confidence < 0.50:
                continue   # skip low-confidence detections
            spans.append(RedactionSpan(
                start=r.start,
                end=r.end,
                pii_type=pii_type,
                digits_found=len(re.sub(r'\D', '', text[r.start:r.end])),
                detection_method="ai",
                confidence=confidence,
            ))
        return spans
    except Exception as e:
        logger.warning(f"Presidio analysis failed: {e}")
        return []


# ── Main entry point ──────────────────────────────────────────────────────────

def process_transcript(raw: str) -> VoiceGuardResult:
    """
    Full Voice Guard pipeline — dual layer (rule engine + Presidio AI).

    1. Normalize spoken numbers to digits
    2. Rule engine: direct pattern matching (formatted SSNs)
    3. Rule engine: trigger-based state machine (interrupted speech)
    4. AI layer: Presidio (triggerless detection, context-aware)
    5. Merge all spans — most restrictive wins on overlaps
    6. Apply redactions
    7. Return VoiceGuardResult
    """
    t0 = time.time()

    # Step 1: Normalize
    normalized = normalize_spoken_numbers(raw)
    text_lower  = normalized.lower()

    all_spans: List[RedactionSpan] = []
    warnings: List[str] = []

    # Step 2: Direct pattern matching (formatted numbers, no trigger needed)
    all_spans.extend(_apply_direct_patterns(normalized))

    # Step 3: Trigger-based state machine
    pos = 0
    while pos < len(text_lower):
        found = _find_trigger(text_lower, pos)
        if not found:
            break
        pattern, trigger_end = found

        digits, first_pos, last_pos = _vacuum_digits(normalized, trigger_end, window_chars=300)

        if not digits:
            pos = trigger_end
            continue

        conf, flagged = _score_confidence(
            len(digits),
            pattern.digit_count,
            pattern.digit_min,
            pattern.digit_max,
        )

        expected = pattern.digit_count or pattern.digit_max
        if len(digits) >= max(pattern.digit_min, pattern.digit_count) - 1 and last_pos > first_pos:
            if len(digits) >= (pattern.digit_min if pattern.digit_count == 0 else pattern.digit_count) - 1:
                all_spans.append(RedactionSpan(
                    start=first_pos,
                    end=last_pos + 1,
                    pii_type=pattern.pii_type,
                    digits_found=len(digits),
                    detection_method="rule",
                    confidence=conf,
                ))
                if flagged:
                    warnings.append(
                        f"Partial {pattern.pii_type} match: found {len(digits)} digits, "
                        f"expected {expected}. Flagged for review."
                    )

        pos = trigger_end

    # Step 4: Presidio AI layer — catches triggerless and context-aware PII
    presidio_spans = _run_presidio(normalized)
    all_spans.extend(presidio_spans)

    # Step 5: Merge overlapping spans (keep highest confidence)
    all_spans.sort(key=lambda s: s.start)
    merged: List[RedactionSpan] = []
    for span in all_spans:
        if merged and span.start < merged[-1].end:
            # Overlap — keep higher confidence, prefer "both" as detection method
            if span.confidence >= merged[-1].confidence:
                prev = merged[-1]
                merged[-1] = span
                # Mark as caught by both layers if methods differ
                if prev.detection_method != span.detection_method:
                    merged[-1].detection_method = "both"
        else:
            merged.append(span)

    # Step 6: Apply redactions (process in reverse to preserve indices)
    clean = normalized
    for span in sorted(merged, key=lambda s: s.start, reverse=True):
        tag = f"[REDACTED-{span.pii_type}]"
        clean = clean[:span.start] + tag + clean[span.end:]

    # Step 7: Build result
    pii_types   = list({s.pii_type for s in merged})
    avg_conf    = (sum(s.confidence for s in merged) / len(merged)) if merged else 1.0
    any_flagged = any(s.confidence < 0.80 for s in merged) or bool(warnings)

    # Detection method summary
    methods = {s.detection_method for s in merged}
    if "both" in methods or ({"rule", "ai"} <= methods):
        method = "both"
    elif "ai" in methods and "rule" not in methods:
        method = "ai"
    elif merged:
        method = "rule"
    else:
        method = "none"

    processing_ms = int((time.time() - t0) * 1000)

    return VoiceGuardResult(
        clean_transcript=clean,
        redactions=merged,
        pii_types_found=pii_types,
        detection_method=method,
        confidence_score=round(avg_conf, 3),
        flagged_for_review=any_flagged,
        processing_ms=processing_ms,
        warnings=warnings,
    )


def presidio_available() -> bool:
    """Returns True if the Presidio AI layer loaded successfully."""
    return _presidio_ready
