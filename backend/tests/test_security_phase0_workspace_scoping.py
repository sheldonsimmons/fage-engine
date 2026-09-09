"""
tests/test_security_phase0_workspace_scoping.py — Phase 0 of the security
architecture assessment: narrow, no-schema-risk fixes to the highest-blast-
radius confirmed cross-tenant findings that didn't require the full
User/Role/Membership system to close.

Covers: VoiceEvent gained a workspace_id column and its GET/DELETE routes
now require + enforce it (previously: zero scoping, any caller could read
or wipe every workspace's redacted call transcripts at once); the audit
JSONL export and single-event-detail endpoints now require + enforce
workspace_id too (previously: a bare, unscoped full-log download and a
sequentially-enumerable event-detail endpoint).

Deliberately not covered here (out of Phase 0 scope, per the assessment):
Agent Registry by-ID routes, connection resolver, budget-by-department
routes, WorkItem resolvers — these need the full centralized
get_current_membership() enforcement layer (Phase 2 of the assessment),
not a narrow patch, without materially higher regression risk to live
product pages.
"""
import json
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.db import Base, get_db
from database.models import AuditEvent, VoiceEvent
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


# ── Voice Guard ──────────────────────────────────────────────────────────────

def _voice_event(db, *, workspace_id, department="Support", call_id):
    db.add(VoiceEvent(
        workspace_id=workspace_id, department=department, call_id=call_id,
        clean_transcript=f"transcript for {call_id}", redactions_count=0,
        timestamp=datetime.utcnow(),
    ))
    db.commit()


def test_voice_events_requires_workspace_id():
    client, db = _client()
    resp = client.get("/api/voice/events")
    assert resp.status_code == 422  # FastAPI: missing required query param


def test_voice_events_scoped_to_workspace():
    client, db = _client()
    _voice_event(db, workspace_id="WS-A", call_id="call-a")
    _voice_event(db, workspace_id="WS-B", call_id="call-b")

    resp = client.get("/api/voice/events", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200
    call_ids = [e["call_id"] for e in resp.json()]
    assert call_ids == ["call-a"]


def test_voice_events_delete_requires_workspace_id():
    client, db = _client()
    resp = client.delete("/api/voice/events")
    assert resp.status_code == 422


def test_voice_events_delete_scoped_does_not_wipe_other_workspace():
    client, db = _client()
    _voice_event(db, workspace_id="WS-A", call_id="call-a")
    _voice_event(db, workspace_id="WS-B", call_id="call-b")

    resp = client.delete("/api/voice/events", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1

    remaining = db.query(VoiceEvent).all()
    assert len(remaining) == 1
    assert remaining[0].workspace_id == "WS-B"


# ── Audit ────────────────────────────────────────────────────────────────────

def _audit_event(db, *, workspace_id, event_type="ROUTING", department="Support"):
    e = AuditEvent(
        workspace_id=workspace_id, event_type=event_type, department=department,
        model_tier="micro", risk_level="low", decision_outcome="Routed to Scout",
        prompt_payload="hello world", rationale="test rationale",
        context_snapshot=json.dumps({"organizational_attribution": {"workspace_id": workspace_id}}),
        timestamp=datetime.utcnow(),
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def test_audit_event_detail_requires_workspace_id():
    client, db = _client()
    e = _audit_event(db, workspace_id="WS-A")
    resp = client.get(f"/api/audit/{e.id}")
    assert resp.status_code == 422


def test_audit_event_detail_denies_cross_tenant_access():
    client, db = _client()
    e = _audit_event(db, workspace_id="WS-A")
    resp = client.get(f"/api/audit/{e.id}", params={"workspace_id": "WS-B"})
    assert resp.status_code == 404


def test_audit_event_detail_allows_same_tenant_access():
    client, db = _client()
    e = _audit_event(db, workspace_id="WS-A")
    resp = client.get(f"/api/audit/{e.id}", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200
    assert resp.json()["id"] == e.id


def test_audit_export_requires_workspace_id():
    client, db = _client()
    resp = client.get("/api/audit/export")
    assert resp.status_code == 422


def test_audit_export_only_returns_matching_workspace_lines(tmp_path, monkeypatch):
    client, db = _client()

    log_path = tmp_path / "fage_audit.jsonl"
    lines = [
        {"audit_id": 1, "department": "WS-A:Sales", "context_snapshot": {}},
        {"audit_id": 2, "department": "Support", "context_snapshot": {"organizational_attribution": {"workspace_id": "WS-B"}}},
        {"audit_id": 3, "department": "WS-B:Support", "context_snapshot": {}},
    ]
    log_path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    import api.routes_auditor as routes_auditor
    monkeypatch.setattr(routes_auditor, "export_jsonl_path", lambda: str(log_path))

    resp = client.get("/api/audit/export", params={"workspace_id": "WS-A"})
    assert resp.status_code == 200
    returned_ids = [json.loads(l)["audit_id"] for l in resp.text.strip().splitlines()]
    assert returned_ids == [1]

    resp_b = client.get("/api/audit/export", params={"workspace_id": "WS-B"})
    returned_ids_b = [json.loads(l)["audit_id"] for l in resp_b.text.strip().splitlines()]
    assert returned_ids_b == [2, 3]
