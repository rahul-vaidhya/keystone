"""F10 auth integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.auth import InviteToken, Organization, User
from app.utils.constants import LOGIN_LOCKOUT_THRESHOLD, ROLE_MEMBER, ROLE_OWNER
from app.utils.passwords import hash_password
from main import app


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _accept_invite(
    client: AsyncClient, invite_body: dict, password: str = "password123"
) -> dict:
    """Accept a self-serve invite link (``InviteOut`` response body) and return the
    resulting ``TokenResponse`` JSON — the invitee's own log-straight-in tokens."""
    resp = await client.post(
        "/auth/accept-invite",
        json={
            "org_id": invite_body["org_id"],
            "token": invite_body["invite_token"],
            "password": password,
        },
    )
    assert resp.status_code == 200
    return resp.json()


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
        json={"email": "newbie@test.com", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201
    assert invite.json()["user"]["role"] == ROLE_MEMBER

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
        json={"email": "promotee@test.com", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["user"]["id"]

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
        json={"email": "plainmember@test.com", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["user"]["id"]

    tokens = await _accept_invite(client, invite.json())
    member_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

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
        json={"email": "selfadmin@test.com", "role": "admin"},
    )
    admin_tokens = await _accept_invite(client, invite.json())
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    admin_id = invite.json()["user"]["id"]

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
        json={"email": "orgadmin@test.com", "role": "admin"},
    )
    assert invite.status_code == 201

    admin_tokens = await _accept_invite(client, invite.json())
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

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
        json={"email": "orgmember@test.com", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201

    member_tokens = await _accept_invite(client, invite.json())
    member_headers = {"Authorization": f"Bearer {member_tokens['access_token']}"}

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


# ---- Deactivation (member removal) ----


async def test_deactivate_member_blocks_login_and_active_session(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "deactowner@test.com", "password": "password123", "org_name": "Nu"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "deactme@test.com", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["user"]["id"]

    tokens = await _accept_invite(client, invite.json())
    member_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Sanity: the member's token works before deactivation.
    me = await client.get("/auth/me", headers=member_headers)
    assert me.status_code == 200

    status_resp = await client.patch(
        f"/auth/users/{target_id}/status",
        headers=owner_headers,
        json={"is_active": False},
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["is_active"] is False

    # The already-issued access token is rejected on the VERY NEXT request.
    me_after = await client.get("/auth/me", headers=member_headers)
    assert me_after.status_code == 401

    # Login is blocked with the same generic message as a wrong password.
    relogin = await client.post(
        "/auth/login", json={"email": "deactme@test.com", "password": "password123"}
    )
    assert relogin.status_code == 401


async def test_reactivate_member_restores_login(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "reactowner@test.com", "password": "password123", "org_name": "Xi"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "reactme@test.com", "role": ROLE_MEMBER},
    )
    target_id = invite.json()["user"]["id"]
    await _accept_invite(client, invite.json())

    await client.patch(
        f"/auth/users/{target_id}/status", headers=owner_headers, json={"is_active": False}
    )
    reactivate = await client.patch(
        f"/auth/users/{target_id}/status", headers=owner_headers, json={"is_active": True}
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["is_active"] is True

    login = await client.post(
        "/auth/login", json={"email": "reactme@test.com", "password": "password123"}
    )
    assert login.status_code == 200


async def test_deactivate_cannot_target_self(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "selfdeact@test.com", "password": "password123", "org_name": "Omicron"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    me = await client.get("/auth/me", headers=owner_headers)
    owner_id = me.json()["id"]

    resp = await client.patch(
        f"/auth/users/{owner_id}/status", headers=owner_headers, json={"is_active": False}
    )
    assert resp.status_code == 403


async def test_deactivate_cannot_target_owner(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "ownertarget@test.com", "password": "password123", "org_name": "Pi"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    owner_id = (await client.get("/auth/me", headers=owner_headers)).json()["id"]

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "piadmin@test.com", "role": "admin"},
    )
    admin_tokens = await _accept_invite(client, invite.json())
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    resp = await client.patch(
        f"/auth/users/{owner_id}/status", headers=admin_headers, json={"is_active": False}
    )
    assert resp.status_code == 403


async def test_deactivate_forbidden_for_member(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "rhoowner@test.com", "password": "password123", "org_name": "Rho"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite_a = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "rhomember@test.com", "role": ROLE_MEMBER},
    )
    invite_b = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "rhotarget@test.com", "role": ROLE_MEMBER},
    )
    target_id = invite_b.json()["user"]["id"]

    tokens = await _accept_invite(client, invite_a.json())
    member_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.patch(
        f"/auth/users/{target_id}/status", headers=member_headers, json={"is_active": False}
    )
    assert resp.status_code == 403
    assert invite_a.status_code == 201


# ---- Self-service password change ----


async def test_change_password_requires_correct_current_password(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "pwchange1@test.com", "password": "password123", "org_name": "Sigma"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    resp = await client.post(
        "/auth/me/password",
        headers=headers,
        json={"current_password": "wrongpass", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401


async def test_change_password_succeeds_and_invalidates_old_session(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "pwchange2@test.com", "password": "password123", "org_name": "Tau"},
    )
    old_token = signup.json()["access_token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}

    resp = await client.post(
        "/auth/me/password",
        headers=old_headers,
        json={"current_password": "password123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]
    assert new_token != old_token

    # The OLD token (pre-password-change token_version) is now rejected.
    stale = await client.get("/auth/me", headers=old_headers)
    assert stale.status_code == 401

    # The NEW token (returned by the password-change call itself) still works.
    fresh = await client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert fresh.status_code == 200

    # And the new password logs in correctly; the old one no longer does.
    relogin_new = await client.post(
        "/auth/login", json={"email": "pwchange2@test.com", "password": "newpassword456"}
    )
    assert relogin_new.status_code == 200
    relogin_old = await client.post(
        "/auth/login", json={"email": "pwchange2@test.com", "password": "password123"}
    )
    assert relogin_old.status_code == 401


# ---- Login lockout ----


async def test_login_locks_after_repeated_failures(
    client: AsyncClient,
    session_factory,
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "lockout1@test.com", "password": "password123", "org_name": "Upsilon"},
    )

    for _ in range(LOGIN_LOCKOUT_THRESHOLD):
        resp = await client.post(
            "/auth/login", json={"email": "lockout1@test.com", "password": "wrongpass"}
        )
        assert resp.status_code == 401

    # Even the CORRECT password is now rejected — the account, not the credential, is locked.
    locked = await client.post(
        "/auth/login", json={"email": "lockout1@test.com", "password": "password123"}
    )
    assert locked.status_code == 423
    assert "locked" in locked.json()["detail"].lower()


async def test_login_success_resets_failure_counter(
    client: AsyncClient,
    session_factory,
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "lockout2@test.com", "password": "password123", "org_name": "Phi"},
    )

    for _ in range(LOGIN_LOCKOUT_THRESHOLD - 1):
        resp = await client.post(
            "/auth/login", json={"email": "lockout2@test.com", "password": "wrongpass"}
        )
        assert resp.status_code == 401

    good = await client.post(
        "/auth/login", json={"email": "lockout2@test.com", "password": "password123"}
    )
    assert good.status_code == 200

    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == "lockout2@test.com"))
        ).scalar_one()
        assert user.failed_login_attempts == 0
        assert user.locked_until is None


async def test_login_lockout_expires_after_window(
    client: AsyncClient,
    session_factory,
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "lockout3@test.com", "password": "password123", "org_name": "Chi"},
    )

    async with session_factory() as session, session.begin():
        user = (
            await session.execute(select(User).where(User.email == "lockout3@test.com"))
        ).scalar_one()
        user.failed_login_attempts = LOGIN_LOCKOUT_THRESHOLD
        user.locked_until = datetime.now(UTC) - timedelta(seconds=1)  # already expired

    resp = await client.post(
        "/auth/login", json={"email": "lockout3@test.com", "password": "password123"}
    )
    assert resp.status_code == 200


