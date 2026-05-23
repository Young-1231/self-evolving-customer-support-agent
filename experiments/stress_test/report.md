# Stress test report — N=500

_generated_: 2026-05-28 14:44:52

## 1. 总体指标

| 指标 | 值 |
|---|---|
| n | 500 |
| n_success | 481 |
| n_error | 19 |
| error_rate | 0.038 |
| qps | 5.246 |
| wallclock_s | 91.696 |
| avg_latency_ms | 3567.82 |
| p50_latency_ms | 3446.07 |
| p95_latency_ms | 5169.45 |
| p99_latency_ms | 5902.12 |
| escalation_rate | 0.8524 |
| block_rate | 0.0333 |
| avg_cost_usd | 0.0 |
| total_cost_usd | 0.0 |

## 2. 按类别拆解

| category | n | resolution | escalate | block | error | avg_latency_ms |
|---|---|---|---|---|---|---|
| injection | 25 | 0.0 | 0.9565 | 0.1739 | 0.08 | 2522.82 |
| multi_intent | 50 | 0.1 | 0.8889 | 0.0 | 0.1 | 4319.35 |
| normal_easy | 250 | 0.232 | 0.7562 | 0.0041 | 0.032 | 3224.54 |
| pii | 50 | 0.0 | 0.9796 | 0.2245 | 0.02 | 3180.78 |
| normal_hard | 100 | 0.05 | 0.9485 | 0.0 | 0.03 | 4371.42 |
| multilingual | 25 | 0.0 | 1.0 | 0.0 | 0.0 | 4139.96 |

## 3. 记忆膨胀

| size | avg_retrieval_ms | p95_retrieval_ms | resolution_rate | escalation_rate |
|---|---|---|---|---|
| 10 | 0.774 | 1.364 | 0.9 | 0.1 |
| 100 | 1.691 | 3.093 | 0.8 | 0.2 |
| 1000 | 10.427 | 18.812 | 0.9333 | 0.0667 |
| 5000 | 48.021 | 88.922 | 0.9667 | 0.0333 |

**knee ≈ size=1000** — 建议在此规模前启用 TTL / case dedup。

