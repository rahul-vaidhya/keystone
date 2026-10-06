# Sparse (bm25) vs dense (dense) on SciFact test

Per-query nDCG@10 compared; a 'win' needs a margin > 0.1.

| bucket | queries |
|---|---|
| bm25_wins | 52 |
| dense_wins | 66 |
| ties | 182 |

## Query characterization per bucket

| bucket | n_queries | avg_query_len_tokens | frac_high_idf_terms | frac_queries_with_digits | frac_terms_unseen_in_corpus | mean_term_idf |
|---|---|---|---|---|---|---|
| bm25_wins | 52.000 | 11.942 | 0.401 | 0.288 | 0.031 | 3.369 |
| dense_wins | 66.000 | 13.606 | 0.337 | 0.348 | 0.033 | 3.029 |
| ties | 182.000 | 13.176 | 0.368 | 0.418 | 0.019 | 3.251 |

High-idf term = appears in at most ~100 of 5183 docs (idf >= 3.94).

## Examples: bm25_wins

- `1024` (bm25 1.00 vs dense 0.00): Recurrent mutations occur frequently within CTCF anchor sites adjacent to oncogenes.
- `3` (bm25 1.00 vs dense 0.36): 1,000 genomes project enables mapping of genetic sequence variation consisting of rare variants with larger penetrance effects than common variants.
- `295` (bm25 1.00 vs dense 0.36): Crosstalk between dendritic cells (DCs) and innate lymphoid cells (ILCs) is important in the regulation of intestinal homeostasis.
- `845` (bm25 1.00 vs dense 0.36): Neutrophil extracellular traps (NETs) are released by ANCA-stimulated neutrophils.
- `294` (bm25 0.63 vs dense 0.00): Crossover hot spots are not found within gene promoters in Saccharomyces cerevisiae.

## Examples: dense_wins

- `384` (bm25 0.00 vs dense 1.00): Epidemiological disease burden from noncommunicable diseases is more prevalent in low economic settings.
- `628` (bm25 0.00 vs dense 1.00): Infection of human T-cell lymphotropic virus type 1 is most frequent in individuals of African origin.
- `800` (bm25 0.00 vs dense 1.00): Modifying the epigenome in the brain affects the normal human aging process by affecting certain genes related to neurogenesis.
- `1088` (bm25 0.00 vs dense 1.00): Silencing of Bcl2 is important for the maintenance and progression of tumors.
- `1200` (bm25 0.00 vs dense 1.00): The binding orientation of the ML-SA1 activator at hTRPML2 is different from the binding orientation of the ML-SA1 activator at hTRPML1.
