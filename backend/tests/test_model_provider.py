"""
Tests for core/model_provider.py.

Note: the live demo dataset (backend/fage.db) has model_name = NULL on
every existing TokenTransaction row -- only model_tier (micro/flagship/...)
is populated. That means provider resolution correctly falls back to
"Unknown" for all current records; this is a data-population gap in that
dataset, not a bug in resolve_provider(). These tests exercise the
resolution logic directly against realistic model names instead.
"""
from core.model_provider import resolve_provider


def test_resolves_known_registry_model_id():
    registry = {"claude-sonnet-4-6": "Anthropic", "gpt-4o": "OpenAI"}
    assert resolve_provider("claude-sonnet-4-6", registry=registry) == "Anthropic"
    assert resolve_provider("gpt-4o", registry=registry) == "OpenAI"


def test_registry_lookup_is_case_insensitive():
    registry = {"claude-sonnet-4-6": "Anthropic"}
    assert resolve_provider("Claude-Sonnet-4-6", registry=registry) == "Anthropic"


def test_falls_back_to_prefix_heuristic_when_not_in_registry():
    assert resolve_provider("claude-opus-4-9-preview", registry={}) == "Anthropic"
    assert resolve_provider("gpt-6-nano", registry={}) == "OpenAI"
    assert resolve_provider("o3-mini", registry={}) == "OpenAI"
    assert resolve_provider("gemini-3-pro", registry={}) == "Google"
    assert resolve_provider("mistral-large-3", registry={}) == "Mistral"
    assert resolve_provider("llama-4-70b", registry={}) == "Meta"


def test_falls_back_to_keyword_heuristic_for_display_names():
    assert resolve_provider("Claude Opus 4.8", registry={}) == "Anthropic"
    assert resolve_provider("OpenAI GPT-5", registry={}) == "OpenAI"


def test_registry_takes_priority_over_heuristic():
    # A model whose name looks like it should be Anthropic by prefix, but
    # the registry says otherwise (e.g. a white-labeled/custom entry) --
    # the admin-configured registry is authoritative.
    registry = {"claude-custom-router": "Azure OpenAI"}
    assert resolve_provider("claude-custom-router", registry=registry) == "Azure OpenAI"


def test_none_or_empty_model_name_is_unknown():
    assert resolve_provider(None) == "Unknown"
    assert resolve_provider("") == "Unknown"
    assert resolve_provider("   ") == "Unknown"


def test_unrecognized_model_name_is_unknown_not_guessed():
    assert resolve_provider("internal-custom-model-v3", registry={}) == "Unknown"
