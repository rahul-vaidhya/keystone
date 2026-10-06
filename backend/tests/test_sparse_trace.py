"""Pure unit tests for the sparse IR explain traces (``sparse/trace.py``): each trace
returns exactly the same result set as the plain ``scoring`` API it mirrors, plus the
intermediate steps the Search page displays."""

from __future__ import annotations

import pytest

from app.services.retrieval.sparse import (
    InvertedIndex,
    SparseDoc,
    boolean_search,
    phrase_search,
    search,
)
from app.services.retrieval.sparse.trace import (
    BOOLEAN_SYNTAX_HELP,
    MalformedBooleanQuery,
    boolean_search_trace,
    index_avg_postings_length,
    matching_surface_forms,
    phrase_occurrence_spans,
    phrase_search_trace,
    query_pipeline,
    ranked_search_trace,
    snippet_around,
    term_stats,
    validate_boolean_query,
)


@pytest.fixture
def corpus() -> InvertedIndex:
    docs = [
        ("d1", "Ionic Bonds", "The octet rule explains ionic bonding of sodium chloride."),
        ("d2", "Covalent Bonds", "A covalent bond shares electrons; the octet rule holds."),
        ("d3", "Hydrogen Bonding", "A hydrogen bond is weaker than a covalent bond."),
        ("d4", "Lattice", "Lattice enthalpy measures ionic lattice energy."),
        ("d5", "", "Hydrogen gas is diatomic and the hydrogen bond is not covalent."),
    ]
    return InvertedIndex.build(
        [SparseDoc(doc_id=d, zones={"heading": h, "body": b}) for d, h, b in docs], champion_r=2
    )


def test_query_pipeline_shows_each_stage() -> None:
    p = query_pipeline("The Octet RULES of bonding")
    assert p.raw_tokens == ["The", "Octet", "RULES", "of", "bonding"]
    assert p.casefolded == ["the", "octet", "rules", "of", "bonding"]
    assert p.stop_words_removed == ["the", "of"]
    assert p.kept_tokens == ["octet", "rules", "bonding"]
    assert p.stems == ["octet", "rule", "bond"]


def test_term_stats_reports_df_idf_postings_and_elimination(corpus: InvertedIndex) -> None:
    stats = {
        s.term: s for s in term_stats(corpus, query_pipeline("bond octet zzz"), idf_threshold=0.3)
    }
    assert stats["bond"].df == 4
    assert stats["bond"].postings == {"body": 4, "heading": 3}
    assert stats["bond"].champions == {"body": 2, "heading": 2}  # champion_r = 2
    assert stats["bond"].eliminated  # idf = log10(5/4) ~ 0.097 <= 0.3
    assert stats["octet"].df == 2 and not stats["octet"].eliminated
    assert stats["zzz"].df == 0 and stats["zzz"].eliminated
    # no threshold (Boolean / phrase) -> nothing is ever marked eliminated
    assert not any(s.eliminated for s in term_stats(corpus, query_pipeline("bond zzz")))


def test_ranked_trace_matches_search_and_contributions_sum_to_score(corpus) -> None:
    for scheme in ("tfidf", "bm25"):
        kwargs = dict(scheme=scheme, zone_weights={"heading": 2.0, "body": 1.0})
        hits, trace = ranked_search_trace(
            corpus, "octet rule bond", k=3, use_champions=False, idf_threshold=0.0, **kwargs
        )
        plain = search(corpus, "octet rule bond", k=3, explain=True, **kwargs)
        assert [h.doc_id for h in hits] == [h.doc_id for h in plain]
        for h in hits:
            assert sum(c.weight for c in h.contributions) == pytest.approx(h.score)
        assert trace.docs_with_postings == 4 and trace.docs_scored == 4
        assert trace.champion_candidates is None


def test_ranked_trace_index_elimination_and_champions(corpus) -> None:
    _, trace = ranked_search_trace(
        corpus,
        "bond octet",
        k=5,
        scheme="bm25",
        zone_weights=None,
        use_champions=True,
        idf_threshold=0.3,
    )
    assert trace.eliminated_terms == ["bond"]
    assert trace.active_terms == ["octet"]
    assert trace.champion_candidates is not None and trace.champion_candidates <= 2
    assert trace.docs_scored <= trace.docs_with_postings


