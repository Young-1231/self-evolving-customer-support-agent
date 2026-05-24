# Stress test report — Exp A: KB augmentation only

_generated_: 2026-05-28 15:44:25

_kb_docs_: **176**

## Overall

| metric | value |
|---|---|
| n | 500 |
| n_success | 485 |
| n_error | 15 |
| error_rate | 0.03 |
| qps | 3.921 |
| wallclock_s | 123.693 |
| avg_latency_ms | 3842.9 |
| p50_latency_ms | 3694.8 |
| p95_latency_ms | 5564.46 |
| p99_latency_ms | 6325.62 |
| escalation_rate | 0.8557 |
| block_rate | 0.0268 |
| avg_kb_hits | 3.942 |

## Per-category breakdown

| category | n | resolution | escalate | block | error | avg_latency_ms |
|---|---|---|---|---|---|---|
| injection | 25 | 0.0 | 0.9583 | 0.1667 | 0.04 | 2688.14 |
| multi_intent | 50 | 0.08 | 0.9111 | 0.0 | 0.1 | 4439.99 |
| normal_easy | 250 | 0.244 | 0.749 | 0.0 | 0.028 | 3479.34 |
| pii | 50 | 0.0 | 0.9592 | 0.1837 | 0.02 | 3619.09 |
| normal_hard | 100 | 0.02 | 0.9798 | 0.0 | 0.01 | 4650.75 |
| multilingual | 25 | 0.0 | 1.0 | 0.0 | 0.0 | 4650.1 |
