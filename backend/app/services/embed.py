"""Embeddable website chatbot widget use cases (docs/embed-widget-plan.md).

One widget = one notebook + a public capability id (``public_id``) + an origin
allowlist + an active flag. Admin ops validate the notebook exists via
``knowledge_service.get_notebook`` (a cross-module SERVICE call — never the knowledge
repository or its ORM models, module-boundary rule). The public chat path builds an
anonymous ``TenantContext(org_id=..., user_id=None, role=None)`` (the established
worker/accept-invite pattern) and delegates ONLY to ``chat_service.stream_ask`` — this
module never reimplements retrieval, prompting, or LLM calls.

``public_id`` is stored PLAINTEXT, unlike invite tokens (``app/services/auth.py``'s
sha256-hashed capability tokens): a widget id is public by design — it is embedded
verbatim in the customer's page source the moment the snippet/iframe URL is pasted
in, so hashing it at rest buys no security; there is nothing secret to protect. What
DOES protect the endpoint is the origin allowlist, the Redis rate limits below, and
instant revocation via ``is_active``.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.config import db as db_mod
from app.config.settings import settings
from app.middleware.context import TenantContext
from app.models.chat import ChatRequest
from app.models.embed import (
    EmbedChatRequest,
    EmbedConfigOut,
    Widget,
    WidgetCreateRequest,
    WidgetOut,
    WidgetUpdateRequest,
)
from app.services.base import BaseRepository
from app.services.chat import chat_service
from app.services.knowledge import knowledge_service
from app.services.seams import LLM, Embedder, Reranker
from app.utils.rate_limit import RateLimiter


# ---- exceptions ----
class EmbedError(Exception):
    """Base embed-widget failure."""


class WidgetNotFound(EmbedError):
    """ONE generic exception covers a missing widget id, a revoked (``is_active=false``)
    widget, AND a widget belonging to a different org than the URL's ``org_id`` — same
    anti-enumeration collapse as ``InvalidInviteToken`` (``app/services/auth.py``): an
    attacker probing public ids/org ids must never learn which failure mode occurred."""


class OriginNotAllowed(EmbedError):
    pass


class WidgetRateLimited(EmbedError):
    pass


# ---- repository ----
class WidgetRepository(BaseRepository[Widget]):
    model = Widget

    async def create(
        self,
        *,
        knowledge_base_id: uuid.UUID,
        name: str,
        public_id: str,
        allowed_origins: list[str],
        created_by: uuid.UUID | None,
    ) -> Widget:
        widget = Widget(
            org_id=self._ctx.org_id,
            knowledge_base_id=knowledge_base_id,
            name=name,
            public_id=public_id,
            allowed_origins=allowed_origins,
            created_by=created_by,
        )
        self._db.add(widget)
        await self._db.flush()
        return widget

    async def list(self) -> list[Widget]:
        stmt = self._scoped().order_by(Widget.created_at)
        return list(await self._db.scalars(stmt))

    async def get_by_id(self, widget_id: uuid.UUID) -> Widget | None:
        stmt = self._scoped().where(Widget.id == widget_id)
        return await self._db.scalar(stmt)

    async def get_active_by_public_id(self, public_id: str) -> Widget | None:
        """Org-scoped (via ``_scoped()``) AND ``is_active`` — a revoked or wrong-org
        widget simply doesn't match, collapsing into the same "not found" the caller
        already handles for a nonexistent public_id (anti-enumeration)."""
        stmt = self._scoped().where(Widget.public_id == public_id, Widget.is_active.is_(True))
        return await self._db.scalar(stmt)

    async def update(
        self,
        widget: Widget,
        *,
        name: str | None,
        allowed_origins: list[str] | None,
        is_active: bool | None,
    ) -> Widget:
        """Sets ``updated_at`` explicitly rather than relying on the column's server-side
        default refreshing — the same MissingGreenlet-avoidance pattern used by every
        other domain's ``update`` (e.g. ``knowledge.service.NotebookRepository.update``)."""
        if name is not None:
            widget.name = name
        if allowed_origins is not None:
            widget.allowed_origins = allowed_origins
        if is_active is not None:
            widget.is_active = is_active
        widget.updated_at = datetime.now(UTC)
        await self._db.flush()
        return widget

    async def delete(self, widget: Widget) -> None:
        await self._db.delete(widget)


# ---- service ----


