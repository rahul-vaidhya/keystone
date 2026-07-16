"""F60 enforced-RLS teeth tests.

The DoD test (and friends): connect as the restricted ``app_user`` role — NOT the
Testcontainers superuser every other test file runs as (superusers bypass RLS even under
FORCE, which is why the rest of the suite is unaffected by migration 0015) — deliberately
OMIT the app-level ``WHERE org_id`` filter, and prove Postgres alone blocks the leak:

- filter-omitted raw SELECTs see only the GUC's org (zero cross-tenant rows);
- an unset GUC reads ZERO rows (fail-closed), on tenant tables AND the auth bootstrap;
- ``WITH CHECK`` rejects writing a row carrying another org's ``org_id``;
- the ``app.auth_email`` bootstrap policy widens reads to exactly the named email's
  ``users`` rows and their orgs — nothing else.

Plus a golden-path HTTP flow (signup → login → me → invite → folders → cross-org 404)
driven end-to-end over an ``app_user``-bound engine, so the tenant_session refactor is
proven against a genuinely restricted role, not just against raw SQL.

Emails here use the ``rls-`` prefix (shared session-scoped container — see conftest).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.auth import User
from main import app

_APP_USER_PASSWORD = "app-user-test-pw"  # test-container-only credential, never a real one


@pytest.fixture
async def app_user_engine(pg_url: str):
    """Provision LOGIN for ``app_user`` (migration 0015 creates it NOLOGIN — credentials
    are per-environment, never in a migration) and hand back an engine connected AS that
    restricted role."""
    admin_engine = create_async_engine(pg_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"ALTER ROLE app_user WITH LOGIN PASSWORD '{_APP_USER_PASSWORD}'"))
    await admin_engine.dispose()

    engine = create_async_engine(
        pg_url.replace("://veratas:veratas@", f"://app_user:{_APP_USER_PASSWORD}@")
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_user_db(app_user_engine):
    """Rebind the app-global engine/session factory to the ``app_user`` connection, so
    the REAL tenant_session/auth_session helpers — and every HTTP request through the
    app — run as the restricted role. Restored after the test."""
    from app.config import db as db_mod

    orig_engine, orig_maker = db_mod.engine, db_mod.sessionmaker
    db_mod.engine = app_user_engine
    db_mod.sessionmaker = async_sessionmaker(
        app_user_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        yield app_user_engine
    finally:
        db_mod.engine, db_mod.sessionmaker = orig_engine, orig_maker


@pytest.fixture
async def client(app_user_db) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _signup(client: AsyncClient, email: str, org_name: str) -> str:
    resp = await client.post(
        "/auth/signup",
        json={"email": email, "password": "password123", "org_name": org_name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _org_id_of(session_factory, email: str) -> uuid.UUID:
    """Org lookup via the superuser engine — test scaffolding, not the code under test."""
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user.org_id


async def test_golden_path_http_flow_as_app_user(client: AsyncClient) -> None:
    """The whole auth bootstrap + tenant plumbing survives a genuinely restricted role:
    signup (org INSERT under a pre-set GUC), login (auth_email policies + lockout write),
    me (current_user under tenant_session), invite, folders, and cross-org 404."""
    token_a = await _signup(client, "rls-owner-a@test.com", "RlsOrgA")
    token_b = await _signup(client, "rls-owner-b@test.com", "RlsOrgB")

    # Wrong password first: exercises the failed-attempt counter UPDATE, which runs
    # under the tenant policy only after login() switches the GUC to the matched org.
    bad = await client.post(
        "/auth/login", json={"email": "rls-owner-a@test.com", "password": "wrong-password"}
    )
    assert bad.status_code == 401

    good = await client.post(
        "/auth/login", json={"email": "rls-owner-a@test.com", "password": "password123"}
    )
    assert good.status_code == 200, good.text

    me = await client.get("/auth/me", headers=_auth(token_a))
    assert me.status_code == 200
    assert me.json()["email"] == "rls-owner-a@test.com"

    invite = await client.post(
        "/auth/invite",
        json={"email": "rls-member-a@test.com", "role": "member"},
        headers=_auth(token_a),
    )
    assert invite.status_code == 201, invite.text
    users_a = await client.get("/auth/users", headers=_auth(token_a))
    assert users_a.status_code == 200
    assert {u["email"] for u in users_a.json()} == {
        "rls-owner-a@test.com",
        "rls-member-a@test.com",
    }

    folder = await client.post(
        "/documents/folders", json={"name": "rls-folder-a"}, headers=_auth(token_a)
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]

    # Org B sees none of it: empty list, and the direct fetch 404s.
    folders_b = await client.get("/documents/folders", headers=_auth(token_b))
    assert folders_b.status_code == 200
    assert all(f["name"] != "rls-folder-a" for f in folders_b.json())
    cross = await client.get(f"/documents/folders/{folder_id}", headers=_auth(token_b))
    assert cross.status_code == 404


async def test_teeth_filter_omitted_reads_zero_cross_tenant_rows(
    client: AsyncClient, app_user_engine, session_factory
) -> None:
    """THE DoD test: as ``app_user``, with the app-level filter deliberately omitted,
    raw SQL sees only the GUC's org — and with the GUC unset, nothing at all."""
    await _signup(client, "rls-teeth-a@test.com", "RlsTeethA")
    await _signup(client, "rls-teeth-b@test.com", "RlsTeethB")
    org_a = await _org_id_of(session_factory, "rls-teeth-a@test.com")

    async with app_user_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_a)})
        # No WHERE clause anywhere below — RLS alone must scope the result.
        user_orgs = (await conn.execute(text("SELECT org_id FROM users"))).scalars().all()
        assert user_orgs, "expected org A's own rows to be visible"
        assert set(user_orgs) == {org_a}

        org_ids = (await conn.execute(text("SELECT id FROM organizations"))).scalars().all()
        assert set(org_ids) == {org_a}

        folder_orgs = (await conn.execute(text("SELECT org_id FROM folders"))).scalars().all()
        assert set(folder_orgs) <= {org_a}

    # Fresh transaction, GUC never set → fail-closed: zero rows everywhere, even though
    # both orgs' rows exist.
    async with app_user_engine.begin() as conn:
        for table in ("users", "organizations", "folders", "documents"):
            count = await conn.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            assert count == 0, f"unset GUC must read zero rows from {table}, got {count}"


