# Exp E_v2 — v2.7 merged-answer guardrail

Architecture fix for the Exp E observed-mode collapse.
Specialists run in ``mode='core'`` (raw sub-answer, no guardrail).
Orchestrator merges sub-answers, then runs **ONE** ``check_output``
on the merged text + union(contexts).  Input guardrail also runs once.

## Headline comparison

| Config | escalation | multi_intent res (n) | p50 | n total |
|---|---|---|---|---|
| Original | 85.6% | 8.0% (50) | 3695ms | 500 |
| Exp B | 92.0% | 0.0% (47) | 4102ms | 500 |
| Exp C | 93.0% | 9.1% (44) | 168ms | 485 |
| Exp D | 67.2% | 0.0% (47) | 5099ms | 500 |
| Exp E (core) | 36.6% | 46.8% (47) | 4820ms | 500 |
| Exp E (observed) | 85.2% | 0.0% (47) | 5142ms | 500 |
| Exp E_v2 (merged) | 45.0% | 0.0% (47) | 3736ms | 500 |

## v2.7 success criterion

- multi_intent res > 30%: **FAIL** (0.0%)
- overall escalation < 75%: **PASS** (45.0%)
- overall: **FAIL**

## Router stats (Exp E_v2)

- LLM calls: 500
- cache hits: 0
- parse failures: 0
- wallclock: 123.5s
