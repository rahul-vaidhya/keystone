# Citation checker accuracy on SciFact claims

Each pair = one claim sentence citing one abstract `[1]`, scored by the app's own `score_claims` (lexical = best tf-idf ltc cosine over the abstract's sentence windows, idf from the from-scratch sparse index over all 5,183 SciFact abstracts; semantic = `text-embedding-3-small` cosine vs the whole abstract; combined = mean). App threshold: `CITATION_SUPPORT_THRESHOLD = 0.33` (supported iff combined >= it).

- **dev** (reported): support=138, contradict=71, hard_neg=300, easy_neg=300
- **train** (threshold tuning only): support=370, contradict=194, hard_neg=809, easy_neg=809
- positive = SUPPORT evidence abstract; hard_neg = top-BM25 abstract outside the claim's evidence/cited set ("cited the wrong but on-topic chunk"); easy_neg = random abstract.

## Mean scores by pair kind (dev)

| kind | n | lexical | semantic | combined | % called supported @ app threshold |
|---|---|---|---|---|---|
| support | 138 | 0.497 | 0.651 | 0.574 | 97.8% |
| contradict | 71 | 0.412 | 0.618 | 0.515 | 88.7% |
| hard_neg | 300 | 0.359 | 0.494 | 0.426 | 80.7% |
| easy_neg | 300 | 0.019 | 0.209 | 0.114 | 1.0% |

## SUPPORT vs hard negatives (dev: 138 pos / 300 neg)

| signal | ROC-AUC | P @0.33 | R @0.33 | F1 @0.33 | Acc @0.33 | train best-F1 thr | dev P @thr | dev R @thr | dev F1 @thr | dev Acc @thr | dev oracle F1 (thr) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| lexical | 0.716 | 0.401 | 0.761 | 0.525 | 0.566 | 0.382 | 0.437 | 0.703 | 0.539 | 0.621 | 0.580 (0.464) |
| semantic | 0.848 | 0.336 | 1.000 | 0.503 | 0.377 | 0.575 | 0.596 | 0.812 | 0.687 | 0.767 | 0.691 (0.576) |
| combined | 0.808 | 0.358 | 0.978 | 0.524 | 0.441 | 0.492 | 0.546 | 0.688 | 0.609 | 0.721 | 0.634 (0.483) |

## SUPPORT vs easy negatives (dev: 138 pos / 300 neg)

| signal | ROC-AUC | P @0.33 | R @0.33 | F1 @0.33 | Acc @0.33 | train best-F1 thr | dev P @thr | dev R @thr | dev F1 @thr | dev Acc @thr | dev oracle F1 (thr) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| lexical | 0.997 | 0.991 | 0.761 | 0.861 | 0.922 | 0.167 | 0.971 | 0.971 | 0.971 | 0.982 | 0.978 (0.135) |
| semantic | 0.999 | 0.807 | 1.000 | 0.893 | 0.925 | 0.460 | 0.971 | 0.971 | 0.971 | 0.982 | 0.982 (0.442) |
| combined | 0.999 | 0.978 | 0.978 | 0.978 | 0.986 | 0.263 | 0.958 | 1.000 | 0.979 | 0.986 | 0.986 (0.329) |

## SUPPORT vs all negatives (dev: 138 pos / 600 neg)

| signal | ROC-AUC | P @0.33 | R @0.33 | F1 @0.33 | Acc @0.33 | train best-F1 thr | dev P @thr | dev R @thr | dev F1 @thr | dev Acc @thr | dev oracle F1 (thr) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| lexical | 0.856 | 0.399 | 0.761 | 0.524 | 0.741 | 0.382 | 0.435 | 0.703 | 0.537 | 0.774 | 0.578 (0.464) |
| semantic | 0.923 | 0.311 | 1.000 | 0.474 | 0.585 | 0.575 | 0.593 | 0.812 | 0.685 | 0.860 | 0.689 (0.576) |
| combined | 0.903 | 0.355 | 0.978 | 0.521 | 0.664 | 0.492 | 0.546 | 0.688 | 0.609 | 0.835 | 0.634 (0.483) |

## Contradicted claims (dev) — a measured limitation

| signal | ROC-AUC SUPPORT vs CONTRADICT | % CONTRADICT pairs called supported @0.33 |
|---|---|---|
| lexical | 0.622 | 59.2% |
| semantic | 0.588 | 100.0% |
| combined | 0.609 | 88.7% |

## Evidence localization (SUPPORT pairs, dev n=138 of 138; train n=370)

Does the checker's best-matching passage (argmax lexical window — the passage behind the lexical score) overlap a gold rationale sentence? Primary rows use windows over the abstract text only (the gold indices refer to it); the app-view rows use the full chunk the checker actually sees (title + abstract), where a best-matching title counts as a miss.

