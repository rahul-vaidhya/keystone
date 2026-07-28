"""F03 seams + fakes, extended in F23 with the real OpenRouter parser adapter.

These are pure unit tests: no DB, no Docker, no API keys (the DoD is "the whole suite runs
with fakes, no API keys"). They assert the fakes are deterministic and conform to the seam
Protocols, the factory switches on PARSER_MODE/EMBEDDER_MODE/LLM_MODE independently (F23 —
was a single SEAMS_MODE), the real adapters fail loudly when their vendor/credentials are
absent instead of silently degrading, and (F23) the pure-logic pieces of `RealParser`
(negligible-text threshold, non-PDF rejection, markdown heading recovery) behave correctly
without any network call.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.models.ingestion import ChunkHit
from app.services.seams import (
    EMBED_DIM,
    LLM,
    Embedder,
    FakeEmbedder,
    FakeLLM,
    FakeParser,
    FakeReranker,
    Message,
    ParsedDoc,
    Parser,
    RealEmbedder,
    RealLLM,
    RealParser,
    RealReranker,
    Reranker,
    SeamNotConfigured,
    SeamTransientError,
    _is_negligible_text,
    _parse_markdown_outline,
    get_embedder,
    get_llm,
    get_parser,
    get_reranker,
)

# --- FakeEmbedder ---------------------------------------------------------------------


async def test_fake_embedder_is_deterministic_and_right_width() -> None:
    emb = FakeEmbedder()
    (a,) = await emb.embed(["hello"])
    (b,) = await emb.embed(["hello"])
    assert a == b
    assert len(a) == emb.dim == EMBED_DIM


async def test_fake_embedder_varies_by_text_and_is_unit_norm() -> None:
    emb = FakeEmbedder()
    a, b = await emb.embed(["alpha", "beta"])
    assert a != b
    assert sum(x * x for x in a) == pytest.approx(1.0, abs=1e-9)


def test_fake_embedder_exposes_model_name() -> None:
    assert FakeEmbedder().model == f"fake-embed-{EMBED_DIM}"


# --- FakeParser -----------------------------------------------------------------------


async def test_fake_parser_returns_parseddoc_with_valid_offsets() -> None:
    doc = await FakeParser().extract(b"ignored", "application/pdf")
    assert isinstance(doc, ParsedDoc)
    assert doc.language == "en"
    assert doc.page_count == 1
    assert doc.outline, "fake parser must return a non-empty outline"
    # Every outline node's offsets index into the returned text.
    for node in doc.outline:
        assert 0 <= node.char_start < node.char_end <= len(doc.text)
        assert doc.text[node.char_start : node.char_end]


# --- FakeLLM --------------------------------------------------------------------------


async def test_fake_llm_streams_grounded_cited_answer() -> None:
    """A prompt carrying a numbered context block (``[1]``) gets a citing answer — F40's
    grounding contract, exercised against the fake."""
    prompt = "[1] Veratas is a knowledge base.\n\nQuestion: What is Veratas?"
    chunks = [c async for c in FakeLLM().stream([Message(role="user", content=prompt)])]
    text = "".join(chunks)
    assert text.strip()
    assert "[1]" in text  # cites the provided context
    assert "What is Veratas?" in text


async def test_fake_llm_refuses_when_no_context_present() -> None:
    """No numbered context block in the prompt -> the fixed refusal string, deterministic
    — lets F40's tests assert refusal-SHAPED output, not just that plumbing ran."""
    prompt = "(no context was retrieved for this notebook)\n\nQuestion: What is Veratas?"
    chunks = [c async for c in FakeLLM().stream([Message(role="user", content=prompt)])]
    text = "".join(chunks).strip()
    assert text == FakeLLM.REFUSAL


def test_fake_llm_exposes_model_name() -> None:
    assert FakeLLM().model == "fake-llm"


# --- FakeReranker ----------------------------------------------------------------------


def _chunk_hit(distance: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        content="some chunk content",
        char_start=0,
        char_end=19,
        distance=distance,
    )


