"""Ingestion repositories — all SQL for sections/chunks/embeddings lives here
(codestandards "Layering"). Idempotent re-run of the structuring stage is
delete-then-rebuild: the service deletes a document's existing sections+chunks and
inserts fresh ones inside one transaction, rather than upserting row-by-row. The
embedding stage instead upserts row-by-row on the ``embeddings`` table's
``unique(owner_type, owner_id, model)`` constraint — that constraint exists precisely so
a re-embed updates the vector in place rather than needing a delete-then-rebuild.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.ingestion.models import Chunk, Embedding, Section
from app.platform.repository import BaseRepository


class SectionRepository(BaseRepository[Section]):
    model = Section

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        stmt = delete(Section).where(
            Section.org_id == self._ctx.org_id, Section.document_id == document_id
        )
        await self._db.execute(stmt)

    async def bulk_create(self, sections: list[Section]) -> None:
        self._db.add_all(sections)
        await self._db.flush()


class ChunkRepository(BaseRepository[Chunk]):
    model = Chunk

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        stmt = delete(Chunk).where(
            Chunk.org_id == self._ctx.org_id, Chunk.document_id == document_id
        )
        await self._db.execute(stmt)

    async def bulk_create(self, chunks: list[Chunk]) -> None:
        self._db.add_all(chunks)
        await self._db.flush()

    async def list_for_document(self, document_id: uuid.UUID) -> list[Chunk]:
        """The embedding stage's input: every chunk F21 built for this document, in
        document order, so embeddings are produced in a stable order too."""
        stmt = (
            select(Chunk)
            .where(Chunk.org_id == self._ctx.org_id, Chunk.document_id == document_id)
            .order_by(Chunk.ordinal)
        )
        return list(await self._db.scalars(stmt))


class EmbeddingRepository(BaseRepository[Embedding]):
    model = Embedding

    async def upsert_chunk_embeddings(
        self,
        document_id: uuid.UUID,
        rows: list[dict],
    ) -> None:
        """Upsert ``owner_type='chunk'`` rows on ``unique(owner_type, owner_id, model)`` —
        re-embedding the same chunk under the same model updates the vector in place
        instead of inserting a duplicate. ``rows`` are plain dicts (``owner_id``, ``model``,
        ``dim``, ``embedding``); ``org_id``/``document_id``/``owner_type`` are filled in here."""
        if not rows:
            return
        values = [
            {
                "org_id": self._ctx.org_id,
                "document_id": document_id,
                "owner_type": "chunk",
                **row,
            }
            for row in rows
        ]
        stmt = pg_insert(Embedding).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_type", "owner_id", "model"],
            set_={
                "embedding": stmt.excluded.embedding,
                "dim": stmt.excluded.dim,
            },
        )
        await self._db.execute(stmt)
