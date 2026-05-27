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
  6. Level 3 Boost     — context-aware confidence scoring and cluster detection
  7. Audit Record      — return structured result for logging
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
        # Email — standard format + spoken format ("derek dot morrison at acme dot com")
        PatternRecognizer(
            supported_entity="EMAIL_ADDRESS",
            patterns=[
                Pattern("EMAIL_STANDARD", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", 0.95),
                Pattern("EMAIL_SPOKEN",   r"\b[\w][\w.]*(?:\s+dot\s+[\w]+)*\s+at\s+[\w]+(?:\s+[\w]+)*\s+dot\s+(?:com|org|net|edu|gov|io)\b", 0.88),
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
    # Ordinal forms — spoken dates: "the ninth", "august 9th", "twenty-first"
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
    # Shorthand ordinals ("9th", "21st", "3rd") handled below via regex
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

    # Convert numeric ordinals to plain digits: "9th" → "9", "21st" → "21"
    text = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', r'\1', text, flags=re.IGNORECASE)

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

        # Month names are NOT normalized globally — they are handled specifically
        # in the DOB trigger state machine so they don't corrupt the clean output
        # for non-PII contexts ("the meeting is scheduled for March 15th").

        # Spoken separator words → punctuation
        if word in ("dash", "hyphen", "slash"):
            result.append("-")
            i += 1
            continue

        # Keep original word
        result.append(words[i])
        i += 1

    normalized = " ".join(result)

    # Collapse "D D D - D D - D D D D" → "DDD-DD-DDDD" (spoken SSN with dash separators)
    normalized = re.sub(
        r'(?<!\d)(\d) (\d) (\d) - (\d) (\d) - (\d) (\d) (\d) (\d)(?!\d)',
        r'\1\2\3-\4\5-\6\7\8\9',
        normalized,
    )
    # Collapse long runs of space-separated single digits (compact SSN / card)
    # e.g. "4 2 8 5 5 9 1 7 3" → "428559173"
    normalized = re.sub(
        r'(?<!\d)(\d)( \d){8,15}(?!\d)',
        lambda m: m.group(0).replace(" ", ""),
        normalized,
    )

    return normalized


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
            "full social", "confirm your social", "your full social",
            "social for me", "social on file", "my social number",
            "social number is",
        ],
        digit_count=9,
        window_seconds=20,
        redact_label="SSN",
    ),
    PIIPattern(
        pii_type="CREDIT_CARD",
        triggers=[
            "credit card", "card number", "debit card",
            "card is", "my card", "my visa", "visa card",
            "my mastercard", "mastercard card", "my amex", "amex card",
            "american express card", "card on file", "full number is",
            "full card is", "full card number",
        ],
        digit_count=0, digit_min=13, digit_max=16,
        window_seconds=20,
        redact_label="CREDIT-CARD",
    ),
    # "ending in" / "card ending" — caller gives last 4 digits only.
    # Treated as partial PII — still redacted but only 4 digits collected.
    # Kept separate so it doesn't bleed into a following full-number trigger.
    PIIPattern(
        pii_type="CREDIT_CARD",
        triggers=[
            "ending in", "card ending in", "ends in", "card ending",
            "last four", "last 4", "last four digits", "last 4 digits",
        ],
        digit_count=0, digit_min=4, digit_max=4,
        window_seconds=10,
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
        digit_count=0, digit_min=4, digit_max=8,
        window_seconds=20,
        redact_label="DATE-OF-BIRTH",
    ),
    PIIPattern(
        pii_type="PHONE",
        triggers=[
            "phone number", "call me at", "reach me at",
            "you can reach me at", "cell number", "mobile number",
            "callback number", "call back at", "telephone number",
            "my phone", "phone is", "my new number", "new number is",
            "my cell is", "my direct", "direct number", "direct line",
            "text me at", "call me on", "my phone number", "my cell number",
            "my number is", "other number is",
        ],
        digit_count=10,
        window_seconds=15,
        redact_label="PHONE",
    ),
    PIIPattern(
        pii_type="PASSPORT",
        triggers=[
            "passport number", "passport is", "my passport",
            "visa number", "green card number", "alien registration",
            "a-number", "national id",
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
            "military id", "voter registration", "student id", "school id",
            "workday id", "payroll id", "badge number", "employee number is",
        ],
        digit_count=0, digit_min=6, digit_max=12,
        window_seconds=20,
        redact_label="MEMBER-ID",
    ),
    # ── CVV / security code — 3-4 digits ─────────────────────────────────────
    PIIPattern(
        pii_type="CVV",
        triggers=[
            "cvv", "cvv is", "security code", "security code is",
            "card verification", "cvc", "cvc is", "cvv2",
            "three digit code", "four digit code", "back of the card",
        ],
        digit_count=0, digit_min=3, digit_max=4,
        window_seconds=10,
        redact_label="CVV",
    ),
    # ── PIN — 4-6 digits ──────────────────────────────────────────────────────
    PIIPattern(
        pii_type="PIN",
        triggers=[
            "pin is", "my pin", "pin number", "pin number is",
            "atm pin", "debit pin", "access pin", "door code",
            "badge code", "access code is",
        ],
        digit_count=0, digit_min=4, digit_max=6,
        window_seconds=10,
        redact_label="PIN",
    ),
    # ── OTP / verification codes — 4-8 digits ─────────────────────────────────
    PIIPattern(
        pii_type="OTP",
        triggers=[
            "verification code", "verification code is", "one time code",
            "one-time code", "one time passcode", "otp", "otp is",
            "mfa code", "2fa code", "two factor code", "authenticator code",
            "reset code", "reset code is", "temporary code", "passcode is",
            "the code is", "your code is", "confirmation code",
        ],
        digit_count=0, digit_min=4, digit_max=8,
        window_seconds=10,
        redact_label="OTP",
    ),
    # ── Card expiry — 4 digits (MMYY) ────────────────────────────────────────
    PIIPattern(
        pii_type="CARD_EXPIRY",
        triggers=[
            "expiration date", "expiration date is", "expiry date",
            "expiry is", "expires", "card expires", "good through",
            "valid through", "valid thru", "exp date",
        ],
        digit_count=0, digit_min=4, digit_max=4,
        window_seconds=10,
        redact_label="CARD-EXPIRY",
    ),
    # ── Billing ZIP — 5 digits ────────────────────────────────────────────────
    PIIPattern(
        pii_type="ZIP_CODE",
        triggers=[
            "billing zip", "billing zip code", "billing postal code",
            "zip code is", "my zip is", "zip is", "postal code is",
        ],
        digit_count=5,
        window_seconds=10,
        redact_label="ZIP-CODE",
    ),
    # ── Additional phone triggers (relationship / role context) ───────────────
    PIIPattern(
        pii_type="PHONE",
        triggers=[
            "his number is", "her number is", "their number is",
            "patient phone", "patient's phone", "patient phone number",
            "customer phone", "work phone", "home phone",
            "emergency phone", "emergency number", "whatsapp is",
            "signal number", "best number", "best number to reach",
        ],
        digit_count=10,
        window_seconds=15,
        redact_label="PHONE",
    ),
    # ── Additional DOB triggers (institutional contexts) ──────────────────────
    PIIPattern(
        pii_type="DATE_OF_BIRTH",
        triggers=[
            "patient dob", "applicant dob", "customer birthdate",
            "my child was born on", "child's birthday", "child dob",
            "their date of birth", "his date of birth", "her date of birth",
            "date of birth on file",
        ],
        digit_count=0, digit_min=4, digit_max=8,
        window_seconds=20,
        redact_label="DATE-OF-BIRTH",
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
    # SSN — standard 3-2-4 format
    (re.compile(r'\b(\d{3})[-\s](\d{2})[-\s](\d{4})\b'),  "SSN", "SSN", 0.97),
    # SSN — non-standard 9-digit groupings (not 3-3-4 which would be phone)
    # Separator allows any mix of spaces and dashes: "-", " ", " -", "- ", " - "
    # e.g. "441-617877", "441 617877", "441 -617877"
    (re.compile(r'\b\d{3}[\s\-]+\d{6}\b'),                 "SSN", "SSN", 0.88),
    (re.compile(r'\b\d{6}[\s\-]+\d{3}\b'),                 "SSN", "SSN", 0.85),
    (re.compile(r'\b\d{4}[\s\-]+\d{5}\b'),                 "SSN", "SSN", 0.85),
    (re.compile(r'\b\d{2}[\s\-]+\d{7}\b'),                 "SSN", "SSN", 0.85),
    # Credit card — 4x4 grouped
    (re.compile(r'\b(\d{4}[\s-]){3}\d{4}\b'),              "CREDIT_CARD", "CREDIT-CARD", 0.93),
    # Phone — standard 3-3-4 format (10 digits)
    (re.compile(r'\b(\d{3})[-.\s](\d{3})[-.\s](\d{4})\b'), "PHONE", "PHONE", 0.85),
]


def _apply_direct_patterns(text: str) -> List[RedactionSpan]:
    """Catch formatted PII that appears without a trigger phrase."""
    text_lower = text.lower()
    spans = []
    for pattern, pii_type, label, conf in _DIRECT_PATTERNS:
        for m in pattern.finditer(text):
            # Suppress direct PHONE pattern when DOB trigger phrase appears
            # within 150 chars before the match — date digits read aloud look
            # identical to phone number patterns (e.g. "08 09 1985").
            if pii_type == "PHONE":
                dob_window = text_lower[max(0, m.start() - 150):m.start()]
                if _DOB_CONTEXT.search(dob_window):
                    continue
            spans.append(RedactionSpan(
                start=m.start(), end=m.end(),
                pii_type=pii_type,
                digits_found=len(re.sub(r'\D', '', m.group())),
                detection_method="rule",
                confidence=conf,
            ))
    return spans


# ── Presidio AI layer ─────────────────────────────────────────────────────────

# Entity types Presidio detects that are NOT PII in a call-center context.
# Organization names (Acme Corp, Microsoft) and NRP (nationalities/religions)
# are not sensitive data we should redact from transcripts.
_PRESIDIO_SKIP_ENTITIES = {"ORGANIZATION", "NRP", "ORG"}

# Temporal reference words that indicate a DATE_TIME entity is a relative
# time expression ("last tuesday", "two o'clock"), NOT a date-of-birth.
# When these appear within 30 chars before a DATE_TIME span, we skip it.
_TEMPORAL_REFS = re.compile(
    r'\b(last|next|this|yesterday|today|tomorrow|ago|since|until|'
    r'o\'clock|oclock|around|about|at|by|before|after|during|'
    r'morning|afternoon|evening|night|noon|midnight|am|pm|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday|'
    r'january|february|march|april|june|july|august|september|'
    r'october|november|december|week|month|year|hour|minute)\b',
    re.IGNORECASE,
)

# Office/building location words — LOCATION entities in this context are
# workspace references, not personal home addresses worth redacting.
_OFFICE_LOCATION = re.compile(
    r'\b(office|floor|building|suite|room|campus|headquarters|hq|'
    r'branch|site|location|desk|station)\b',
    re.IGNORECASE,
)

# Street address components — a LOCATION span containing these is a business
# or mailing address, not personal spoken PII worth redacting.
_STREET_ADDRESS = re.compile(
    r'\b(street|st|avenue|ave|boulevard|blvd|drive|dr|road|rd|lane|ln|'
    r'court|ct|place|pl|way|parkway|pkwy|highway|hwy|corporate|'
    r'registered|incorporated|inc|llc|ltd|usa|united states)\b',
    re.IGNORECASE,
)

# Personal location context — someone sharing THEIR location on a call.
# Without one of these nearby, a detected LOCATION is almost certainly
# a business name, city reference, or footer address — not actionable PII.
_PERSONAL_LOCATION = re.compile(
    r'\b(my address|home address|i live at|i\'m at|i am at|located at|'
    r'shipping address|billing address|mailing address|i reside|'
    r'my home|send it to|deliver to|pick me up at|i\'m located)\b',
    re.IGNORECASE,
)


def _run_presidio(text: str) -> List[RedactionSpan]:
    """
    Run Presidio AnalyzerEngine on the normalized transcript.

    Level 2 (spaCy NLP active):
      - Full NLP understanding — context-aware, not just pattern matching
      - Detects: SSN, credit card, phone, email, IP, driver's license, ITIN,
        person name, date, bank/routing, passport, member ID
      - Skips: ORGANIZATION (company names), NRP, office LOCATION references
      - Confidence threshold: 0.40 for personal PII, 0.80 for LOCATION/DATE_TIME

    Returns RedactionSpans with detection_method="ai".
    Silently returns empty list if Presidio is unavailable.
    """
    if not _presidio_ready or _analyzer is None:
        return []

    threshold = 0.40 if hasattr(_analyzer, 'nlp_engine') else 0.50

    spans = []
    try:
        results = _analyzer.analyze(text=text, language="en")
        for r in results:
            # Skip entity types that are not PII in call-center context
            if r.entity_type in _PRESIDIO_SKIP_ENTITIES:
                continue

            # DATE_TIME: only flag as DATE-OF-BIRTH when a DOB context phrase
            # appears nearby. Generic timestamps (email headers, timestamps,
            # "9:04am CST", "24 hours") are nearly always false positives.
            if r.entity_type == "DATE_TIME":
                # Only look BEFORE the span for DOB context — looking forward
                # causes false positives when a number appears earlier in the
                # transcript and "date of birth" is mentioned later.
                before_window = text[max(0, r.start - 120):r.start].lower()
                after_window  = text[r.end:r.end + 15].lower()  # tiny forward — covers "is 01/09/85"
                if not (_DOB_CONTEXT.search(before_window) or _DOB_CONTEXT.search(after_window)):
                    continue
                # Skip temporal references ("last tuesday", "two o'clock")
                if _TEMPORAL_REFS.search(text[max(0, r.start - 50):r.end + 20].lower()):
                    continue
                # Require higher confidence for dates — lots of false positives
                if r.score < 0.70:
                    continue

            # PHONE_NUMBER: suppress if a DOB trigger phrase appears within 150
            # chars before the span. Date digits read aloud after "date of birth"
            # are routinely misclassified as phone numbers by NLP models because
            # the digit groupings look similar (e.g. "08 09 1985").
            if r.entity_type == "PHONE_NUMBER":
                dob_window = text[max(0, r.start - 150):r.start].lower()
                if _DOB_CONTEXT.search(dob_window):
                    continue

            # LOCATION: only flag when someone is sharing their personal address.
            # Business addresses, city references, and footer boilerplate are
            # not actionable PII in a call-center or support context.
            if r.entity_type == "LOCATION":
                span_text = text[r.start:r.end]
                wide_window = text[max(0, r.start - 80):r.end + 80].lower()
                # Skip if it looks like a street/business address
                if _STREET_ADDRESS.search(span_text):
                    continue
                # Skip office/workspace references
                if _OFFICE_LOCATION.search(wide_window):
                    continue
                # Skip unless a personal location phrase is nearby
                if not _PERSONAL_LOCATION.search(wide_window):
                    continue
                if r.score < 0.80:
                    continue

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


# ── Level 3: Context-aware confidence boosting ────────────────────────────────
#
# Runs AFTER the rule + AI merge. Applies intelligence that neither layer can
# see individually — cross-span signals, surrounding word context, and
# cluster-level patterns.
#
# Four enhancement passes:
#   1. Co-occurrence boost — multiple PII types in one call = each is more real
#   2. Confirming context boost — phrases like "my name is" near a PERSON entity
#   3. Business context suppression — "invoice/order/ticket" near a digit cluster
#   4. Cluster flagging — 3+ distinct PII types triggers mandatory review flag

# Words that suggest a number is business/operational, not personal
_BUSINESS_CONTEXT = re.compile(
    r'\b(invoice|order|ticket|ref|reference|case|tracking|po number|item|sku|'
    r'serial|transaction|confirmation|booking|reservation|claim|extension|'
    r'flight|hotel|rental|appointment|schedule)\b',
    re.IGNORECASE,
)

# Phrases that strongly confirm a PERSON entity is a real name being given
_NAME_CONFIRM_PHRASES = re.compile(
    r'\b(my name is|this is|i am|i\'m|name\'s|name is|speaking with|'
    r'you\'re speaking to|i go by|they call me)\b',
    re.IGNORECASE,
)

# Words that suggest a date is operational, not a date-of-birth
_DATE_BUSINESS_CONTEXT = re.compile(
    r'\b(invoice date|order date|shipped|due date|expiry|expiration|'
    r'scheduled|appointment|meeting|renewal)\b',
    re.IGNORECASE,
)


def _level3_boost(spans: List[RedactionSpan], text: str, warnings: List[str]) -> tuple:
    """
    Level 3 post-processing: context-aware confidence adjustments and cluster flagging.

    Returns (enhanced_spans, extra_flagged) where extra_flagged means L3 wants
    the call flagged regardless of individual confidence scores.
    """
    if not spans:
        return spans, False

    text_lower = text.lower()
    extra_flagged = False

    # ── Pass 1: Co-occurrence boost ───────────────────────────────────────────
    # When a caller shares 2+ distinct PII types, each individual detection is
    # more likely real. A scammer or a caller in distress giving multiple pieces
    # of personal info in one call is a strong signal.
    distinct_types = {s.pii_type for s in spans}
    boost = 0.0
    if len(distinct_types) >= 3:
        boost = 0.06   # strong cluster signal
        extra_flagged = True
        warnings.append(
            f"Level 3: {len(distinct_types)} distinct PII types in one call — "
            "elevated risk, flagged for human review."
        )
    elif len(distinct_types) == 2:
        boost = 0.03   # moderate cluster signal

    for span in spans:
        if boost > 0:
            span.confidence = round(min(span.confidence + boost, 0.97), 3)

    # ── Pass 2: Confirming context boost (PERSON names) ───────────────────────
    # "my name is John Smith" — the phrase confirms the entity is being shared
    # intentionally. Boost PERSON-NAME confidence.
    for span in spans:
        if span.pii_type != "PERSON-NAME":
            continue
        # Look at the 60 chars before the span
        window_before = text_lower[max(0, span.start - 60):span.start]
        if _NAME_CONFIRM_PHRASES.search(window_before):
            span.confidence = round(min(span.confidence + 0.10, 0.97), 3)

    # ── Pass 3: Business context suppression ──────────────────────────────────
    # "invoice number 123456" or "order ID 789012" — these look like MEMBER_ID
    # or phone numbers to the pattern engine but are operational IDs, not PII.
    # Suppress confidence so they don't trigger false redactions.
    suppressed = []
    for span in spans:
        if span.pii_type not in ("MEMBER-ID", "ROUTING-NUMBER", "BANK-ACCOUNT", "DATE-OF-BIRTH",
                                  "CVV", "PIN", "CARD-EXPIRY", "ZIP-CODE"):
            suppressed.append(span)
            continue
        # Check 80 chars before the span for business context words
        window_before = text_lower[max(0, span.start - 80):span.start]
        # DATE-OF-BIRTH, MEMBER-ID, OTP: drop immediately on ANY business context
        # word — "confirmation code for the booking" is a reference number,
        # not an auth OTP. Same logic applies to invoice/order numbers.
        if span.pii_type in ("DATE-OF-BIRTH", "MEMBER-ID", "OTP"):
            if _BUSINESS_CONTEXT.search(window_before) or _DATE_BUSINESS_CONTEXT.search(window_before):
                warnings.append(
                    f"Level 3: suppressed {span.pii_type} (business context near pos {span.start})"
                )
                continue  # drop — it's a false positive
            suppressed.append(span)
            continue

        # Other types (ROUTING-NUMBER, BANK-ACCOUNT): reduce confidence
        if _BUSINESS_CONTEXT.search(window_before):
            span.confidence = round(max(span.confidence - 0.20, 0.10), 3)
            if span.confidence < 0.40:
                warnings.append(
                    f"Level 3: suppressed {span.pii_type} (business context near pos {span.start})"
                )
                continue
        suppressed.append(span)

    final = suppressed

    return final, extra_flagged


# ── Main entry point ──────────────────────────────────────────────────────────

_HTML_TAG = re.compile(r'<[^>]+>')
_HTML_ENTITY = re.compile(r'&(?:#\d+|#x[\da-fA-F]+|[a-zA-Z]+);')

def _strip_html(text: str) -> str:
    """Strip HTML tags and decode common entities if the text looks like HTML."""
    if '<' not in text:
        return text
    # Replace block-level tags with newlines so sentences don't merge
    text = re.sub(r'<(?:br|p|div|li|tr|td|th|h\d)(?:\s[^>]*)?>',
                  '\n', text, flags=re.IGNORECASE)
    text = _HTML_TAG.sub(' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&copy;', '©').replace('&quot;', '"')
    text = _HTML_ENTITY.sub(' ', text)
    # Collapse excessive whitespace
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# Phrases that strongly suggest a DATE_TIME entity IS a date-of-birth,
# not an operational timestamp. Required within 100 chars of a bare date.
_DOB_CONTEXT = re.compile(
    r'\b(date of birth|dob|birthday|born on|born in|birth date|date of birth is)\b',
    re.IGNORECASE,
)

# Email header prefixes — dates on these lines are metadata, never DOB
_EMAIL_HEADER = re.compile(
    r'^(from|to|cc|bcc|date|sent|subject|reply-to|x-mailer|mime-version|content-type)\s*:',
    re.IGNORECASE | re.MULTILINE,
)


def process_transcript(raw: str) -> VoiceGuardResult:
    """
    Full Voice Guard pipeline — dual layer (rule engine + Presidio AI).

    1. Strip HTML (if input is an email / HTML content)
    2. Normalize spoken numbers to digits
    3. Rule engine: direct pattern matching (formatted SSNs)
    4. Rule engine: trigger-based state machine (interrupted speech)
    5. AI layer: Presidio (triggerless detection, context-aware)
    6. Merge all spans — most restrictive wins on overlaps
    7. Apply redactions
    8. Return VoiceGuardResult
    """
    t0 = time.time()

    # Step 1: Strip HTML tags so context windows work on plain text
    stripped = _strip_html(raw)

    # Step 2: Normalize
    normalized = normalize_spoken_numbers(stripped)
    text_lower  = normalized.lower()

    all_spans: List[RedactionSpan] = []
    warnings: List[str] = []

    # Step 3: Direct pattern matching (formatted numbers, no trigger needed)
    all_spans.extend(_apply_direct_patterns(normalized))

    # Step 4: Trigger-based state machine
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
            max_gap=8,    # 8 chars — handles filler words ("uh", "um", a dash or
                          # space) between spoken digits without crossing into
                          # the next sentence or next PII field's digits.
                          # 20 was too wide: "3 2 7 [my PIN is] 4 8 2 1" had a
                          # 9-char gap that let the CVV vacuum steal PIN digits.
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

        # Ambiguous trigger disambiguation — "my number is" / "other number is"
        # could be SSN (9 digits) or PHONE (10 digits). Relabel based on count.
        _AMBIGUOUS_PHONE_TRIGGERS = {"my number is", "other number is"}
        if trigger_text in _AMBIGUOUS_PHONE_TRIGGERS:
            if len(digits) == 9:
                # Treat as SSN — same digit count, more sensitive label
                from dataclasses import replace as _dc_replace
                pattern = _dc_replace(
                    pattern,
                    pii_type="SSN",
                    redact_label="SSN",
                    digit_count=9,
                    digit_min=0,
                    digit_max=0,
                )
            # 10 digits → stays PHONE (original pattern unchanged)

        # For DATE_OF_BIRTH: months are not normalized globally, so extend the
        # span backward from first_pos to include any month name between the
        # trigger and the first digit. "date of birth is January 3 1977" →
        # span covers "January 3 1977" not just "3 1977".
        dob_span_start = first_pos
        if pattern.pii_type == "DATE_OF_BIRTH":
            pre_digit = normalized[trigger_end:first_pos].lower()
            for month_name in _MONTHS.keys():
                idx = pre_digit.rfind(month_name)
                if idx != -1:
                    dob_span_start = trigger_end + idx
                    break

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
                    start=dob_span_start,
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

    # Step 5: Presidio AI layer — catches triggerless and context-aware PII
    presidio_spans = _run_presidio(normalized)
    all_spans.extend(presidio_spans)

    # Step 6: Merge overlapping spans
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

        # Determine if either span came from the TRIGGER state machine.
        # Use trigger_phrase (non-empty only on state machine spans) to
        # distinguish triggered spans from direct-pattern spans — both have
        # detection_method="rule" but direct patterns have no trigger context.
        existing_is_triggered = (existing.detection_method == "rule" and bool(existing.trigger_phrase))
        incoming_is_triggered = (span.detection_method == "rule" and bool(span.trigger_phrase))

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

    # Step 6b: Level 3 — context-aware confidence boost + cluster detection
    merged, l3_flagged = _level3_boost(merged, normalized, warnings)

    # Step 7: Apply redactions (process in reverse to preserve indices).
    # Month names are no longer normalized globally so normalized ≈ stripped
    # for non-PII text — "March 15th at 2pm" stays readable.
    clean = normalized
    for span in sorted(merged, key=lambda s: s.start, reverse=True):
        tag = f"[REDACTED-{span.pii_type}]"
        clean = clean[:span.start] + tag + clean[span.end:]

    # Step 8: Build result
    pii_types   = list({s.pii_type for s in merged})
    avg_conf    = (sum(s.confidence for s in merged) / len(merged)) if merged else 1.0
    any_flagged = l3_flagged or any(s.confidence < 0.80 for s in merged) or bool(warnings)

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
    """Returns 'nlp+l3' if Level 3 active, 'nlp' for L2, 'pattern' for fallback, 'unavailable' if neither."""
    if not _presidio_ready or _analyzer is None:
        return "unavailable"
    base = "nlp" if hasattr(_analyzer, "nlp_engine") else "pattern"
    return base + "+l3"
