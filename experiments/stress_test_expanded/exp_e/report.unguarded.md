# Exp E — Subagent + Handoff multi-specialist routing (v2.3 R2)

Drop-in MultiAgentOrchestrator replaces SupportAgent at the top level.
Router (1 LLM call/ticket, cached) splits multi_intent tickets into N
focused sub-queries; each is dispatched to a domain specialist that
filters retrieved contexts to its KB topic set.  Merge: per-question
prefixed answer, escalate=any, confidence=min.

## Headline comparison

| Config | escalation | multi_intent res (n) | p50 | n total |
|---|---|---|---|---|
| Original | 85.6% | 8.0% (50) | 3695ms | 500 |
| Exp B | 92.0% | 0.0% (47) | 4102ms | 500 |
| Exp C | 93.0% | 9.1% (44) | 168ms | 485 |
| Exp D | 67.2% | 0.0% (47) | 5099ms | 500 |
| Exp E | 36.6% | 46.8% (47) | 4820ms | 500 |

## Router stats (Exp E)

- LLM calls: 500
- cache hits: 0
- parse failures: 0
- wallclock: 154.7s