async def test_fake_reranker_preserves_order_and_truncates_to_top_k() -> None:
    """TRUE identity passthrough — candidate order (whatever it arrived in, e.g. cosine-
    distance order from search_chunks) is preserved verbatim, never re-sorted by score."""
    candidates = [_chunk_hit(0.9), _chunk_hit(0.1), _chunk_hit(0.5)]
    result = await FakeReranker().rerank("a query", candidates, top_k=2)
    assert len(result) == 2
    assert [hit.chunk_id for hit in result] == [candidates[0].chunk_id, candidates[1].chunk_id]


async def test_fake_reranker_stamps_rerank_score_as_one_minus_distance() -> None:
    candidates = [_chunk_hit(0.3)]
    (result,) = await FakeReranker().rerank("q", candidates, top_k=8)
    assert result.rerank_score == pytest.approx(0.7)
    assert result.distance == 0.3  # distance untouched


async def test_fake_reranker_top_k_larger_than_candidates_returns_all() -> None:
    candidates = [_chunk_hit(0.1), _chunk_hit(0.2)]
    result = await FakeReranker().rerank("q", candidates, top_k=8)
    assert len(result) == 2


async def test_fake_reranker_never_calls_network() -> None:
    """No candidates -> no vendor call, no error — same contract as a real 0-candidate
    scope."""
    assert await FakeReranker().rerank("q", [], top_k=8) == []


def test_real_llm_exposes_configured_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_MODEL", "some-model")
    assert RealLLM().model == "some-model"


# --- RealLLM transient-error classification --------------------------------------------


def test_real_llm_classifies_rate_limit_and_server_errors_as_transient() -> None:
    from app.services.seams.real_llm import _classify_transient

    class _FakeStatusError(Exception):
        def __init__(self, status_code: int) -> None:
            super().__init__("boom")
            self.status_code = status_code

    assert _classify_transient(_FakeStatusError(429)) is True
    assert _classify_transient(_FakeStatusError(503)) is True
    assert _classify_transient(_FakeStatusError(400)) is False
    assert _classify_transient(ValueError("not a seam error")) is False


# --- Protocol conformance -------------------------------------------------------------


def test_fakes_conform_to_protocols() -> None:
    assert isinstance(FakeParser(), Parser)
    assert isinstance(FakeEmbedder(), Embedder)
    assert isinstance(FakeLLM(), LLM)
    assert isinstance(FakeReranker(), Reranker)


def test_real_adapters_conform_to_protocols() -> None:
    assert isinstance(RealParser(), Parser)
    assert isinstance(RealEmbedder(), Embedder)
    assert isinstance(RealLLM(), LLM)
    assert isinstance(RealReranker(), Reranker)


# --- Factory --------------------------------------------------------------------------


def test_factory_returns_fakes_by_default() -> None:
    assert isinstance(get_parser(), FakeParser)
    assert isinstance(get_embedder(), FakeEmbedder)
    assert isinstance(get_llm(), FakeLLM)
    assert isinstance(get_reranker(), FakeReranker)


def test_factory_returns_real_adapters_independently_per_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each seam's mode is its own switch (F23, extended to the reranker seam) — flipping
    # PARSER_MODE must not affect the others, validating the whole point of the per-seam
    # refinement.
    monkeypatch.setattr(settings, "PARSER_MODE", "real")
    assert isinstance(get_parser(), RealParser)
    assert isinstance(get_embedder(), FakeEmbedder)
    assert isinstance(get_llm(), FakeLLM)
    assert isinstance(get_reranker(), FakeReranker)

    monkeypatch.setattr(settings, "PARSER_MODE", "fake")
    monkeypatch.setattr(settings, "EMBEDDER_MODE", "real")
    monkeypatch.setattr(settings, "LLM_MODE", "real")
    monkeypatch.setattr(settings, "RERANKER_MODE", "real")
    assert isinstance(get_parser(), FakeParser)
    assert isinstance(get_embedder(), RealEmbedder)
    assert isinstance(get_llm(), RealLLM)
    assert isinstance(get_reranker(), RealReranker)


def test_factory_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDER_MODE", "bogus")
    with pytest.raises(SeamNotConfigured):
        get_embedder()


def test_factory_rejects_unknown_reranker_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RERANKER_MODE", "bogus")
    with pytest.raises(SeamNotConfigured):
        get_reranker()


# --- Real adapters fail loudly when unconfigured --------------------------------------


async def test_real_embedder_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)
    with pytest.raises(SeamNotConfigured):
        await RealEmbedder().embed(["x"])


