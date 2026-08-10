"""
scripts/sync_all_salesforce_outcomes.py's connection-selection logic --
the part that decides which one connection per workspace is actually
worth syncing, out of however many rows have accumulated from re-auth
attempts, package-install retries, and connections that broke and were
replaced.
"""
import os

os.environ.setdefault("CONNECTION_ENCRYPTION_KEY", "WMMH4wtJGK5t1mAXvaUT8Kgfk-sglB_izB-izrf7FFQ=")

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.models import IntegrationConnection
from api.routes_connections import _encrypt

import importlib.util
import sys

_SPEC = importlib.util.spec_from_file_location(
    "sync_all_salesforce_outcomes",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "sync_all_salesforce_outcomes.py"),
)
sync_all = importlib.util.module_from_spec(_SPEC)
sys.modules["sync_all_salesforce_outcomes"] = sync_all
_SPEC.loader.exec_module(sync_all)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_syncs_both_orgs_when_one_workspace_has_two_distinct_salesforce_connections():
    """
    Found live in production: one CostPilot workspace had two genuinely
    different Salesforce orgs connected (different instance_url), and the
    old workspace-only dedup silently dropped one of them -- accounts that
    only existed in the dropped org looked like they had no data at all,
    when the real problem was "never queried."
    """
    db = _session()
    org_a = IntegrationConnection(
        workspace_id="WS-MULTI", platform="salesforce", display_name="org-a", status="connected",
        instance_url="https://cpcom-dev-ed.develop.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow() - timedelta(hours=2),
    )
    org_b = IntegrationConnection(
        workspace_id="WS-MULTI", platform="salesforce", display_name="org-b", status="mapping",
        instance_url="https://aicom177-dev-ed.develop.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=None,
    )
    db.add_all([org_a, org_b])
    db.commit()

    selected = sync_all._select_connections_to_sync(db)
    assert len(selected) == 2
    display_names = {c.display_name for c in selected}
    assert display_names == {"org-a", "org-b"}


def test_dedups_multiple_rows_for_the_same_org_within_one_workspace():
    """Multiple re-auth attempts against the SAME org should still collapse
    to one connection, unlike two genuinely different orgs."""
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-SAMEORG", platform="salesforce", display_name="old-row", status="error",
        instance_url="https://same-org.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow() - timedelta(days=5),
    ))
    newer = IntegrationConnection(
        workspace_id="WS-SAMEORG", platform="salesforce", display_name="newer-row", status="mapping",
        instance_url="https://same-org.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(newer)
    db.commit()

    selected = sync_all._select_connections_to_sync(db)
    assert len(selected) == 1
    assert selected[0].display_name == "newer-row"


def test_picks_most_recently_successful_connection_per_workspace():
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-A", platform="salesforce", display_name="old", status="error",
        instance_url="https://a.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow() - timedelta(days=10),
    ))
    newer = IntegrationConnection(
        workspace_id="WS-A", platform="salesforce", display_name="newer", status="mapping",
        instance_url="https://a2.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.add(newer)
    db.add(IntegrationConnection(
        workspace_id="WS-A", platform="salesforce", display_name="draft", status="draft",
        instance_url=None, access_token_encrypted=None, last_success_at=None,
    ))
    db.commit()

    selected = sync_all._select_connections_to_sync(db)
    assert len(selected) == 1
    assert selected[0].display_name == "newer"


def test_includes_connection_that_has_never_succeeded_yet():
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-B", platform="salesforce", display_name="never-synced", status="authorizing",
        instance_url="https://b.my.salesforce.com", access_token_encrypted=_encrypt("x"), last_success_at=None,
    ))
    db.commit()

    selected = sync_all._select_connections_to_sync(db)
    assert len(selected) == 1
    assert selected[0].display_name == "never-synced"


def test_excludes_workspace_with_only_broken_connections():
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-C", platform="salesforce", display_name="broken", status="error",
        instance_url="https://c.my.salesforce.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow(),
    ))
    db.commit()

    assert sync_all._select_connections_to_sync(db) == []


def test_excludes_connections_without_a_real_token():
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-D", platform="salesforce", display_name="no-token", status="draft",
        instance_url=None, access_token_encrypted=None, last_success_at=None,
    ))
    db.commit()

    assert sync_all._select_connections_to_sync(db) == []


def test_ignores_non_salesforce_platforms():
    db = _session()
    db.add(IntegrationConnection(
        workspace_id="WS-E", platform="servicenow", display_name="sn", status="mapping",
        instance_url="https://e.service-now.com", access_token_encrypted=_encrypt("x"),
        last_success_at=datetime.utcnow(),
    ))
    db.commit()

    assert sync_all._select_connections_to_sync(db) == []
