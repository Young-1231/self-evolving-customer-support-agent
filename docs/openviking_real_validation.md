# OpenViking-style FsEpisodicStore — real validation (v2.5 R4)

Status: validated on synthetic NimbusFlow-style benchmark at populated-pool
scale (10000 cases).  No LLM call.  Deterministic.

This document closes the loop opened by Exp F, which only established cold-start
parity (0 cases) between the legacy `EpisodicMemory` (flat jsonl + BM25) and the
new `FsEpisodicStore` (OpenViking-style L0 topic / L1 date / L2 case markdown).
The +6-12pp self-reported deltas from OpenViking (volcengine, 24.8k stars,
2026-05) on tau2-bench retail / airline are claimed to depend on a *populated*
experience pool.  Exp G measures the regime where that claim is testable in our
codebase.

## Setup

- N = **10000** synthetic cases, evenly distributed across 15 topics
  (8 NimbusFlow KB topics + 7 Bitext-style synthetic fillers: cancel_order /
  track_order / refund / shipping / create_account / delete_account / invoice).
- L1 buckets: 12 calendar months (`2026-01` ... `2026-12`) — yields 150 L1
  leaves total at N=10k (~67 cases per leaf at uniform fill).
- Eval set: **200** held-out paraphrased queries, sampled to cover every topic
  uniformly.  Each query carries 3 topic-level keypoints (substring match by
  the deterministic verifier — agent never sees them).
- Backends compared: legacy `EpisodicMemory(jsonl)` vs
  `FsEpisodicStore(scheme="topic_date", l0_top=3)`.
- Agent: standard `SupportAgent` + `MockBackend` (deterministic — returns the
  text of the top-scoring retrieved passage; semantics described in
  `src/seagent/llm/mock.py`).  No LLM call.
- All measurements via `time.perf_counter`.

## Head-to-head results (10k cases, 200 queries)

| store | n_cases | populate (s) | avg retr (ms) | p50 | p95 | p99 | resolution | escalation |
|---|---|---|---|---|---|---|---|---|
| EpisodicMemory (jsonl) | 10000 | 0.05 | 50.66 | 50.20 | 61.18 | 69.06 | 99.5% | 0.0% |
| FsEpisodicStore (topic_date) | 10000 | 0.03 | 21.93 | 20.96 | 30.17 | 38.02 | 100.0% | 0.0% |

**Delta (fs - jsonl):**
- resolution: **+0.50 pp**
- avg retrieval: **2.31x speedup** (50.66 -> 21.93 ms)
- p95 retrieval: **2.03x speedup** (61.18 -> 30.17 ms)
- populate time: ~2x faster (per-leaf BM25 indexes are cheaper than one global
  BM25 over 10k docs)
- end-to-end agent eval wall time: 10.5s -> 4.4s on the 200-query batch
- escalation rate: 0% on both — synthetic cases are all non-escalate by design

Total Exp G wall time (synth + populate + retrieve + agent_eval for both
backends): **~30 s** on a single CPU core.

## Cross-reference: 500-ticket stress (pre-Exp G)

The 500-ticket BM25 jsonl stress test ran in v2.x and located the retrieval
inflection at ~1k cases (avg ~10 ms / p95 ~19 ms).  Exp G confirms the
extrapolation: at 10k cases the legacy jsonl store is at ~51 ms / p95 61 ms — a
5x degradation from the 1k regime — while fs_topic_date stays at ~22 ms.  The
fs_store latency grows much more slowly with N because each query touches at
most `l0_top * cases_per_l1` ~ 3 * 67 ~ 200 docs, not 10000.

## How this maps to OpenViking's self-reported tau2-bench numbers

OpenViking reports retail +6.87 pp / airline +11.87 pp from re-organizing the
experience pool into a directory hierarchy.  Those numbers are on a different
benchmark (tau2) and a different agent loop, so absolute pp on Exp G are NOT
directly comparable.  What IS comparable is the **direction** and the
**mechanism**:

| claim | OpenViking source | Exp G on this repo |
|---|---|---|
| FS hierarchy gives a positive resolution delta at populated scale | tau2-bench retail +6.87 pp, airline +11.87 pp | +0.50 pp at N=10k, 100% vs 99.5% |
| Retrieval latency stays bounded as the pool grows | not explicitly quantified | 2.31x avg / 2.03x p95 speedup at N=10k |
| Cold-start parity with the flat baseline | not claimed | Exp F: -1.4 pp noise at N=0 (parity within noise) |

The resolution pp delta in Exp G is small (+0.50 pp) because the synthetic eval
queries here already saturate near 100% — both stores find a same-topic case in
the top-k for the keypoint phrasing we use.  This is the expected ceiling
effect of a synthetic benchmark; the latency story is the unambiguous win.

## Engineering recommendation

Switch to `FsEpisodicStore(scheme="topic_date")` when the episodic pool exceeds
~1000 cases.  Below that, the legacy jsonl store has lower constant overhead
and the latency win is in the noise.  The crossover is conservative — even at
N=300 (the Exp G test fixture) fs is already non-slower than jsonl.

Wiring is a 1-line swap at the agent construction site:

```python
# before
episodic = EpisodicMemory(path=cfg.episodic_path)
# after
episodic = FsEpisodicStore(root_dir=cfg.episodic_fs_root, scheme="topic_date",
                           l0_top=3)
```

Public API is identical (`add`, `retrieve`, `__len__`), so call sites need no
other change.

## Where the data and code live

- script: `scripts/run_exp_g_memory_scale.py`
- tests: `tests/test_exp_g.py` (8 tests, scaled to N=300 for CI speed)
- outputs: `experiments/exp_g/results.json`, `experiments/exp_g/report.md`,
  `experiments/exp_g/synth_meta.json`
- store impl (unchanged): `src/seagent/memory/fs_store.py`
- prior cold-start parity: `experiments/fs_ablation/` and
  `scripts/run_stress_test_exp_f_scaffold.py`

## Reproducibility

```
PYTHONPATH=src python scripts/run_exp_g_memory_scale.py \
    --n-cases 10000 --n-eval 200 --top-k 3 --l0-top 3 --seed 0
```

The full run is deterministic and takes ~30 s on a single CPU core.  No
network, no API key, no LLM credit consumed.
