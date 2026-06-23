"""Object storage client (S3-compatible — Cloudflare R2 in production).

Not one of the 3 seams (architecture.md: Parser/Embedder/LLM only) — pgvector, the
object store, and the queue are called directly, never swapped behind a Protocol+factory.
This module is the one place we touch the S3 SDK, so vendor details stay out of services
(codestandards "External calls").

Raw uploads are keyed ``org/{org_id}/doc/{document_id}/source{ext}`` (librarydocs.md
"Object storage").
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Protocol

from app.platform.config import settings


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...


class R2ObjectStore:
    """Real adapter, lazily constructed so importing this module never requires boto3
    configuration to be present (mirrors the seam adapters' lazy-construction pattern)."""

    def __init__(self) -> None:
        import boto3

        self._client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        )
        self._bucket = settings.R2_BUCKET

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)


def get_object_store() -> ObjectStore:
    """FastAPI dependency factory. Tests override this via ``app.dependency_overrides``
    with an in-memory fake instead of constructing a real R2 client."""
    return R2ObjectStore()


def build_storage_key(org_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename).suffix
    return f"org/{org_id}/doc/{document_id}/source{ext}"


def build_artifact_key(org_id: uuid.UUID, document_id: uuid.UUID, stage: str) -> str:
    """Persisted ingestion-stage artifact key (librarydocs.md "Object storage":
    ``.../artifacts/{stage}.json``)."""
    return f"org/{org_id}/doc/{document_id}/artifacts/{stage}.json"