class EmbedService:
    def _to_widget_out(self, widget: Widget, org_id: uuid.UUID) -> WidgetOut:
        base = settings.PUBLIC_APP_URL.rstrip("/")
        return WidgetOut(
            id=widget.id,
            name=widget.name,
            knowledge_base_id=widget.knowledge_base_id,
            public_id=widget.public_id,
            allowed_origins=list(widget.allowed_origins),
            is_active=widget.is_active,
            created_at=widget.created_at,
            embed_snippet=(
                f'<script src="{base}/widget.js" data-org="{org_id}" '
                f'data-widget-id="{widget.public_id}" async></script>'
            ),
            iframe_url=f"{base}/embed?org={org_id}&widget={widget.public_id}",
        )

    async def create_widget(self, ctx: TenantContext, req: WidgetCreateRequest) -> WidgetOut:
        # Cross-module validation goes through the SERVICE, never the knowledge
        # repository/ORM directly (module-boundary rule) — raises NotebookNotFound
        # (already mapped to 404) if the notebook doesn't exist or belongs elsewhere.
        await knowledge_service.get_notebook(ctx, req.knowledge_base_id)

        public_id = secrets.token_urlsafe(16)
        async with db_mod.tenant_session(ctx.org_id) as session:
            widget = await WidgetRepository(session, ctx).create(
                knowledge_base_id=req.knowledge_base_id,
                name=req.name,
                public_id=public_id,
                allowed_origins=list(req.allowed_origins),
                created_by=ctx.user_id,
            )
        return self._to_widget_out(widget, ctx.org_id)

    async def list_widgets(self, ctx: TenantContext) -> list[WidgetOut]:
        async with db_mod.tenant_session(ctx.org_id) as session:
            widgets = await WidgetRepository(session, ctx).list()
        return [self._to_widget_out(w, ctx.org_id) for w in widgets]

    async def update_widget(
        self, ctx: TenantContext, widget_id: uuid.UUID, req: WidgetUpdateRequest
    ) -> WidgetOut:
        async with db_mod.tenant_session(ctx.org_id) as session:
            repo = WidgetRepository(session, ctx)
            widget = await repo.get_by_id(widget_id)
            if widget is None:
                raise WidgetNotFound("Widget not found")
            updated = await repo.update(
                widget,
                name=req.name,
                allowed_origins=req.allowed_origins,
                is_active=req.is_active,
            )
        return self._to_widget_out(updated, ctx.org_id)

    async def delete_widget(self, ctx: TenantContext, widget_id: uuid.UUID) -> None:
        async with db_mod.tenant_session(ctx.org_id) as session:
            repo = WidgetRepository(session, ctx)
            widget = await repo.get_by_id(widget_id)
            if widget is None:
                raise WidgetNotFound("Widget not found")
            await repo.delete(widget)

    async def _get_active_widget(self, org_id: uuid.UUID, public_id: str) -> Widget:
        ctx = TenantContext(org_id=org_id)
        async with db_mod.tenant_session(org_id) as session:
            widget = await WidgetRepository(session, ctx).get_active_by_public_id(public_id)
        if widget is None:
            raise WidgetNotFound("Widget not found")
        return widget

    async def get_public_config(self, org_id: uuid.UUID, public_id: str) -> EmbedConfigOut:
        """Public-facing (no auth). Anti-enumeration: missing/revoked/wrong-org all
        raise the same ``WidgetNotFound`` -> 404."""
        widget = await self._get_active_widget(org_id, public_id)
        ctx = TenantContext(org_id=org_id)
        notebook = await knowledge_service.get_notebook(ctx, widget.knowledge_base_id)
        return EmbedConfigOut(widget_name=widget.name, notebook_name=notebook.name)

    async def _enforce_public_chat_guards(
        self,
        widget: Widget,
        req: EmbedChatRequest,
        *,
        rate_limiter: RateLimiter,
        client_ip: str,
    ) -> None:
        """Origin allowlist: a NON-empty ``allowed_origins`` list requires
        ``req.parent_origin`` to be a member; an EMPTY list means allow-all (the plan's
        recommended build-time decision, so trying a widget locally isn't a wall — the
        admin UI is expected to warn about this). Rate limits: per-widget AND per-IP,
        both enforced — either one tripping rejects the request."""
        if widget.allowed_origins and req.parent_origin not in widget.allowed_origins:
            raise OriginNotAllowed("Origin not allowed for this widget")

        widget_ok = await rate_limiter.hit(
            f"widget:{widget.id}", settings.WIDGET_RATE_LIMIT_PER_MINUTE
        )
        if not widget_ok:
            raise WidgetRateLimited("Widget rate limit exceeded")

        ip_ok = await rate_limiter.hit(
            f"widgetip:{widget.id}:{client_ip}", settings.WIDGET_IP_RATE_LIMIT_PER_MINUTE
        )
        if not ip_ok:
            raise WidgetRateLimited("Per-IP rate limit exceeded")

    async def public_chat_stream(
        self,
        org_id: uuid.UUID,
        public_id: str,
        req: EmbedChatRequest,
        *,
        embedder: Embedder,
        llm: LLM,
        reranker: Reranker,
        rate_limiter: RateLimiter,
        client_ip: str,
    ) -> AsyncIterator[dict]:
        """Deliberately a plain ``async def`` with NO ``yield`` in its own body (not an
        async-generator function) — every validation step below (widget lookup, origin
        allowlist, rate limits) executes eagerly the instant this coroutine is awaited,
        BEFORE the caller ever constructs a ``StreamingResponse``. This is load-bearing:
        if this were instead written as ``async def ...: ... ; async for e in
        chat_service.stream_ask(...): yield e``, none of the code above the first
        ``yield`` would run until the returned generator is first iterated — pushing a
        missing/revoked widget, a disallowed origin, or a rate-limit rejection into a
        mid-stream SSE ``{"type":"error"}`` event instead of a real 404/403/429 HTTP
        status. The controller ``await``s this method, THEN builds the
        ``StreamingResponse`` around the async iterator it returns.

        Delegates ONLY to ``chat_service.stream_ask`` for the actual retrieval/prompt/
        LLM pipeline (module-boundary rule) — never reimplements it. ``widget_id`` is
        threaded through so the persisted conversation is marked widget-originated."""
        widget = await self._get_active_widget(org_id, public_id)
        await self._enforce_public_chat_guards(
            widget, req, rate_limiter=rate_limiter, client_ip=client_ip
        )

        ctx = TenantContext(org_id=org_id, user_id=None, role=None)
        chat_req = ChatRequest(notebook_id=widget.knowledge_base_id, query=req.query, k=req.k)
        correlation_id = str(uuid.uuid4())
        return chat_service.stream_ask(
            ctx,
            chat_req,
            embedder=embedder,
            llm=llm,
            reranker=reranker,
            correlation_id=correlation_id,
            widget_id=widget.id,
        )


embed_service = EmbedService()
