"""
tests/test_route_outcome_payload_limits.py — Universal Outcome Ingestion is
a push surface reachable by any caller with a valid workspace key, unlike
the pull-based adapters (which only ever see CostPilot's own SOQL/REST
query results) -- so it needs its own payload size and timestamp sanity
bounds, which nothing else on the outcome path enforces today. See
core/outcome_ingestion.py::_validate_payload().
"""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routes_router import RouteRequest, route_payload
from core.outcome_contract import MAX_OUTCOME_STRING_FIELD_LENGTH
from database.db import Base


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _outcome_request(**outcome_overrides):
    outcome = {"status": "approved", "date": datetime.utcnow().isoformat()}
    outcome.update(outcome_overrides)
    return RouteRequest.model_validate({
        "mode": "outcome",
        "event_id": "evt-limits-1",
        "source": {"platform": "Custom Claims App", "workspace_id": "WS-1"},
        "work": {
            "external_id": "CLAIM-1", "type": "claim",
            "source_platform": "Custom Claims App", "sync_if_missing": True,
        },
        "outcome": outcome,
    })


def _expect_422(request, db):
    try:
        route_payload(request, db)
        assert False, "expected an HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422, f"expected 422, got {getattr(exc, 'status_code', None)}"


def test_oversized_status_field_is_rejected():
    db = _session()
    _expect_422(_outcome_request(status="x" * (MAX_OUTCOME_STRING_FIELD_LENGTH + 1)), db)


def test_status_field_at_the_limit_is_accepted():
    db = _session()
    response = route_payload(_outcome_request(status="x" * MAX_OUTCOME_STRING_FIELD_LENGTH), db)
    assert response.outcome_recorded is True


def test_far_future_date_is_rejected():
    db = _session()
    far_future = (datetime.utcnow() + timedelta(days=30)).isoformat()
    _expect_422(_outcome_request(date=far_future), db)


def test_date_before_the_year_2000_is_rejected():
    db = _session()
    _expect_422(_outcome_request(date="1999-01-01T00:00:00"), db)


def test_date_within_bounds_is_accepted():
    db = _session()
    response = route_payload(_outcome_request(date=(datetime.utcnow() - timedelta(days=1)).isoformat()), db)
    assert response.outcome_recorded is True


def test_omitted_date_is_accepted():
    db = _session()
    request = _outcome_request()
    request.outcome_context.date = None
    response = route_payload(request, db)
    assert response.outcome_recorded is True
