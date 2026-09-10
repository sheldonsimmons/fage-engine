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
    def ensure_column(conn, table: str, column: str, definition: str) -> bool:
        """Returns True only the first time this column is actually created,
        so callers can run one-time backfill logic exactly once."""
        if engine.dialect.name == "sqlite":
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            if column in existing:
                return False
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
            conn.commit()
            return True
        else:
            existing = conn.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).fetchone()
            if existing:
                return False
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"))
            conn.commit()
            return True

    with engine.connect() as conn:
        try:
            ensure_column(conn, "workspaces", "api_key", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspaces_api_key "
                "ON workspaces (api_key) WHERE api_key IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "archived", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()  # Column already exists or DB doesn't support IF NOT EXISTS
        try:
            ensure_column(conn, "registered_agents", "pruning_enabled", "BOOLEAN DEFAULT TRUE")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "routing_configs", "tier_names_json", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "routing_configs", "budget_pressure_threshold_pct", "FLOAT DEFAULT 80.0")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "allowed_providers_json", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "matched_term_ids_json", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "model_registry", "department", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "department_budgets", "throttle_tier", "INTEGER DEFAULT 1")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "min_tier", "INTEGER DEFAULT 1")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "max_tier", "INTEGER DEFAULT 4")
        except Exception:
            conn.rollback()
        try:
            # Nullable/unbackfilled here (unlike department_budgets.workspace_id
            # below, which backfills unprefixed rows to "default") -- a real
            # chunk of RegisteredAgent rows have no reliable single-workspace
            # signal at all. backfill_workspaces.py only fills the unambiguous
            # "workspace_id:Dept"-prefixed rows; see models.py's comment.
            ensure_column(conn, "registered_agents", "workspace_id", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            # Governance/lifecycle metadata, fully separate from the
            # runtime `status` column above -- see models.py's comment.
            ensure_column(conn, "registered_agents", "business_purpose", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "owner", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "approval_status", "VARCHAR DEFAULT 'unreviewed'")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "department_budgets", "raw_payload_logging_enabled", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "department_budgets", "raw_retention_days", "INTEGER DEFAULT 30")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "department_budgets", "archived", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()
        try:
            # department_budgets never got a real workspace_id column when
            # token_transactions/audit_events did — it's the reason budget
            # scoping had to be inferred from a "workspace_id:DeptName"
            # string prefix on `department` everywhere. Nullable/unbackfilled
            # here; backfill_workspaces.py populates it once from that prefix.
            ensure_column(conn, "department_budgets", "workspace_id", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "raw_payload", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "raw_logged_at", "TIMESTAMP")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "matched_keywords_json", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "token_transactions", "usage_source", "VARCHAR DEFAULT 'estimated'")
        except Exception:
            conn.rollback()
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
                conn.rollback()
        for table in ("token_transactions", "audit_events"):
            try:
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_governed_request_id "
                    f"ON {table} (governed_request_id)"
                ))
                conn.commit()
            except Exception:
                conn.rollback()
        try:
            ensure_column(conn, "token_transactions", "event_id", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            # Superseded by the workspace-scoped index below -- an earlier
            # deploy created this globally-unique-on-event_id-alone index,
            # which would let two different customers' event_id values
            # collide. Drop it before the correctly-scoped one takes over.
            conn.execute(text("DROP INDEX IF EXISTS uq_token_transactions_event_id"))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            # Client-supplied idempotency key -- a real unique constraint,
            # not just an index, since it's what route_payload() relies on
            # to detect and reject a resubmitted event. Scoped to
            # (workspace_id, event_id), not event_id alone, so two
            # different customers reusing the same event_id string can
            # never collide with or see each other's transaction.
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_token_transactions_workspace_event_id "
                "ON token_transactions (workspace_id, event_id) WHERE event_id IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "token_transactions", "work_item_id", "INTEGER REFERENCES work_items(id)")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "work_item_id", "INTEGER REFERENCES work_items(id)")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "is_simulation", "BOOLEAN DEFAULT FALSE NOT NULL")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "cost_usd", "FLOAT")
        except Exception:
            conn.rollback()
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
                    conn.rollback()
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
                    conn.rollback()
        for column, definition in (
            ("provider_status_code", "INTEGER"),
        ):
            try:
                ensure_column(conn, "token_transactions", column, definition)
            except Exception:
                conn.rollback()
        for column, definition in (
            ("selected_model_name", "VARCHAR"),
            ("selected_model_tier", "VARCHAR"),
            ("routing_reason_code", "VARCHAR"),
        ):
            try:
                ensure_column(conn, "audit_events", column, definition)
            except Exception:
                conn.rollback()
        for table, column, definition in (
            ("registered_agents", "owner_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
            ("work_items", "org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
            ("work_users", "primary_org_unit_id", "INTEGER REFERENCES organizational_units(id)"),
        ):
            try:
                ensure_column(conn, table, column, definition)
            except Exception:
                conn.rollback()
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
                    conn.rollback()
        try:
            # Lets reporting GROUP BY business_purpose in SQL instead of
            # reclassifying every row in Python on every request -- see
            # models.py's TokenTransaction.business_purpose comment.
            ensure_column(conn, "token_transactions", "business_purpose", "VARCHAR")
        except Exception:
            conn.rollback()
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
                conn.rollback()
        try:
            ensure_column(conn, "sensitive_terms", "enabled", "BOOLEAN DEFAULT TRUE")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "sensitive_terms", "is_recommended", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "sensitive_terms", "deleted_at", "TIMESTAMP")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "work_accounts", "merged_into_work_account_id", "INTEGER REFERENCES work_accounts(id)")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "integration_connections", "last_outcome_sync_at", "TIMESTAMP")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "integration_connections", "tracked_objects_json", "TEXT")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "registered_agents", "discovery_source", "TEXT")
        except Exception:
            conn.rollback()
        try:
            mode_column_just_created = ensure_column(conn, "registered_agents", "mode", "VARCHAR DEFAULT 'observe'")
            if mode_column_just_created:
                # One-time backfill, the moment this column is first created:
                # agents that already show activity (not idle, or have logged
                # token transactions) were already operating as Control in
                # practice, so mark them as such rather than resetting live
                # production integrations to Observe out from under them.
                # Genuinely brand-new agents registered after this point still
                # get the "mode" column's own DEFAULT 'observe'.
                conn.execute(text("""
                    UPDATE registered_agents
                    SET mode = 'control'
                    WHERE mode = 'observe'
                      AND (
                        status != 'idle'
                        OR EXISTS (
                            SELECT 1 FROM token_transactions
                            WHERE token_transactions.agent_id = registered_agents.id
                        )
                      )
                """))
                conn.commit()
        except Exception:
            conn.rollback()
        # trial_accounts — create + add new columns
        try:
            from database.models import TrialAccount
            TrialAccount.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            conn.rollback()
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
                conn.rollback()

        try:
            ensure_column(conn, "integration_connections", "connection_key", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_integration_connections_connection_key "
                "ON integration_connections (connection_key) WHERE connection_key IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "token_transactions", "connection_key", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_token_transactions_connection_key "
                "ON token_transactions (connection_key)"
            ))
            conn.commit()
        except Exception:
            conn.rollback()

        # reporting_activity — read-only flat view for future third-party BI
        # tool access (Tableau/PowerBI/Metabase connecting directly to
        # Postgres). Postgres-only: CREATE OR REPLACE VIEW isn't valid SQLite
        # syntax, and nothing in the app queries this view, so skipping it
        # entirely on SQLite (local tests) is safe.
        #
        # The `workspace_id` output column mirrors core/workspace_scope.py's
        # workspace_filter() exactly (real column first, "WORKSPACE:Dept"
        # department-prefix fallback second) -- that helper exists precisely
        # because this same scoping logic was once reimplemented slightly
        # differently in two other files and silently drifted apart
        # (routes_dashboard.py undercounted spend ~5x for a while). If
        # workspace_filter() ever changes, this view's COALESCE/split_part
        # expression must change with it.
        #
        # Deliberately excludes actor_email/actor_name/actor_external_id/
        # origin_record_name (PII / external-record identifiers not needed
        # for aggregate reporting). No per-tenant access control (roles/RLS)
        # yet -- that's provisioned per customer when there's a real request,
        # not built speculatively here.
        #
        # Universal Outcome Ingestion (core/outcome_ingestion.py) columns.
        # event_id + the composite unique index below is the same
        # workspace-scoped idempotency pattern as
        # uq_token_transactions_workspace_event_id above, applied to
        # outcome events instead of transactions -- and, unlike that
        # column, mandatory at the API layer (see ingest_outcome()) rather
        # than optional, since outcome writes drive business-value
        # reporting and duplicate delivery would double-count it.
        try:
            ensure_column(conn, "work_item_outcome_events", "event_id", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "work_item_outcome_events", "is_simulation", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()
        try:
            # WorkItemOutcome already has source_system; WorkItemOutcomeEvent
            # never did, because until Universal Outcome Ingestion every
            # event for one WorkItem came from the same adapter/platform.
            # Needed now so two disagreeing sources both get their own
            # history row without losing which one said what.
            ensure_column(conn, "work_item_outcome_events", "source_system", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            # Mirrors TokenTransaction.connection_key -- lets Universal
            # Connection verification scope an outcome event to exactly one
            # connection instead of a looser workspace+platform match.
            ensure_column(conn, "work_item_outcome_events", "connection_key", "VARCHAR")
        except Exception:
            conn.rollback()
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_work_item_outcome_events_workspace_event_id "
                "ON work_item_outcome_events (workspace_id, event_id) WHERE event_id IS NOT NULL"
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "work_item_outcomes", "is_simulation", "BOOLEAN DEFAULT FALSE")
        except Exception:
            conn.rollback()
        try:
            # Security audit finding: voice_events had no tenant-scoping
            # column at all -- GET/DELETE /api/voice/events read or wiped
            # every workspace's Voice Guard data at once. Nullable since
            # legacy rows predate this column and have no signal to
            # backfill from; those rows remain unscoped/orphaned by design
            # rather than guessed at.
            ensure_column(conn, "voice_events", "workspace_id", "VARCHAR")
        except Exception:
            conn.rollback()
        for col, defn in [
            ("modality", "VARCHAR"),
            ("transcription_confidence", "FLOAT"),
            ("clarification_requested", "BOOLEAN"),
        ]:
            try:
                # CostPilot Voice (Phase 1) -- ask_interactions extended
                # with voice-specific fields rather than a new table (see
                # AskInteraction's own docstring). Existing rows are
                # implicitly modality="text" until the frontend starts
                # tagging voice questions explicitly.
                ensure_column(conn, "ask_interactions", col, defn)
            except Exception:
                conn.rollback()

        # Security architecture assessment, Phase 1 (Foundation) — auth/RBAC
        # tables. Purely additive: create-if-missing, same TrialAccount
        # precedent above, no existing table altered. Seeds the 4 builtin
        # roles + their permission bundles from core/rbac.py exactly once
        # (idempotent — checks for existing rows first).
        try:
            from database.models import User, Role, RolePermission, UserWorkspaceMembership, UserSession
            User.__table__.create(bind=engine, checkfirst=True)
            Role.__table__.create(bind=engine, checkfirst=True)
            RolePermission.__table__.create(bind=engine, checkfirst=True)
            UserWorkspaceMembership.__table__.create(bind=engine, checkfirst=True)
            UserSession.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            conn.rollback()
        # Phase 0 (security architecture assessment, next slice): identity
        # threading. Nullable on both tables -- NULL for every historical
        # row and for any question/event that never carried a session, in
        # the same soft-rollout spirit as AUTH_ENFORCEMENT_ENABLED itself.
        try:
            ensure_column(conn, "ask_interactions", "user_id", "INTEGER REFERENCES users(id)")
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "user_id", "INTEGER REFERENCES users(id)")
        except Exception:
            conn.rollback()
        try:
            from core.rbac import BUILTIN_ROLES
            from database.db import SessionLocal
            seed_db = SessionLocal()
            try:
                for key, spec in BUILTIN_ROLES.items():
                    role = seed_db.query(Role).filter_by(key=key).first()
                    if not role:
                        role = Role(key=key, label=spec["label"], is_builtin=True)
                        seed_db.add(role)
                        seed_db.commit()
                        seed_db.refresh(role)
                    existing_perms = {
                        p.permission for p in
                        seed_db.query(RolePermission).filter_by(role_id=role.id).all()
                    }
                    for permission in spec["permissions"]:
                        if permission not in existing_perms:
                            seed_db.add(RolePermission(role_id=role.id, permission=permission))
                seed_db.commit()
            finally:
                seed_db.close()
        except Exception:
            conn.rollback()

        # Permissioned Actions, Action Proposals slice 1: the reusable
        # propose -> confirm -> execute -> audit flow. Purely additive,
        # same create-if-missing precedent as the RBAC tables above.
        try:
            from database.models import ActionProposal
            ActionProposal.__table__.create(bind=engine, checkfirst=True)
        except Exception:
            conn.rollback()
        try:
            ensure_column(conn, "audit_events", "proposal_id", "INTEGER REFERENCES action_proposals(id)")
        except Exception:
            conn.rollback()

        # Uses its OWN connection, deliberately not the shared `conn` above.
        # The trial_accounts loop just above issues raw ALTER TABLE ADD
        # COLUMN statements without IF NOT EXISTS, which fail (columns
        # already exist) on every deploy after the first -- each is caught
        # by its own bare except, but with no explicit rollback, that
        # leaves `conn`'s transaction in Postgres's "aborted, commands
        # ignored until end of transaction block" state for the rest of
        # this function. A fresh connection sidesteps that entirely rather
        # than depending on fixing pre-existing, unrelated migration code.
        if engine.dialect.name == "postgresql":
            try:
                with engine.connect() as view_conn:
                    view_conn.execute(text("""
                        CREATE OR REPLACE VIEW reporting_activity AS
                        SELECT
                            COALESCE(tt.workspace_id, split_part(tt.department, ':', 1)) AS workspace_id,
                            tt.id AS transaction_id,
                            tt.timestamp,
                            tt.department,
                            tt.source_platform,
                            tt.model_tier,
                            tt.model_name,
                            tt.input_tokens,
                            tt.output_tokens,
                            tt.cost_usd,
                            tt.tokens_saved,
                            tt.was_pruned,
                            tt.business_purpose,
                            wi.id AS work_item_id,
                            wi.name AS work_item_name,
                            wi.external_id AS work_item_external_id,
                            wi.context_type,
                            wi.context_template,
                            wi.source_record_type,
                            wa.id AS account_id,
                            wa.name AS account_name,
                            wa.external_id AS account_external_id,
                            wo.outcome_status,
                            wo.outcome_value,
                            wo.outcome_success,
                            wo.is_closed,
                            ra.id AS agent_id,
                            ra.name AS agent_name,
                            ra.department AS agent_department
                        FROM token_transactions tt
                        LEFT JOIN work_items wi ON wi.id = tt.work_item_id
                        LEFT JOIN work_accounts wa ON wa.id = wi.account_id
                        LEFT JOIN work_item_outcomes wo ON wo.work_item_id = wi.id
                        LEFT JOIN registered_agents ra ON ra.id = tt.agent_id
                    """))
                    view_conn.commit()
            except Exception:
                conn.rollback()
