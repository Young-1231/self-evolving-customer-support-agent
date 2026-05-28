# v2.5 R3 — Filesystem context store ablation (OpenViking-style)

Synthetic NimbusFlow benchmark (mock backend, deterministic, zero LLM cost).

## Final-round comparison

| condition | final res | final cov | final repeat err | final esc F1 |
|---|---|---|---|---|
| static | 34.2% | 42.3% | 100.0% | 0.00 |
| jsonl_episodic | 71.1% | 76.5% | 40.0% | 0.71 |
| fs_topic_date | 71.1% | 73.9% | 44.0% | 0.75 |
| fs_flat | 71.1% | 76.5% | 40.0% | 0.71 |

## Per-round detail

| condition | round | cases | resolution | keypoint cov | esc F1 | human intv | repeat err |
|---|---|---|---|---|---|---|---|
| static | 0 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 1 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 2 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 3 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 4 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 5 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| static | 6 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| jsonl_episodic | 0 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| jsonl_episodic | 1 | 2 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| jsonl_episodic | 2 | 3 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| jsonl_episodic | 3 | 10 | 47.4% | 52.0% | 0.36 | 10.5% | 80.0% |
| jsonl_episodic | 4 | 17 | 57.9% | 66.0% | 0.46 | 15.8% | 60.0% |
| jsonl_episodic | 5 | 21 | 63.2% | 69.5% | 0.67 | 21.1% | 52.0% |
| jsonl_episodic | 6 | 24 | 71.1% | 76.5% | 0.71 | 26.3% | 40.0% |
| fs_topic_date | 0 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_topic_date | 1 | 2 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_topic_date | 2 | 3 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_topic_date | 3 | 10 | 47.4% | 52.0% | 0.40 | 7.9% | 80.0% |
| fs_topic_date | 4 | 17 | 63.2% | 66.9% | 0.67 | 13.2% | 56.0% |
| fs_topic_date | 5 | 21 | 63.2% | 69.5% | 0.67 | 13.2% | 56.0% |
| fs_topic_date | 6 | 24 | 71.1% | 73.9% | 0.75 | 23.7% | 44.0% |
| fs_flat | 0 | 0 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_flat | 1 | 2 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_flat | 2 | 3 | 34.2% | 42.3% | 0.00 | 2.6% | 100.0% |
| fs_flat | 3 | 10 | 47.4% | 52.0% | 0.36 | 10.5% | 80.0% |
| fs_flat | 4 | 17 | 57.9% | 66.0% | 0.46 | 15.8% | 60.0% |
| fs_flat | 5 | 21 | 63.2% | 69.5% | 0.67 | 21.1% | 52.0% |
| fs_flat | 6 | 24 | 71.1% | 76.5% | 0.71 | 26.3% | 40.0% |

## Filesystem store topology

```json
{
  "fs_topic_date": {
    "n_cases": 24,
    "scheme": "topic_date",
    "n_l0": 8,
    "n_l1": 21,
    "l0_sizes": {
      "account_security": 3,
      "integrations_api": 5,
      "billing": 5,
      "data_export": 2,
      "permissions": 1,
      "mobile_app": 2,
      "troubleshooting": 2,
      "general": 4
    }
  },
  "fs_flat": {
    "n_cases": 24,
    "scheme": "flat",
    "n_l0": 1,
    "n_l1": 1,
    "l0_sizes": {
      "all": 24
    }
  }
}
```

## Notes

- `fs_flat` is the sanity baseline: identical retrieval semantics to
  `jsonl_episodic`, just routed through the filesystem layout.  Any
  divergence flags a regression in the L0/L1/L2 wiring.
- `fs_topic_date` uses the L0 (topic) + L1 (YYYY-MM) hierarchy.  In
  the synthetic 76-query corpus the gain over the flat baseline is
  marginal because the BM25 corpus is already small; the layered
  structure shows its value at 1k+ scale (see test_fs_store.py).
- OpenViking self-reports +6.87pp (retail) / +11.87pp (airline) on
  tau2-bench.  Verifying those numbers on this codebase requires
  Exp F — see `scripts/run_stress_test_exp_f_scaffold.py`.
