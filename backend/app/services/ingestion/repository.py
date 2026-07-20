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

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.ingestion import Chunk, ChunkHit, Embedding, Section, SectionHit
from app.services.base import BaseRepository


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

    async def list_for_document(self, document_id: uuid.UUID) -> list[Section]:
        """V2 enrichment stage's input: every section built for this document, in
        document order (by char_start), so summaries are produced in a stable order too."""
        stmt = (
            select(Section)
            .where(Section.org_id == self._ctx.org_id, Section.document_id == document_id)
            .order_by(Section.char_start)
        )
        return list(await self._db.scalars(stmt))

    async def update_enrichment(self, rows: list[dict]) -> None:
        """Updates summary and topics for successful enriched sections. Each row is a dict
        with ``id`` (section uuid), ``summary`` (str), and ``topics`` (list[str])."""
        for row in rows:
            stmt = (
                update(Section)
                .where(Section.id == row["id"], Section.org_id == self._ctx.org_id)
                .values(summary=row["summary"], topics=row["topics"])
            )
            await self._db.execute(stmt)


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

    async def get_by_ids(
        self, chunk_ids: list[uuid.UUID]
    ) -> list[tuple[Chunk, int | None, int | None]]:
        """F41 citation resolution's input: re-fetches chunk rows by id, scoped to the
        caller's org — an independent backstop (not merely relying on the caller having
        already resolved these ids through an org-scoped notebook elsewhere), same
        reasoning as ``EmbeddingRepository.search_chunks``'s own ``org_id`` filter.

        LEFT-joins ``sections`` (via the nullable ``Chunk.section_id`` FK) to also surface
        the owning section's page range for citation display — a chunk's citable span
        always exists on the chunk row itself, so this join must never turn a chunk row
        into zero rows: it's an OUTER join, and the section side is independently
        org-scoped in the join predicate (never trusting the FK alone). Returns
        ``(chunk, page_start, page_end)`` tuples; both page fields are ``None`` when the
        chunk has no ``section_id`` or the section row can't be found."""
        if not chunk_ids:
            return []
        stmt = (
            select(Chunk, Section.page_start, Section.page_end)
            .outerjoin(
                Section,
                (Chunk.section_id == Section.id) & (Section.org_id == self._ctx.org_id),
            )
            .where(Chunk.org_id == self._ctx.org_id, Chunk.id.in_(chunk_ids))
        )
        result = await self._db.execute(stmt)
        return [(chunk, page_start, page_end) for chunk, page_start, page_end in result]


class EmbeddingRepository(BaseRepository[Embedding]):
    model = Embedding

    async def upsert_embeddings(
        self,
        document_id: uuid.UUID,
        owner_type: str,
        rows: list[dict],
    ) -> None:
        """Upsert embeddings on ``unique(owner_type, owner_id, model)`` — re-embedding the
        same resource under the same model updates the vector in place instead of inserting
        a duplicate. ``rows`` are plain dicts (``owner_id``, ``model``, ``dim``,
        ``embedding``); ``org_id``/``document_id``/``owner_type`` are filled in here.
        Used for both chunks (F22) and sections (V2 enrichment)."""
        if not rows:
            return
        values = [
            {
                "org_id": self._ctx.org_id,
                "document_id": document_id,
                "owner_type": owner_type,
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

    async def upsert_chunk_embeddings(
        self,
        document_id: uuid.UUID,
        rows: list[dict],
    ) -> None:
        """Upsert ``owner_type='chunk'`` rows on ``unique(owner_type, owner_id, model)`` —
        re-embedding the same chunk under the same model updates the vector in place
        instead of inserting a duplicate. ``rows`` are plain dicts (``owner_id``, ``model``,
        ``dim``, ``embedding``); ``org_id``/``document_id``/``owner_type`` are filled in here."""
        await self.upsert_embeddings(document_id, "chunk", rows)

    async def search_chunks(
        self,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        k: int,
        *,
        section_ids: list[uuid.UUID] | None = None,
    ) -> list[ChunkHit]:
        """The MVP ``flat_vector`` retrieval query (librarydocs.md "pgvector",
        architecture.md ``retrieve()``). Filters ``org_id`` directly — an independent
        backstop, not merely a consequence of the caller already having resolved
        ``document_ids`` to an org-scoped notebook (see F31's repository-level isolation
        test) — plus ``owner_type='chunk'`` and ``model = :active_model`` so a re-embed
        under a new model name never returns duplicate hits per chunk. Caller passes ``k``
        straight through to ``LIMIT``; no over-fetch (no reranker exists yet to justify one).
        When ``section_ids`` is provided (V2 hierarchical retrieval), narrows chunks to those
        sections only."""
        if not document_ids:
            return []
        where_clauses = [
            Embedding.org_id == self._ctx.org_id,
            Embedding.owner_type == "chunk",
            Embedding.model == model,
            Embedding.document_id.in_(document_ids),
        ]
        if section_ids and section_ids:
            where_clauses.append(Chunk.section_id.in_(section_ids))
        stmt = (
            select(
                Embedding.owner_id.label("chunk_id"),
                Chunk.document_id,
                Chunk.content,
                Chunk.char_start,
                Chunk.char_end,
                Embedding.embedding.cosine_distance(query_vector).label("distance"),
            )
            .join(Chunk, Chunk.id == Embedding.owner_id)
            .where(*where_clauses)
            .order_by("distance")
            .limit(k)
        )
        rows = await self._db.execute(stmt)
        return [ChunkHit(**row._mapping) for row in rows]

    async def search_sections(
        self,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        s: int,
    ) -> list[SectionHit]:
        """V2 hierarchical (coarse-to-fine) retrieval's coarse pass: kNN over section
        embeddings (owner_type='section'). Filters ``org_id`` directly (independent
        backstop) plus ``owner_type='section'`` and ``model = :active_model``. Caller
        passes ``s`` (top-sections count) straight through to ``LIMIT``."""
        if not document_ids:
            return []
        stmt = (
            select(
                Embedding.owner_id.label("section_id"),
                Section.document_id,
                Section.heading,
                Section.path,
                Section.topics,
                Embedding.embedding.cosine_distance(query_vector).label("distance"),
            )
            .join(Section, Section.id == Embedding.owner_id)
            .where(
                Embedding.org_id == self._ctx.org_id,
                Embedding.owner_type == "section",
                Embedding.model == model,
                Embedding.document_id.in_(document_ids),
            )
            .order_by("distance")
            .limit(s)
        )
        rows = await self._db.execute(stmt)
        return [SectionHit(**row._mapping) for row in rows]
