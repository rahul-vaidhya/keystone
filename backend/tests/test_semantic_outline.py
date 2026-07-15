"""Offline unit tests for semantic outline derivation.

Tests the heading detection logic, text search, and outline construction without
a database or HTTP client. Uses a fake LLM that streams predefined payloads.
"""

from __future__ import annotations

from app.services.ingestion.semantic_outline import (
    derive_semantic_outline,
    outline_is_degenerate,
)
from app.services.seams import Message


class _OutlineLLM:
    """Fake LLM that streams predefined JSON payloads, one per window."""

    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads
        self.calls = 0

    @property
    def model(self) -> str:
        return "outline-fake"

    async def stream(self, messages: list[Message]) -> None:
        """Stream tokens from the current payload, split on spaces."""
        # Verify that messages are Message objects, not dicts.
        # This ensures the bug (dict-passing) is caught by tests.
        for msg in messages:
            _ = msg.role  # Access attribute to ensure it's a Message object
            _ = msg.content

        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        for tok in payload.split(" "):
            yield tok + " "


def test_outline_is_degenerate_empty() -> None:
    """Empty outlines are degenerate."""
    assert outline_is_degenerate([]) is True


def test_outline_is_degenerate_page_wrapper() -> None:
    """A page-level wrapper outline is degenerate."""
    outline = [
        {"heading": "document.pdf", "level": 1, "char_start": 0, "char_end": 100},
        {"heading": "Metadata", "level": 2, "char_start": 100, "char_end": 200},
        {"heading": "Contents", "level": 2, "char_start": 200, "char_end": 300},
        {"heading": "Page 1", "level": 3, "char_start": 300, "char_end": 400},
        {"heading": "Page 2", "level": 3, "char_start": 400, "char_end": 500},
    ]
    assert outline_is_degenerate(outline) is True


def test_outline_is_degenerate_real_heading_present() -> None:
    """An outline with a real heading is not degenerate."""
    outline = [
        {"heading": "document.pdf", "level": 1, "char_start": 0, "char_end": 100},
        {"heading": "4.1 Kossel-Lewis Approach", "level": 2, "char_start": 100, "char_end": 200},
    ]
    assert outline_is_degenerate(outline) is False


async def test_derive_semantic_outline_happy_path() -> None:
    """Derive headings from LLM output with exact text search."""
    text = "# Introduction\nWelcome here.\n# Methods\nWe did this.\n# Results\nWe found that."
    page_count = 1
    llm = _OutlineLLM(
        ['[{"heading": "# Introduction", "level": 1}, {"heading": "# Methods", "level": 1}]']
    )

    nodes = await derive_semantic_outline(text, page_count, llm)

    assert len(nodes) == 2
    assert nodes[0]["heading"] == "# Introduction"
    assert nodes[0]["level"] == 1
    assert nodes[0]["char_start"] == text.index("# Introduction")
    # First node ends where the second level-1 starts.
    assert nodes[0]["char_end"] == text.index("# Methods")

    assert nodes[1]["heading"] == "# Methods"
    assert nodes[1]["level"] == 1
    assert nodes[1]["char_start"] == text.index("# Methods")
    # Last node ends at len(text).
    assert nodes[1]["char_end"] == len(text)

    assert nodes[0]["page_start"] == 1
    assert nodes[0]["page_end"] == 1


async def test_derive_semantic_outline_repeated_heading() -> None:
    """Locate the same heading string multiple times — cursor must advance."""
    text = "Diet and lifestyle\nLions eat meat. Diet includes hunting.\nTigers also Diet."
    page_count = 1
    llm = _OutlineLLM(['[{"heading": "Diet", "level": 1}, {"heading": "Diet", "level": 1}]'])

    nodes = await derive_semantic_outline(text, page_count, llm)

    assert len(nodes) == 2
    # First "Diet" is at index 0.
    assert nodes[0]["char_start"] == 0
    # Second "Diet" is later in the text.
    assert nodes[1]["char_start"] > nodes[0]["char_start"]
    assert text[nodes[1]["char_start"] : nodes[1]["char_start"] + 4] == "Diet"


