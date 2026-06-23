"""Factories — one independent switch per seam (decided F23)."""

from __future__ import annotations

from app.platform.config import settings
from app.platform.seams.fakes import FakeEmbedder, FakeLLM, FakeParser
from app.platform.seams.protocols import LLM, Embedder, Parser
from app.platform.seams.real_llm import RealEmbedder, RealLLM
from app.platform.seams.real_parser import RealParser
from app.platform.seams.types import SeamNotConfigured

_VALID_MODES = ("fake", "real")


def _resolve_mode(mode: str, setting_name: str) -> str:
    if mode not in _VALID_MODES:
        raise SeamNotConfigured(f"unknown {setting_name}={mode!r}; expected one of {_VALID_MODES}.")
    return mode


def get_parser() -> Parser:
    mode = _resolve_mode(settings.PARSER_MODE, "PARSER_MODE")
    return FakeParser() if mode == "fake" else RealParser()


def get_embedder() -> Embedder:
    mode = _resolve_mode(settings.EMBEDDER_MODE, "EMBEDDER_MODE")
    return FakeEmbedder() if mode == "fake" else RealEmbedder()


def get_llm() -> LLM:
    mode = _resolve_mode(settings.LLM_MODE, "LLM_MODE")
    return FakeLLM() if mode == "fake" else RealLLM()
