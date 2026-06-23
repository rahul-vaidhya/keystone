"""Authoritative ingestion pipeline status enum (architecture.md "Ingestion pipeline").

Single source of truth, referenced everywhere a document's pipeline state is read or
written (repository writes, router responses, future worker transitions, UI color
mapping). There is no ``parsed`` state — do not invent ad-hoc values.
"""

from __future__ import annotations

from enum import StrEnum


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    STRUCTURING = "STRUCTURING"
    EMBEDDING = "EMBEDDING"
    READY = "READY"
    FAILED = "FAILED"
