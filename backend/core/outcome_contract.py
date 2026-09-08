"""
core/outcome_contract.py — the canonical Universal Outcome field names and
retrieval methods, formalized once.

Before this module, the canonical shape (status/value/date/success) that
every outcome adapter (core/outcome_adapters/*.py) maps platform data into
existed only as a naming convention repeated in comments across those
files and in database/models.py's WorkItemOutcome/WorkItemOutcomeEvent
docstrings. This is that convention made real, so new code (in particular
core/outcome_ingestion.py) has an actual import target instead of another
copy of the same comment.
"""

OUTCOME_STATUS = "outcome_status"
OUTCOME_VALUE = "outcome_value"
OUTCOME_DATE = "outcome_date"
OUTCOME_SUCCESS = "outcome_success"
IS_CLOSED = "is_closed"

# How a WorkItemOutcome/WorkItemOutcomeEvent row was populated. "push" is
# new -- added for Universal Outcome Ingestion (core/outcome_ingestion.py);
# the other three predate it and describe the existing pull-based adapters
# (core/outcome_adapters/*.py) and bulk import path.
RETRIEVAL_METHOD_WEBHOOK = "webhook"
RETRIEVAL_METHOD_SYNC = "sync"
RETRIEVAL_METHOD_ON_DEMAND = "on_demand"
RETRIEVAL_METHOD_IMPORT = "import"
RETRIEVAL_METHOD_PUSH = "push"

RETRIEVAL_METHODS = frozenset({
    RETRIEVAL_METHOD_WEBHOOK,
    RETRIEVAL_METHOD_SYNC,
    RETRIEVAL_METHOD_ON_DEMAND,
    RETRIEVAL_METHOD_IMPORT,
    RETRIEVAL_METHOD_PUSH,
})

# Payload/timestamp safety bounds for Universal Outcome Ingestion -- this
# is a push surface reachable by any caller with a valid workspace key,
# unlike the pull-based adapters, which only ever see CostPilot's own
# SOQL/REST query results. See core/outcome_ingestion.py.
MAX_OUTCOME_STRING_FIELD_LENGTH = 500
