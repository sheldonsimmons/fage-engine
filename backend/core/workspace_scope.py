"""
core/workspace_scope.py — single source of truth for scoping a query to one workspace.

TokenTransaction and AuditEvent both got a real workspace_id column added
in a later migration; activity written through the universal /api/route
ingestion path (including the traffic simulator) sets that column but
leaves department unprefixed. Older rows, and RegisteredAgent (which has
no workspace_id column at all), still only carry the legacy department
string prefix ("workspace_id:DeptName").

This scoping logic was previously reimplemented independently in
routes_dashboard.py and routes_models.py — two copies that could silently
drift apart, which is exactly what caused the dashboard to undercount
spend/calls by ~5x for any workspace with recent /api/route activity (the
copy in routes_dashboard.py hadn't been updated to prefer the real column).
One function, used everywhere, so that bug class can't recur.
"""

from sqlalchemy import and_, or_


def workspace_filter(model, workspace_id: str | None):
    """
    Returns a SQLAlchemy filter clause scoping `model` to `workspace_id`,
    or None if no workspace_id was given (caller should skip filtering).

    Prefers the real workspace_id column when the model has one
    (TokenTransaction, AuditEvent), falling back to the department prefix
    match only for legacy rows that predate that column. Models with no
    workspace_id column at all (RegisteredAgent) use the prefix match only.
    """
    if not workspace_id:
        return None
    prefix_match = model.department.like(f"{workspace_id}:%")
    if hasattr(model, "workspace_id"):
        return or_(
            model.workspace_id == workspace_id,
            and_(model.workspace_id.is_(None), prefix_match),
        )
    return prefix_match
