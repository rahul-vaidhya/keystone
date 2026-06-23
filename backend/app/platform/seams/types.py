"""Shared types crossing the seam boundary, and the seam-misconfiguration error."""

from __future__ import annotations

from dataclasses import dataclass


class SeamNotConfigured(RuntimeError):
    """A real seam was selected but its vendor/credentials are not wired up.

    Raised by the real adapters (never the fakes), so an accidental ``SEAMS_MODE=real``
    without keys fails loudly instead of silently degrading.
    """


@dataclass(frozen=True, slots=True)
class OutlineNode:
    """One heading in a parsed document's outline, with structural offsets only.

    The structuring stage (F21) turns these into the `sections` tree — char offsets give
    provenance; page/level support hierarchy later. Semantic fields (summary/topics) are
    NOT here: they are V2 enrichment, populated by a backfill job behind a flag.
    """

    heading: str
    level: int
    char_start: int
    char_end: int
    page_start: int = 1
    page_end: int = 1


@dataclass(frozen=True, slots=True)
class ParsedDoc:
    """What the `Parser` seam returns: raw text + outline + page count + detected language.

    `language` is returned by the parser (it detects it) per the foundation-review L2 fix.
    """

    text: str
    outline: list[OutlineNode]
    language: str
    page_count: int


@dataclass(frozen=True, slots=True)
class Message:
    """A chat turn handed to the `LLM` seam. `role` is system|user|assistant."""

    role: str
    content: str
