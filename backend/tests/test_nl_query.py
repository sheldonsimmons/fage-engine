"""
tests/test_nl_query.py — correctness check for POST /nl-query (Milestone 5,
the AI report builder's natural-language -> query_metrics translation).

The Anthropic call is mocked: this test suite is not about whether the LLM
picks the right fields (that's a prompting/eval concern), it's about the
code path around it -- that the returned report_state actually gets
executed through run_query_metrics (real numbers, not LLM-computed ones),
that current_report_state round-trips for refinement, and that a
translation with no tool_use block fails loudly instead of silently.
"""
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import TokenTransaction
from main import app


def _client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), TestingSessionLocal()


def _tx(db, *, cost_usd, department="WS1:Sales"):
    db.add(TokenTransaction(
        department=department, model_tier="Analyst", model_name="claude-3-5-sonnet",
        source_platform="Salesforce", input_tokens=10, output_tokens=10, cost_usd=cost_usd,
        timestamp=datetime.utcnow(), workspace_id="WS1", is_simulation=False,
        usage_source="estimated", routing_reason="ROUTINE",
    ))


class _FakeToolUseBlock:
    def __init__(self, input_dict):
        self.type = "tool_use"
        self.name = "query_metrics"
        self.input = input_dict


class _FakeAnthropicClient:
    def __init__(self, tool_input, **_kwargs):
        self._tool_input = tool_input
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_kwargs):
        return SimpleNamespace(content=[_FakeToolUseBlock(self._tool_input)])


def _patch_anthropic(monkeypatch, tool_input):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _FakeAnthropicClient(tool_input, **kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_translated_request_executes_real_query_not_llm_numbers(monkeypatch):
    client, db = _client()
    _tx(db, cost_usd=5.0, department="WS1:Sales")
    _tx(db, cost_usd=9.0, department="WS1:Engineering")
    db.commit()

    _patch_anthropic(monkeypatch, {
        "metrics": ["ai_spend"], "dimensions": ["department"], "filters": {},
        "days": 30, "period_key": "none", "compare_to": "none", "sort": "", "limit": 20,
    })

    resp = client.post("/api/reports/bot-efficiency/nl-query", json={
        "question": "Show AI spend by department", "workspace_id": "WS1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["report_state"]["dimensions"] == ["department"]
    spends = {r["dimensions"]["department"]: r["ai_spend"] for r in body["result"]["rows"]}
    assert 5.0 in spends.values()
    assert 9.0 in spends.values()


def test_no_tool_use_block_returns_502_not_silent_failure(monkeypatch):
    client, db = _client()

    class _EmptyClient:
        def __init__(self, **_kw):
            self.messages = SimpleNamespace(create=lambda **_kw: SimpleNamespace(content=[]))

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _EmptyClient(**kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    resp = client.post("/api/reports/bot-efficiency/nl-query", json={"question": "asdf"})
    assert resp.status_code == 502


def test_missing_api_key_returns_503(monkeypatch):
    client, db = _client()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resp = client.post("/api/reports/bot-efficiency/nl-query", json={"question": "Show AI spend"})
    assert resp.status_code == 503


def test_current_report_state_is_passed_to_translation_prompt(monkeypatch):
    """Doesn't assert on prompt wording (that's a prompting concern) -- just
    that current_report_state reaches the translation call at all, since a
    silently-dropped state would break conversational refinement entirely."""
    client, db = _client()
    seen = {}

    class _CapturingClient:
        def __init__(self, **_kw):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            seen["messages"] = kwargs.get("messages")
            return SimpleNamespace(content=[_FakeToolUseBlock({
                "metrics": ["ai_spend"], "dimensions": [], "filters": {"platform": "Salesforce"},
                "days": 30, "period_key": "none", "compare_to": "none", "sort": "", "limit": 20,
            })])

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _CapturingClient(**kw))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    prior_state = {"metrics": ["ai_spend"], "dimensions": [], "filters": {}, "days": 30, "period_key": "none"}
    resp = client.post("/api/reports/bot-efficiency/nl-query", json={
        "question": "Only show Salesforce", "current_report_state": prior_state,
    })
    assert resp.status_code == 200
    assert "ai_spend" in str(seen["messages"])
