"""Ingestion repositories — all SQL for sections/chunks lives here (codestandards
"Layering"). Idempotent re-run of the structuring stage is delete-then-rebuild: the
service deletes a document's existing sections+chunks and inserts fresh ones inside
one transaction, rather than upserting row-by-row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete

from app.ingestion.models import Chunk, Section
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
