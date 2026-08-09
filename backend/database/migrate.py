"""
migrate.py — single source of truth for lightweight column migrations.

Both the app (main.py, on every dyno boot) and the release phase
(scripts/release.sh, before any dyno boots) call run_migrations() so the
two can never drift out of sync with each other again. Safe to re-run
any number of times.
"""

from sqlalchemy import text

from database.db import engine
from database import models


def create_tables():
    models.Base.metadata.create_all(bind=engine)


def run_migrations():
    """Add new columns to existing tables without requiring Alembic."""
    def ensure_column(conn, table: str, column: str, definition: str):
        if engine.dialect.name == "sqlite":
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                conn.commit()
        else:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"))
            conn.commit()

    with engine.connect() as conn:
        try:
            ensure_column(conn, "registered_agents", "archived", "BOOLEAN DEFAULT FALSE")
        except Exception:
            pass  # Column already exists or DB doesn't support IF NOT EXISTS
        try:
            ensure_column(conn, "registered_agents", "pruning_enabled", "BOOLEAN DEFAULT TRUE")
        except Exception:
            pass
        try:
            ensure_column(conn, "routing_configs", "tier_names_json", "TEXT")
        except Exception:
            pass
        try:
            ensure_column(conn, "model_registry", "department", "VARCHAR")
        except Exception:
            pass
        try:
            ensure_column(conn, "department_budgets", "throttle_tier", "INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            ensure_column(conn, "registered_agents", "min_tier", "INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            ensure_column(conn, "registered_agents", "max_tier", "INTEGER DEFAULT 4")
        except Exception:
            pass
        try:
            ensure_column(conn, "department_budgets", "raw_payload_logging_enabled", "BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        try:
            ensure_column(conn, "department_budgets", "raw_retention_days", "INTEGER DEFAULT 30")
        except Exception:
            pass
        try:
            ensure_column(conn, "department_budgets", "archived", "BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        try:
            # department_budgets never got a real workspace_id column when
            # token_transactions/audit_events did — it's the reason budget
            # scoping had to be inferred from a "workspace_id:DeptName"
            # string prefix on `department` everywhere. Nullable/unbackfilled
            # here; backfill_workspaces.py populates it once from that prefix.
            ensure_column(conn, "department_budgets", "workspace_id", "VARCHAR")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "raw_payload", "TEXT")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "raw_logged_at", "TIMESTAMP")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "matched_keywords_json", "TEXT")
        except Exception:
            pass
        try:
            ensure_column(conn, "token_transactions", "usage_source", "VARCHAR DEFAULT 'estimated'")
        except Exception:
            pass
        for column, definition in (
            ("model_name", "VARCHAR"),
            ("resolved_model_tier", "VARCHAR"),
            ("model_source", "VARCHAR"),
            ("routing_cascaded", "BOOLEAN DEFAULT FALSE"),
            ("is_simulation", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ):
            try:
                ensure_column(conn, "token_transactions", column, definition)
            except Exception:
                pass
        for table in ("token_transactions", "audit_events"):
            try:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_governed_request_id "
                    f"ON {table} (governed_request_id)"
                ))
                conn.commit()
            except Exception:
                pass
        try:
            ensure_column(conn, "token_transactions", "work_item_id", "INTEGER REFERENCES work_items(id)")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "work_item_id", "INTEGER REFERENCES work_items(id)")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "is_simulation", "BOOLEAN DEFAULT FALSE NOT NULL")
        except Exception:
            pass
        try:
            ensure_column(conn, "audit_events", "cost_usd", "FLOAT")
        except Exception:
            pass
        for table in ("token_transactions", "audit_events"):
            for column, definition in (
                ("work_user_id", "INTEGER REFERENCES work_users(id)"),
                ("actor_external_id", "VARCHAR"),
                ("actor_name", "VARCHAR"),
                ("actor_email", "VARCHAR"),
                ("actor_source_platform", "VARCHAR"),
                ("origin_record_id", "VARCHAR"),
                ("origin_record_type", "VARCHAR"),
                ("origin_record_name", "VARCHAR"),
            ):
                try:
                    ensure_column(conn, table, column, definition)
                except Exception:
                    pass
        for table in ("token_transactions", "audit_events"):
            for column, definition in (
                ("governed_request_id", "VARCHAR"),
                ("requested_model_name", "VARCHAR"),
                ("requested_model_tier", "VARCHAR"),
                ("routing_policy_version", "VARCHAR"),
                ("execution_status", "VARCHAR"),
            ):
                try:
                    ensure_column(conn, table, column, definition)
                except Exception:
                    pass
        for column, definition in (
            ("provider_status_code", "INTEGER"),
        ):
            try:
                ensure_column(conn, "token_transactions", column, definition)
            except Exception:
                pass
        for column, definition in (
            ("selected_model_name", "VARCHAR"),
            ("selected_model_tier", "VARCHAR"),
            ("routing_reason_code", "VARCHAR"),
        ):
            try:
                ensure_column(conn, "audit_events", column, definition)
            except Exception:
                pass
        for table, column, definition in (
            ("registered_agents", "owner_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
            ("work_items", "org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
            ("work_users", "primary_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
        ):
            try:
                ensure_column(conn, table, column, definition)
            except Exception:
                pass
        for table in ("token_transactions", "audit_events"):
            for column, definition in (
                ("workspace_id", "VARCHAR"),
                ("actor_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
                ("actor_org_unit_name", "VARCHAR"),
                ("agent_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
                ("agent_org_unit_name", "VARCHAR"),
                ("work_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
                ("work_org_unit_name", "VARCHAR"),
                ("charged_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
                ("charged_org_unit_name", "VARCHAR"),
                ("attribution_source", "VARCHAR"),
                ("attribution_confidence", "VARCHAR"),
            ):
                try:
                    ensure_column(conn, table, column, definition)
                except Exception:
                    pass
        try:
            # Lets reporting GROUP BY business_purpose in SQL instead of
            # reclassifying every row in Python on every request -- see
            # models.py's TokenTransaction.business_purpose comment.
            ensure_column(conn, "token_transactions", "business_purpose", "VARCHAR")
        except Exception:
            pass
        for column, definition in (
            ("context_type", "VARCHAR DEFAULT 'project' NOT NULL"),
            ("context_template", "VARCHAR"),
            ("source_record_type", "VARCHAR"),
            ("source_record_id", "VARCHAR"),
            ("budget_warning_pct", "FLOAT DEFAULT 80 NOT NULL"),
            ("budget_action", "VARCHAR DEFAULT 'warn' NOT NULL"),
            ("merged_into_work_item_id", "INTEGER REFERENCES work_items(id)"),
        ):
            try:
                ensure_column(conn, "work_items", column, definition)
            except Exception:
                pass
        try:
            ensure_column(conn, "sensitive_terms", "enabled", "BOOLEAN DEFAULT TRUE")
        except Exception:
            pass
        try:
            ensure_column(conn, "sensitive_terms", "is_recommended", "BOOLEAN DEFAULT FALSE")
        except Exception:
            pass
        try:
            ensure_column(conn, "sensitive_terms", "deleted_at", "TIMESTAMP")
        except Exception:
            pass
        # trial_accounts — create + add new columns
        try:
            from database.models import TrialAccount
            TrialAccount.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            pass
        for col, defn in [
            ("secret_key",     "VARCHAR"),
            ("platform",       "VARCHAR"),
            ("setup_complete", "BOOLEAN DEFAULT FALSE"),
            ("trial_call_cap", "INTEGER DEFAULT 500"),
            ("trial_spend_cap_usd", "FLOAT DEFAULT 10.0"),
            ("requested_plan", "VARCHAR"),
            ("upgrade_requested_at", "TIMESTAMP"),
            ("business_context_config_json", "TEXT"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE trial_accounts ADD COLUMN {col} {defn}"))
                conn.commit()
            except Exception:
                pass
