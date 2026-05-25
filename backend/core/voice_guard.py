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

# ── Presidio AI layer — Level 2: full spaCy NLP engine ───────────────────────
#
# Loads Presidio's AnalyzerEngine backed by spaCy en_core_web_sm.
# This upgrades from pattern-only regex matching to full NLP understanding:
#   - Named Entity Recognition (PERSON, LOCATION, DATE_TIME, ORG)
#   - Context-aware detection (understands sentence meaning, not just patterns)
#   - Email, IP address, driver's license, ITIN detection
#   - Lower confidence threshold (0.40 vs 0.50) — NLP is more precise
#
# Graceful fallback: if spaCy model is not available, falls back to
# pattern-only recognizers so the rule engine still runs unaffected.

_presidio_ready = False
_analyzer = None

try:
    from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    # Build spaCy NLP engine — en_core_web_sm provides tokenization,
    # POS tagging, and NER for English. Required for context-aware detection.
    _nlp_config = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }
    _provider   = NlpEngineProvider(nlp_configuration=_nlp_config)
    _nlp_engine = _provider.create_engine()

    # Custom recognizers — supplement built-in Presidio recognizers with
    # patterns for PII types that need stronger coverage in call center speech.
    _custom_recognizers = [
        # Member ID / Policy / Insurance / Medicare — not in Presidio built-ins
        PatternRecognizer(
            supported_entity="MEMBER_ID",
            patterns=[
                Pattern("MEMBER_6_12", r"(?<!\d)\d{6,12}(?!\d)", 0.55),
            ]
        ),
        # Bank routing — ABA format starts with 0
        PatternRecognizer(
            supported_entity="US_BANK_NUMBER",
            patterns=[
                Pattern("ROUTING_ABA", r"\b0\d{8}\b", 0.80),
            ]
        ),
        # Email — explicit pattern in case NLP misses informal formats
        PatternRecognizer(
            supported_entity="EMAIL_ADDRESS",
            patterns=[
                Pattern("EMAIL_STANDARD", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", 0.95),
            ]
        ),
        # IP address
        PatternRecognizer(
            supported_entity="IP_ADDRESS",
            patterns=[
                Pattern("IPV4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 0.85),
            ]
        ),
    ]

    # Build the full analyzer — combines built-in recognizers + custom ones + NLP
    _analyzer = AnalyzerEngine(
        nlp_engine=_nlp_engine,
        supported_languages=["en"],
    )
    for _r in _custom_recognizers:
        _analyzer.registry.add_recognizer(_r)

    _presidio_ready = True
    logger.info("Presidio AnalyzerEngine loaded with spaCy NLP (en_core_web_sm) — Level 2 active")

except Exception as e:
    logger.warning(f"Presidio NLP engine not available — attempting pattern-only fallback: {e}")

    # Pattern-only fallback — maintains Level 1 behavior if spaCy not available
    try:
        from presidio_analyzer import PatternRecognizer, Pattern
        _fallback_recognizers = [
            PatternRecognizer("US_SSN", patterns=[
                Pattern("SSN_DASHES",  r"\b\d{3}-\d{2}-\d{4}\b", 0.95),
                Pattern("SSN_SPACES",  r"\b\d{3} \d{2} \d{4}\b", 0.90),
                Pattern("SSN_COMPACT", r"(?<!\d)\d{9}(?!\d)",     0.55),
            ]),
            PatternRecognizer("CREDIT_CARD", patterns=[
                Pattern("CC_16_GROUPED", r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b", 0.95),
                Pattern("CC_16_COMPACT", r"(?<!\d)\d{16}(?!\d)",                    0.70),
            ]),
            PatternRecognizer("PHONE_NUMBER", patterns=[
                Pattern("PHONE_DASHES", r"\b\d{3}[-.]\d{3}[-.]\d{4}\b",  0.85),
                Pattern("PHONE_PARENS", r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}", 0.90),
            ]),
        ]

        class _PatternOnlyAnalyzer:
            """Minimal stand-in — mimics AnalyzerEngine.analyze() interface."""
            def __init__(self, recognizers):
                self._recs = recognizers
            def analyze(self, text, language="en"):
                results = []
                for rec in self._recs:
                    results.extend(rec.analyze(text=text, entities=[rec.supported_entities[0]]))
                return results

        _analyzer = _PatternOnlyAnalyzer(_fallback_recognizers)
        _presidio_ready = True
        logger.info("Presidio running in pattern-only fallback mode (no spaCy)")
    except Exception as e2:
        logger.warning(f"Presidio pattern fallback also failed — rule engine only: {e2}")


# PII type mapping: Presidio entity type → (our redact label, base confidence)
# Level 2 additions: US_DRIVER_LICENSE, US_ITIN, MEMBER_ID now active
_PRESIDIO_TYPE_MAP = {
    "US_SSN":            ("SSN",             0.92),
    "CREDIT_CARD":       ("CREDIT-CARD",     0.93),
    "PHONE_NUMBER":      ("PHONE",           0.85),
    "DATE_TIME":         ("DATE-OF-BIRTH",   0.75),
    "US_BANK_NUMBER":    ("ROUTING-NUMBER",  0.88),
    "US_PASSPORT":       ("PASSPORT",        0.88),
    "EMAIL_ADDRESS":     ("EMAIL",           0.92),
    "PERSON":            ("PERSON-NAME",     0.72),
    "LOCATION":          ("LOCATION",        0.70),
    "IP_ADDRESS":        ("IP-ADDRESS",      0.90),
    "MEDICAL_LICENSE":   ("MEMBER-ID",       0.88),
    "NRP":               ("MEMBER-ID",       0.80),
    "BANK_ACCOUNT":      ("BANK-ACCOUNT",    0.85),
    "US_DRIVER_LICENSE": ("DRIVERS-LICENSE", 0.87),  # Level 2 — now detected
    "US_ITIN":           ("TAX-ID",          0.88),  # Level 2 — now detected
    "MEMBER_ID":         ("MEMBER-ID",       0.72),  # custom recognizer
}


# ── Spoken-word digit normalization ───────────────────────────────────────────

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}

# Month names → two-digit month number strings (for DOB normalization)
_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05",     "june": "06",     "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "oct": "10", "nov": "11", "dec": "12",
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

        if word in _MONTHS:
            result.append(_MONTHS[word])
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
            "american express", "card on file",
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
    # Bank account number — separate from routing, triggered by account context.
    # "my account" and "and my account" are intentionally excluded — they fire
    # on generic phrases like "update my account information" far from any digits.
    PIIPattern(
        pii_type="BANK_ACCOUNT",
        triggers=[
            "account number", "account number is", "account is",
            "bank account", "checking account", "savings account",
            "account ending", "my account is", "my account number",
        ],
        digit_count=0, digit_min=6, digit_max=17,
        window_seconds=20,
        redact_label="BANK-ACCOUNT",
    ),
    PIIPattern(
        pii_type="DATE_OF_BIRTH",
        triggers=[
            "date of birth", "date of birth is", "dob", "birthday",
            "born on", "born in", "birth date",
        ],
        digit_count=8,
        window_seconds=20,
        redact_label="DATE-OF-BIRTH",
    ),
    PIIPattern(
        pii_type="PHONE",
        triggers=[
            "phone number", "call me at", "my number is", "reach me at",
            "you can reach me at", "cell number", "mobile number",
            "callback number", "call back at", "telephone number",
            "my phone", "phone is",
        ],
        digit_count=10,
        window_seconds=15,
        redact_label="PHONE",
    ),
    PIIPattern(
        pii_type="PASSPORT",
        triggers=[
            "passport number", "passport is", "my passport",
        ],
        digit_count=0, digit_min=6, digit_max=9,
        window_seconds=20,
        redact_label="PASSPORT",
    ),
    PIIPattern(
        pii_type="MEMBER_ID",
        triggers=[
            "member id", "member number", "employee id", "employee number",
            "medicare number", "medicaid number", "policy number",
            "insurance id", "member id is", "policy is",
        ],
        digit_count=0, digit_min=6, digit_max=12,
        window_seconds=20,
        redact_label="MEMBER-ID",
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
    detection_method: str   # "rule" | "ai" | "both"
    confidence: float
    trigger_phrase: str = ""   # The phrase that activated this detection (empty for AI/direct-pattern)


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
    detection_details: List[dict] = field(default_factory=list)  # Per-redaction breakdown


# ── Core processing functions ─────────────────────────────────────────────────

def _find_trigger(text_lower: str, pos: int) -> Optional[Tuple[PIIPattern, int, str]]:
    """
    Scan from pos for the NEAREST trigger phrase.
    Returns (pattern, end_position_of_trigger, matched_trigger_text) or None.
    Finds the earliest-starting trigger from pos.
    On position ties, prefers the longer trigger (more specific match).
    """
    best_idx: Optional[int] = None
    best_end: Optional[int] = None
    best_pattern: Optional[PIIPattern] = None
    best_trigger: str = ""
    best_len = 0

    for trigger, pattern in _TRIGGER_MAP.items():
        idx = text_lower.find(trigger, pos)
        if idx == -1:
            continue
        # Prefer earlier position; on tie, prefer longer trigger (more specific)
        if best_idx is None or idx < best_idx or (idx == best_idx and len(trigger) > best_len):
            best_idx = idx
            best_end = idx + len(trigger)
            best_pattern = pattern
            best_trigger = trigger
            best_len = len(trigger)

    if best_pattern is None:
        return None
    return best_pattern, best_end, best_trigger


def _vacuum_digits(text: str, start: int, window_chars: int = 300,
                   max_digits: int = 0, max_gap: int = 60) -> Tuple[str, int, int]:
    """
    From start position, collect digit characters within window_chars.
    Stops early if:
      - max_digits reached (prevents consuming digits from the next number)
      - gap between digits exceeds max_gap chars (a long non-digit stretch
        signals we've crossed into a new sentence / new number)

    Returns (digits_string, first_digit_pos, last_digit_pos).
    """
    segment = text[start:start + window_chars]
    first_pos = -1
    last_pos = -1
    digits = []
    last_digit_i = -1  # index in segment of the last digit collected

    for i, ch in enumerate(segment):
        if ch.isdigit():
            # Check gap from last collected digit — if too large, stop
            if last_digit_i != -1 and (i - last_digit_i) > max_gap:
                break

            if first_pos == -1:
                first_pos = start + i
            last_pos = start + i
            last_digit_i = i
            digits.append(ch)

            # Stop as soon as we have the expected number of digits.
            # No +1 tolerance — adjacent numbers (e.g. routing then account)
            # share no whitespace gap so a tolerance digit bleeds into the next number.
            if max_digits > 0 and len(digits) >= max_digits:
                break

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
    Run Presidio AnalyzerEngine on the normalized transcript.

    Level 2 (spaCy NLP active):
      - Full NLP understanding — context-aware, not just pattern matching
      - Detects: SSN, credit card, phone, email, IP, driver's license, ITIN,
        person name, location, date, bank/routing, passport, member ID
      - Confidence threshold: 0.40 (lower than pattern-only 0.50 because
        NLP context makes detections more reliable)

    Fallback (pattern-only mode):
      - Same behavior as Level 1 — regex patterns, 0.50 threshold

    Returns RedactionSpans with detection_method="ai".
    Silently returns empty list if Presidio is unavailable.
    """
    if not _presidio_ready or _analyzer is None:
        return []

    # Confidence threshold — lower for NLP mode (more precise), higher for pattern-only
    threshold = 0.40 if hasattr(_analyzer, 'nlp_engine') else 0.50

    spans = []
    try:
        results = _analyzer.analyze(text=text, language="en")
        for r in results:
            if r.score < threshold:
                continue
            pii_type, base_conf = _PRESIDIO_TYPE_MAP.get(
                r.entity_type, (r.entity_type, 0.70)
            )
            confidence = round(max(r.score, base_conf), 3)
            spans.append(RedactionSpan(
                start=r.start,
                end=r.end,
                pii_type=pii_type,
                digits_found=len(re.sub(r'\D', '', text[r.start:r.end])),
                detection_method="ai",
                confidence=confidence,
            ))
    except Exception as e:
        logger.warning(f"Presidio analysis failed: {e}")
    return spans


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
        pattern, trigger_end, trigger_text = found

        # max_digits: stop early once we have enough (prevents eating next number)
        # For range patterns (credit card 13-16), use the max as the cap
        _max_dig = pattern.digit_count if pattern.digit_count > 0 else pattern.digit_max
        digits, first_pos, last_pos = _vacuum_digits(
            normalized, trigger_end,
            window_chars=300,
            max_digits=_max_dig,
            max_gap=60,
        )

        if not digits:
            pos = trigger_end
            continue

        # Proximity guard: if the first digit is more than 80 chars after the
        # trigger, the trigger was contextual (e.g. "update my account information")
        # and these digits belong to a later, separate PII field — skip.
        if first_pos - trigger_end > 80:
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
                    trigger_phrase=trigger_text,
                ))
                if flagged:
                    warnings.append(
                        f"Partial {pattern.pii_type} match: found {len(digits)} digits, "
                        f"expected {expected}. Flagged for review."
                    )
                # Advance past the digits we just consumed so the next trigger
                # search starts after them, not from the middle of this number.
                pos = last_pos + 1
                continue

        pos = trigger_end

    # Step 4: Presidio AI layer — catches triggerless and context-aware PII
    presidio_spans = _run_presidio(normalized)
    all_spans.extend(presidio_spans)

    # Step 5: Merge overlapping spans
    # Priority rules:
    #   1. Trigger-sourced spans (state machine) always win on PII TYPE — context is authoritative
    #   2. On confidence tie, triggered span wins
    #   3. If both layers caught the same span, mark detection_method as "both"
    #
    # This prevents a 9-digit routing number from being mislabeled as SSN:
    # the state machine fired on "routing number" trigger → ROUTING label wins
    # even if Presidio's compact-SSN pattern scores higher.

    # Tag each span with whether it came from a trigger (context-aware) detection
    TRIGGER_TYPES = {"ROUTING", "DATE_OF_BIRTH", "PHONE", "CREDIT_CARD", "SSN",
                     "PASSPORT"}  # all state machine types are context-authoritative

    all_spans.sort(key=lambda s: s.start)
    merged: List[RedactionSpan] = []

    for span in all_spans:
        if not merged or span.start >= merged[-1].end:
            merged.append(span)
            continue

        # Overlapping span — decide which to keep
        existing = merged[-1]
        prev_method = existing.detection_method

        # Determine if either span came from the trigger state machine
        # (detection_method == "rule" and pii_type matches a trigger-defined type)
        existing_is_triggered = (existing.detection_method == "rule")
        incoming_is_triggered = (span.detection_method == "rule")

        if existing_is_triggered and not incoming_is_triggered:
            # Keep existing — its label came from trigger context, don't overwrite with AI guess
            winner = existing
        elif incoming_is_triggered and not existing_is_triggered:
            # Incoming has trigger context — it wins on label
            winner = span
        elif span.confidence > existing.confidence:
            # Both same source type — higher confidence wins
            winner = span
        else:
            winner = existing

        merged[-1] = winner
        # Mark as caught by both layers if methods differ
        if prev_method != span.detection_method:
            merged[-1].detection_method = "both"

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

    # Build per-redaction detail for the UI breakdown table
    detection_details = [
        {
            "pii_type":        s.pii_type,
            "trigger_phrase":  s.trigger_phrase,
            "confidence":      round(s.confidence, 3),
            "detection_method": s.detection_method,
        }
        for s in merged
    ]

    return VoiceGuardResult(
        clean_transcript=clean,
        redactions=merged,
        pii_types_found=pii_types,
        detection_method=method,
        confidence_score=round(avg_conf, 3),
        flagged_for_review=any_flagged,
        processing_ms=processing_ms,
        warnings=warnings,
        detection_details=detection_details,
    )


def presidio_available() -> bool:
    """Returns True if the Presidio analyzer loaded successfully."""
    return _presidio_ready and _analyzer is not None


def presidio_mode() -> str:
    """Returns 'nlp' if spaCy Level 2 is active, 'pattern' if fallback, 'unavailable' if neither."""
    if not _presidio_ready or _analyzer is None:
        return "unavailable"
    return "nlp" if hasattr(_analyzer, "nlp_engine") else "pattern"
