"""
core/pruner.py — Context-Pruning Sweeper  [Step 2]

Strips junk from incoming text payloads before they are sent to any AI model.
Targets the most common enterprise waste categories:
  - HTML tags and inline CSS
  - Email reply chains (everything after "-----Original Message-----")
  - Corporate email signatures
  - Legal disclaimer blocks
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
    header_pattern = re.compile(
        r"^(From|To|Cc|Bcc|Date|Subject|Reply-To|X-[\w-]+|MIME-Version|Content-Type|Content-Transfer-Encoding):.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    return header_pattern.sub("", text)


def strip_reply_chains(text: str) -> str:
    """
    Remove email reply chain history.
    Cuts everything after common reply-chain markers.
    """
    markers = [
        r"-{3,}\s*Original Message\s*-{3,}",
        r"On .+wrote:",
        r"From:\s*.+\nSent:\s*.+\nTo:\s*.+",
        r"_{3,}",   # long underscores used as dividers in some clients
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
        r"(privacy policy|data protection|GDPR|CCPA)[\s\S]{0,400}",
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
        # Physical address patterns
        r"\d{2,5}\s+\w+\s+(Street|St|Avenue|Ave|Blvd|Boulevard|Road|Rd|Drive|Dr|Suite|Ste)[^\n]*",
        # Website lines
        r"(www\.|https?://)[^\s\n]{5,80}",
    ]
    for pattern in sig_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text


def collapse_whitespace(text: str) -> str:
    """Replace 3+ consecutive blank lines with a single blank line, strip leading/trailing space."""
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
