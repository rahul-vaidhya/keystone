"""F03 — the 3 seams + fakes.

These are pure unit tests: no DB, no Docker, no API keys (the DoD is "the whole suite runs
with fakes, no API keys"). They assert the fakes are deterministic and conform to the seam
Protocols, the factory switches on SEAMS_MODE, and the real adapters fail loudly when their
vendor/credentials are absent instead of silently degrading.
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
    chunks = [c async for c in FakeLLM().stream([Message(role="user", content="What is Veratas?")])]
    text = "".join(chunks)
    assert text.strip()
    assert "[1]" in text  # cites the provided context
    assert "What is Veratas?" in text


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


def test_factory_returns_real_adapters_in_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "SEAMS_MODE", "real")
    assert isinstance(get_parser(), RealParser)
    assert isinstance(get_embedder(), RealEmbedder)
    assert isinstance(get_llm(), RealLLM)


def test_factory_rejects_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "SEAMS_MODE", "bogus")
    with pytest.raises(SeamNotConfigured):
        get_embedder()


# --- Real adapters fail loudly when unconfigured --------------------------------------


async def test_real_embedder_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.settings, "OPENAI_API_KEY", None)
    with pytest.raises(SeamNotConfigured):
        await RealEmbedder().embed(["x"])


async def test_real_parser_is_phase2_stub() -> None:
    with pytest.raises(SeamNotConfigured):
        await RealParser().extract(b"x", "application/pdf")
