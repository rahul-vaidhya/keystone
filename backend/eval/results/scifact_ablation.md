# SciFact retrieval ablation (BEIR test split)

5183 docs, 300 queries, retrieval depth 100. Latency = mean per-query search time in this process (dense excludes the query embedding API call, which is cached; hybrid = bm25 + dense + fusion; rerank = TEI round-trip over the top 50).

| method | P@1 | P@5 | P@10 | R@10 | R@100 | MRR@10 | nDCG@10 | mean query ms |
|---|---|---|---|---|---|---|---|---|
| tfidf | 0.4833 | 0.1467 | 0.0847 | 0.7590 | 0.9026 | 0.5788 | 0.6186 | 15.98 |
| bm25 | 0.5500 | 0.1660 | 0.0917 | 0.8318 | 0.9270 | 0.6467 | 0.6863 | 17.93 |
| bm25_zones | 0.5200 | 0.1587 | 0.0880 | 0.7960 | 0.9209 | 0.6167 | 0.6553 | 17.04 |
| bm25_champions | 0.5500 | 0.1660 | 0.0917 | 0.8318 | 0.9287 | 0.6467 | 0.6863 | 6.15 |
| dense | 0.5933 | 0.1780 | 0.0967 | 0.8536 | 0.9700 | 0.6800 | 0.7164 | 1.37 |
| hybrid_rrf | 0.6100 | 0.1780 | 0.0970 | 0.8649 | 0.9767 | 0.7002 | 0.7348 | 19.71 |

## Sparse index stats

- n_docs: 5183
- vocabulary_size: 26327
- avg_doc_postings_length: 17.89
- total_doc_postings: 470995
- avg_zone_postings_length: {'body': 17.57, 'title': 1.94}
- champion_r: 50
- index_build_s: 13.06

Dense model: `text-embedding-3-small` (cosine, exact brute-force kNN).