async def test_real_parser_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    with pytest.raises(SeamNotConfigured):
        await RealParser().extract(b"x", "application/pdf")


async def test_real_parser_rejects_non_pdf_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-key")
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(ValueError, match="PDF only"):
        await RealParser().extract(b"x", docx_mime)


# --- RealParser pure-logic helpers (no network) ----------------------------------------


def test_negligible_text_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE", 20)
    assert _is_negligible_text("", page_count=1) is True
    assert _is_negligible_text("x" * 5, page_count=1) is True
    assert _is_negligible_text("x" * 25, page_count=1) is False
    # Same short text spread over many pages becomes negligible per-page.
    assert _is_negligible_text("x" * 25, page_count=10) is True


def test_parse_markdown_outline_recovers_nested_headings() -> None:
    text = "# Title\nintro text\n## A\nbody a\n## B\nbody b\n### B.1\nbody b1\n"
    outline = _parse_markdown_outline(text, page_count=1)
    headings = [(n.heading, n.level) for n in outline]
    assert headings == [("Title", 1), ("A", 2), ("B", 2), ("B.1", 3)]
    # offsets index into the same text, and every node's range is non-empty.
    for node in outline:
        assert 0 <= node.char_start < node.char_end <= len(text)
    # "B"'s range extends to cover its child "B.1" (next-same-or-shallower-level rule).
    title, a, b, b1 = outline
    assert a.char_end == b.char_start
    assert b1.char_start >= b.char_start
    assert b1.char_end <= b.char_end


def test_parse_markdown_outline_is_empty_when_no_headings() -> None:
    # A valid F23 finding, not a bug: flat output passes through as an empty outline,
    # exercising F21's degenerate-outline contract rather than fabricating structure.
    assert _parse_markdown_outline("just plain prose, no markdown headings here.", 1) == []


# --- RealReranker -----------------------------------------------------------------------


async def test_real_reranker_raises_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RERANKER_URL", None)
    with pytest.raises(SeamNotConfigured):
        await RealReranker().rerank("q", [_chunk_hit(0.5)], top_k=8)


async def test_real_reranker_no_candidates_returns_empty_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero candidates short-circuits before RERANKER_URL is even checked — no vendor
    call is possible or needed."""
    monkeypatch.setattr(settings, "RERANKER_URL", None)
    assert await RealReranker().rerank("q", [], top_k=8) == []


def test_real_reranker_classifies_timeout_connect_and_status_errors_as_transient() -> None:
    from app.services.seams.real_reranker import _classify_transient

    httpx = pytest.importorskip("httpx")

    class _FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    def _status_error(status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://example.test/rerank")
        return httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(status_code, request=request)
        )

    assert _classify_transient(httpx.TimeoutException("timed out")) is True
    assert _classify_transient(httpx.ConnectError("connection refused")) is True
    assert _classify_transient(_status_error(429)) is True
    assert _classify_transient(_status_error(503)) is True
    assert _classify_transient(_status_error(400)) is False
    assert _classify_transient(ValueError("not a seam error")) is False


async def test_real_reranker_wraps_transient_httpx_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient httpx failure (mocked — no real network call) is re-raised as
    SeamTransientError, mirroring RealLLM's classification contract."""
    httpx = pytest.importorskip("httpx")
    monkeypatch.setattr(settings, "RERANKER_URL", "http://localhost:8081")

    async def _boom(query: str, texts: list[str]) -> list[dict]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.seams.real_reranker._call_tei_rerank", _boom)

    with pytest.raises(SeamTransientError):
        await RealReranker().rerank("q", [_chunk_hit(0.5)], top_k=8)


async def test_real_reranker_propagates_non_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-transient failure (e.g. a bug, a bad request) is NOT wrapped — it propagates
    immediately rather than being silently retried."""
    monkeypatch.setattr(settings, "RERANKER_URL", "http://localhost:8081")

    async def _boom(query: str, texts: list[str]) -> list[dict]:
        raise ValueError("not a seam error")

    monkeypatch.setattr("app.services.seams.real_reranker._call_tei_rerank", _boom)

    with pytest.raises(ValueError, match="not a seam error"):
        await RealReranker().rerank("q", [_chunk_hit(0.5)], top_k=8)
