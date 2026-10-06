"""Per-sentence citation checker (``CITATION_CHECK_ENABLED``): makes "every generated
claim can be traced to a ranked source" measurable per sentence.

For each answer sentence:

* ``citations`` — the ``[n]`` markers in that sentence, resolved against the SAME
  numbered ``ContextBlock`` list the answer's prompt was built from.
* ``lexical`` — max over cited chunks of ``tfidf_cosine`` (ltc vectors with the corpus
  idf of the from-scratch sparse index over the notebook's allowed documents).
* ``semantic`` — max over cited chunks of the cosine between the sentence embedding and
  the chunk-content embedding — ONE batched ``Embedder.embed`` seam call for every
  sentence + every cited chunk text.
* ``score`` — mean of whichever of the two are available; ``status`` = ``supported`` /
  ``weak`` / ``uncited``.

Never fails a chat turn: a sparse-index build failure ⇒ ``lexical=None``, an embedder
failure ⇒ ``semantic=None``. The splitting/scoring helpers are pure functions.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

from app.config.logging import get_logger
from app.config.settings import settings
from app.models.chat import ClaimCheck
from app.models.retrieval import ContextBlock
from app.services.retrieval.sparse import InvertedIndex, tfidf_cosine
from app.services.seams import Embedder

logger = get_logger(__name__)

_MARKER_RE = re.compile(r"\[(\d+)\]")
# A sentence ends at terminal punctuation, optionally followed by trailing citation
# markers ("... octet rule. [1][2]"), followed by whitespace or end of text.
_BOUNDARY_RE = re.compile(r"[.!?]+(?:\s*\[\d+\])*(?=\s|$)")
_MIN_WORDS = 3
_MIN_CHARS = 12

# Fixed non-claim answers that must never be checked (kept in sync with chat.service).
_SKIP_SENTENCES = {
    "I don't have that in the provided sources.",
    "The available sources don't contain a strong match for this question.",
}


def split_sentences(answer: str) -> list[str]:
    """Pure: split ``answer`` into sentences, keeping each sentence's ``[n]`` markers
    (including markers placed after the full stop). Newlines (bullets/paragraphs) are
    hard boundaries."""
    sentences: list[str] = []
    for line in answer.splitlines():
        line = line.strip()
        if not line:
            continue
        start = 0
        for match in _BOUNDARY_RE.finditer(line):
            piece = line[start : match.end()].strip()
            if piece:
                sentences.append(piece)
            start = match.end()
        tail = line[start:].strip()
        if tail:
            # A tail made only of markers belongs to the previous sentence.
            if sentences and not _MARKER_RE.sub("", tail).strip():
                sentences[-1] = f"{sentences[-1]} {tail}"
            else:
                sentences.append(tail)
    return sentences


def sentence_markers(sentence: str) -> list[int]:
    """Pure: ``[n]`` markers in order of first appearance, deduplicated."""
    seen: list[int] = []
    for m in _MARKER_RE.finditer(sentence):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def strip_markers(sentence: str) -> str:
    return re.sub(r"\s+", " ", _MARKER_RE.sub("", sentence)).strip()


def is_checkable(sentence: str) -> bool:
    """Pure: skip very short fragments (list labels, "Yes.") and the fixed refusal /
    weak-evidence messages."""
    text = strip_markers(sentence)
    if text in _SKIP_SENTENCES:
        return False
    return len(text) >= _MIN_CHARS and len(text.split()) >= _MIN_WORDS


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def combine(lexical: float | None, semantic: float | None) -> float | None:
    available = [s for s in (lexical, semantic) if s is not None]
    return sum(available) / len(available) if available else None


def score_claims(
    sentences: list[str],
    blocks: list[ContextBlock],
    *,
    lexical_fn: Callable[[str, str], float] | None,
    vectors: dict[str, list[float]] | None,
    threshold: float,
) -> list[ClaimCheck]:
    """Pure scoring core. ``lexical_fn(sentence_text, chunk_text)`` (``None`` ⇒ lexical
    unavailable); ``vectors`` maps text → embedding for every sentence text + cited chunk
    text (``None`` ⇒ semantic unavailable). Only checkable sentences are returned."""
    checks: list[ClaimCheck] = []
    for sentence in sentences:
        if not is_checkable(sentence):
            continue
        text = strip_markers(sentence)
        markers = sentence_markers(sentence)
        if not markers:
            checks.append(ClaimCheck(sentence=sentence, citations=[], status="uncited"))
            continue
        cited = [blocks[m - 1] for m in markers if 1 <= m <= len(blocks)]
        lexical: float | None = None
        semantic: float | None = None
        if cited and lexical_fn is not None:
            lexical = max(lexical_fn(text, b.content) for b in cited)
        if cited and vectors is not None:
            sv = vectors.get(text)
            scores = [cosine(sv, vectors[b.content]) for b in cited if b.content in vectors]
            if sv is not None and scores:
                semantic = max(scores)
        score = combine(lexical, semantic)
        status = "supported" if score is not None and score >= threshold else "weak"
        checks.append(
            ClaimCheck(
                sentence=sentence,
                citations=markers,
                lexical=None if lexical is None else round(lexical, 4),
                semantic=None if semantic is None else round(semantic, 4),
                score=None if score is None else round(score, 4),
                status=status,
            )
        )
    return checks


async def check_claims(
    answer: str,
    blocks: list[ContextBlock],
    *,
    index: InvertedIndex | None,
    embedder: Embedder,
    correlation_id: str,
) -> list[ClaimCheck]:
    """Orchestrates the one batched embed call around the pure ``score_claims``. ``index``
    is the sparse index over the notebook's allowed documents (``None`` if it couldn't be
    built ⇒ lexical scores are ``None``)."""
    sentences = [s for s in split_sentences(answer) if is_checkable(s)]
    if not sentences:
        return []

    lexical_fn = (lambda a, b: tfidf_cosine(index, a, b)) if index is not None else None

    texts: list[str] = []
    for s in sentences:
        texts.append(strip_markers(s))
        for m in sentence_markers(s):
            if 1 <= m <= len(blocks):
                texts.append(blocks[m - 1].content)
    unique_texts = list(dict.fromkeys(texts))
    vectors: dict[str, list[float]] | None = None
    try:
        embedded = await embedder.embed(unique_texts)
        vectors = dict(zip(unique_texts, embedded, strict=True))
    except Exception as exc:  # noqa: BLE001 — a check must never fail the chat turn
        logger.warning(
            "chat.citation_check_embed_failed",
            correlation_id=correlation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    checks = score_claims(
        sentences,
        blocks,
        lexical_fn=lexical_fn,
        vectors=vectors,
        threshold=settings.CITATION_SUPPORT_THRESHOLD,
    )
    logger.info(
        "chat.citation_check_done",
        correlation_id=correlation_id,
        supported=sum(c.status == "supported" for c in checks),
        weak=sum(c.status == "weak" for c in checks),
        uncited=sum(c.status == "uncited" for c in checks),
    )
    return checks