| method | dev hit@1 | train hit@1 |
|---|---|---|
| checker best window (single sentence or adjacent pair) | 55.1% | 54.1% |
| checker best single-sentence window | 54.3% | 51.6% |
| baseline: first abstract sentence | 6.5% | 7.6% |
| baseline: first adjacent-pair window | 14.5% | 17.0% |
| baseline: random window (expected) | 27.0% | 30.2% |
| baseline: random single sentence (expected) | 19.4% | 22.3% |
| app view (title + abstract windows): checker best window | 39.9% | 40.3% |
| app view: share of pairs where the TITLE was the best window | 34.1% | 32.2% |

_Run: 2991 pairs in 38s; embedding cost this run: 0 claim chars (~0 tokens) — abstracts and dev claims reused from the ablation's cache._

## Interpretation

_(Embedding cost: the one-time embedding of 777 train claims was 68,839 chars, ~17k tokens of
`text-embedding-3-small`. Abstracts and dev claims came from the retrieval ablation's existing
cache. Hand-written; preserved across reruns of `python -m eval.citation_check_eval`. Numbers below are from the run above.)_

**What the checker is good at.** Telling a claim's real source apart from an unrelated chunk
is essentially solved: SUPPORT vs random abstracts gives ROC-AUC 0.999 (combined) and, at the
app's 0.33 threshold, P = R = 0.978. A citation pointing at the wrong *topic* will be flagged
`weak` almost every time.

**What it is weak at: the "wrong but on-topic chunk".** Against hard negatives (the top-BM25
abstract that is *not* the evidence) the combined score still ranks well (AUC 0.808), but the
0.33 threshold sits far too low: 80.7% of hard negatives get `supported` (precision 0.358 at
recall 0.978). At 0.33 the label mostly means "on topic", not "this passage backs the claim".

**Does the lexical + semantic fusion help? Not on this data.** Semantic-only beats the
equal-weight mean on hard negatives (AUC 0.848 vs 0.808; paired bootstrap 95% CI for the
difference [0.004, 0.078], so the gap is small but probably real). A weight sweep (lexical
weight 0 / 0.25 / 0.5 / 0.75 / 1 → AUC 0.848 / 0.844 / 0.808 / 0.760 / 0.716) shows the lexical
signal only dilutes the semantic one here. Two caveats point the other way, so this is not a
verdict for removing the lexical channel: (1) the hard negatives were *mined with BM25*, so
they are lexically similar by construction, which handicaps the lexical signal (selection
bias); (2) SciFact claims are annotator paraphrases, whereas LLM answers in the app tend to
reuse the source's wording, where tf-idf overlap tells you more. The lexical channel also
supplies the passage-level evidence (below), which the whole-chunk embedding can't.

**Contradicted claims: a measured limitation.** The checker measures topical, lexical-semantic
support, not entailment. 88.7% of CONTRADICT pairs are called `supported` at 0.33, and SUPPORT vs
CONTRADICT is close to chance (AUC 0.609 combined, 0.588 semantic). A sentence that states the
*opposite* of its cited source will usually pass. Catching that needs an NLI/entailment model
(e.g. a cross-encoder fine-tuned on SciFact/MNLI). That is out of scope for this checker, and
the UI should not present `supported` as "verified true".

**Evidence localization.** The best-matching lexical window overlaps a gold rationale sentence
for 55.1% of dev SUPPORT pairs (54.3% single sentences only). Baselines: 6.5% for the first
sentence, 27.0% for a random window, 19.4% for a random sentence. So the passage the checker
points at is about 2× better than chance and 8× better than lead-sentence. In the app's own
view (title + abstract in one chunk), the title is the best window 34% of the time, which drops
hit@1 to 39.9%. A heading or title that restates the claim wins the lexical max. If the
best-matching window is ever surfaced as a highlighted span, heading lines should be excluded
from the window set.

**Threshold recommendation (not applied; the app default stays 0.33).** For the combined score,
the best-F1 threshold tuned on *train* is **0.49**. On dev it gives P 0.546 / R 0.688 /
F1 0.609 vs hard negatives, against F1 0.524 at 0.33. The dev-oracle optimum is 0.483, so the
tuned value transfers well. At 0.49 the share of dev pairs called `supported` is 71% for
SUPPORT, 27% for hard negatives, 0% for random, and 56% for CONTRADICT. If only the semantic
score were used, the equivalent cut is 0.575 (dev F1 0.687 vs hard negatives). Caveat: the best
threshold depends on the real mix of correct and wrong citations, which this benchmark doesn't
reflect. 0.33 is still the right cut if the only goal is catching off-topic citations
(F1 0.978 vs random).

**Fidelity notes.** The pairs go through the app's real `score_claims` and `chunk_windows`,
with no reimplementation. idf comes from the from-scratch `InvertedIndex` over the SciFact
corpus, not over a notebook. One SciFact abstract plays one chunk; abstracts (~1.5k chars) are
somewhat longer than the app's ~1k-char chunks. Mixed-label evidence docs would be skipped (there are none in train or dev). Claims with no evidence (NEI) only contribute negatives.
