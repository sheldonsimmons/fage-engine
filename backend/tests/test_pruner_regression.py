from api.routes_pruner import PrunePreviewRequest, preview_pruning
from core.pruner import detect_payload_type, prune


def test_plain_business_text_remains_unchanged():
    text = "Summarize the customer interview and list the three main concerns."
    assert prune(text) == {
        "cleaned_text": text,
        "raw_tokens": 16,
        "clean_tokens": 16,
        "tokens_saved": 0,
        "compression_pct": 0.0,
        "filters_applied": ["collapse_whitespace"],
        "filter_details": [],
    }


def test_repeated_business_boilerplate_snapshot_is_unchanged():
    text = (
        "Important customer detail\n"
        "Important customer detail\n"
        "Important customer detail\n"
        "Next action: call the customer."
    )
    assert prune(text) == {
        "cleaned_text": "Important customer detail\nNext action: call the customer.",
        "raw_tokens": 27,
        "clean_tokens": 14,
        "tokens_saved": 13,
        "compression_pct": 48.1,
        "filters_applied": ["dedupe_repeated_lines", "collapse_whitespace"],
        "filter_details": [{"name": "dedupe_repeated_lines", "tokens_saved": 13}],
    }


def test_code_detection_and_preview_preserve_code_byte_for_byte():
    code = "def total(items):\n    return sum(items)"
    detected, _ = detect_payload_type(code)
    result = preview_pruning(PrunePreviewRequest(text=code, payload_type="auto"))

    assert detected == "code"
    assert result.decision == "bypassed"
    assert result.cleaned_text == code
    assert result.tokens_saved == 0
    assert result.filters_applied == []


def test_agent_override_preview_bypasses_existing_algorithm():
    text = "<p>Keep the original payload exactly as supplied.</p>"
    result = preview_pruning(
        PrunePreviewRequest(text=text, payload_type="text", pruning_enabled=False)
    )

    assert result.decision == "bypassed"
    assert result.cleaned_text == text
    assert result.tokens_saved == 0
