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

import pytest

from app.platform import config
from app.platform.seams import (
    EMBED_DIM,
    LLM,
    Embedder,
    FakeEmbedder,
    FakeLLM,
    FakeParser,
    Message,
    ParsedDoc,
    Parser,
    RealEmbedder,
    RealLLM,
    RealParser,
    SeamNotConfigured,
    _is_negligible_text,
    _parse_markdown_outline,
    get_embedder,
    get_llm,
    get_parser,
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


def test_real_llm_exposes_configured_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "LLM_MODEL", "some-model")
    assert RealLLM().model == "some-model"


# --- RealLLM transient-error classification --------------------------------------------


def test_real_llm_classifies_rate_limit_and_server_errors_as_transient() -> None:
    from app.platform.seams.real_llm import _classify_transient

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


def test_real_adapters_conform_to_protocols() -> None:
    assert isinstance(RealParser(), Parser)
    assert isinstance(RealEmbedder(), Embedder)
    assert isinstance(RealLLM(), LLM)


# --- Factory --------------------------------------------------------------------------


def test_factory_returns_fakes_by_default() -> None:
    assert isinstance(get_parser(), FakeParser)
    assert isinstance(get_embedder(), FakeEmbedder)
    assert isinstance(get_llm(), FakeLLM)


def test_factory_returns_real_adapters_independently_per_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each seam's mode is its own switch (F23) — flipping PARSER_MODE must not affect the
    # others, validating the whole point of the per-seam refinement.
    monkeypatch.setattr(config.settings, "PARSER_MODE", "real")
    assert isinstance(get_parser(), RealParser)
    assert isinstance(get_embedder(), FakeEmbedder)
    assert isinstance(get_llm(), FakeLLM)

    monkeypatch.setattr(config.settings, "PARSER_MODE", "fake")
    monkeypatch.setattr(config.settings, "EMBEDDER_MODE", "real")
    monkeypatch.setattr(config.settings, "LLM_MODE", "real")
    assert isinstance(get_parser(), FakeParser)
    assert isinstance(get_embedder(), RealEmbedder)
    assert isinstance(get_llm(), RealLLM)


def test_factory_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "EMBEDDER_MODE", "bogus")
    with pytest.raises(SeamNotConfigured):
        get_embedder()


# --- Real adapters fail loudly when unconfigured --------------------------------------


async def test_real_embedder_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "OPENAI_API_KEY", None)
    with pytest.raises(SeamNotConfigured):
        await RealEmbedder().embed(["x"])


async def test_real_parser_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "OPENROUTER_API_KEY", None)
    with pytest.raises(SeamNotConfigured):
        await RealParser().extract(b"x", "application/pdf")


async def test_real_parser_rejects_non_pdf_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "OPENROUTER_API_KEY", "test-key")
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with pytest.raises(ValueError, match="PDF only"):
        await RealParser().extract(b"x", docx_mime)


# --- RealParser pure-logic helpers (no network) ----------------------------------------


def test_negligible_text_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "PARSER_OCR_FALLBACK_MIN_CHARS_PER_PAGE", 20)
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
