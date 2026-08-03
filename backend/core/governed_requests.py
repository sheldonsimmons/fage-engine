"""Shared identity helpers for one end-to-end governed AI request."""

from uuid import uuid4


GOVERNED_REQUEST_PREFIX = "cp_req_"
ROUTING_POLICY_VERSION = "costpilot-routing-v1"


def new_governed_request_id() -> str:
    """Return an opaque correlation ID safe to expose in APIs and audit evidence."""
    return f"{GOVERNED_REQUEST_PREFIX}{uuid4().hex}"
