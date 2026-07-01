"""Chat (F40) error types."""

from __future__ import annotations


class GenerationFailed(RuntimeError):
    """The LLM seam call failed and retries were exhausted. Mapped to a 503 by
    `platform/http.py` — a clean, defined failure path (no uncaught exception), mirroring
    the ingestion stage failure-model discipline. Raised before any persistence happens
    (`ChatService.ask` calls the LLM before `_persist`), so there's no partial
    conversation/message row left behind on this failure path."""