# ---- Self-serve invite links ----


async def test_invite_response_has_no_password_field_and_carries_token(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink1@test.com", "password": "password123", "org_name": "Psi"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink1member@test.com", "role": ROLE_MEMBER},
    )
    assert invite.status_code == 201
    body = invite.json()
    assert "password" not in body
    assert "password" not in body["user"]
    assert body["invite_token"]
    assert body["org_id"]
    assert body["user"]["email"] == "invlink1member@test.com"


async def test_invited_user_cannot_login_before_accepting(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink2@test.com", "password": "password123", "org_name": "Omega"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink2member@test.com", "role": ROLE_MEMBER},
    )

    # No password has been set yet — password_hash is None, so login() rejects it via
    # the existing `if not user.password_hash` branch, same as any wrong password.
    resp = await client.post(
        "/auth/login", json={"email": "invlink2member@test.com", "password": "anything123"}
    )
    assert resp.status_code == 401


async def test_accept_invite_succeeds_and_logs_in(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink3@test.com", "password": "password123", "org_name": "AcceptOrg"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink3member@test.com", "role": ROLE_MEMBER},
    )
    body = invite.json()

    accept = await client.post(
        "/auth/accept-invite",
        json={
            "org_id": body["org_id"],
            "token": body["invite_token"],
            "password": "brandnew123",
        },
    )
    assert accept.status_code == 200
    assert accept.json()["access_token"]

    me = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {accept.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == "invlink3member@test.com"

    # The invitee's own password now works via ordinary login too.
    login = await client.post(
        "/auth/login", json={"email": "invlink3member@test.com", "password": "brandnew123"}
    )
    assert login.status_code == 200


async def test_accept_invite_token_cannot_be_reused(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink4@test.com", "password": "password123", "org_name": "ReuseOrg"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink4member@test.com", "role": ROLE_MEMBER},
    )
    body = invite.json()
    accept_body = {
        "org_id": body["org_id"],
        "token": body["invite_token"],
        "password": "firstpass123",
    }

    first = await client.post("/auth/accept-invite", json=accept_body)
    assert first.status_code == 200

    second = await client.post(
        "/auth/accept-invite", json={**accept_body, "password": "secondpass123"}
    )
    assert second.status_code == 400


