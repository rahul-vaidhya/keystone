"""F20 parsing + F21 structuring + F22 embedding stages — the staged ingestion pipeline
(architecture.md "Ingestion pipeline"). Calls into ``documents.service`` for every
document-table mutation (module boundary rule: a module reaches another module only
through its service, never its repository); reaches the ``Parser``/``Embedder`` seams and
the object store (not a seam — called directly) to do the actual work.

This package is a structural split of what used to be one flat ``service.py`` (444 lines
mixing the parsing/structuring/embedding/search stages) — the refactor that introduced
this split made ZERO logic changes. Each stage's algorithm lives in its own module
(``parsing.py``/``structuring.py``/``embedding.py``/``search.py``); ``IngestionService``
here is pure delegation, so ``ingestion_service.run_parsing_stage(...)`` etc. resolve
exactly as before.
"""

from __future__ import annotations

import uuid

from app.documents.schemas import DocumentOut
from app.ingestion.schemas import ChunkHit
from app.ingestion.service import embedding as _embedding
from app.ingestion.service import parsing as _parsing
from app.ingestion.service import search as _search
from app.ingestion.service import structuring as _structuring
from app.platform.context import TenantContext
from app.platform.seams import Embedder, Parser
from app.platform.storage import ObjectStore


class IngestionService:
    async def run_parsing_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        parser: Parser,
        object_store: ObjectStore,
    ) -> DocumentOut:
        return await _parsing.run_parsing_stage(
            ctx, document_id, parser=parser, object_store=object_store
        )

    async def run_structuring_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        object_store: ObjectStore,
    ) -> DocumentOut:
        return await _structuring.run_structuring_stage(ctx, document_id, object_store=object_store)

    async def run_embedding_stage(
        self,
        ctx: TenantContext,
        document_id: uuid.UUID,
        *,
        embedder: Embedder,
    ) -> DocumentOut:
        return await _embedding.run_embedding_stage(ctx, document_id, embedder=embedder)

    async def search_chunks(
        self,
        ctx: TenantContext,
        *,
        query_vector: list[float],
        document_ids: list[uuid.UUID],
        model: str,
        k: int,
    ) -> list[ChunkHit]:
        return await _search.search_chunks(
            ctx, query_vector=query_vector, document_ids=document_ids, model=model, k=k
        )


ingestion_service = IngestionService()
