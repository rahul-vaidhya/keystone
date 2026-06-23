"""Debugging utility (not part of the app/tests) for manually inspecting what the real
parser + structuring logic actually produce for a given PDF: the raw outline, the
recovered section tree, the generated chunks, and their char offsets.

Reuses the real production code paths directly (``RealParser.extract`` and
``app.ingestion.service._build_sections_and_chunks``) so the output is exactly what the
pipeline would build — nothing here is reimplemented or approximated. Writes nothing to
the database; everything stays in memory and is printed/dumped to a JSON file for manual
review.

Run from the ``backend/`` directory (so ``.env`` is found and ``OPENROUTER_API_KEY`` is
picked up):

    python scripts/inspect_document.py ../pdf/kech104.pdf
    python scripts/inspect_document.py ../pdf/kech104.pdf --json /tmp/out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import structlog

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.ingestion.service import _build_sections_and_chunks
from app.platform.seams import RealParser


def _gap_overlap_report(sections, chunks) -> list[str]:
    """Objective, mechanical checks only (no judgment calls): for every leaf section,
    do its chunks tile the section's char range with no gaps and no overlaps?"""
    issues: list[str] = []
    parent_ids = {s.parent_section_id for s in sections if s.parent_section_id}
    leaves = [s for s in sections if s.id not in parent_ids]
    for section in leaves:
        own = sorted(
            (c for c in chunks if c.section_id == section.id), key=lambda c: c.char_start
        )
        if not own:
            if section.char_end > section.char_start:
                issues.append(f"[{section.path}] leaf has no chunks but a non-empty range")
            continue
        if own[0].char_start != section.char_start:
            issues.append(
                f"[{section.path}] first chunk starts at {own[0].char_start}, "
                f"section starts at {section.char_start}"
            )
        for prev, nxt in zip(own, own[1:]):
            if nxt.char_start < prev.char_end:
                issues.append(
                    f"[{section.path}] overlap: chunk ends {prev.char_end}, "
                    f"next starts {nxt.char_start}"
                )
            elif nxt.char_start > prev.char_end:
                issues.append(
                    f"[{section.path}] gap: chunk ends {prev.char_end}, "
                    f"next starts {nxt.char_start}"
                )
        if own[-1].char_end != section.char_end:
            issues.append(
                f"[{section.path}] last chunk ends at {own[-1].char_end}, "
                f"section ends at {section.char_end}"
            )
    return issues


async def main(pdf_path: Path, json_out: Path | None) -> None:
    blob = pdf_path.read_bytes()

    with structlog.testing.capture_logs() as logs:
        parsed = await RealParser().extract(blob, "application/pdf")
    engine_log = next((e for e in logs if e.get("event") == "seams.real_parser_extracted"), None)

    outline_dicts = [
        {
            "heading": n.heading,
            "level": n.level,
            "char_start": n.char_start,
            "char_end": n.char_end,
            "page_start": n.page_start,
            "page_end": n.page_end,
        }
        for n in parsed.outline
    ]

    org_id, document_id = uuid.uuid4(), uuid.uuid4()
    sections, chunks = _build_sections_and_chunks(
        org_id, document_id, parsed.text, outline_dicts, parsed.page_count
    )

    print("\n--- document ---")
    print(f"pdf: {pdf_path}")
    print(f"engine used: {engine_log['engine'] if engine_log else 'unknown'}")
    print(f"language: {parsed.language}, pages: {parsed.page_count}, text_len: {len(parsed.text)}")

    print(f"\n--- raw outline ({len(parsed.outline)} headings) ---")
    for n in parsed.outline:
        print(
            f"  level={n.level} heading={n.heading!r} chars=({n.char_start},{n.char_end}) "
            f"pages=({n.page_start},{n.page_end})"
        )

    print(f"\n--- section tree ({len(sections)} sections) ---")
    for s in sorted(sections, key=lambda s: s.path):
        indent = "  " * s.depth
        print(
            f"{indent}[{s.path}] heading={s.heading!r} chars=({s.char_start},{s.char_end}) "
            f"pages=({s.page_start},{s.page_end})"
        )

    print(f"\n--- chunks ({len(chunks)}) ---")
    section_by_id = {s.id: s for s in sections}
    for c in sorted(chunks, key=lambda c: c.ordinal):
        section_path = section_by_id[c.section_id].path
        preview = c.content[:80].replace("\n", " ")
        print(
            f"  #{c.ordinal} section=[{section_path}] chars=({c.char_start},{c.char_end}) "
            f"tokens~{c.token_count} text={preview!r}..."
        )

    issues = _gap_overlap_report(sections, chunks)
    print(f"\n--- mechanical gap/overlap check: {len(issues)} issue(s) ---")
    for issue in issues:
        print(f"  {issue}")
    if not issues:
        print("  none — every leaf section's chunks tile its char range exactly")

    if json_out:
        json_out.write_text(
            json.dumps(
                {
                    "engine": engine_log["engine"] if engine_log else None,
                    "language": parsed.language,
                    "page_count": parsed.page_count,
                    "text_len": len(parsed.text),
                    "outline": outline_dicts,
                    "sections": [
                        {
                            "id": str(s.id),
                            "parent_section_id": str(s.parent_section_id)
                            if s.parent_section_id
                            else None,
                            "path": s.path,
                            "depth": s.depth,
                            "heading": s.heading,
                            "char_start": s.char_start,
                            "char_end": s.char_end,
                            "page_start": s.page_start,
                            "page_end": s.page_end,
                        }
                        for s in sections
                    ],
                    "chunks": [
                        {
                            "ordinal": c.ordinal,
                            "section_id": str(c.section_id),
                            "char_start": c.char_start,
                            "char_end": c.char_end,
                            "token_count": c.token_count,
                            "content": c.content,
                        }
                        for c in chunks
                    ],
                    "gap_overlap_issues": issues,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nFull JSON dump written to {json_out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf_path", type=Path)
    ap.add_argument("--json", type=Path, default=None, dest="json_out")
    args = ap.parse_args()
    asyncio.run(main(args.pdf_path, args.json_out))
