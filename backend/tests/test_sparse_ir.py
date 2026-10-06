"""Pure unit tests for the from-scratch sparse IR core (no DB, no app wiring)."""

from __future__ import annotations

import math

import pytest

from app.services.retrieval.sparse import (
    STOP_WORDS,
    InvertedIndex,
    SparseDoc,
    analyze,
    and_not,
    boolean_search,
    intersect,
    phrase_search,
    search,
    tfidf_cosine,
    tokenize,
    union,
)


def _body(doc_id: str, text: str) -> SparseDoc:
    return SparseDoc(doc_id=doc_id, zones={"body": text})


@pytest.fixture
def tiny() -> InvertedIndex:
    # N = 3. ionic: df 1 (tf 2 in d1); bond: df 2; d1 len 3, d2 len 2, d3 len 2.
    return InvertedIndex.build(
        [
            _body("d1", "ionic bond ionic"),
            _body("d2", "covalent bond"),
            _body("d3", "metal lattice"),
        ]
    )


# ---- text analysis ---------------------------------------------------------------------


def test_tokenize_case_folds_and_splits_on_non_alphanumerics() -> None:
    assert tokenize("Hello, WORLD! re-use x_y Straße") == [
        "hello",
        "world",
        "re",
        "use",
        "x",
        "y",
        "strasse",
    ]


def test_analyze_drops_stop_words_and_stems() -> None:
    assert "the" in STOP_WORDS and "of" in STOP_WORDS
    assert analyze("The bonding of ionic bonds") == ["bond", "ionic", "bond"]
    assert analyze("connected connection connecting") == ["connect"] * 3
    assert analyze("the of and") == []


# ---- index -----------------------------------------------------------------------------


def test_dictionary_df_idf_and_positional_postings(tiny: InvertedIndex) -> None:
    assert tiny.n_docs == 3
    assert tiny.df("bond") == 2 and tiny.df("bonds") == 2  # raw word resolves to its stem
    assert tiny.idf("ionic") == pytest.approx(math.log10(3))
    assert tiny.idf("bond") == pytest.approx(math.log10(1.5))
    assert tiny.idf("unseen") == 0.0
    assert tiny.postings("ionic", "body") == [("d1", [0, 2])]
    assert tiny.postings("bond", "body") == [("d1", [1]), ("d2", [1])]
    assert tiny.vocabulary_size() == len({"ionic", "bond", *analyze("covalent metal lattice")})


def test_df_counts_a_document_once_across_zones() -> None:
    idx = InvertedIndex.build(
        [SparseDoc("a", {"heading": "bond", "body": "bond bond"}), _body("b", "x")]
    )
    assert idx.df("bond") == 1
    assert idx.postings("bond", "heading") == [("a", [0])]


def test_positions_keep_stop_word_gaps() -> None:
    idx = InvertedIndex.build([_body("a", "rules of thumb")])
    assert idx.postings("thumb", "body") == [("a", [2])]


# ---- ranked retrieval ------------------------------------------------------------------


def test_tfidf_is_smart_lnc_ltc_cosine(tiny: InvertedIndex) -> None:
    idf_i, idf_b = math.log10(3), math.log10(1.5)
    q_norm = math.sqrt(idf_i**2 + idf_b**2)
    l2 = 1 + math.log10(2)
    expected_d1 = (idf_i * l2 + idf_b * 1) / (q_norm * math.sqrt(l2**2 + 1))
    expected_d2 = idf_b / (q_norm * math.sqrt(2))

    hits = search(tiny, "ionic bonds", scheme="tfidf")
    assert [h.doc_id for h in hits] == ["d1", "d2"]
    assert hits[0].score == pytest.approx(expected_d1)
    assert hits[1].score == pytest.approx(expected_d2)


def test_bm25_matches_hand_computed_value(tiny: InvertedIndex) -> None:
    k1, b = 1.2, 0.75
    avg = 7 / 3
    idf_i, idf_b = math.log10(3), math.log10(1.5)

    def term(idf: float, tf: int, length: int) -> float:
        return idf * (k1 + 1) * tf / (k1 * ((1 - b) + b * length / avg) + tf)

    hits = search(tiny, "ionic bond", scheme="bm25")
    assert [h.doc_id for h in hits] == ["d1", "d2"]
    assert hits[0].score == pytest.approx(term(idf_i, 2, 3) + term(idf_b, 1, 3))
    assert hits[1].score == pytest.approx(term(idf_b, 1, 2))


def test_heap_top_k_ordering_and_deterministic_tie_break() -> None:
    idx = InvertedIndex.build(
        [_body("z", "apple"), _body("y", "apple"), _body("x", "apple apple"), _body("w", "pear")]
    )
    hits = search(idx, "apple", k=2, scheme="bm25")
    assert [h.doc_id for h in hits] == ["x", "y"]  # x has higher tf; y beats z on doc id
    assert [h.doc_id for h in search(idx, "apple", k=10)] == ["x", "y", "z"]
    assert search(idx, "apple", k=0) == []


def test_champion_lists_restrict_candidates() -> None:
    docs = [_body("a", "cat cat cat"), _body("b", "cat"), _body("c", "cat cat"), _body("d", "dog")]
    idx = InvertedIndex.build(docs, champion_r=1)
    assert [d for d, _ in idx.champions("cat", "body")] == ["a"]
    assert [h.doc_id for h in search(idx, "cat", use_champions=True)] == ["a"]
    assert [h.doc_id for h in search(idx, "cat")] == ["a", "c", "b"]


