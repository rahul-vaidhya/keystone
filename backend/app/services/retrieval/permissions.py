"""Permission resolution for retrieval — the single hook where V2 groups/grants
permission logic slots in (architecture.md). A separate concern from search-strategy
orchestration (``service.py``): this module never touches embeddings/chunks/the
seams, only documents/folders/tags via ``documents.service``/``access_roles``.
"""

from __future__ import annotations

import uuid

from app.middleware.context import TenantContext
from app.services.access_roles import (
    resolve_access_controlling_tags,
    resolve_folder_effective_tags,
    resolve_user_granted_tags,
)
from app.services.documents import documents_service
from app.utils.constants import ADMIN_ROLES


async def resolve_allowed_documents(ctx: TenantContext) -> list[uuid.UUID]:
    """The single seam where V2 groups/grants permission logic slots in (architecture.md).

    ``owner``/``admin`` always see every org document (unchanged from the original MVP
    stub). For everyone else: a tag becomes "access-controlling" the moment it's granted
    to any Access Role (docs/access-roles-dnd-plan.md) — a resource carrying none of the
    org's access-controlling tags stays open to everyone, exactly as before this feature
    existed. A resource carrying one DOES gate: visible only to a member holding a role
    granted at least one of those tags. Folder tags are inherited down the subtree
    (``_inherited_folder_tags``); a document's own direct tags (``document_tags``) add to
    whatever it inherits from its folder chain. No caching: this runs fresh on every
    call, so granting/revoking a tag takes effect on the very next request."""
    docs = await documents_service.list_documents(ctx)
    if ctx.role in ADMIN_ROLES:
        return [d.id for d in docs]

    access_controlling = await resolve_access_controlling_tags(ctx)
    if not access_controlling:
        # Nobody has ever granted any tag to any Access Role — nothing is gated yet, so
        # skip the folder/document-tag fetches entirely.
        return [d.id for d in docs]

    folders = await documents_service.list_folders(ctx)
    inherited_folder_tags = resolve_folder_effective_tags(folders)
    doc_tag_ids = await documents_service.list_document_tag_ids_by_documents(
        ctx, [d.id for d in docs]
    )
    user_granted = await resolve_user_granted_tags(ctx)

    allowed: list[uuid.UUID] = []
    for doc in docs:
        folder_tags = inherited_folder_tags.get(doc.folder_id, set()) if doc.folder_id else set()
        effective_tags = folder_tags | set(doc_tag_ids.get(doc.id, []))
        gating_tags = effective_tags & access_controlling
        if not gating_tags or (gating_tags & user_granted):
            allowed.append(doc.id)
    return allowed
