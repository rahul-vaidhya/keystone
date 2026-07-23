"""Embed widget domain models and Pydantic schemas (docs/embed-widget-plan.md).

One widget = one notebook + a public capability id + an origin allowlist + an active
flag. ``public_id`` is stored PLAINTEXT (unlike invite tokens' sha256-hashed
capability tokens — see ``app/services/embed.py`` for the full rationale): it is
public by design, visible in the customer's page source the instant the snippet is
pasted, so hashing it at rest buys nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config.db import Base


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Public by design (visible in the customer's page source) — plaintext, GLOBALLY
    # unique (not per-org): the public URL/script only carries org_id + public_id and
    # must resolve to exactly one widget with no prior org context.
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    allowed_origins: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set explicitly in the repository's update() method — never `onupdate=func.now()`
    # (the documented MissingGreenlet gotcha: the ORM attribute only refreshes via a
    # post-flush IO round trip the caller's synchronous `model_validate` can't trigger
    # inside an async session).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---- API schemas ----


class WidgetCreateRequest(BaseModel):
    knowledge_base_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    allowed_origins: list[str] = Field(default_factory=list)


class WidgetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allowed_origins: list[str] | None = None
    is_active: bool | None = None


class WidgetOut(BaseModel):
    """``embed_snippet``/``iframe_url`` are computed (never stored) from
    ``settings.PUBLIC_APP_URL`` at read time — see ``EmbedService._to_widget_out``."""

    id: uuid.UUID
    name: str
    knowledge_base_id: uuid.UUID
    public_id: str
    allowed_origins: list[str]
    is_active: bool
    created_at: datetime
    embed_snippet: str
    iframe_url: str


class EmbedConfigOut(BaseModel):
    """Public-facing (served with NO auth) — deliberately exposes nothing about the
    widget/org beyond these two display strings."""

    widget_name: str
    notebook_name: str


class EmbedChatRequest(BaseModel):
    query: str = Field(min_length=1)
    k: int = Field(default=8, ge=1, le=50)
    # Captured client-side by the embed page from the parent frame's
    # `window.location.origin` (relayed through `widget.js`'s iframe query param) and
    # checked against the widget's `allowed_origins`. Spoofable by a non-browser
    # client — the accepted caveat named in the plan; rate limiting + `is_active`
    # revocation are the real backstops.
    parent_origin: str
