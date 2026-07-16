"""V2 hierarchical retrieval - real-seam output-quality eval harness (opt-in, NOT part of
CI). Measures what hierarchical (coarse-to-fine) retrieval actually RETURNS versus flat,
using REAL parser/embedder/LLM seams (fakes are semantically meaningless per the
documented F40 gotcha - hash-based FakeEmbedder distances are ~0.93 regardless of
relevance). Ingests `pdf/kech104.pdf` ONCE (all 3 V2 flags on), then reuses that corpus
for every golden question, every flat-vs-hierarchical comparison, and the
top_sections=4-vs-8 sensitivity check. Prints a side-by-side report table (run with
`pytest -s`).

Run manually (same one-key-feeds-all-3-seams pattern as every other live-validation
session - export OPENAI_API_KEY/OPENAI_BASE_URL as PROCESS ENV ONLY, same value as the
working OPENROUTER_API_KEY already in backend/.env, never written to .env):

    $env:OPENAI_API_KEY = $env:OPENROUTER_API_KEY
    $env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
    pytest -m hierarchical_eval -s tests/test_hierarchical_eval.py

Expensive and slow (real LLM calls): semantic outline (~2-3 calls), enrichment (~30+
calls, historically ~120s for ~33 sections), plus one real LLM call per golden question
via /chat/ask. Never re-ingest per question - ingest once, reuse the corpus for
everything below.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.settings import settings
from app.models.ingestion import Chunk, Embedding, Section
from app.services.queue import get_job_queue
from app.services.seams import (
    RealEmbedder,
    RealLLM,
    RealParser,
    get_embedder,
    get_llm,
    get_parser,
)
from app.services.storage import get_object_store
from main import app
from tests.conftest import FakeJobQueue

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
PDF_PATH = Path(__file__).resolve().parents[2] / "pdf" / "kech104.pdf"

_REFUSAL = "I don't have that in the provided sources."

pytestmark = [
    pytest.mark.hierarchical_eval,
    pytest.mark.skipif(
        not (OPENROUTER_API_KEY and OPENAI_API_KEY and OPENAI_BASE_URL and PDF_PATH.exists()),
        reason=(
            "set OPENROUTER_API_KEY, OPENAI_API_KEY, OPENAI_BASE_URL and ensure "
            "pdf/kech104.pdf exists to run this opt-in V2 hierarchical-retrieval eval"
        ),
    ),
]

GOLDEN_QUESTIONS = [
    {
        "question": "What is the octet rule in chemical bonding?",
        "expected_keywords": ["eight", "electrons", "octet"],
        "expected_section_hint": "octet",
    },
    {
        "question": "What are the exceptions to the octet rule? Give an example.",
        "expected_keywords": ["exception", "incomplete", "expanded"],
        "expected_section_hint": "octet",
    },
    {
        "question": "What is lattice enthalpy?",
        "expected_keywords": ["lattice", "enthalpy"],
        "expected_section_hint": "lattice",
    },
    {
        "question": "According to VSEPR theory, what determines the shape of a molecule?",
        "expected_keywords": ["repulsion", "electron", "pair"],
        "expected_section_hint": "vsepr",
    },
    {
        "question": "What is sp3 hybridisation? Give an example.",
        "expected_keywords": ["sp3", "tetrahedral", "hybridisation"],
        "expected_section_hint": "hybridisation",
    },
    {
        "question": (
            "What is the bond order of the N2 molecule according to molecular orbital theory?"
        ),
        "expected_keywords": ["bond order", "three", "3"],
        "expected_section_hint": "molecular orbital",
    },
    {
        "question": "What is hydrogen bonding?",
        "expected_keywords": ["hydrogen bond", "electronegative"],
        "expected_section_hint": "hydrogen",
    },
    {
        "question": "What is formal charge and how is it calculated?",
        "expected_keywords": ["formal charge", "lone pair"],
        "expected_section_hint": "formal charge",
    },
]

BAIT_QUESTIONS = [
    "Who won the 2022 FIFA World Cup?",
    "What is the capital city of France?",
]


class _InMemoryObjectStore:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = data

    async def get(self, key: str) -> bytes:
        return self.puts[key]


@pytest.fixture
async def client(session_factory, tenant_engine) -> AsyncClient:
    store = _InMemoryObjectStore()
    app.dependency_overrides[get_object_store] = lambda: store
    app.dependency_overrides[get_parser] = lambda: RealParser()
    app.dependency_overrides[get_embedder] = lambda: RealEmbedder()
    app.dependency_overrides[get_llm] = lambda: RealLLM()
    app.dependency_overrides[get_job_queue] = lambda: FakeJobQueue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_object_store, None)
    app.dependency_overrides.pop(get_parser, None)
    app.dependency_overrides.pop(get_embedder, None)
    app.dependency_overrides.pop(get_llm, None)
    app.dependency_overrides.pop(get_job_queue, None)


async def _lookup_section(session_factory, chunk_id: uuid.UUID) -> tuple[str | None, str | None]:
    """Returns (heading, path) for the section that owns this chunk, or (None, None) if
    the chunk has no section."""
    async with session_factory() as session:
        chunk = (
            await session.execute(select(Chunk).where(Chunk.id == chunk_id))
        ).scalar_one_or_none()
        if chunk is None or chunk.section_id is None:
            return None, None
        section = (
            await session.execute(select(Section).where(Section.id == chunk.section_id))
        ).scalar_one_or_none()
        if section is None:
            return None, None
        return section.heading, section.path


def _section_matches_hint(heading: str | None, path: str | None, hint: str) -> bool:
    haystack = f"{heading or ''} {path or ''}".lower()
    return hint.lower() in haystack


async def test_hierarchical_eval_end_to_end(client: AsyncClient, session_factory) -> None:
    # ---- Setup: enable all 3 V2 flags for this test's duration ----
    orig_semantic = settings.SEMANTIC_OUTLINE_ENABLED
    orig_enrichment = settings.ENRICHMENT_ENABLED
    orig_hierarchical = settings.HIERARCHICAL_RETRIEVAL_ENABLED
    orig_top_sections = settings.HIERARCHICAL_TOP_SECTIONS
    settings.SEMANTIC_OUTLINE_ENABLED = True
    settings.ENRICHMENT_ENABLED = True
    settings.HIERARCHICAL_RETRIEVAL_ENABLED = True
    settings.HIERARCHICAL_TOP_SECTIONS = 8

    try:
        # ---- Signup + upload + ingest to READY, ONCE ----
        resp = await client.post(
            "/auth/signup",
            json={
                "email": "hiereval-eval@test.com",
                "password": "password123",
                "org_name": "HierEval",
            },
        )
        assert resp.status_code == 201, resp.text
        tokens = resp.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        pdf_bytes = PDF_PATH.read_bytes()
        resp = await client.post(
            "/documents/upload",
            headers=headers,
            files={"file": (PDF_PATH.name, pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 201, resp.text
        doc = resp.json()
        doc_id = doc["id"]

        resp = await client.post(f"/ingestion/documents/{doc_id}/parse", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "STRUCTURING", resp.text

        resp = await client.post(f"/ingestion/documents/{doc_id}/structure", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "EMBEDDING", resp.text

        resp = await client.post(f"/ingestion/documents/{doc_id}/embed", headers=headers)
        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["status"] == "READY", resp.text

        resp = await client.post(f"/ingestion/documents/{doc_id}/enrich", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "READY", resp.text

        # Confirm section embeddings actually exist (enrichment produced them).
        async with session_factory() as session:
            section_embeddings = (
                (
                    await session.execute(
                        select(Embedding).where(
                            Embedding.document_id == uuid.UUID(doc_id),
                            Embedding.owner_type == "section",
                        )
                    )
                )
                .scalars()
                .all()
            )
        section_embedding_count = len(section_embeddings)
        print(f"\n[setup] section embeddings created: {section_embedding_count}")
        assert section_embedding_count > 0, "enrichment produced zero section embeddings"

        # ---- Create a notebook, attach the document ----
        resp = await client.post(
            "/notebooks", headers=headers, json={"name": "Hierarchical Eval Notebook"}
        )
        assert resp.status_code == 201, resp.text
        notebook_id = resp.json()["id"]
        resp = await client.post(f"/notebooks/{notebook_id}/documents/{doc_id}", headers=headers)
        assert resp.status_code == 204, resp.text

        # ---- Retrieval comparison: flat vs hierarchical(8) vs hierarchical(4) ----
        # k=4 (not the default 8) is deliberate: RetrievalService._retrieve_hits computes
        # the coarse-pass section count as max(k, HIERARCHICAL_TOP_SECTIONS), so with
        # k=8 a HIERARCHICAL_TOP_SECTIONS=4 setting would be silently clamped back up to
        # 8 and the sensitivity check would be a no-op. k=4 lets top_sections=4 actually
        # bind (max(4,4)=4) while top_sections=8 still dominates (max(4,8)=8).
        RETRIEVAL_K = 4
        rows = []
        for q in GOLDEN_QUESTIONS:
            question = q["question"]
            hint = q["expected_section_hint"]
            row: dict = {"question": question}

            # Flat.
            settings.HIERARCHICAL_RETRIEVAL_ENABLED = False
            resp = await client.post(
                "/retrieval/search",
                headers=headers,
                json={"notebook_id": notebook_id, "query": question, "k": RETRIEVAL_K},
            )
            assert resp.status_code == 200, resp.text
            flat_results = resp.json()["results"]
            flat_heading = flat_path = None
            if flat_results:
                flat_heading, flat_path = await _lookup_section(
                    session_factory, uuid.UUID(flat_results[0]["chunk_id"])
                )
            flat_match = _section_matches_hint(flat_heading, flat_path, hint)
            row["flat_top_section"] = flat_heading or flat_path or "(no hit)"
            row["flat_match"] = flat_match

            # Hierarchical, top_sections=8.
            settings.HIERARCHICAL_RETRIEVAL_ENABLED = True
            settings.HIERARCHICAL_TOP_SECTIONS = 8
            with structlog.testing.capture_logs() as logs:
                resp = await client.post(
                    "/retrieval/search",
                    headers=headers,
                    json={"notebook_id": notebook_id, "query": question, "k": RETRIEVAL_K},
                )
            assert resp.status_code == 200, resp.text
            hier8_results = resp.json()["results"]
            hier8_events = {e.get("event") for e in logs}
            if "retrieval.hierarchical_used" in hier8_events:
                hier8_path_used = "used"
            elif "retrieval.hierarchical_fallback_no_sections" in hier8_events:
                hier8_path_used = "fallback_no_sections"
            elif "retrieval.hierarchical_fallback_no_chunks" in hier8_events:
                hier8_path_used = "fallback_no_chunks"
            else:
                hier8_path_used = "unknown"
            hier8_heading = hier8_path = None
            if hier8_results:
                hier8_heading, hier8_path = await _lookup_section(
                    session_factory, uuid.UUID(hier8_results[0]["chunk_id"])
                )
            hier8_match = _section_matches_hint(hier8_heading, hier8_path, hint)
            row["hier8_top_section"] = hier8_heading or hier8_path or "(no hit)"
            row["hier8_match"] = hier8_match
            row["hier8_path"] = hier8_path_used

            # Hierarchical, top_sections=4 (sensitivity check).
            settings.HIERARCHICAL_TOP_SECTIONS = 4
            resp = await client.post(
                "/retrieval/search",
                headers=headers,
                json={"notebook_id": notebook_id, "query": question, "k": RETRIEVAL_K},
            )
            assert resp.status_code == 200, resp.text
            hier4_results = resp.json()["results"]
            hier4_heading = hier4_path = None
            if hier4_results:
                hier4_heading, hier4_path = await _lookup_section(
                    session_factory, uuid.UUID(hier4_results[0]["chunk_id"])
                )
            hier4_match = _section_matches_hint(hier4_heading, hier4_path, hint)
            row["hier4_top_section"] = hier4_heading or hier4_path or "(no hit)"
            row["hier4_match"] = hier4_match

            if hier8_match and not flat_match:
                winner = "hierarchical"
            elif flat_match and not hier8_match:
                winner = "flat"
            else:
                winner = "tie"
            row["winner"] = winner

            # ---- Evidence + provenance grading via real /chat/ask (hierarchical ON, top=8) ----
            settings.HIERARCHICAL_TOP_SECTIONS = 8
            resp = await client.post(
                "/chat/ask",
                headers=headers,
                json={"notebook_id": notebook_id, "query": question, "k": 8},
            )
            assert resp.status_code == 200, resp.text
            chat_json = resp.json()
            answer = chat_json["answer"]
            citations = chat_json["citations"]

            keyword_hits = sum(1 for kw in q["expected_keywords"] if kw.lower() in answer.lower())
            # "at least half" of expected_keywords, rounded up for odd counts.
            evidence_pass = keyword_hits >= (len(q["expected_keywords"]) + 1) // 2
            row["evidence_pass"] = evidence_pass

            if citations:
                provenance_pass = True
                for c in citations:
                    heading, path = await _lookup_section(session_factory, uuid.UUID(c["chunk_id"]))
                    if not _section_matches_hint(heading, path, hint):
                        provenance_pass = False
                        break
            else:
                provenance_pass = False
            row["provenance_pass"] = provenance_pass
            row["citation_count"] = len(citations)
            row["answer_preview"] = answer[:160].replace("\n", " ")

            rows.append(row)

        # ---- Bait questions: exact refusal + 0 citations, both flat and hierarchical ----
        bait_rows = []
        for bait in BAIT_QUESTIONS:
            bait_row: dict = {"question": bait}
            for mode, flag in [("flat", False), ("hierarchical", True)]:
                settings.HIERARCHICAL_RETRIEVAL_ENABLED = flag
                settings.HIERARCHICAL_TOP_SECTIONS = 8
                resp = await client.post(
                    "/chat/ask",
                    headers=headers,
                    json={"notebook_id": notebook_id, "query": bait, "k": 8},
                )
                assert resp.status_code == 200, resp.text
                body = resp.json()
                bait_row[f"{mode}_refusal_exact"] = body["answer"] == _REFUSAL
                bait_row[f"{mode}_citations"] = len(body["citations"])
            bait_rows.append(bait_row)

        # ---- Report ----
        lines = []
        lines.append("=" * 100)
        lines.append("V2 HIERARCHICAL RETRIEVAL - REAL-SEAM EVAL REPORT")
        lines.append(f"Corpus: {PDF_PATH.name} | doc_id={doc_id}")
        lines.append(f"Sections enriched (embeddings): {section_embedding_count}")
        lines.append("=" * 100)
        header = (
            f"{'Question':<58} | {'Flat top-section':<22} | {'Hier(8) top-section':<22} | "
            f"{'Hier(4) top-section':<22} | Winner | Evid | Prov"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for r in rows:
            lines.append(
                f"{r['question'][:56]:<58} | {str(r['flat_top_section'])[:20]:<22} | "
                f"{str(r['hier8_top_section'])[:20]:<22} | "
                f"{str(r['hier4_top_section'])[:20]:<22} | "
                f"{r['winner']:<6} | {'PASS' if r['evidence_pass'] else 'FAIL':<4} | "
                f"{'PASS' if r['provenance_pass'] else 'FAIL'}"
            )
        lines.append("")
        lines.append("Per-question detail:")
        for r in rows:
            lines.append(f"- Q: {r['question']}")
            lines.append(
                f"    flat_match={r['flat_match']} hier8_match={r['hier8_match']} "
                f"hier4_match={r['hier4_match']} hier8_path={r['hier8_path']} "
                f"citations={r['citation_count']}"
            )
            lines.append(f"    answer_preview: {r['answer_preview']}")
        lines.append("")
        lines.append("Bait questions (out-of-scope, expect exact refusal + 0 citations):")
        for br in bait_rows:
            lines.append(
                f"- Q: {br['question']} | flat: refusal_exact={br['flat_refusal_exact']} "
                f"citations={br['flat_citations']} | hierarchical: "
                f"refusal_exact={br['hierarchical_refusal_exact']} "
                f"citations={br['hierarchical_citations']}"
            )

        winner_counts = {"flat": 0, "hierarchical": 0, "tie": 0}
        for r in rows:
            winner_counts[r["winner"]] += 1
        evidence_pass_count = sum(1 for r in rows if r["evidence_pass"])
        provenance_pass_count = sum(1 for r in rows if r["provenance_pass"])
        hier4_vs_hier8_agree = sum(1 for r in rows if r["hier4_match"] == r["hier8_match"])

        lines.append("")
        lines.append("=" * 100)
        lines.append("SUMMARY")
        lines.append(
            f"Winner tally (section-match on top hit): flat={winner_counts['flat']} "
            f"hierarchical={winner_counts['hierarchical']} tie={winner_counts['tie']}"
        )
        lines.append(
            f"Evidence PASS (>=half expected keywords in answer): {evidence_pass_count}/{len(rows)}"
        )
        lines.append(
            f"Provenance PASS (every citation's section matches hint): "
            f"{provenance_pass_count}/{len(rows)}"
        )
        lines.append(
            f"HIERARCHICAL_TOP_SECTIONS=4 vs =8 agreement on top-hit section match: "
            f"{hier4_vs_hier8_agree}/{len(rows)}"
        )
        bait_all_refused = all(
            br["flat_refusal_exact"]
            and br["flat_citations"] == 0
            and br["hierarchical_refusal_exact"]
            and br["hierarchical_citations"] == 0
            for br in bait_rows
        )
        lines.append(f"Bait questions correctly refused in BOTH modes: {bait_all_refused}")
        lines.append("=" * 100)

        report_text = "\n".join(lines)
        print("\n" + report_text)

        # ---- Hard structural assertions only. The golden-question keyword/section
        # grading above is informational/empirical (printed for a human to read), not a
        # pytest pass/fail gate - real LLM output varies run to run, and this is a
        # measurement harness, not a CI correctness gate. Only the most robust,
        # deterministic-by-design invariant (exact refusal string, zero citations) is
        # hard-asserted. ----
        assert bait_all_refused, (
            "bait questions must be refused verbatim with 0 citations in both modes"
        )

    finally:
        settings.SEMANTIC_OUTLINE_ENABLED = orig_semantic
        settings.ENRICHMENT_ENABLED = orig_enrichment
        settings.HIERARCHICAL_RETRIEVAL_ENABLED = orig_hierarchical
        settings.HIERARCHICAL_TOP_SECTIONS = orig_top_sections
