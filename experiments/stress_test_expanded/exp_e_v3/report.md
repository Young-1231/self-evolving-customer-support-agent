# Exp E_v3 — v2.8 per-sub aggregated guardrail

Third iteration on the multi_intent guardrail design.
Specialists run in ``mode='core'`` (raw sub-answer).  Orchestrator
runs ``GuardrailPipeline.check_output`` **per (sub_answer, sub_contexts)**
and aggregates:

  - groundedness: any-supported = supported (the bundle is grounded if
    at least one sub is fully supported by its own contexts).
  - PII: per-sub redaction, merged answer stitched from per-sub
    ``redacted_answer`` so surface PII can't accumulate.
  - policy: ANY BLOCK -> BLOCK; else ANY REWRITE -> REWRITE; else ALLOW.
  - escalate: majority vote (> 50% subs voting ESCALATE) OR any BLOCK
    OR groundedness failure (no sub supported).

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

## v2.8 success criterion

- multi_intent res >= 30%: **FAIL** (0.0%)
- overall escalation <= 60%: **PASS** (37.6%)
- overall block_rate > 0% (safety preserved): **PASS** (13.2%)
- overall: **FAIL**

## Router stats (Exp E_v3)

- LLM calls: 500
- cache hits: 0
- parse failures: 0
- wallclock: 139.0s