@pytest.mark.parametrize(
    "query",
    [
        "hydrogen AND bond NOT covalent",
        "octet rule",
        "ionic OR hydrogen",
        "NOT covalent",
        "lattice OR octet AND ionic",
        "zzz AND bond",
    ],
)
def test_boolean_trace_matches_boolean_search(corpus, query) -> None:
    assert boolean_search_trace(corpus, query).result == boolean_search(corpus, query)


def test_boolean_trace_processes_terms_in_increasing_df_order(corpus) -> None:
    trace = boolean_search_trace(corpus, "bond AND covalent AND octet NOT hydrogen")
    assert trace.operators == ["AND", "AND", "NOT"]
    steps = trace.clauses[0].steps
    assert [s.op for s in steps] == ["START", "AND", "AND", "AND NOT"]
    positive_dfs = [s.df for s in steps if s.op in ("START", "AND")]
    assert positive_dfs == sorted(positive_dfs)
    # each AND can only shrink the intermediate result
    sizes = [s.result_size for s in steps]
    assert sizes == sorted(sizes, reverse=True)
    assert trace.union_steps[-1].result_size == len(trace.result)


def test_boolean_trace_reports_implicit_and(corpus) -> None:
    trace = boolean_search_trace(corpus, "octet rule")
    assert trace.operators == ["AND (implicit)"]


@pytest.mark.parametrize("phrase", ["octet rule", "hydrogen bond", "lattice enthalpy", "rule zzz"])
def test_phrase_trace_matches_phrase_search(corpus, phrase) -> None:
    trace = phrase_search_trace(corpus, phrase)
    assert trace.result == phrase_search(corpus, phrase)
    assert len(trace.result) <= trace.candidates


def test_phrase_trace_positional_check_filters_candidates(corpus) -> None:
    # "covalent bond" co-occur in d2, d3, d5 bodies but are adjacent only in d2 and d3.
    trace = phrase_search_trace(corpus, "covalent bond")
    body = next(z for z in trace.zones if z.zone == "body")
    assert body.candidates == 3 and body.matched == 2
    assert trace.result == ["d2", "d3"]
    zone, positions = trace.matches["d2"][0]
    assert zone == "body" and positions == [1]


def test_highlight_helpers() -> None:
    assert matching_surface_forms("Bonds and bonding: the Bond.", {"bond"}) == [
        "Bonds",
        "bonding",
        "Bond",
    ]
    assert matching_surface_forms("the of", {"the"}) == []
    long = "x " * 300 + "target word here " + "y " * 300
    snip = snippet_around(long, ["target"], width=100)
    assert "target" in snip and snip.startswith("…") and snip.endswith("…")
    assert snippet_around("short", ["zzz"]) == "short"


def test_avg_postings_length(corpus) -> None:
    terms = corpus.terms()
    assert index_avg_postings_length(corpus) == pytest.approx(
        sum(corpus.df(t) for t in terms) / len(terms)
    )
    assert index_avg_postings_length(InvertedIndex.build([])) == 0.0


@pytest.mark.parametrize(
    "query",
    ["AND NOT", "AND bond", "bond OR", "bond NOT", "hydrogen AND (bond", "a OR AND b", "NOT AND x"],
)
def test_validate_boolean_query_rejects_malformed(query: str) -> None:
    with pytest.raises(MalformedBooleanQuery) as exc:
        validate_boolean_query(query)
    assert BOOLEAN_SYNTAX_HELP in str(exc.value)


@pytest.mark.parametrize(
    "query", ["hydrogen AND bond NOT covalent", "NOT ionic", "octet rule", "a AND NOT b OR c"]
)
def test_validate_boolean_query_accepts_well_formed(query: str) -> None:
    validate_boolean_query(query)


def test_phrase_snippet_centres_on_the_verified_occurrence() -> None:
    filler = "Filler words here. " * 20
    text = "The enthalpy of lattice formation is discussed. " + filler + "Lattice enthalpy is big."
    idx = InvertedIndex.build([SparseDoc(doc_id="d", zones={"body": text})])
    trace = phrase_search_trace(idx, "lattice enthalpy")
    [(zone, positions)] = trace.matches["d"]
    assert zone == "body"
    spans = phrase_occurrence_spans(text, positions, 2)
    assert [text[a:b] for a, b in spans] == ["Lattice enthalpy"]
    snip = snippet_around(text, ["Lattice enthalpy"], width=120, anchor=spans[0])
    assert "Lattice enthalpy is big" in snip
    assert "enthalpy of lattice" not in snip
