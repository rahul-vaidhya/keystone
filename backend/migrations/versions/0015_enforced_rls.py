"""enforced RLS (F60): policies + FORCE RLS on every tenant table, app_user grants

Phase 6 / F60 — the teeth. Unlike 0002 (whose body was gated on ``settings.RLS_ENABLED``
and stayed inert in every dev/test DB), this migration is UNCONDITIONAL: isolation must
never depend on a config flag. It is idempotent against both possible prior states
(0002 ran with the flag on → roles + 2 policies already exist; flag off → nothing exists):
roles are created only if absent, and every policy is dropped-if-exists before creation.

What it does:
1. Ensures the ``migrator`` (privileged, table-owning, BYPASSRLS so future data-backfill
   migrations aren't blocked by FORCE RLS) and ``app_user`` (restricted, RLS-constrained,
   NOLOGIN — provisioning LOGIN + password is per-environment, never hardcoded here) roles.
2. ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` and a ``tenant_isolation`` policy (FOR ALL,
   keyed on the ``app.org_id`` GUC) on all 18 tenant tables. ``organizations`` keys on
   ``id`` (it has no org_id); everything else keys on ``org_id``. ``current_setting(...,
   true)`` is missing_ok → an unset GUC yields NULL → zero rows (fail-closed).
3. Two narrow permissive SELECT policies for the pre-tenant auth bootstrap (signup's
   global email check, login's cross-org candidate search — see ``auth_session`` in
   app/config/db.py): ``users`` rows matching the transaction-local ``app.auth_email``
   GUC, and the ``organizations`` rows those users belong to (login's AmbiguousLogin
   needs the org names). Unset GUC → NULL → matches nothing.
4. DML grants to ``app_user`` (schema usage, per-table CRUD, sequence usage).

Every FUTURE tenant-scoped table must ship its own ``ENABLE``/``FORCE`` + policy + grant
in its own migration — same pattern as below.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-14

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every tenant-scoped table as of 0014 and the column its policy keys on. The tenancy
# root ``organizations`` keys on ``id``; every other table carries ``org_id`` directly
# (the "no scope-via-parent" rule — even join/child tables have their own org_id).
_RLS_TABLES: dict[str, str] = {
    "organizations": "id",
    "users": "org_id",
    "folders": "org_id",
    "tags": "org_id",
    "documents": "org_id",
    "document_tags": "org_id",
    "sections": "org_id",
    "chunks": "org_id",
    "embeddings": "org_id",
    "knowledge_bases": "org_id",
    "knowledge_base_documents": "org_id",
    "conversations": "org_id",
    "messages": "org_id",
    "access_roles": "org_id",
    "user_access_roles": "org_id",
    "access_role_tags": "org_id",
    "folder_tags": "org_id",
    "message_traces": "org_id",
}


def upgrade() -> None:
    # --- Roles (idempotent — 0002's flag-gated body may or may not have created them).
    # migrator gets BYPASSRLS: it owns the tables, and a future data-backfill migration
    # must not be silently blocked by the FORCE RLS applied below. ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
                CREATE ROLE migrator NOINHERIT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user NOINHERIT;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER ROLE migrator BYPASSRLS")

    # --- Tenant isolation on every table: ENABLE + FORCE (owner constrained too;
    # superusers are exempt by Postgres design, which is why the dev/test suite —
    # connecting as the container superuser — is unaffected). ---
    for table, column in _RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        # NULLIF guards the cast: once ANY transaction on a pooled connection has
        # set_config'd the GUC, it resets to '' (empty string, NOT missing) at
        # transaction end — and ''::uuid raises instead of matching nothing. NULLIF
        # turns both "never set" (NULL) and "reset" ('') into NULL → zero rows.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({column} = NULLIF(current_setting('app.org_id', true), '')::uuid)
                WITH CHECK ({column} = NULLIF(current_setting('app.org_id', true), '')::uuid)
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")

    # --- Pre-tenant auth bootstrap (SELECT-only; policies are OR-combined, so these
    # widen reads exactly to the explicitly-named email and its orgs, nothing else).
    # users.email is citext, so the comparison against the text GUC stays
    # case-insensitive — identical semantics to the app's email lookups. ---
    op.execute("DROP POLICY IF EXISTS auth_email_lookup ON users")
    op.execute(
        """
        CREATE POLICY auth_email_lookup ON users FOR SELECT
            USING (email = current_setting('app.auth_email', true))
        """
    )
    op.execute("DROP POLICY IF EXISTS auth_email_lookup ON organizations")
    op.execute(
        """
        CREATE POLICY auth_email_lookup ON organizations FOR SELECT
            USING (id IN (
                SELECT org_id FROM users
                WHERE email = current_setting('app.auth_email', true)
            ))
        """
    )

    # --- Schema-level access for the restricted role. UUID PKs are client-generated so
    # no sequences exist today; the blanket sequence grant is cheap insurance. ---
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user")


def downgrade() -> None:
    op.execute("REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM app_user")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user")
    op.execute("DROP POLICY IF EXISTS auth_email_lookup ON organizations")
    op.execute("DROP POLICY IF EXISTS auth_email_lookup ON users")
    for table, _column in _RLS_TABLES.items():
        op.execute(f"REVOKE ALL ON {table} FROM app_user")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER ROLE migrator NOBYPASSRLS")
    # Roles are cluster-global and may own objects elsewhere; deliberately left in place.
