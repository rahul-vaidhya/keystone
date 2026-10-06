# SciFact retrieval ablation (BEIR test split)

5183 docs, 300 queries, retrieval depth 100. Latency = mean per-query search time in this process (dense excludes the query embedding API call, which is cached; hybrid = bm25 + dense + fusion; rerank = TEI round-trip over the top 50).

| method | P@1 | P@5 | P@10 | R@10 | R@100 | MRR@10 | nDCG@10 | mean query ms |
|---|---|---|---|---|---|---|---|---|
| tfidf | 0.4833 | 0.1467 | 0.0847 | 0.7590 | 0.9026 | 0.5788 | 0.6186 | 9.75 |
| bm25 | 0.5500 | 0.1660 | 0.0917 | 0.8318 | 0.9270 | 0.6467 | 0.6863 | 8.65 |
| bm25_zones | 0.5200 | 0.1587 | 0.0880 | 0.7960 | 0.9209 | 0.6167 | 0.6553 | 14.17 |
| bm25_champions | 0.5500 | 0.1660 | 0.0917 | 0.8318 | 0.9287 | 0.6467 | 0.6863 | 4.11 |

## Sparse index stats

- n_docs: 5183
- vocabulary_size: 26327
- avg_doc_postings_length: 17.89
- total_doc_postings: 470995
- avg_zone_postings_length: {'body': 17.57, 'title': 1.94}
- champion_r: 50
- index_build_s: 8.67
