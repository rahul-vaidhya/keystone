"""F24 ingestion pipeline auto-dispatch: each stage's arq job, on success, enqueues the
next stage's job — never the whole chain upfront. Registered in ``worker.py``'s
``WorkerSettings.functions``.

Redelivery safety (arq is at-least-once, not exactly-once) has two layers:
1. Each job reads the document's status BEFORE calling its stage and compares it to the
   status AFTER. The F20-F22 stage methods are already idempotent (a document already
   past a stage is returned unchanged) — this lets a SEQUENTIALLY redelivered job (one
   that finds the document already advanced) skip the next-stage enqueue. This alone is
   NOT sufficient under a concurrent redelivery: the "before" read happens in a separate
   transaction from the stage's actual claim, so two deliveries running close together
   can both read the same "before" status and both decide to enqueue.
2. The actual correctness guarantee is ``platform/queue.py``'s ``job_id``: every
   next-stage enqueue passes a deterministic ``job_id`` (``f"ingestion:{stage}:
   {document_id}"``), and arq itself refuses to create a second job sharing an
   already-queued/active ``job_id`` — so even if both deliveries above decide to enqueue,
   only one job is actually created. Layer 1 just avoids the redundant enqueue attempt in
   the common (sequential) case; layer 2 is what makes it correct under concurrency.

Known gap, not built here (named, not hidden): if the enqueue call itself fails — at
upload time, or here between stages — the chain silently stops and the document is
stranded at whatever status it reached. There is no sweeper/re-dispatch in F24; see
memory.md.
"""

from __future__ import annotations

import uuid

from app.models.documents import DocumentStatus
from app.platform.context import TenantContext
from app.platform.queue import JobQueue
from app.platform.seams import get_embedder, get_parser
from app.platform.storage import get_object_store
from app.services.documents import documents_service
from app.services.ingestion import ingestion_service


async def run_parsing_stage_job(ctx: dict, *, org_id: str, document_id: str) -> None:
    tenant_ctx = TenantContext(org_id=uuid.UUID(org_id))
    doc_id = uuid.UUID(document_id)

    before = await documents_service.get_document(tenant_ctx, doc_id)
    after = await ingestion_service.run_parsing_stage(
        tenant_ctx, doc_id, parser=get_parser(), object_store=get_object_store()
    )
    if before.status != after.status and after.status == DocumentStatus.STRUCTURING:
        job_queue: JobQueue = ctx["job_queue"]
        await job_queue.enqueue(
            "run_structuring_stage_job",
            job_id=f"ingestion:structuring:{document_id}",
            org_id=org_id,
            document_id=document_id,
        )


async def run_structuring_stage_job(ctx: dict, *, org_id: str, document_id: str) -> None:
    tenant_ctx = TenantContext(org_id=uuid.UUID(org_id))
    doc_id = uuid.UUID(document_id)

    before = await documents_service.get_document(tenant_ctx, doc_id)
    after = await ingestion_service.run_structuring_stage(
        tenant_ctx, doc_id, object_store=get_object_store()
    )
    if before.status != after.status and after.status == DocumentStatus.EMBEDDING:
        job_queue: JobQueue = ctx["job_queue"]
        await job_queue.enqueue(
            "run_embedding_stage_job",
            job_id=f"ingestion:embedding:{document_id}",
            org_id=org_id,
            document_id=document_id,
        )


async def run_embedding_stage_job(ctx: dict, *, org_id: str, document_id: str) -> None:
    tenant_ctx = TenantContext(org_id=uuid.UUID(org_id))
    doc_id = uuid.UUID(document_id)

    # Terminal stage: the document lands on READY or FAILED, nothing further to chain.
    await ingestion_service.run_embedding_stage(tenant_ctx, doc_id, embedder=get_embedder())
