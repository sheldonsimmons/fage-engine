"""
core/model_provider.py — resolve which AI provider (Anthropic, OpenAI, ...) a
recorded model name belongs to.

TokenTransaction stores model_name (e.g. "claude-sonnet-4-6", "gpt-4o-mini")
but never a provider column directly — provider is only recorded on
ModelRegistry/KnownModel (the admin-configured model catalog), which not
every historical model_name is guaranteed to match (models get renamed or
retired in the registry while old transactions keep the name they were
charged under). This gives "provider" a real, queryable answer either way:
prefer the authoritative registry when the name is still in it, and fall
back to the same id-prefix heuristic core/model_client.py already uses to
route a live call, so a provider can always be named instead of showing
"Unknown" for perfectly identifiable models.
"""
from typing import Optional

_PREFIX_PROVIDERS = (
    ("claude-", "Anthropic"),
    ("gpt-", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("gemini-", "Google"),
    ("mistral-", "Mistral"),
    ("llama-", "Meta"),
    ("azure-", "Azure OpenAI"),
)

# Keyword fallback for display names / free-form model labels that don't
# use a provider's raw API id prefix (e.g. "Claude Opus 4.8", "flagship").
_KEYWORD_PROVIDERS = (
    ("claude", "Anthropic"),
    ("anthropic", "Anthropic"),
    ("gpt", "OpenAI"),
    ("openai", "OpenAI"),
    ("gemini", "Google"),
    ("mistral", "Mistral"),
    ("llama", "Meta"),
)

def _load_registry_map(db) -> dict:
    # Queried once per report call (not per-row) -- cheap enough that a
    # process-lifetime cache isn't worth the staleness/test-isolation risk
    # of caching across different DB sessions and fixtures.
    from database.models import ModelRegistry, KnownModel

    mapping: dict = {}
    try:
        for row in db.query(ModelRegistry.model_id, ModelRegistry.provider).all():
            if row[0]:
                mapping[row[0].lower()] = row[1]
        for row in db.query(KnownModel.model_id, KnownModel.provider).all():
            if row[0]:
                mapping.setdefault(row[0].lower(), row[1])
    except Exception:
        # Table not migrated yet / test DB without these tables -- fall
        # back to the prefix/keyword heuristic below rather than raising.
        return {}
    return mapping


def load_provider_registry(db) -> dict:
    """Query the model->provider registry once; pass the result to resolve_provider() in a loop."""
    return _load_registry_map(db)


def resolve_provider(model_name: Optional[str], db=None, registry: Optional[dict] = None) -> str:
    """
    Return the provider name for a recorded model_name, or "Unknown".

    Pass a preloaded `registry` (from load_provider_registry(db), queried
    once) when resolving many rows in a loop -- passing `db` instead would
    re-run the registry query on every single call, an N+1 query pattern
    that's fine for a one-off lookup but not for aggregating a whole
    report's worth of transactions.
    """
    if not model_name:
        return "Unknown"
    name = model_name.strip()
    if not name:
        return "Unknown"

    if registry is None and db is not None:
        registry = _load_registry_map(db)
    if registry:
        provider = registry.get(name.lower())
        if provider:
            return provider

    lowered = name.lower()
    for prefix, provider in _PREFIX_PROVIDERS:
        if lowered.startswith(prefix):
            return provider
    for keyword, provider in _KEYWORD_PROVIDERS:
        if keyword in lowered:
            return provider
    return "Unknown"