async def test_derive_semantic_outline_heading_not_in_text() -> None:
    """Headings not found in text are skipped with a log; others are still located."""
    text = "Chapter 1: Introduction\nChapter 2: Methods"
    page_count = 1
    # LLM returns two headings, only the first is in the text.
    payload = (
        '[{"heading": "Chapter 1: Introduction", "level": 1}, '
        '{"heading": "NonExistent", "level": 1}]'
    )
    llm = _OutlineLLM([payload])

    nodes = await derive_semantic_outline(text, page_count, llm)

    # Only one heading located.
    assert len(nodes) == 1
    assert nodes[0]["heading"] == "Chapter 1: Introduction"


async def test_derive_semantic_outline_garbage_json() -> None:
    """Malformed JSON from LLM returns empty list."""
    text = "Some text"
    page_count = 1
    llm = _OutlineLLM(["not valid json at all"])

    nodes = await derive_semantic_outline(text, page_count, llm)

    # Returns empty on parse failure.
    assert nodes == []


async def test_derive_semantic_outline_code_fenced() -> None:
    """Code-fenced JSON responses are parsed correctly."""
    text = "# Heading One\n# Heading Two"
    page_count = 1
    payload = (
        "```json\n"
        '[{"heading": "# Heading One", "level": 1}, '
        '{"heading": "# Heading Two", "level": 1}]\n```'
    )
    llm = _OutlineLLM([payload])

    nodes = await derive_semantic_outline(text, page_count, llm)

    assert len(nodes) == 2
    assert nodes[0]["heading"] == "# Heading One"
    assert nodes[1]["heading"] == "# Heading Two"


async def test_derive_semantic_outline_multi_window() -> None:
    """Text split into multiple windows; LLM called per window."""
    # Build text that's exactly 200 chars: 100-char window 0 + 100-char window 1
    # Window 0: heading + padding
    window0_heading = "Section One"
    window0_text = window0_heading + " " + "a" * (100 - len(window0_heading) - 1)
    # Window 1: heading + padding
    window1_heading = "Section Two"
    window1_text = window1_heading + " " + "b" * (100 - len(window1_heading) - 1)
    text = window0_text + window1_text
    assert len(text) == 200  # Exactly 200 chars for 2 windows at 100 chars each
    page_count = 1

    # Two payloads: one heading per window.
    llm = _OutlineLLM(
        [
            '[{"heading": "Section One", "level": 1}]',
            '[{"heading": "Section Two", "level": 1}]',
        ]
    )

    nodes = await derive_semantic_outline(text, page_count, llm, window_chars=100)

    # Both windows' headings were located.
    assert len(nodes) == 2
    assert llm.calls == 2
    assert nodes[0]["heading"] == "Section One"
    assert nodes[1]["heading"] == "Section Two"


async def test_derive_semantic_outline_nested_levels_char_end() -> None:
    """char_end is computed correctly per the level rule."""
    text = "# H1 Parent\n## H2 Child\n# Another H1"
    page_count = 1
    payload = (
        '[{"heading": "# H1 Parent", "level": 1}, '
        '{"heading": "## H2 Child", "level": 2}, '
        '{"heading": "# Another H1", "level": 1}]'
    )
    llm = _OutlineLLM([payload])

    nodes = await derive_semantic_outline(text, page_count, llm)

    assert len(nodes) == 3
    # H1 Parent (level 1) ends where the next level 1 starts (Another H1).
    assert nodes[0]["char_end"] == nodes[2]["char_start"]
    # H2 Child (level 2) ends where the next level <= 2 starts (Another H1).
    assert nodes[1]["char_end"] == nodes[2]["char_start"]
    # Another H1 (level 1) ends at len(text).
    assert nodes[2]["char_end"] == len(text)
