"""Document use cases: listing, upload + checksum dedupe, the F20-F22 ingestion
status-pipeline transitions, and the narrow cross-module accessors other modules' services
call (module-boundary rule: a module reaches another module only through its service)."""

from __future__ import annotations

import hashlib
import uuid

from app.config import db as db_mod
from app.middleware.context import TenantContext
from app.models.documents import Document, DocumentOut, DocumentStatus, DocumentTag
from app.services.base import BaseRepository
from app.services.documents.exceptions import (
    DocumentNotFound,
    FolderNotFound,
    UnsupportedFileType,
)
from app.services.storage import ObjectStore, build_artifact_key, build_storage_key

# ---- exceptions (imported from documents.exceptions) ----
# Re-exported here for backwards compatibility


# ---- repository ----
class DocumentRepository(BaseRepository[Document]):
    model = Document

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        stmt = self._scoped().where(Document.id == document_id)
        return await self._db.scalar(stmt)

    async def get_by_checksum(self, checksum: str) -> Document | None:
        stmt = self._scoped().where(Document.checksum == checksum)
        return await self._db.scalar(stmt)

    async def list_by_ids(self, document_ids: list[uuid.UUID]) -> list[Document]:
        """Org-scoped batch lookup. First caller: ``knowledge.service`` resolving the document
        rows for a notebook's attached document ids (knowledge owns the join table, not
        ``documents`` itself, so it asks this service rather than reading the table directly)."""
        if not document_ids:
            return []
        stmt = self._scoped().where(Document.id.in_(document_ids))
        return list(await self._db.scalars(stmt))

    async def list(
        self, *, folder_id: uuid.UUID | None = None, tag_id: uuid.UUID | None = None
    ) -> list[Document]:
        stmt = self._scoped()
        if folder_id is not None:
            stmt = stmt.where(Document.folder_id == folder_id)
        if tag_id is not None:
            stmt = stmt.join(DocumentTag, DocumentTag.document_id == Document.id).where(
                DocumentTag.tag_id == tag_id
            )
        stmt = stmt.order_by(Document.created_at)
        return list(await self._db.scalars(stmt))

    async def create_upload(
        self,
        *,
        folder_id: uuid.UUID | None,
        title: str,
        uploaded_by: uuid.UUID | None = None,
    ) -> Document:
        """Insert the row with an id assigned (flushed) but no storage_key/checksum yet —
        the caller needs the id to build the object-store key before the upload completes.
        ``uploaded_by`` is set only here, at genuine-new-document creation time — a
        checksum-dedupe hit returns the EXISTING row untouched (see ``upload_document``),
        so re-uploading an identical file never overwrites the original uploader."""
        document = Document(
            org_id=self._ctx.org_id, folder_id=folder_id, title=title, uploaded_by=uploaded_by
        )
        self._db.add(document)
        await self._db.flush()
        return document

    async def mark_uploaded(
        self,
        document: Document,
        *,
        storage_key: str,
        checksum: str,
        mime_type: str,
        byte_size: int,
    ) -> None:
        document.storage_key = storage_key
        document.checksum = checksum
        document.mime_type = mime_type
        document.byte_size = byte_size
        await self._db.flush()

    async def begin_parsing(self, document_id: uuid.UUID) -> Document | None:
        """Transition to PARSING if eligible; otherwise return the document unchanged so the
        caller can treat it as an idempotent no-op (F20: "parsing must be idempotent and
        resumable"). Eligible: a fresh ``UPLOADED`` doc, a doc stuck in ``PARSING`` (crashed
        before the artifact was persisted, so there's nothing to resume from but a re-parse),
        or a doc that previously ``FAILED`` at the parsing stage (retry). A doc already past
        parsing (``STRUCTURING``/``EMBEDDING``/``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status in (DocumentStatus.UPLOADED, DocumentStatus.PARSING) or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.PARSING
        )
        if eligible:
            document.status = DocumentStatus.PARSING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_parsing(
        self, document: Document, *, language: str, page_count: int, artifact_key: str
    ) -> None:
        document.language = language
        document.page_count = page_count
        document.metadata_ = {**document.metadata_, "parse_artifact_key": artifact_key}
        document.status = DocumentStatus.STRUCTURING
        await self._db.flush()

    async def mark_failed(
        self, document: Document, *, failed_stage: str, error_detail: str
    ) -> None:
        document.status = DocumentStatus.FAILED
        document.failed_stage = failed_stage
        document.error_detail = error_detail
        await self._db.flush()

    async def begin_structuring(self, document_id: uuid.UUID) -> Document | None:
        """Transition to (or remain in) ``STRUCTURING`` if eligible; otherwise return the
        document unchanged so the caller treats it as an idempotent no-op (F21: "idempotent
        re-run"). Eligible: a doc already in ``STRUCTURING`` (the state F20 leaves it in, or
        a crashed-and-resumed structuring run — there's nothing to resume but a rebuild), or
        a doc that previously ``FAILED`` at the structuring stage (retry). A doc already past
        structuring (``EMBEDDING``/``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status == DocumentStatus.STRUCTURING or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.STRUCTURING
        )
        if eligible:
            document.status = DocumentStatus.STRUCTURING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_structuring(self, document: Document) -> None:
        document.status = DocumentStatus.EMBEDDING
        await self._db.flush()

    async def begin_embedding(self, document_id: uuid.UUID) -> Document | None:
        """Transition to (or remain in) ``EMBEDDING`` if eligible; otherwise return the
        document unchanged so the caller treats it as an idempotent no-op (F22: "re-running
        embedding must not create duplicate rows"). Eligible: a doc already in ``EMBEDDING``
        (the state F21 leaves it in, or a crashed-and-resumed embedding run), or a doc that
        previously ``FAILED`` at the embedding stage (retry). A doc already past embedding
        (``READY``) is left as-is."""
        document = await self.get_by_id(document_id)
        if document is None:
            return None
        eligible = document.status == DocumentStatus.EMBEDDING or (
            document.status == DocumentStatus.FAILED
            and document.failed_stage == DocumentStatus.EMBEDDING
        )
        if eligible:
            document.status = DocumentStatus.EMBEDDING
            document.failed_stage = None
            document.error_detail = None
            await self._db.flush()
        return document

    async def complete_embedding(self, document: Document) -> None:
        document.status = DocumentStatus.READY
        await self._db.flush()

    async def delete(self, document: Document) -> None:
        await self._db.delete(document)

    async def move(self, document: Document, *, folder_id: uuid.UUID | None) -> None:
        document.folder_id = folder_id
        await self._db.flush()


# ---- service ----


async def list_documents(
    ctx: TenantContext,
    *,
    folder_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
) -> list[DocumentOut]:
    async with db_mod.tenant_session(ctx.org_id) as session:
        docs = await DocumentRepository(session, ctx).list(folder_id=folder_id, tag_id=tag_id)
    emails_by_uploader = await _resolve_uploader_emails(ctx, docs)
    return [
        DocumentOut.model_validate(d).model_copy(
            update={"uploader_email": emails_by_uploader.get(d.uploaded_by)}
        )
        for d in docs
    ]


async def _resolve_uploader_emails(
    ctx: TenantContext, docs: list[Document]
) -> dict[uuid.UUID, str]:
    """Resolve every distinct ``uploaded_by`` id in ``docs`` to its email in ONE batch
    call — never N+1 per-document lookups. Never exposes the raw uploader uuid to the
    frontend (see ``DocumentOut.uploader_email``); this is the module-boundary-respecting
    accessor into the ``auth`` domain (module boundary rule: reach another domain only
    through its ``services/<domain>.py``, never its repository/ORM directly — local
    import here to avoid a documents<->auth circular import, same precedent as
    ``access_roles.service``'s ``from app.services.auth import auth_service``)."""
    from app.services.auth import auth_service

    uploader_ids = {d.uploaded_by for d in docs if d.uploaded_by is not None}
    if not uploader_ids:
        return {}
    return await auth_service.get_users_by_ids(ctx, list(uploader_ids))


# Only PDFs can be ingested (the real Parser seam is PDF-only). Browsers derive the
# multipart Content-Type from the extension, but some send a generic type for PDFs,
# so a generic type is accepted when the filename itself says .pdf.
_PDF_MIME_TYPES = frozenset({"application/pdf", "application/x-pdf"})
_GENERIC_MIME_TYPES = frozenset({"application/octet-stream", "binary/octet-stream", ""})
UNSUPPORTED_FILE_TYPE_MESSAGE = "Only PDF files can be uploaded. Please choose a .pdf file."


def is_supported_upload(filename: str, content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    has_pdf_extension = filename.lower().endswith(".pdf")
    if mime in _PDF_MIME_TYPES:
        return True
    return mime in _GENERIC_MIME_TYPES and has_pdf_extension


async def upload_document(
    ctx: TenantContext,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    folder_id: uuid.UUID | None,
    object_store: ObjectStore,
) -> tuple[DocumentOut, bool]:
    """Upload to the object store and record the document. Re-uploading a byte-identical
    file (same org, same checksum) returns the existing document instead of a duplicate
    (``unique(org_id, checksum)`` — the DoD this enforces)."""
    from app.services.documents.folders import FolderRepository

    if not is_supported_upload(filename, content_type):
        raise UnsupportedFileType(UNSUPPORTED_FILE_TYPE_MESSAGE)

    checksum = hashlib.sha256(data).hexdigest()
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        if folder_id is not None:
            if await FolderRepository(session, ctx).get_by_id(folder_id) is None:
                raise FolderNotFound("Folder not found")

        existing = await repo.get_by_checksum(checksum)
        if existing is not None:
            return DocumentOut.model_validate(existing), False

        document = await repo.create_upload(
            folder_id=folder_id, title=filename, uploaded_by=ctx.user_id
        )
        key = build_storage_key(ctx.org_id, document.id, filename)
        await object_store.put(key, data, content_type)
        await repo.mark_uploaded(
            document,
            storage_key=key,
            checksum=checksum,
            mime_type=content_type,
            byte_size=len(data),
        )
        out = DocumentOut.model_validate(document)
    return out, True


async def begin_parsing(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    """Called by ``ingestion.service`` (never the repository directly — module boundary
    rule) to claim a document for the parsing stage. Returns the document unchanged, with
    whatever status it already had, when parsing isn't eligible to (re)start — the caller
    checks the returned status to decide whether to proceed."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        document = await DocumentRepository(session, ctx).begin_parsing(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        out = DocumentOut.model_validate(document)
    return out


async def complete_parsing(
    ctx: TenantContext,
    document_id: uuid.UUID,
    *,
    language: str,
    page_count: int,
    artifact_key: str,
) -> DocumentOut:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        await repo.complete_parsing(
            document, language=language, page_count=page_count, artifact_key=artifact_key
        )
        out = DocumentOut.model_validate(document)
    return out


async def fail_stage(
    ctx: TenantContext, document_id: uuid.UUID, *, failed_stage: str, error_detail: str
) -> DocumentOut:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        await repo.mark_failed(document, failed_stage=failed_stage, error_detail=error_detail)
        out = DocumentOut.model_validate(document)
    return out


async def begin_structuring(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    """Called by ``ingestion.service`` (never the repository directly — module boundary
    rule) to claim a document for the structuring stage. Returns the document unchanged
    when structuring isn't eligible to (re)start — the caller checks the returned status."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        document = await DocumentRepository(session, ctx).begin_structuring(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        out = DocumentOut.model_validate(document)
    return out


async def complete_structuring(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        await repo.complete_structuring(document)
        out = DocumentOut.model_validate(document)
    return out


async def begin_embedding(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    """Called by ``ingestion.service`` (never the repository directly — module boundary
    rule) to claim a document for the embedding stage. Returns the document unchanged
    when embedding isn't eligible to (re)start — the caller checks the returned status."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        document = await DocumentRepository(session, ctx).begin_embedding(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        out = DocumentOut.model_validate(document)
    return out


async def complete_embedding(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        await repo.complete_embedding(document)
        out = DocumentOut.model_validate(document)
    return out


async def get_document(ctx: TenantContext, document_id: uuid.UUID) -> DocumentOut:
    """Org-scoped existence lookup for OTHER modules (module-boundary rule: cross-module
    calls go through a service, never a repository). First caller: ``knowledge.service``
    validates a document belongs to the attaching org before joining it into a notebook —
    a plain FK can't express that, since it only proves the document exists somewhere, not
    that it's in the same org as the notebook."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        document = await DocumentRepository(session, ctx).get_by_id(document_id)
    if document is None:
        raise DocumentNotFound("Document not found")
    return DocumentOut.model_validate(document)


async def list_by_ids(ctx: TenantContext, document_ids: list[uuid.UUID]) -> list[DocumentOut]:
    """Org-scoped batch lookup for OTHER modules (module-boundary rule). First caller:
    ``knowledge.service`` resolving the document rows attached to a notebook — knowledge
    owns the join table, not ``documents`` itself, so it asks this service rather than
    reading the ``documents`` table directly."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        documents = await DocumentRepository(session, ctx).list_by_ids(document_ids)
    return [DocumentOut.model_validate(d) for d in documents]


async def get_parse_artifact_key(ctx: TenantContext, document_id: uuid.UUID) -> str:
    """Narrow accessor for ``ingestion.service`` (structuring stage): the F20 parsing
    artifact key, written into ``metadata_`` by ``complete_parsing``. A dedicated method
    rather than exposing ``metadata`` on ``DocumentOut`` — keeps the internal storage
    layout out of the public HTTP response shape."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        document = await DocumentRepository(session, ctx).get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
    return document.metadata_["parse_artifact_key"]


async def delete_document(
    ctx: TenantContext, document_id: uuid.UUID, *, object_store: ObjectStore
) -> None:
    """Hard delete — the document row is removed inside a transaction first; every child
    row (sections/chunks/embeddings/document_tags/knowledge_base_documents) is already
    ``ON DELETE CASCADE`` on ``document_id``, so no other repository needs to be touched.
    Blobs are deleted AFTER the DB commit, never before: if blob deletion fails partway,
    we're left with an orphaned blob (an accepted, already-named gap — the future orphan
    sweep's job), which is far safer than the reverse order (a DB delete failing after the
    blob is already gone would leave a document pointing at nothing)."""
    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        storage_key = document.storage_key
        artifact_key = document.metadata_.get("parse_artifact_key")
        await repo.delete(document)

    if storage_key:
        await object_store.delete(storage_key)
    if artifact_key:
        await object_store.delete(artifact_key)
    # Delete the deterministic semantic outline artifact if it was cached.
    semantic_key = build_artifact_key(ctx.org_id, document_id, "semantic_outline")
    await object_store.delete(semantic_key)


async def move_document(
    ctx: TenantContext, document_id: uuid.UUID, folder_id: uuid.UUID | None
) -> DocumentOut:
    """Re-point a document to a different folder (or ``None`` for org root) — the
    drag-and-drop target for dragging a document row onto a folder in the tree. No path
    rebuild needed (unlike folder move): a document carries only a ``folder_id``, no
    materialized path of its own.

    Both the source and destination folder must be accessible to ``ctx`` (Access-Role
    tag gating, same rule folder rename/move/delete uses) — otherwise a member could
    smuggle a document out of a restricted folder they can't otherwise touch, or into
    one, without ever being allowed to manage the folder itself."""
    from app.services.documents.folders import FolderRepository, _assert_folder_access

    async with db_mod.tenant_session(ctx.org_id) as session:
        existing = await DocumentRepository(session, ctx).get_by_id(document_id)
        if existing is None:
            raise DocumentNotFound("Document not found")
        source_folder_id = existing.folder_id
    if source_folder_id is not None:
        await _assert_folder_access(ctx, source_folder_id)
    if folder_id is not None:
        await _assert_folder_access(ctx, folder_id)

    async with db_mod.tenant_session(ctx.org_id) as session:
        repo = DocumentRepository(session, ctx)
        document = await repo.get_by_id(document_id)
        if document is None:
            raise DocumentNotFound("Document not found")
        if folder_id is not None:
            if await FolderRepository(session, ctx).get_by_id(folder_id) is None:
                raise FolderNotFound("Folder not found")
        await repo.move(document, folder_id=folder_id)
    return DocumentOut.model_validate(document)
