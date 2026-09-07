"""
tests/test_ask_interaction_logging.py — Phase 1 of the governed
continuous-learning plan: every Ask CostPilot question should produce
one AskInteraction row, purely observationally (nothing reads this
table to change behavior yet).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes_efficiency import AskCostPilotRequest, ask_costpilot
from database.db import Base
from database.models import AskInteraction


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_a_real_question_logs_one_interaction_row():
    db = _session()
    ask_costpilot(AskCostPilotRequest(question="What is our budget status?", workspace_id="WS-LOG"), db=db)

    rows = db.query(AskInteraction).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.workspace_id == "WS-LOG"
    assert row.question_text == "What is our budget status?"
    assert row.intent == "budget"
    assert row.latency_ms is not None and row.latency_ms >= 0


def test_help_question_logs_as_unsupported_not_a_reporting_intent():
    db = _session()
    ask_costpilot(AskCostPilotRequest(question="What can you do?", workspace_id="WS-LOG"), db=db)

    row = db.query(AskInteraction).first()
    assert row.intent == "help"
    assert row.unsupported is True


def test_logging_never_breaks_the_real_answer_when_db_is_none():
    # No AskInteraction row possible without a real session -- must not
    # raise, and the real answer must still come back correctly. "help" is
    # used here (not "budget") because the underlying deterministic report
    # itself requires a real db session regardless of logging -- this test
    # is specifically about the logging path tolerating db=None, not about
    # every intent being answerable without a database.
    result = ask_costpilot(AskCostPilotRequest(question="What can you do?", workspace_id="WS-LOG"), db=None)
    assert result["intent"] == "help"


def test_each_question_gets_its_own_row():
    db = _session()
    ask_costpilot(AskCostPilotRequest(question="What is our budget status?", workspace_id="WS-LOG"), db=db)
    ask_costpilot(AskCostPilotRequest(question="What can you do?", workspace_id="WS-LOG"), db=db)

    rows = db.query(AskInteraction).order_by(AskInteraction.id).all()
    assert len(rows) == 2
    assert rows[0].intent == "budget"
    assert rows[1].intent == "help"
