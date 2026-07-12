"""F10 auth integration tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.auth import Organization, User
from app.utils.constants import ROLE_MEMBER, ROLE_OWNER
from app.utils.passwords import hash_password
from main import app


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_signup_login_me_and_refresh(
    client: AsyncClient,
    session_factory,
) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "owner@test.com", "password": "password123", "org_name": "Acme"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert client.cookies.get("veratas_refresh")

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["email"] == "owner@test.com"
    assert me_body["role"] == ROLE_OWNER

    refresh = await client.post("/auth/refresh")
    assert refresh.status_code == 200
    assert "access_token" in refresh.json()


async def test_login_with_password(
    client: AsyncClient,
    session_factory,
) -> None:
    org_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(Organization(id=org_id, name="Beta"))
        session.add(
            User(
                org_id=org_id,
                email="member@test.com",
                role=ROLE_MEMBER,
                password_hash=hash_password("secretpass"),
            )
        )

    login = await client.post(
        "/auth/login",
        json={"email": "member@test.com", "password": "secretpass"},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


async def test_invite_requires_admin(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "admin@test.com", "password": "password123", "org_name": "Gamma"},
    )
    token = signup.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    invite = await client.post(
        "/auth/invite",
        headers=headers,
        json={"email": "newbie@test.com", "password": "password123", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201
    assert invite.json()["role"] == ROLE_MEMBER

    users = await client.get("/auth/users", headers=headers)
    assert users.status_code == 200
    emails = {u["email"] for u in users.json()}
    assert emails >= {"admin@test.com", "newbie@test.com"}


async def test_change_role_admin_can_promote_member(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "owner2@test.com", "password": "password123", "org_name": "Delta"},
    )
    owner_token = signup.json()["access_token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "promotee@test.com", "password": "password123", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["id"]

    resp = await client.patch(
        f"/auth/users/{target_id}/role",
        headers=owner_headers,
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


async def test_change_role_member_forbidden(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "owner3@test.com", "password": "password123", "org_name": "Epsilon"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "plainmember@test.com", "password": "password123", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["id"]

    login = await client.post(
        "/auth/login", json={"email": "plainmember@test.com", "password": "password123"}
    )
    member_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await client.patch(
        f"/auth/users/{target_id}/role",
        headers=member_headers,
        json={"role": "admin"},
    )
    assert resp.status_code == 403


async def test_change_role_cannot_target_owner(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "owner4@test.com", "password": "password123", "org_name": "Zeta"},
    )
    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {signup.json()['access_token']}"}
    )
    owner_id = me.json()["id"]
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    resp = await client.patch(
        f"/auth/users/{owner_id}/role",
        headers=owner_headers,
        json={"role": "admin"},
    )
    assert resp.status_code == 403


async def test_change_role_cannot_self_change(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "owner5@test.com", "password": "password123", "org_name": "Eta"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "selfadmin@test.com", "password": "password123", "role": "admin"},
    )
    admin_login = await client.post(
        "/auth/login", json={"email": "selfadmin@test.com", "password": "password123"}
    )
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    admin_id = invite.json()["id"]

    resp = await client.patch(
        f"/auth/users/{admin_id}/role",
        headers=admin_headers,
        json={"role": "member"},
    )
    assert resp.status_code == 403


async def test_get_org_returns_current_name(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "orgview@test.com", "password": "password123", "org_name": "Theta"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    resp = await client.get("/auth/org", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Theta"


async def test_rename_org_owner_can_rename(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "orgowner@test.com", "password": "password123", "org_name": "Iota"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    resp = await client.patch(
        "/auth/org",
        headers=owner_headers,
        json={"org_name": "Iota Renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Iota Renamed"

    check = await client.get("/auth/org", headers=owner_headers)
    assert check.json()["name"] == "Iota Renamed"


async def test_rename_org_admin_can_rename(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "orgadminowner@test.com", "password": "password123", "org_name": "Kappa"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "orgadmin@test.com", "password": "password123", "role": "admin"},
    )
    assert invite.status_code == 201

    admin_login = await client.post(
        "/auth/login", json={"email": "orgadmin@test.com", "password": "password123"}
    )
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    resp = await client.patch(
        "/auth/org",
        headers=admin_headers,
        json={"org_name": "Kappa Renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Kappa Renamed"


async def test_rename_org_member_forbidden(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "orgmemberowner@test.com", "password": "password123", "org_name": "Lambda"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "orgmember@test.com", "password": "password123", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201

    member_login = await client.post(
        "/auth/login", json={"email": "orgmember@test.com", "password": "password123"}
    )
    member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}"}

    resp = await client.patch(
        "/auth/org",
        headers=member_headers,
        json={"org_name": "Should Not Work"},
    )
    assert resp.status_code == 403


async def test_rename_org_rejects_empty_name(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "orgvalidate@test.com", "password": "password123", "org_name": "Mu"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    resp = await client.patch(
        "/auth/org",
        headers=owner_headers,
        json={"org_name": ""},
    )
    assert resp.status_code == 422
