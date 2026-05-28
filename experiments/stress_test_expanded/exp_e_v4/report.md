# Exp E_v4 — v2.9 policy regex fix

Final iteration on the multi_intent guardrail design.  Identical
config to Exp E_v3 (specialists mode='core', per-sub aggregated
guardrail), with ONE source change:

``src/seagent/guardrails/policy.py`` now uses a context-aware money
detector that masks order-ID-like spans first and requires a strict
currency token (``$``/``¥``/``RMB``/``元``/...) before matching a
number as an amount.  This closes the multi_intent 0% loop seen in
Exp E_v3 where ``订单号 #38294`` was misclassified as a $38,294
refund commitment.

## Headline comparison

| Config | escalation | block | multi_intent res (n) | p50 | n total |
|---|---|---|---|---|---|
| Original | 85.6% | 2.7% | 8.0% (50) | 3695ms | 500 |
| Exp B | 92.0% | 19.8% | 0.0% (47) | 4102ms | 500 |
| Exp C | 93.0% | 19.0% | 9.1% (44) | 168ms | 485 |
| Exp D | 67.2% | 19.0% | 0.0% (47) | 5099ms | 500 |
| Exp E (core) | 36.6% | 0.0% | 46.8% (47) | 4820ms | 500 |
| Exp E (observed) | 85.2% | 8.0% | 0.0% (47) | 5142ms | 500 |
| Exp E_v2 (merged) | 45.0% | 15.2% | 0.0% (47) | 3736ms | 500 |
| Exp E_v3 (per_sub_agg) | 37.6% | 13.2% | 0.0% (47) | 3794ms | 500 |
| Exp E_v4 (v2.9 regex) | 33.2% | 0.6% | 55.3% (47) | 4690ms | 500 |

## v2.9 success criterion

- multi_intent res >= 30%: **PASS** (55.3%)
- overall escalation <= 60%: **PASS** (33.2%)
- overall block_rate > 0% (safety preserved): **PASS** (0.6%)
- overall: **PASS**

## Safety-regression sanity check

- pii category block_rate: 0.0% (n=48)
- injection category block_rate: 20.0% (n=10)

## Router stats (Exp E_v4)

- LLM calls: 500
- cache hits: 0
- parse failures: 0
- wallclock: 182.1s
