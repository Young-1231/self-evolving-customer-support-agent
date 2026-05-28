# Exp G — 10k+ episodic memory scale stress (v2.5 R4)

Synthetic NimbusFlow-style benchmark, mock backend, deterministic, zero LLM cost.  N=10000 cases across 15 topics, eval set = 200 held-out paraphrased queries.

## Head-to-head: jsonl vs fs_topic_date

| store | n_cases | populate (s) | avg retr (ms) | p50 | p95 | p99 | resolution | escalation |
|---|---|---|---|---|---|---|---|---|
| EpisodicMemory (jsonl) | 10000 | 0.05 | 50.66 | 50.20 | 61.18 | 69.06 | 99.5% | 0.0% |
| FsEpisodicStore (topic_date) | 10000 | 0.03 | 21.93 | 20.96 | 30.17 | 38.02 | 100.0% | 0.0% |

**Delta (fs - jsonl):** resolution +0.50 pp; retrieval avg speedup = 2.31x  (50.66 -> 21.93 ms).

## Notes

- Synthetic data; absolute numbers are NOT directly comparable to OpenViking's tau2-bench retail (+6.87pp) / airline (+11.87pp) self-reports, but the **direction** (fs_store gains as the pool grows) is the falsifiable claim.
- jsonl populate uses a single end-of-load `_reindex()` to avoid the default O(N^2) add-then-reindex; this is a **read-only** access of an existing private method, no source is modified.
- fs_store buckets: n_l0=15, n_l1=150  (l0_top=3 kept per query).