def test_index_elimination_drops_low_idf_terms(tiny: InvertedIndex) -> None:
    # bond idf ~0.176, ionic idf ~0.477: threshold 0.3 eliminates "bond".
    hits = search(tiny, "ionic bond", idf_threshold=0.3, explain=True)
    assert [h.doc_id for h in hits] == ["d1"]
    assert {c.term for c in hits[0].contributions} == {"ionic"}
    # A term present in every document has idf 0 and is eliminated by the default.
    idx = InvertedIndex.build([_body("a", "the cat sat"), _body("b", "a cat ran")])
    assert search(idx, "cat") == []


def test_zone_weighting_changes_ranking() -> None:
    idx = InvertedIndex.build(
        [
            SparseDoc("h", {"heading": "bond", "body": "lattice"}),
            SparseDoc("b", {"heading": "lattice", "body": "bond"}),
            SparseDoc("x", {"heading": "metal", "body": "metal"}),
        ]
    )
    heading_first = search(idx, "bond", zone_weights={"heading": 2.0, "body": 1.0})
    body_first = search(idx, "bond", zone_weights={"heading": 1.0, "body": 2.0})
    assert [h.doc_id for h in heading_first] == ["h", "b"]
    assert [h.doc_id for h in body_first] == ["b", "h"]
    assert heading_first[0].score == pytest.approx(2 * heading_first[1].score)
    # A zone missing from zone_weights is ignored entirely.
    assert [h.doc_id for h in search(idx, "bond", zone_weights={"heading": 1.0})] == ["h"]


@pytest.mark.parametrize("scheme", ["tfidf", "bm25"])
def test_explain_contributions_sum_to_score(scheme: str) -> None:
    idx = InvertedIndex.build(
        [
            SparseDoc("a", {"heading": "ionic bonding", "body": "ionic bonds form in salts"}),
            SparseDoc("b", {"heading": "metals", "body": "metallic bonds and electrons"}),
            SparseDoc("c", {"heading": "gases", "body": "noble gases are inert"}),
        ]
    )
    hits = search(
        idx,
        "ionic bonds salts",
        scheme=scheme,
        zone_weights={"heading": 2.0, "body": 1.0},
        explain=True,
    )  # noqa: E501
    assert hits
    for hit in hits:
        assert hit.contributions
        assert sum(c.weight for c in hit.contributions) == pytest.approx(hit.score)
        for c in hit.contributions:
            assert c.tf == idx.tf(c.term, c.zone, hit.doc_id)
            assert c.idf == pytest.approx(idx.idf(c.term))
    assert search(idx, "ionic", explain=False)[0].contributions == ()


# ---- phrase / boolean / cosine ---------------------------------------------------------


def test_phrase_query_vs_bag_of_words() -> None:
    idx = InvertedIndex.build(
        [_body("p", "salt water is wet"), _body("q", "water with salt"), _body("r", "fresh")]
    )
    assert {h.doc_id for h in search(idx, "salt water")} == {"p", "q"}
    assert phrase_search(idx, "salt water") == ["p"]
    assert phrase_search(idx, "water salt") == []
    assert phrase_search(idx, "the of") == []
    assert phrase_search(idx, "salt water", zone="heading") == []


def test_phrase_query_with_stop_word_gap_and_zones() -> None:
    idx = InvertedIndex.build(
        [
            SparseDoc("a", {"heading": "rules of thumb", "body": "x"}),
            SparseDoc("b", {"heading": "x", "body": "rules thumb"}),
        ]
    )
    assert phrase_search(idx, "rules of thumb") == ["a"]
    assert phrase_search(idx, "rules thumb") == ["b"]
    assert phrase_search(idx, "rules of thumb", zone="body") == []


def test_merge_algorithms() -> None:
    assert intersect(["a", "c", "e"], ["b", "c", "e", "f"]) == ["c", "e"]
    assert union(["a", "c"], ["b", "c", "d"]) == ["a", "b", "c", "d"]
    assert and_not(["a", "b", "c"], ["b", "d"]) == ["a", "c"]


def test_boolean_and_or_not() -> None:
    idx = InvertedIndex.build(
        [
            _body("1", "ionic bond salt"),
            _body("2", "covalent bond"),
            _body("3", "ionic lattice"),
            _body("4", "noble gas"),
        ]
    )
    assert boolean_search(idx, "ionic AND bond") == ["1"]
    assert boolean_search(idx, "ionic bond") == ["1"]  # implicit AND
    assert boolean_search(idx, "covalent OR lattice") == ["2", "3"]
    assert boolean_search(idx, "bond AND NOT salt") == ["2"]
    assert boolean_search(idx, "NOT bond") == ["3", "4"]
    assert boolean_search(idx, "ionic AND bond OR gas") == ["1", "4"]  # AND binds tighter
    assert boolean_search(idx, "bonds") == ["1", "2"]  # operands are stemmed
    assert boolean_search(idx, "unseen AND bond") == []


def test_tfidf_cosine_between_texts(tiny: InvertedIndex) -> None:
    assert tfidf_cosine(tiny, "ionic bond", "ionic bonds") == pytest.approx(1.0)
    assert tfidf_cosine(tiny, "ionic", "covalent") == 0.0
    assert tfidf_cosine(tiny, "the of", "ionic") == 0.0
    mid = tfidf_cosine(tiny, "ionic bond", "bond")
    assert 0.0 < mid < 1.0
