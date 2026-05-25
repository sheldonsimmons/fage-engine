"""
config.py — Central configuration for FAGE.

All pricing parameters, model definitions, and system defaults live here.
To swap from simulated to live models, update the model names and API endpoints.
"""

# ─────────────────────────────────────────────────────────────────────────────
# AI MODEL PRICING  (simulated — $ per million tokens)
# ─────────────────────────────────────────────────────────────────────────────

MICRO_MODEL = {
    "name": "micro-model-v1",            # Swap to "gpt-3.5-turbo" for live
    "display_name": "Micro (Economy)",
    "input_cost_per_million": 0.15,      # $0.15 per 1M input tokens
    "output_cost_per_million": 0.60,     # $0.60 per 1M output tokens
    "avg_latency_ms": 400,
}

FLAGSHIP_MODEL = {
    "name": "flagship-model-v1",         # Swap to "gpt-4o" for live
    "display_name": "Flagship (Premium)",
    "input_cost_per_million": 3.00,      # $3.00 per 1M input tokens
    "output_cost_per_million": 15.00,    # $15.00 per 1M output tokens
    "avg_latency_ms": 2200,
}

# ─────────────────────────────────────────────────────────────────────────────
# ROUTING THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

# Payloads estimated above this token count route to the flagship model
COMPLEXITY_TOKEN_THRESHOLD = 500

# Keywords that force flagship routing regardless of token count
COMPLEXITY_KEYWORDS = [
    "legal", "compliance", "lawsuit", "contract", "audit", "fraud",
    "critical", "billing dispute", "breach", "regulatory",
    "data loss", "outage", "gdpr", "hipaa",
]

# ─────────────────────────────────────────────────────────────────────────────
# DEPARTMENTS & DEFAULT BUDGET CAPS  (USD / month)
# ─────────────────────────────────────────────────────────────────────────────

DEPARTMENTS = ["Support", "Sales", "Marketing", "Operations", "Trips Team"]

DEFAULT_BUDGET_CAPS = {
    "Support":     200.00,
    "Sales":       300.00,
    "Marketing":   250.00,
    "Operations":  150.00,
    "Trips Team":  200.00,
}

# Budget usage % that triggers auto-throttle to micro model
THROTTLE_TRIGGER_PERCENT = 100.0   # 100% = cap fully consumed

# Budget usage % that triggers a warning (not yet throttled)
WARN_TRIGGER_PERCENT = 80.0        # 80% = yellow warning state

# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT PRUNING SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

# Rough token estimate: 1 token ≈ 4 characters (OpenAI standard)
CHARS_PER_TOKEN = 4

# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_LOG_DIR = "audit_logs"
AUDIT_LOG_FILENAME = "fage_audit.jsonl"   # Append-only JSON Lines format
