r"""Text analysis pipeline for the from-scratch sparse IR core (IIR ch. 2, "The term
vocabulary and postings lists").

The pipeline every document zone AND every query passes through, in order:

1. **Unicode normalization** (NFKC) — folds compatibility characters (e.g. ligatures,
   full-width digits) to a canonical form before tokenizing.
2. **Tokenization** — split on any run of non-alphanumeric characters (Unicode-aware:
   ``[^\W_]+`` matches letters/digits in any script, never underscores/punctuation).
3. **Case folding** — ``str.casefold()`` (a stronger, Unicode-correct form of lowercasing,
   e.g. German ``ß`` -> ``ss``), the IIR "case folding" normalization step.
4. **Stop-word removal** — drop very-high-frequency function words (``STOP_WORDS``),
   which carry almost no discriminating power (their idf is near zero anyway).
5. **Stemming** — the Porter stemmer (Porter, 1980; NLTK's implementation in its
   ``ORIGINAL_ALGORITHM`` mode), conflating inflectional/derivational variants
   (``bonds``/``bonding`` -> ``bond``) into one index term (equivalence classing).

Pure module: no DB, no app imports.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from nltk.stem.porter import PorterStemmer

# A small, hand-curated English stop list (function words: articles, pronouns,
# auxiliaries, prepositions, conjunctions). Deliberately short — IIR notes modern
# systems use small or no stop lists because idf already down-weights common terms;
# we keep one so the index stays compact and phrase/boolean queries skip noise words.
STOP_WORDS: frozenset[str] = frozenset(
    """
    a about above after again against all am an and any are as at be because been
    before being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers herself him himself
    his how i if in into is it its itself just me more most my myself no nor not of off
    on once only or other our ours ourselves out over own same she should so some such
    than that the their theirs them themselves then there these they this those through
    to too under until up very was we were what when where which while who whom why will
    with would you your yours yourself yourselves
    """.split()
)

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STEMMER = PorterStemmer(mode=PorterStemmer.ORIGINAL_ALGORITHM)


def tokenize(text: str) -> list[str]:
    """Tokenization + normalization + case folding (IIR §2.2). Returns every token in
    document order — stop words are KEPT here (``analyze`` removes them), so a token's
    list index is its position in the raw token stream."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _TOKEN_RE.findall(normalized)


@lru_cache(maxsize=65536)
def stem(token: str) -> str:
    """Porter stemming (IIR §2.2.4) of one already-case-folded token. Memoized — the
    same surface forms recur constantly across a corpus."""
    return _STEMMER.stem(token)


def analyze_with_positions(text: str) -> list[tuple[str, int]]:
    """``analyze`` but each index term is paired with its position in the ORIGINAL
    token stream (before stop-word removal). Positions feed the positional index
    (IIR §2.4.2): keeping original positions means a phrase query whose stop words were
    removed still checks the true word distance between the remaining terms."""
    return [(stem(tok), pos) for pos, tok in enumerate(tokenize(text)) if tok not in STOP_WORDS]


def analyze(text: str) -> list[str]:
    """Full analysis pipeline: tokenize -> case-fold -> drop stop words -> Porter stem.
    Output tokens are the index's dictionary terms."""
    return [term for term, _ in analyze_with_positions(text)]