async def test_teeth_with_check_rejects_cross_tenant_write(
    client: AsyncClient, app_user_engine, session_factory
) -> None:
    """The write side: scoped to org A, inserting a row that CARRIES org B's org_id must
    be rejected by the policy's WITH CHECK — Postgres, not application code."""
    await _signup(client, "rls-write-a@test.com", "RlsWriteA")
    await _signup(client, "rls-write-b@test.com", "RlsWriteB")
    org_a = await _org_id_of(session_factory, "rls-write-a@test.com")
    org_b = await _org_id_of(session_factory, "rls-write-b@test.com")

    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_user_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_a)}
            )
            await conn.execute(
                text("INSERT INTO tags (id, org_id, name) VALUES (:id, :org, :name)"),
                {"id": str(uuid.uuid4()), "org": str(org_b), "name": "rls-evil-tag"},
            )


async def test_teeth_auth_email_policy_is_exactly_one_email_wide(
    client: AsyncClient, app_user_engine, session_factory
) -> None:
    """The pre-tenant bootstrap policy widens reads to the named email's users rows and
    their orgs — and to nothing else (folders stay at zero)."""
    await _signup(client, "rls-boot-a@test.com", "RlsBootA")
    await _signup(client, "rls-boot-b@test.com", "RlsBootB")
    org_a = await _org_id_of(session_factory, "rls-boot-a@test.com")

    async with app_user_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.auth_email', :email, true)"),
            {"email": "rls-boot-a@test.com"},
        )
        emails = (await conn.execute(text("SELECT email FROM users"))).scalars().all()
        assert emails == ["rls-boot-a@test.com"]

        org_ids = (await conn.execute(text("SELECT id FROM organizations"))).scalars().all()
        assert set(org_ids) == {org_a}

        folder_count = await conn.scalar(text("SELECT count(*) FROM folders"))
        assert folder_count == 0


def test_no_bare_sessionmaker_outside_db_module() -> None:
    """Guard against the drift F60 closed: every app DB access must go through
    tenant_session/auth_session (config/db.py). A bare ``sessionmaker()`` call anywhere
    else would open a session whose transaction never sets the GUC — which, as
    ``app_user``, reads zero rows and silently breaks that feature in production."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    allowed = app_root / "config" / "db.py"
    pattern = re.compile(r"(?<!async_)\bsessionmaker\(\)")

    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        if path == allowed:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(app_root.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "bare sessionmaker() outside config/db.py:\n" + "\n".join(offenders)
