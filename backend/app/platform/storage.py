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


class LocalDiskObjectStore:
    """Filesystem-backed implementation of the same ``put``/``get`` port (F05).

    Selected by ``STORAGE_MODE=local`` so the full product — API process *and* arq worker
    — can run with zero cloud creds (the offline-first principle the seam fakes already
    follow, now extended to storage). Keys map 1:1 to nested paths under ``root``, so the
    parse->structure->embed blob round-trip is byte-identical to R2.

    Deliberately NOT production-grade durable: no multipart, no concurrency hardening, no
    fsync ceremony. It is for local dev / future offline integration runs, not prod.

    Missing-key parity: ``get`` raises ``FileNotFoundError`` on an absent key, matching
    R2's get-raises-on-missing behaviour. Callers (ingestion parsing/structuring) catch
    ``except Exception`` broadly, so ``FileNotFoundError`` and boto3's ``ClientError`` take
    the same control-flow branch ("not found"). If a caller is ever narrowed to catch a
    store-specific type, THAT is what breaks this parity — keep the catch broad or define a
    shared not-found type for both stores.
    """

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _path_for(self, key: str) -> Path:
        return self._root / key

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        # content_type is part of the port signature (R2 stores it as object metadata);
        # the local store has nowhere to put it and the callers never read it back, so it
        # is intentionally ignored here.
        path = self._path_for(key)
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        # Path.read_bytes raises FileNotFoundError on a missing key — the parity property.
        return await asyncio.to_thread(self._path_for(key).read_bytes)


def get_object_store() -> ObjectStore:
    """Single selection point for the object store, switched on ``STORAGE_MODE``.

    Both the API process (via FastAPI dependency) and the arq worker (which calls this
    directly) resolve the store here, so they always share the same implementation and the
    same blobs. Tests still override the FastAPI dependency via ``app.dependency_overrides``
    with an in-memory fake.
    """
    mode = settings.STORAGE_MODE
    if mode == "r2":
        return R2ObjectStore()
    if mode == "local":
        return LocalDiskObjectStore(settings.STORAGE_LOCAL_ROOT)
    raise ValueError(f"unknown STORAGE_MODE={mode!r}; expected one of ('r2', 'local').")


def build_storage_key(org_id: uuid.UUID, document_id: uuid.UUID, filename: str) -> str:
    ext = Path(filename).suffix
    return f"org/{org_id}/doc/{document_id}/source{ext}"


def build_artifact_key(org_id: uuid.UUID, document_id: uuid.UUID, stage: str) -> str:
    """Persisted ingestion-stage artifact key (librarydocs.md "Object storage":
    ``.../artifacts/{stage}.json``)."""
    return f"org/{org_id}/doc/{document_id}/artifacts/{stage}.json"
