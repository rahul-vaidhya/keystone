"""Identity constants — roles and token types."""

from __future__ import annotations

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER})
ADMIN_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
