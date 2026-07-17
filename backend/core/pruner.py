"""
core/pruner.py — Context-Pruning Sweeper  [Step 2]

Strips junk from incoming text payloads before they are sent to any AI model.
Targets the most common enterprise waste categories:
  - HTML tags and inline CSS
  - Email reply chains (everything after "-----Original Message-----")
  - MIME headers, tracking pixels, hidden HTML, and campaign footers
  - Corporate email signatures
  - Legal disclaimer blocks
  - Duplicate lines and repeated boilerplate
  - Excessive blank lines and whitespace

Also estimates token counts before and after to prove cost savings.
"""

import re
from config import CHARS_PER_TOKEN


# ─────────────────────────────────────────────────────────────────────────────
# Individual cleaning filters
# Each function takes a string and returns a cleaned string.
# ─────────────────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Remove all HTML tags and decode common HTML entities."""
    # Remove comments before tag stripping so tracking/debug comments do not
    # survive as model context.
    text = re.sub(r"<!--[\s\S]*?-->", " ", text)
    # Remove common invisible/tracking containers before their hidden text is
    # exposed by the generic tag stripper.
    text = re.sub(
        r"<(div|span|p|td|tr|table)[^>]*(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)[^>]*>[\s\S]*?</\1>",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    # Remove common open/click tracking images.
    text = re.sub(
        r"<img[^>]+(?:tracking|open|pixel|beacon)[^>]*>",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    # Remove <style>...</style> blocks entirely
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    # Remove <script>...</script> blocks entirely
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    # Remove all remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&copy;", "(c)")
    text = text.replace("&#39;", "'")
    text = text.replace("&quot;", '"')
    return text


def strip_email_headers(text: str) -> str:
    """Remove raw email header lines (From:, To:, Date:, X-Mailer:, etc.)."""
    HEADER_KEYS = (
        r"From|Sent|To|Cc|Bcc|Date|Subject|Reply-To|Message-ID|X-[\w-]+|MIME-Version|"
        r"Content-Type|Content-Transfer-Encoding|Content-Disposition|"
        r"Received|Return-Path|Delivered-To|Authentication-Results|"
        r"DKIM-Signature|ARC-[\w-]+|X-Originating-IP|X-Spam[\w-]*|"
        r"Thread-Index|Thread-Topic|In-Reply-To|References|"
        r"Importance|Sensitivity|Auto-Submitted"
    )

    # Step 1: Normalize flat text — insert newlines before header keywords.
    # Use only literal spaces/tabs after ":" so normal prose like "send it to:"
    # is not mistaken for a mail header.
    # Handles Salesforce collapsing the email body into a single line
    text = re.sub(
        r'[ \t]+(' + HEADER_KEYS + r'):[ \t]+',
        lambda m: '\n' + m.group(1) + ': ',
        text,
        flags=re.IGNORECASE,
    )

    # Step 2: Strip header lines now that they start at line beginnings
    header_pattern = re.compile(
        r"^(" + HEADER_KEYS + r"):[ \t].*$",
        re.IGNORECASE | re.MULTILINE,
    )
    text = header_pattern.sub("", text)

    # Strip MIME boundary markers
    text = re.sub(r"--(?=[a-zA-Z0-9_=\.\-]*[a-zA-Z0-9_=])[a-zA-Z0-9_=\.\-]{10,}", " ", text)
    text = re.sub(r'boundary\s*=\s*"?[-=_a-zA-Z0-9\.]*"?', " ", text, flags=re.IGNORECASE)
    text = re.sub(r"charset=[\w\-]+", " ", text, flags=re.IGNORECASE)

    # Strip common auto-reply / ticket system boilerplate lines
    boilerplate = re.compile(
        r"^(Your (ticket|case|request) (has been|#).{0,80}|"
        r"Thank you for contacting.{0,80}|"
        r"Expected response time:.{0,80}|"
        r"Ticket reference:.{0,80}|"
        r"This is an automated (message|response|reply).{0,80}|"
        r"Do not reply to this (email|message).{0,80}|"
        r"\[cid:[^\]]+\])$",
        re.IGNORECASE | re.MULTILINE,
    )
    text = boilerplate.sub("", text)

    return text


def strip_markup_noise(text: str) -> str:
    """Remove CSS fragments and HTML/email artifacts that survive tag stripping."""
    # CSS declarations often survive malformed HTML pasted into CRM fields.
    text = re.sub(r"\b[a-zA-Z\-]{2,40}\s*:\s*[^;\n{}]{1,120};", " ", text)
    text = re.sub(r"\.[A-Za-z0-9_-]+\s*\{[^{}]{0,500}\}", " ", text)
    text = re.sub(r"\{[^{}]{0,500}(font-family|font-size|display|width|height)[^{}]{0,500}\}", " ", text, flags=re.IGNORECASE)
    # Leftover HTML document labels with no business value.
    text = re.sub(r"\b(html|head|body|style|doctype)\b", " ", text, flags=re.IGNORECASE)
    # 1x1 pixel / image metadata remnants.
    text = re.sub(r"\b(width|height)\s*=\s*[\"']?1[\"']?", " ", text, flags=re.IGNORECASE)
    return text


def strip_reply_chains(text: str) -> str:
    """
    Remove email reply chain history.
    Cuts everything after common reply-chain markers.
    """
    markers = [
        r"\n-{5,}\s*\n\s*From:\s",
        r"-{3,}\s*Original Message\s*-{3,}",
        r"-{3,}\s*Forwarded Message\s*-{3,}",
        r"\n-{5,}\s*\n",
        r"On .+wrote:",
        r"On .+said:",
        r"From:\s*.+?Sent:\s*.+?To:\s*.+?(?=Subject:|$)",
        r"From:\s*.+?To:\s*.+?Date:\s*.+?(?=Subject:|$)",
        r"From:\s*.+?Date:\s*.+?To:\s*.+?(?=Subject:|$)",
        r"_{5,}",           # long underscores used as dividers
    ]
    for marker in markers:
        match = re.search(marker, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            text = text[: match.start()]
    return text


def strip_legal_disclaimers(text: str) -> str:
    """
    Remove standard corporate legal disclaimer blocks.
    These appear at the bottom of virtually every enterprise email.
    """
    disclaimer_patterns = [
        # "CONFIDENTIALITY NOTICE: ..." blocks
        r"CONFIDENTIALITY NOTICE[\s\S]{0,2000}",
        # "This email is intended for ..." style
        r"This\s+(e-?mail|message|communication)\s+.{0,100}(intended recipient|privileged|confidential)[\s\S]{0,1500}",
        # "Please consider the environment before printing"
        r"Please\s+consider\s+the\s+environment[\s\S]{0,200}",
        # "This communication does not constitute legal advice"
        r"This\s+communication\s+does\s+not\s+constitute[\s\S]{0,300}",
        # Copyright lines
        r"(All rights reserved|©|\(c\)\s*\d{4})[\s\S]{0,300}",
        # Privacy policy references
        r"(privacy policy|data protection|GDPR|CCPA)[\s\S]{0,500}",
        # Marketing preference/footer blocks.
        r"(manage preferences|unsubscribe|email preferences)[\s\S]{0,700}",
        r"(download our app|read our blog|rewards program|gift cards|careers)[\s\S]{0,700}",
        r"(need help\?|visit our help center)[\s\S]{0,500}",
    ]
    for pattern in disclaimer_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
    return text


def strip_signatures(text: str) -> str:
    """
    Remove common email signature blocks.
    Looks for the typical name / title / company / phone pattern.
    """
    sig_patterns = [
        # "Regards, / Name / Title / Company" blocks
        r"(Best regards|Kind regards|Regards|Sincerely|Thanks|Thank you|Cheers),?\s*\n.{1,60}\n.{1,80}\n.{1,80}",
        # Phone / address lines
        r"(Tel|Phone|Mobile|Fax|Ph):\s*[\+\d\s\(\)\-\.]{7,25}",
        # Website lines
        r"(www\.|https?://)[^\s\n]{5,80}",
        # Common mobile/mail client signatures.
        r"Sent from (my )?(iPhone|iPad|Android|Outlook for iPhone|Outlook Mobile)[^\n]*",
    ]
    for pattern in sig_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text


def strip_footer_blocks(text: str) -> str:
    """Remove bottom-heavy support, marketing, and company footer clusters."""
    lines = text.splitlines()
    footer_triggers = re.compile(
        r"(download our app|privacy policy|terms of service|manage preferences|"
        r"unsubscribe|rewards program|gift cards|careers|visit our help center|"
        r"this email may contain promotional information)",
        re.IGNORECASE,
    )

    # Once a footer cluster starts near the lower half of an email, discard the
    # remainder. This avoids removing useful links/details in the active request.
    for idx, line in enumerate(lines):
        if idx > max(3, len(lines) // 2) and footer_triggers.search(line):
            return "\n".join(lines[:idx])
    return text


def dedupe_repeated_lines(text: str) -> str:
    """Collapse repeated boilerplate lines while preserving normal prose order."""
    output = []
    seen_counts = {}
    previous = None
    consecutive_count = 0

    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line.strip()).lower()
        if not normalized:
            output.append(line)
            previous = None
            consecutive_count = 0
            continue

        if normalized == previous:
            consecutive_count += 1
        else:
            consecutive_count = 1
            previous = normalized

        seen_counts[normalized] = seen_counts.get(normalized, 0) + 1

        # Drop obvious repeated boilerplate after the first copy. Short lines
        # like names or city/state fragments are allowed to repeat.
        if consecutive_count > 1 and len(normalized) > 12:
            continue
        if seen_counts[normalized] > 2 and len(normalized) > 18:
            continue
        output.append(line)

    return "\n".join(output)


def collapse_whitespace(text: str) -> str:
    """Replace 3+ consecutive blank lines with a single blank line, strip leading/trailing space."""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)   # trailing spaces on lines
    text = re.sub(r"\n[ \t]+", "\n", text)   # leading spaces on lines
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Token estimation
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Rough token count using the standard 1 token ≈ 4 characters heuristic.
    Accurate enough for POC cost estimation.
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def detect_payload_type(text: str) -> tuple:
    """
    Inspect a payload and infer whether it looks like code or prose.

    Returns (detected_type, reason) where:
      detected_type — "code" | "text"
      reason        — plain-English explanation logged in the audit trail

    Signals checked (first match wins, ordered by confidence):
      1. PEM / private key header          → code, very high confidence
      2. Shebang line                       → code, high confidence
      3. Common code block markers          → code, high confidence
      4. Function / class definition lines  → code, medium confidence
      5. High density of code punctuation  → code, medium confidence
      6. Falls through                      → text (prose assumed)
    """
    sample = text[:2000]  # inspect first 2000 chars only — fast
    first_line = sample.splitlines()[0].strip() if sample.strip() else ""

    # 1. PEM / private key header — unambiguous
    if re.search(r"-----BEGIN\s+\S+\s+(?:PRIVATE\s+)?KEY-----", sample):
        return ("code", "AUTO-DETECTED as code: PEM key header found — pruner bypassed to protect key structure")

    # 2. Shebang line — unambiguous
    if first_line.startswith("#!") and ("python" in first_line or "node" in first_line
                                         or "bash" in first_line or "sh" in first_line):
        return ("code", "AUTO-DETECTED as code: shebang line found — pruner bypassed")

    # 3. Fenced code block markers (Markdown ```python etc.)
    if re.search(r"^```\s*\w*\s*$", sample, re.MULTILINE):
        return ("code", "AUTO-DETECTED as code: fenced code block marker found — pruner bypassed")

    # 4. Function / class / import definition lines
    code_def_patterns = [
        r"^(def |async def |class |public class |private class |function |const |let |var )\s*\w+",
        r"^(import |from \w+ import |require\(|#include |using namespace )",
        r"^(SELECT |INSERT INTO |UPDATE |DELETE FROM |CREATE TABLE |ALTER TABLE )\s",
    ]
    for pat in code_def_patterns:
        if re.search(pat, sample, re.MULTILINE | re.IGNORECASE):
            return ("code", f"AUTO-DETECTED as code: code definition pattern matched — pruner bypassed")

    # 5. High punctuation density — code has lots of {}[]();: relative to words
    #    Count code-specific chars vs total length
    code_chars = sum(sample.count(c) for c in "{}[]();=><")
    words       = len(re.findall(r"[a-zA-Z]{3,}", sample))
    if words > 0 and (code_chars / max(len(sample), 1)) > 0.05 and code_chars > words:
        return ("code", "AUTO-DETECTED as code: high code-character density — pruner bypassed")

    return ("text", "payload classified as text — standard pruning pipeline applied")


def prune(raw_text: str) -> dict:
    """
    Run the full pruning pipeline on a raw text payload.

    Returns a dict with:
      cleaned_text      — the pruned output ready to send to an LLM
      raw_tokens        — estimated token count before pruning
      clean_tokens      — estimated token count after pruning
      tokens_saved      — difference
      compression_pct   — how much smaller the payload is (0–100)
      filters_applied   — list of filter names that ran
    """
    filters_applied = []
    text = raw_text

    before_html = len(text)
    text = strip_html(text)
    if len(text) < before_html:
        filters_applied.append("strip_html")

    before_markup = len(text)
    text = strip_markup_noise(text)
    if len(text) < before_markup:
        filters_applied.append("strip_markup_noise")

    before_headers = len(text)
    text = strip_email_headers(text)
    if len(text) < before_headers:
        filters_applied.append("strip_email_headers")

    before_chain = len(text)
    text = strip_reply_chains(text)
    if len(text) < before_chain:
        filters_applied.append("strip_reply_chains")

    before_legal = len(text)
    text = strip_legal_disclaimers(text)
    if len(text) < before_legal:
        filters_applied.append("strip_legal_disclaimers")

    before_sig = len(text)
    text = strip_signatures(text)
    if len(text) < before_sig:
        filters_applied.append("strip_signatures")

    before_footer = len(text)
    text = strip_footer_blocks(text)
    if len(text) < before_footer:
        filters_applied.append("strip_footer_blocks")

    before_dedupe = len(text)
    text = dedupe_repeated_lines(text)
    if len(text) < before_dedupe:
        filters_applied.append("dedupe_repeated_lines")

    text = collapse_whitespace(text)
    filters_applied.append("collapse_whitespace")

    raw_tokens   = estimate_tokens(raw_text)
    clean_tokens = estimate_tokens(text)
    tokens_saved = raw_tokens - clean_tokens
    compression  = round((tokens_saved / raw_tokens) * 100, 1) if raw_tokens > 0 else 0.0

    return {
        "cleaned_text":    text,
        "raw_tokens":      raw_tokens,
        "clean_tokens":    clean_tokens,
        "tokens_saved":    tokens_saved,
        "compression_pct": compression,
        "filters_applied": filters_applied,
    }
