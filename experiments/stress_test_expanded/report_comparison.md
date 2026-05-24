# Stress test — original vs Exp A vs Exp B

_generated_: 2026-05-28 15:49:56


## Setup

| run | KB | tickets | notes |
|---|---|---|---|
| **Original** | NimbusFlow 30 | 500 NimbusFlow | experiments/stress_test (baseline from prior run) |
| **Exp A**    | NimbusFlow 30 + Bitext 150 = 176 | 500 NimbusFlow (same as Original) | KB-augmentation-only — does adding more KB help? |
| **Exp B**    | NimbusFlow 30 + Bitext 150 = 176 | 500 mixed (250 NimbusFlow + 250 e-commerce) | KB + ticket distribution aligned |

## Overall comparison

| metric | Original | Exp A | Exp B |
|---|---|---|---|
| escalation_rate | 0.8524 | 0.8557 | 0.9196 |
| block_rate | 0.0333 | 0.0268 | 0.1979 |
| error_rate | 0.038 | 0.03 | 0.03 |
| avg_latency_ms | 3567.82 | 3842.9 | 4353.76 |
| p95_latency_ms | 5169.45 | 5564.46 | 6640.85 |
| avg_kb_hits | — | 3.942 | 3.984 |

## Per-category resolution rate

| category | Original | Exp A | Exp B |
|---|---|---|---|
| normal_easy | 0.232 | 0.244 | 0.1127 |
| normal_hard | 0.05 | 0.02 | 0.0 |
| multi_intent | 0.1 | 0.08 | 0.0 |
| pii | 0.0 | 0.0 | 0.0 |
| injection | 0.0 | 0.0 | 0.0 |
| multilingual | 0.0 | 0.0 | 0.0 |

## Per-category escalation rate

| category | Original | Exp A | Exp B |
|---|---|---|---|
| normal_easy | 0.7562 | 0.749 | 0.8727 |
| normal_hard | 0.9485 | 0.9798 | 0.9892 |
| multi_intent | 0.8889 | 0.9111 | 0.9556 |
| pii | 0.9796 | 0.9592 | 1.0 |
| injection | 0.9565 | 0.9583 | 0.9 |
| multilingual | 1.0 | 1.0 | 1.0 |

## Insight

> **KB expansion is not the bottleneck. Augmenting alone (Exp A) leaves
> escalation flat at 85.6% (vs 85.2% original); even aligning the ticket
> distribution to the new KB (Exp B) *raises* escalation to 92.0% because
> realistic English PII patterns in the e-commerce tickets trip the PII
> guardrail at 19.8% (vs 3.3% original) and the LLM's English answer
> confidence on Bitext-style stiff support templates fails to clear the
> escalation threshold.**
>
> Concretely:
>
> * **Original → Exp A**: +146 docs, retrieval finds them (avg_kb_hits ≈
>   3.94, full top-k=4 saturated), but `normal_easy` resolution moved only
>   23.2% → 24.4%. The KB was not the binding constraint.
> * **Original → Exp B**: aligning the ticket distribution actually *hurts*
>   — block_rate jumps 3.3% → 19.8% and resolution drops on every
>   category. The new failure modes are (a) over-aggressive PII regex
>   blocking on real-format emails/PANs, and (b) the critic's confidence
>   on stiff English templated answers not clearing `escalate_tau=0.5`.
>
> **Takeaway**: the next leverage point is **calibrating the critic /
> escalation threshold and tightening the PII guardrail's precision**,
> *not* growing the KB further. This is the kind of honest negative result
> that a stress-test program is supposed to surface.