async def test_accept_invite_rejects_garbage_token(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink5@test.com", "password": "password123", "org_name": "GarbageOrg"},
    )
    org = await client.get(
        "/auth/org", headers={"Authorization": f"Bearer {signup.json()['access_token']}"}
    )
    org_id = org.json()["id"]

    resp = await client.post(
        "/auth/accept-invite",
        json={"org_id": org_id, "token": "not-a-real-token", "password": "whatever123"},
    )
    assert resp.status_code == 400


async def test_accept_invite_rejects_expired_token(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink6@test.com", "password": "password123", "org_name": "ExpiredOrg"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink6member@test.com", "role": ROLE_MEMBER},
    )
    body = invite.json()

    # Superuser DB session (bypasses RLS) — reach in directly to force the token expired,
    # mirroring the existing lockout tests' pattern of manipulating DB state directly.
    async with session_factory() as session, session.begin():
        token = (
            await session.execute(
                select(InviteToken).where(InviteToken.org_id == uuid.UUID(body["org_id"]))
            )
        ).scalar_one()
        token.expires_at = datetime.now(UTC) - timedelta(days=1)

    resp = await client.post(
        "/auth/accept-invite",
        json={
            "org_id": body["org_id"],
            "token": body["invite_token"],
            "password": "whatever123",
        },
    )
    assert resp.status_code == 400


async def test_accept_invite_cross_org_rejected(
    client: AsyncClient,
    session_factory,
) -> None:
    signup_a = await client.post(
        "/auth/signup",
        json={"email": "invlink7a@test.com", "password": "password123", "org_name": "OrgA"},
    )
    owner_a_headers = {"Authorization": f"Bearer {signup_a.json()['access_token']}"}

    signup_b = await client.post(
        "/auth/signup",
        json={"email": "invlink7b@test.com", "password": "password123", "org_name": "OrgB"},
    )
    org_b = await client.get(
        "/auth/org", headers={"Authorization": f"Bearer {signup_b.json()['access_token']}"}
    )
    org_b_id = org_b.json()["id"]

    invite = await client.post(
        "/auth/invite",
        headers=owner_a_headers,
        json={"email": "invlink7member@test.com", "role": ROLE_MEMBER},
    )
    body = invite.json()

    # Correct token, but paired with a DIFFERENT org's id — must not leak across orgs.
    resp = await client.post(
        "/auth/accept-invite",
        json={"org_id": org_b_id, "token": body["invite_token"], "password": "whatever123"},
    )
    assert resp.status_code == 400


async def test_invite_forbidden_for_member(
    client: AsyncClient,
    session_factory,
) -> None:
    signup = await client.post(
        "/auth/signup",
        json={"email": "invlink8@test.com", "password": "password123", "org_name": "ForbidOrg"},
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "invlink8member@test.com", "role": ROLE_MEMBER},
    )
    tokens = await _accept_invite(client, invite.json())
    member_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/auth/invite",
        headers=member_headers,
        json={"email": "invlink8someone@test.com", "role": ROLE_MEMBER},
    )
    assert resp.status_code == 403


# ---- Display name (users.name, migration 0018) ----


async def test_signup_with_name_persists_and_is_returned_by_me(
    client: AsyncClient,
    session_factory,
) -> None:
    resp = await client.post(
        "/auth/signup",
        json={
            "email": "namedsignup@test.com",
            "password": "password123",
            "org_name": "NameOrg",
            "name": "Uma Reviewer",
        },
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["name"] == "Uma Reviewer"


async def test_signup_without_name_leaves_it_null(
    client: AsyncClient,
    session_factory,
) -> None:
    resp = await client.post(
        "/auth/signup",
        json={
            "email": "unnamedsignup@test.com",
            "password": "password123",
            "org_name": "NoNameOrg",
        },
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["name"] is None


async def test_signup_blank_name_normalizes_to_null(
    client: AsyncClient,
    session_factory,
) -> None:
    resp = await client.post(
        "/auth/signup",
        json={
            "email": "blanknamesignup@test.com",
            "password": "password123",
            "org_name": "BlankNameOrg",
            "name": "   ",
        },
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["name"] is None


async def test_invited_member_has_null_name(
    client: AsyncClient,
    session_factory,
) -> None:
    """Name collection isn't wired into the invite flow (out of scope this round) —
    an invited member's ``name`` stays null through invite and after accepting, same
    as any pre-migration user."""
    signup = await client.post(
        "/auth/signup",
        json={
            "email": "nameinviteowner@test.com",
            "password": "password123",
            "org_name": "InviteNameOrg",
        },
    )
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    invite = await client.post(
        "/auth/invite",
        headers=owner_headers,
        json={"email": "nameinvitemember@test.com", "role": ROLE_MEMBER},
    )
    assert invite.json()["user"]["name"] is None

    tokens = await _accept_invite(client, invite.json())
    member_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    me = await client.get("/auth/me", headers=member_headers)
    assert me.status_code == 200
    assert me.json()["name"] is None
