"""rls scaffolding (flag-off): tenant_isolation policies + app_user/migrator role split

Phase 0 / F02. The enforced-RLS design is WRITTEN here but stays INERT in MVP: the whole
body is gated on ``settings.RLS_ENABLED``, which defaults OFF in dev/test, so with the flag
off ``upgrade()`` is a deliberate no-op. The MVP isolation guarantee is the always-on
app-level ``WHERE org_id = :org`` filter in the repositories (app/platform/repository.py);
this migration is the Phase-6 backstop that F60 switches on (it sets ``RLS_ENABLED=true``,
makes the app connect as the restricted ``app_user`` role, and ships the enabling migration).

Pattern for later phases: every NEW tenant-scoped table adds its own ``tenant_isolation``
policy + ``ENABLE``/``FORCE ROW LEVEL SECURITY`` in the same flag-gated style — keyed on
``org_id`` for all tables, and on ``id`` for the tenancy root ``organizations`` (no org_id).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-22

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.config.settings import settings

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tenant-scoped tables that exist as of F02 and the column their policy keys on. The root
# `organizations` keys on `id` (it has no org_id); every other table keys on `org_id`.
_RLS_TABLES: dict[str, str] = {
    "organizations": "id",
    "users": "org_id",
}


def upgrade() -> None:
    if not settings.RLS_ENABLED:
        # Flag OFF (MVP default): policies/roles below are intentionally NOT applied.
        # App-level org_id scoping in the repositories is the MVP guarantee. F60 enables.
        return

    # --- DB-role split (privileged migrator owns tables / bypasses RLS; app_user is the
    # restricted role the running app connects as in Phase 6 and that RLS constrains). ---
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

    # --- Per-table tenant_isolation policy + FORCE RLS. `current_setting('app.org_id',
    # true)` is missing_ok → an unset GUC yields NULL → zero rows (fail-closed). ---
    for table, column in _RLS_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({column} = current_setting('app.org_id', true)::uuid)
                WITH CHECK ({column} = current_setting('app.org_id', true)::uuid)
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def downgrade() -> None:
    if not settings.RLS_ENABLED:
        return

    for table, _column in _RLS_TABLES.items():
        op.execute(f"REVOKE ALL ON {table} FROM app_user")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # Roles are cluster-global and may own objects elsewhere; leave them for F60 to manage.
