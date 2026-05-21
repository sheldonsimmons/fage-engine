"""
core/model_client.py — Live API Broker  [Step 8]

Single entry point for all model calls. Reads .env on startup and routes
to the correct provider, or falls back to simulation if mode is "simulated".

Environment variables:
  FAGE_MODEL_MODE   — "simulated" (default) or "live"
  FAGE_PROVIDER     — "openai" or "anthropic"
  OPENAI_API_KEY    — your OpenAI key
  ANTHROPIC_API_KEY — your Anthropic key
  OPENAI_MICRO_MODEL      — e.g. gpt-3.5-turbo
  OPENAI_FLAGSHIP_MODEL   — e.g. gpt-4o
  ANTHROPIC_MICRO_MODEL   — e.g. claude-haiku-4-5-20251001
  ANTHROPIC_FLAGSHIP_MODEL— e.g. claude-sonnet-4-6
"""

import os
import random
from dotenv import load_dotenv

load_dotenv()

# ── Read config from environment ───────────────────────────────────────────────
MODEL_MODE = os.getenv("FAGE_MODEL_MODE", "simulated").lower()
PROVIDER   = os.getenv("FAGE_PROVIDER",   "openai").lower()

OPENAI_KEY          = os.getenv("OPENAI_API_KEY", "")
OPENAI_MICRO        = os.getenv("OPENAI_MICRO_MODEL",    "gpt-3.5-turbo")
OPENAI_FLAGSHIP     = os.getenv("OPENAI_FLAGSHIP_MODEL", "gpt-4o")

ANTHROPIC_KEY       = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MICRO     = os.getenv("ANTHROPIC_MICRO_MODEL",    "claude-haiku-4-5-20251001")
ANTHROPIC_FLAGSHIP  = os.getenv("ANTHROPIC_FLAGSHIP_MODEL", "claude-sonnet-4-6")

# ── Simulated responses (used when MODE=simulated) ────────────────────────────
ROUTINE_RESPONSES = [
    "Thank you for reaching out. The rate limit for Pro plan users on the v2 API is 1,000 requests per minute. If you need higher limits, our Enterprise tier offers custom rate configurations.",
    "The scheduled task completed successfully. All records were processed and the report has been generated. No anomalies detected.",
    "This is a standard configuration question — the default session timeout is 30 minutes for Pro accounts and can be adjusted in Settings > Security > Session Policy.",
]

COMPLEX_RESPONSES = [
    "After reviewing the full escalation context, I am classifying this as a P0 production incident. I have immediately paged the on-call infrastructure lead and opened a war-room bridge. SLA breach monitoring has been activated. A root-cause analysis will be delivered within 24 hours of resolution.",
    "This billing discrepancy constitutes a contractual breach requiring legal and compliance review. I have placed an immediate hold on all charges exceeding your monthly cap. A Senior Account Manager and Chief Compliance Officer have been notified.",
    "I have reviewed the enterprise renewal requirements including EU data residency mandates, GDPR DPA updates, and revised SLA terms. I am routing this to our Enterprise Legal team. A redlined contract draft will be prepared within 3 business days.",
]


def get_mode_info() -> dict:
    """Return current mode and provider config for the dashboard status display."""
    return {
        "mode":               MODEL_MODE,
        "provider":           PROVIDER if MODEL_MODE == "live" else "simulated",
        "openai_configured":  bool(OPENAI_KEY and not OPENAI_KEY.startswith("YOUR")),
        "anthropic_configured": bool(ANTHROPIC_KEY and not ANTHROPIC_KEY.startswith("YOUR")),
        "micro_model":        _model_id("micro"),
        "flagship_model":     _model_id("flagship"),
    }


def call_model(text: str, tier: str) -> dict:
    """
    Main entry point. Routes to live or simulated based on FAGE_MODEL_MODE.

    Returns:
        response_text  — the model's reply
        model_id       — actual model name used
        input_tokens   — token count (real from API or estimated)
        output_tokens  — token count (real from API or estimated)
        provider       — "openai" | "anthropic" | "simulated"
    """
    if MODEL_MODE == "live":
        if PROVIDER == "anthropic":
            return _call_anthropic(text, tier)
        else:
            return _call_openai(text, tier)
    else:
        return _call_simulated(text, tier)


# ── Simulated call ─────────────────────────────────────────────────────────────

def _call_simulated(text: str, tier: str) -> dict:
    from core.pruner import estimate_tokens
    input_tokens  = estimate_tokens(text)
    output_tokens = max(40, input_tokens // 5) if tier == "micro" else max(100, input_tokens // 2)
    response      = random.choice(ROUTINE_RESPONSES if tier == "micro" else COMPLEX_RESPONSES)
    return {
        "response_text": response,
        "model_id":      f"simulated-{tier}",
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "provider":      "simulated",
    }


# ── OpenAI live call ───────────────────────────────────────────────────────────

def _call_openai(text: str, tier: str) -> dict:
    from openai import OpenAI
    client   = OpenAI(api_key=OPENAI_KEY)
    model_id = OPENAI_MICRO if tier == "micro" else OPENAI_FLAGSHIP

    system_prompt = (
        "You are an enterprise AI assistant embedded in a FinOps governance middleware. "
        "Respond concisely and professionally. For support tickets, provide a clear action plan. "
        "For routine questions, give a direct answer. Always indicate your confidence level."
    )

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": text},
        ],
        max_tokens=400,
        temperature=0.3,
    )

    choice = response.choices[0]
    usage  = response.usage

    return {
        "response_text": choice.message.content,
        "model_id":      model_id,
        "input_tokens":  usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "provider":      "openai",
    }


# ── Anthropic live call ────────────────────────────────────────────────────────

def _call_anthropic(text: str, tier: str) -> dict:
    import anthropic
    client   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    model_id = ANTHROPIC_MICRO if tier == "micro" else ANTHROPIC_FLAGSHIP

    system_prompt = (
        "You are an enterprise AI assistant embedded in a FinOps governance middleware. "
        "Respond concisely and professionally. For support tickets, provide a clear action plan. "
        "For routine questions, give a direct answer. Always indicate your confidence level."
    )

    response = client.messages.create(
        model=model_id,
        max_tokens=400,
        system=system_prompt,
        messages=[{"role": "user", "content": text}],
    )

    usage = response.usage

    return {
        "response_text": response.content[0].text,
        "model_id":      model_id,
        "input_tokens":  usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "provider":      "anthropic",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _model_id(tier: str) -> str:
    if MODEL_MODE != "live":
        return f"simulated-{tier}"
    if PROVIDER == "anthropic":
        return ANTHROPIC_MICRO if tier == "micro" else ANTHROPIC_FLAGSHIP
    return OPENAI_MICRO if tier == "micro" else OPENAI_FLAGSHIP
