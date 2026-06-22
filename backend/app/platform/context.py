"""The tenant context — the single carrier of ``org_id`` through every request and job.

Resolved once at the HTTP edge from the authenticated user (the ``get_ctx`` dependency,
Phase 1) and reconstructed in arq workers from the ``org_id`` on the job payload. Every
repository takes it and scopes its SQL by ``ctx.org_id``; ``org_id`` is NEVER read from a
request body. ``user_id``/``role`` are unset on the worker path (jobs have no user) and are
populated once auth lands in Phase 1.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: str | None = None
