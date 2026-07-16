"""Offline unit tests for semantic outline derivation.

Tests the heading detection logic, text search, and outline construction without
a database or HTTP client. Uses a fake LLM that streams predefined payloads.
"""

from __future__ import annotations

import json

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
    """Text split into multiple overlapping windows; LLM called per window."""
    # Build text with window_chars=100 and overlap=min(500, 100//4)=25, step=75.
    # Windows: [0,100), [75,175), [150,250) — exactly three windows when text is 250 chars.
    # Window 0: heading + padding to 100 chars
    window0_heading = "Section One"
    window0_text = window0_heading + " " + "a" * (100 - len(window0_heading) - 1)
    # Window 1: heading + padding to 100 chars
    window1_heading = "Section Two"
    window1_text = window1_heading + " " + "b" * (100 - len(window1_heading) - 1)
    # Window 2: trailing content to 50 chars (100 + 100 + 50 = 250)
    window2_text = "c" * 50
    text = window0_text + window1_text + window2_text
    assert len(text) == 250
    page_count = 1

    # Three payloads: one per window.
    llm = _OutlineLLM(
        [
            '[{"heading": "Section One", "level": 1}]',
            '[{"heading": "Section Two", "level": 1}]',
            "[]",
        ]
    )

    nodes = await derive_semantic_outline(text, page_count, llm, window_chars=100)

    # Both headings were located.
    assert len(nodes) == 2
    assert llm.calls == 3
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


async def test_generic_headings_filtered_from_derived_outline() -> None:
    """Generic headings (page markers, wrappers, filenames) are filtered before offset location."""
    text = "Contents\nPage 12\n4.1 Chemical Bonding\nreal body text follows here."
    page_count = 1
    # LLM proposes all three headings.
    payload = (
        '[{"heading": "Contents", "level": 1}, '
        '{"heading": "Page 12", "level": 1}, '
        '{"heading": "4.1 Chemical Bonding", "level": 1}]'
    )
    llm = _OutlineLLM([payload])

    nodes = await derive_semantic_outline(text, page_count, llm)

    # Only the real heading survives.
    assert len(nodes) == 1
    assert nodes[0]["heading"] == "4.1 Chemical Bonding"
    assert nodes[0]["char_start"] == text.index("4.1 Chemical Bonding")
    # Last node ends at len(text).
    assert nodes[0]["char_end"] == len(text)


class _SegmentAwareLLM:
    """Returns a heading ONLY if it appears in full inside the window segment it was
    given — models a real LLM, which cannot propose text it never saw whole."""

    def __init__(self, heading: str) -> None:
        self._heading = heading
        self.calls = 0

    @property
    def model(self) -> str:
        return "segment-aware-fake"

    async def stream(self, messages):
        for msg in messages:
            _ = msg.role
            _ = msg.content
        self.calls += 1
        segment = messages[-1].content
        if self._heading in segment:
            yield json.dumps([{"heading": self._heading, "level": 1}])
        else:
            yield "[]"


async def test_boundary_straddling_heading_recovered_via_overlap() -> None:
    """A heading straddling a window boundary is recovered via overlap."""
    # Text of length 175 with a heading at indices 90..114 (24 chars).
    heading_text = "Boundary Section Heading"
    text = "a" * 90 + heading_text + "b" * (175 - 90 - len(heading_text))
    assert len(text) == 175
    # With window_chars=100 and overlap=25, step=75.
    # Windows: [0,100), [75,175) — the heading straddles index 100.
    # Window 0 sees [0,100), which includes indices 0-99, missing the tail of the heading.
    # Window 1 sees [75,175), which fully contains indices 90-114.
    page_count = 1
    llm = _SegmentAwareLLM(heading_text)

    nodes = await derive_semantic_outline(text, page_count, llm, window_chars=100)

    # The heading is fully inside window 1, so it's recovered.
    assert len(nodes) == 1
    assert nodes[0]["heading"] == heading_text
    assert nodes[0]["char_start"] == 90


async def test_adjacent_window_duplicate_proposal_deduped() -> None:
    """Adjacent windows re-proposing the same heading in their overlap zone are deduped."""
    # Heading at indices 78..98 (20 chars), fully inside both [0,100) and [75,175).
    heading_text = "Overlap Zone Heading"
    text = "x" * 78 + heading_text + "y" * (175 - 78 - len(heading_text))
    assert len(text) == 175
    page_count = 1
    llm = _SegmentAwareLLM(heading_text)

    nodes = await derive_semantic_outline(text, page_count, llm, window_chars=100)

    # The heading is in the overlap zone and proposed by both adjacent windows.
    # Dedup logic keeps only one.
    assert len(nodes) == 1
    assert nodes[0]["heading"] == heading_text
    assert nodes[0]["char_start"] == 78


async def test_far_apart_repeated_heading_not_deduped() -> None:
    """The same heading string repeated far apart (not adjacent windows) produces two nodes."""
    # Use _OutlineLLM to propose "Diet" in window 0 and window 2 (non-adjacent).
    # Text of length 250 with window_chars=100 (overlap 25, step 75).
    # Windows: [0,100), [75,175), [150,250) — three windows.
    heading_text = "Diet"
    # Place first "Diet" at index 0 (window 0 only, [0,100)).
    window0_part = heading_text + "x" * (100 - len(heading_text))
    # Place "Habitat" at index 110 (window 1 only, inside [75,175), not in overlap zones).
    window1_part = "x" * 10 + "Habitat" + "x" * (100 - 10 - len("Habitat"))
    # Place second "Diet" at index 200 (window 2 only, [150,250)).
    window2_part = heading_text + "x" * (50 - len(heading_text))
    text = window0_part + window1_part + window2_part
    assert len(text) == 250
    assert text[0:4] == "Diet"
    assert text[200:204] == "Diet"
    page_count = 1

    llm = _OutlineLLM(
        [
            '[{"heading": "Diet", "level": 1}]',
            '[{"heading": "Habitat", "level": 1}]',
            '[{"heading": "Diet", "level": 1}]',
        ]
    )

    nodes = await derive_semantic_outline(text, page_count, llm, window_chars=100)

    # Both occurrences of "Diet" recovered as separate nodes (windows 0 and 2 are non-adjacent).
    diet_nodes = [n for n in nodes if n["heading"] == "Diet"]
    assert len(diet_nodes) == 2
    assert diet_nodes[0]["char_start"] == 0
    assert diet_nodes[1]["char_start"] == 200
