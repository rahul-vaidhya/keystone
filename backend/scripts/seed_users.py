"""One-off dev seed: 3 users (owner/admin/member) in one org, all sharing a password.

Idempotent — re-running it skips users that already exist instead of erroring.
Run from backend/: python -m scripts.seed_users
"""

from __future__ import annotations

import asyncio

from app.exceptions.auth import EmailTaken
from app.platform import db as db_mod
from app.platform.constants import ROLE_ADMIN, ROLE_MEMBER
from app.platform.context import TenantContext
from app.repositories.auth import AuthRepository
from app.schemas.auth import InviteRequest, SignupRequest
from app.services.auth import auth_service

ORG_NAME = "Veratas Demo"
PASSWORD = "hellos123"
OWNER_EMAIL = "owner@gmail.com"
ADMIN_EMAIL = "admin@gmail.com"
MEMBER_EMAIL = "member@gmail.com"


async def main() -> None:
    async with db_mod.sessionmaker() as session:
        existing = await AuthRepository(session).find_login_candidates(OWNER_EMAIL)

    if not existing:
        await auth_service.signup(
            SignupRequest(email=OWNER_EMAIL, password=PASSWORD, org_name=ORG_NAME)
        )
        print(f"Created owner: {OWNER_EMAIL}")
        async with db_mod.sessionmaker() as session:
            existing = await AuthRepository(session).find_login_candidates(OWNER_EMAIL)
    else:
        print(f"Owner already exists: {OWNER_EMAIL}")

    owner_user, _org_name = existing[0]
    ctx = TenantContext(org_id=owner_user.org_id, user_id=owner_user.id, role=owner_user.role)

    for email, role in ((ADMIN_EMAIL, ROLE_ADMIN), (MEMBER_EMAIL, ROLE_MEMBER)):
        try:
            await auth_service.invite(ctx, InviteRequest(email=email, password=PASSWORD, role=role))
            print(f"Created {role}: {email}")
        except EmailTaken:
            print(f"{role.capitalize()} already exists: {email}")


if __name__ == "__main__":
    asyncio.run(main())
