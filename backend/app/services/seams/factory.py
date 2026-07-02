"""Factories — one independent switch per seam (decided F23)."""

from __future__ import annotations

from app.config.settings import settings
from app.services.seams.fakes import FakeEmbedder, FakeLLM, FakeParser
from app.services.seams.protocols import LLM, Embedder, Parser
from app.services.seams.real_llm import RealEmbedder, RealLLM
from app.services.seams.real_parser import RealParser
from app.services.seams.types import SeamNotConfigured

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
