# Stress test report — Exp B: KB + tickets aligned

_generated_: 2026-05-28 15:48:55

_kb_docs_: **176**

## Overall

| metric | value |
|---|---|
| n | 500 |
| n_success | 485 |
| n_error | 15 |
| error_rate | 0.03 |
| qps | 3.512 |
| wallclock_s | 138.1 |
| avg_latency_ms | 4353.76 |
| p50_latency_ms | 4102.2 |
| p95_latency_ms | 6640.85 |
| p99_latency_ms | 7999.56 |
| escalation_rate | 0.9196 |
| block_rate | 0.1979 |
| avg_kb_hits | 3.984 |

## Per-category breakdown

| category | n | resolution | escalate | block | error | avg_latency_ms |
|---|---|---|---|---|---|---|
| multi_intent | 47 | 0.0 | 0.9556 | 0.5111 | 0.0426 | 5043.9 |
| normal_easy | 284 | 0.1127 | 0.8727 | 0.0109 | 0.0317 | 3879.29 |
| pii | 48 | 0.0 | 1.0 | 0.5957 | 0.0208 | 4033.47 |
| multilingual | 15 | 0.0 | 1.0 | 0.0 | 0.0 | 4527.38 |
| normal_hard | 96 | 0.0 | 0.9892 | 0.4301 | 0.0312 | 5725.27 |
| injection | 10 | 0.0 | 0.9 | 0.2 | 0.0 | 2786.13 |
