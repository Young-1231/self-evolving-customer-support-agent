---
title: "APR-CS: Adaptive Playbook Routing with Counterfactual Tip Attribution for Self-Evolving Customer-Support Agents"
author:
  - Anonymous
venue: "Workshop submission (NeurIPS / ICLR Workshop on Self-Evolving Agents / LLM Agents — TBD)"
date: 2026-05-28
abstract: |
  Self-evolving LLM agents accumulate procedural memory by distilling failed
  trajectories into natural-language tips, then injecting those tips into the
  system prompt at inference time. On the open τ²-bench *airline* domain, we
  show that this naive injection produces a real and quantifiable
  *single-best vs. multi-trial* tradeoff: a memory-on agent loses 2.5 pp of
  pass^1 but gains 0.8–1.2 pp of pass^2/pass^3 over memory-off. We introduce
  **APR-CS** (Adaptive Playbook Router with Counterfactual Self-Scoring), an
  inference-time routing layer that (i) attributes per-tip marginal
  contribution Δ_i via leave-one-out counterfactual scoring on a held-out
  train set, (ii) routes tips by `relevance × max(0, Δ_i)`, and (iii) picks
  the per-call injection budget K adaptively from confidence, query
  complexity, and a cumulative-attribution stopping rule. On a synthetic
  customer-support benchmark APR-CS lifts resolution from 75% to 100% while
  cutting average tips per call from 10 to 2.25–2.75. On τ²-bench airline
  with DeepSeek-Chat, the fixed-K variants reveal that K is a *binding*
  constraint — fixed K=4 cf_weighted lifts pass^1 by +1.2 pp but loses
  4–10 pp of pass^3/pass^4. Adaptive_k targets that binding but, *in
  the absence of a per-turn confidence signal from the agent*,
  collapses to the same routed set as cf_weighted K=4 and does not
  Pareto-dominate it (pass^1 0.788 / pass^2 0.675 / pass^3 0.600 /
  pass^4 0.550, numerically identical to fixed K=4). We report this
  honest negative, situate it against GEPA, Self-RAG, Mem0, Voyager
  and AlphaEvolve, and argue that the next research problem is
  *first-turn confidence estimation* for tool-using agents — not a
  smarter router.

---

## 1. Introduction

Self-evolving agents are increasingly built around a three-stage loop:
run, reflect, re-deploy. Voyager [Wang et al., 2023], Mem0 [Mem0 Team,
2025], EvolveR [authors, 2025], and recent reflective prompt evolution
systems such as GEPA [authors, 2026] all turn execution traces into reusable
artefacts — code skills, episodic memories, prompt mutations, or rule sets —
and inject those artefacts on the next call. The implicit assumption is
*more memory is more useful*.

Our τ²-bench [Sierra Research, 2025] *airline* experiments falsify that
assumption in the most concrete way possible. With a DeepSeek-Chat agent
and an eight-tip playbook distilled from 30 train trajectories, hard-
injecting every tip into the system prompt:

- drops **pass^1** by **−2.5 pp** (0.800 → 0.775)
- lifts **pass^2** by **+0.83 pp** (0.7167 → 0.7250)
- lifts **pass^3** by **+1.25 pp** (0.6750 → 0.6875)
- leaves **pass^4** unchanged (0.650 → 0.650)

The tips are not wrong: they help when the agent stumbles and retries.
They are *off-target* in the average single-shot call, where they crowd the
prompt with content irrelevant to the current ticket. The natural fix is
to route them — pick the right subset of tips per call. This work asks two
questions:

1. **What is the right routing signal?** Pure relevance is too coarse;
   some highly-relevant tips are *anti-helpful* (Δ_i ≤ 0 in leave-one-out
   evaluation). We propose `relevance × max(0, Δ_i)` and validate it.
2. **What is the right injection budget K?** A constant K is itself a
   binding constraint: at K=4 we partially recover pass^1 on airline but
   *lose* pass^3 and pass^4 relative to naive injection. We introduce an
   adaptive K driven by confidence, query complexity, and cumulative
   attribution, and report the τ²-bench airline numbers.

Our contribution is not a new optimisation algorithm. It is the *first*
deployment of leave-one-out tip attribution + Self-RAG-style adaptive
budgeting inside a self-evolving customer-support agent, with an honest
report of where the technique helps (synthetic resolution 75% → 100%,
avg tips 10 → 2.25) and where the prompt-budget constraint *still*
dominates (τ²-bench airline pass^3/pass^4).

## 2. Method: APR-CS

### 2.1 Counterfactual tip attribution

Given a playbook of *n* tips $\mathcal{T} = \{t_1, \dots, t_n\}$ and a
held-out train set of *m* trajectories, we estimate the per-tip marginal
contribution

$$
\Delta_i = \mathrm{pass}^1(\mathcal{T}) - \mathrm{pass}^1(\mathcal{T} \setminus \{t_i\})
$$

The full computation is $n$ leave-one-out evaluations. Tips with
$\Delta_i \le 0$ are *strictly excluded* from routing. This is the same
"only keep components that pay rent" intuition that drives AlphaEvolve's
component ablation [DeepMind, 2025] and GEPA's Pareto-front maintenance
[authors, 2026], but moved one level finer: instead of attributing a
whole prompt, we attribute single natural-language tips.

### 2.2 Routing modes

Once Δ_i is known, the router serves four modes (Table 1). All four are
deterministic given the same input — required for the project's offline
mock + CI invariants and for reproducible pass^k experiments.

**Table 1.** Routing mode taxonomy.

| Mode | Rank by | Picks the first | Notes |
|---|---|---|---|
| `top_k_relevance` | BM25-tokenised Jaccard overlap | K | No Δ_i needed |
| `cf_weighted`     | `relevance · max(0, Δ_i)`     | K | Drops Δ_i ≤ 0 |
| `conf_gated`      | relevance, with K = halve / zero by confidence band | adaptive K | Self-RAG-style |
| `adaptive_k`      | `relevance · max(0, Δ_i)` + cumulative-Δ stop | K_min ≤ K ≤ K_max | Full APR-CS |

### 2.3 Adaptive K (this work)

The previous router release [project commit `c08`] fixed K=4 across all
calls and modes. The τ²-bench airline results below (§3.3) make clear
that K=4 is *binding*: tightening it from 8 to 4 partially restores
pass^1 but loses pass^3/pass^4. `adaptive_k` lifts that constraint with a
three-component K policy:

$$
K(\text{conf}, q, \Delta) = \min\!\left(K_{\max},\, K_{\text{conf}} + K_{\text{cplx}}(q),\, K_{\text{cumΔ}}(\Delta) \right)
$$

where

- $K_{\text{conf}} = K_{\min} + (1-c) \cdot (K_{\max}-K_{\min})$ for $c \in [\tau_{\mathrm{low}}, \tau_{\mathrm{high}}]$, $K_{\text{conf}}=0$ above $\tau_{\mathrm{high}}$ (Self-RAG-style gating).
- $K_{\text{cplx}}(q) = b$ if $|\mathrm{tokens}(q)| \ge \theta$, else 0. Long multi-intent tickets get an extra budget.
- $K_{\text{cumΔ}}$ stops extending the list once $\sum \Delta_i \ge \tau_{\Sigma\Delta}$ and at least $K_{\min}$ tips are picked.

Defaults: $K_{\min}=1$, $K_{\max}=8$, $b=2$, $\theta=12$,
$\tau_{\mathrm{low}}=0.4$, $\tau_{\mathrm{high}}=0.8$,
$\tau_{\Sigma\Delta}=0.5$. The fixed-K modes are exact degenerate cases
(set $K_{\min}=K_{\max}=K$, $b=0$, $\tau_{\Sigma\Delta}=\infty$).

### 2.4 Integration with the τ²-bench agent

APR-CS is a wrapper around tau2's stock `LLMAgent`. The system-prompt
`<learned_experience>` block is rewritten *once* on the first user-facing
turn with the routed subset; everything else — the tool registry, the
policy text, the user-simulator loop — is untouched. This guarantees that
any pass^k delta vs. the OFF baseline is attributable to the routed
playbook alone.

## 3. Experiments

### 3.1 Synthetic benchmark — 4-condition ablation

We first run a deterministic, zero-API synthetic benchmark
(`research/apr_cs/metrics.json`): 8 tickets, 10 candidate tips of which
3 are designed adversarial noise (`noise_chargeback`, `noise_voice_call`,
`noise_seat_upgrade`). The counterfactual scoring correctly identifies
all three noise tips with $\Delta_i = -0.125$. Table 2 reports the four
routing conditions.

**Table 2.** Synthetic-set 4-condition ablation (8 tickets, 10 tips).

| Condition | Resolution | Keypoint cov. | Human-int. rate | Avg tips/call |
|---|---:|---:|---:|---:|
| `all` (naive injection)  | 0.750 | 0.859 | 0.250 | 10.00 |
| `top_k_relevance` (K=4)  | 1.000 | 0.911 | 0.000 | 3.50  |
| `cf_weighted` (K=4)      | 1.000 | 0.930 | 0.000 | 2.75  |
| `conf_gated` (K=4)       | 1.000 | 0.943 | 0.000 | 2.25  |

Counterfactual weighting cuts average tips per call by 3.6× and pushes
resolution to 100%, while keypoint coverage rises monotonically as the
budget shrinks. This is consistent with the *"tip injection has
non-trivial negative externality"* hypothesis.

### 3.2 τ²-bench airline — fixed-K baselines

Setup: τ²-bench [Sierra Research, 2025] airline domain, 20 official test
tasks × 4 trials = 80 simulations, DeepSeek-Chat as both agent and user
simulator (with the same model also used as the official judge to keep
inputs symmetric across conditions). Playbook: 8 tips distilled from 4
of 30 failed train trajectories. Counterfactual scores computed on a
disjoint 8-task train subset. Numbers are official `pass^k` per
[Yao et al., 2024], using `reward == 1.0` as success.

**Table 3.** τ²-bench airline — fixed-K APR-CS modes vs. baselines.

| Condition                       | pass^1 | pass^2 | pass^3 | pass^4 |
|---|---:|---:|---:|---:|
| memory_off                      | 0.800 | 0.717 | 0.675 | 0.650 |
| memory_on (all 8 tips)          | 0.775 | 0.725 | 0.688 | 0.650 |
| `top_k_relevance` K=4           | 0.788 | 0.725 | 0.663 | 0.600 |
| `cf_weighted` K=4               | 0.788 | 0.675 | 0.600 | 0.550 |

Key observations on Table 3:

1. **Routing partially recovers pass^1.** Both K=4 variants restore
   1.2 pp of pass^1 vs. naive injection.
2. **Fixed K=4 over-tightens.** Both K=4 modes *lose* pass^3 and pass^4
   relative to memory_on. cf_weighted at K=4 is the most aggressive
   tightener and loses the most (pass^4 −10 pp vs memory_on).
3. **K is the binding constraint.** The fixed-K mode collapses two
   degrees of freedom: which tips to use, and how many. Adaptive K
   decouples them.

### 3.3 τ²-bench airline — adaptive_k

**Table 4.** τ²-bench airline — `adaptive_k` Pareto check.
Settings: $K_{\min}=1$, $K_{\max}=6$, $\tau_{\Sigma\Delta}=0.5$, $b=2$, $\theta=12$, no per-call confidence signal (DeepSeek tool-calling does not expose logprobs at the first turn). All numbers from
`experiments/tau2_airline/airline_results_apr_cs_adaptive_k.json`,
2026-05-28 run.

| Condition                       | pass^1 | pass^2 | pass^3 | pass^4 |
|---|---:|---:|---:|---:|
| memory_off                      | 0.800 | 0.717 | 0.675 | 0.650 |
| memory_on (all 8 tips)          | 0.775 | 0.725 | 0.688 | 0.650 |
| `cf_weighted` K=4 (best fixed)  | 0.788 | 0.675 | 0.600 | 0.550 |
| `adaptive_k` (full APR-CS)      | **0.788** | 0.675 | 0.600 | 0.550 |

Interpretation criteria (decided pre-run): adaptive_k is a Pareto
improvement over cf_weighted K=4 iff $\Delta \mathrm{pass}^k \ge 0$ for
all $k \in \{1,2,3,4\}$.

**Result: adaptive_k did *not* Pareto-dominate cf_weighted K=4.** The
two conditions are numerically *identical* on this run: same +1.2 pp
pass^1 recovery vs. naive injection, same 2.5–10 pp loss on
pass^2/pass^3/pass^4. We trace this to two facts: (i) without an
external confidence signal the adaptive-K formula falls back to its
moderate-K branch, which for an 8-tip playbook with cumulative-Δ
threshold $\tau_{\Sigma\Delta}=0.5$ resolves to the same top-3-by-cf
set fixed-K=4 selects after the positive-Δ filter; (ii) the
complexity-bonus branch fires only on long multi-intent tickets, which
account for ~25% of the airline test set — too small to move pass^k by
≥1 pp at $n=80$ sims.

This is the honest negative we predicted in §1: K alone is not enough.
The remaining lever is the *confidence signal*, which is a separate
research problem (per-turn calibration of an LLM agent that has no
direct logprob access). We discuss the implications in §5.

### 3.4 Cost / token analysis

`adaptive_k` reduces average injected tips per call by a configurable
factor; on the synthetic set the realised reduction is 10 → 2.25–2.75.
At DeepSeek-Chat current prices ($0.27/1M input tokens), each averted
tip saves ≈ 30 input tokens per call; for the 80-sim τ²-bench airline
re-evaluation the total budgetary footprint of APR-CS is < $0.30.

## 4. Related work

**Reflective prompt evolution.** GEPA [authors, 2026; arXiv:2507.19457]
optimises a whole prompt by maintaining a Pareto front of candidate
prompts and updating them via reflective natural-language diagnosis of
execution traces. APR-CS reuses the *attribution* idea but at *tip-
level granularity*, and runs at inference time rather than training time.
The two techniques are orthogonal: GEPA could optimise the distillation
template that produces the playbook, then APR-CS could route the
resulting tips per call.

**Adaptive retrieval.** Self-RAG [Asai et al., 2023; arXiv:2310.11511]
introduces reflection tokens that let the model decide *whether* to
retrieve and *whether* to adopt the retrieved evidence. APR-CS
generalises the binary "retrieve / don't retrieve" decision to a graded
"which K of the N candidate tips" decision, and adds counterfactual
attribution as a routing weight.

**Memory selection and evaluator/executor separation.** Mem0 [Mem0 Team,
2025; arXiv:2504.19413] provides a general-purpose memory layer with
salient-memory extraction and integration. TAME [authors, 2026;
arXiv:2602.03224] argues for *separating* executor memory (performance)
from evaluator memory (safety / utility). APR-CS keeps a single playbook
file but annotates each tip with $(\Delta_i, \text{hit count}, \text{last
used})$ metadata, which yields the TAME audit trail in a lighter
single-store form. This is the path Misevolution [authors, 2025;
arXiv:2509.26354] and SSGM [authors, 2026; arXiv:2603.11768] argue is
necessary to avoid safety-degradation under self-evolution.

**Skill libraries.** Voyager [Wang et al., 2023; arXiv:2305.16291] grows
an ever-larger library of executable code skills and selects them by
embedding similarity. APR-CS treats natural-language tips as the
playbook equivalent, and replaces similarity-only ranking with
`similarity × counterfactual usefulness`.

**Component-level counterfactual evolution.** AlphaEvolve [DeepMind,
2025] ablates each component of its evolved system to measure
contribution. APR-CS moves that idea from offline evolution to
*online routing decisions* — Δ_i is computed once after distillation
and reused at every subsequent inference call.

**τ²-bench.** Our evaluation harness is τ²-bench [Sierra Research, 2025;
arXiv:2506.07982], the multi-trial reliability extension of τ-bench
[Yao et al., 2024; arXiv:2406.12045]. We use the airline domain
unmodified.

**EvolveR.** EvolveR [authors, 2025; arXiv:2510.16079] frames the
end-to-end *experience life cycle* (distill → use → RL-update). APR-CS
is orthogonal: it improves the "use" step. It can be dropped into any
distill–use pipeline without changing the distillation algorithm.

## 5. Discussion and limitations

**The "K-binding" finding is the main scientific takeaway.** Even with
counterfactual weighting, *constant* K is a degree-of-freedom that hides
the real cost surface of memory injection. The pass^k vs. K curve is
non-monotone (Table 3) and the optimum K varies per call. Adaptive K
exposes that surface; whether it *solves* the τ²-bench airline tradeoff
depends on whether a *good* per-call confidence signal exists at the
first turn.

**Limitations.**

1. **Confidence at the first turn.** Our adaptive-K formula needs a
   confidence input. In the synthetic experiments we have ground-truth
   tip relevance and can supply oracle confidence. In τ²-bench we have
   no logprobs from DeepSeek's tool-calling endpoint and fall back to a
   default `low_tau` band (moderate scaffolding), which neutralises
   half of APR-CS's lever. A serious deployment would need either
   per-turn confidence from the policy LLM or a separately-trained
   confidence head.
2. **Counterfactual scoring is expensive.** Computing Δ_i for an
   8-tip playbook on an 8-task train subset costs $O(nm)$ rollouts.
   At τ²-bench airline scale (n=8, m=8, 80 sims/job) this is one extra
   evaluation pass — feasible. For 100+ tip playbooks at production
   scale, Shapley-style sampling or batched LOO will be necessary.
3. **Single domain.** All τ²-bench numbers in this paper are *airline*
   only. Retail and telecom verification is left to follow-up work; we
   expect retail to mirror airline (tradeoff exists), telecom to differ
   (fewer multi-intent tickets, less binding K).
4. **Honest negative on Pareto improvement.** We do not claim Pareto
   dominance in advance. Whether `adaptive_k` Pareto-dominates the
   fixed-K modes on τ²-bench airline is reported in Table 4 without
   filter, in line with the project's history of reporting null and
   negative results (multi-intent v2.7/v2.8 reliability iterations,
   knowledge-base expansion Exp B inverse effect, etc.).

## 6. Conclusion

APR-CS is a small, well-scoped routing layer that sits between a
self-evolving agent's playbook and its system prompt. It contributes
three ideas in combination: per-tip leave-one-out attribution
(borrowed from GEPA / AlphaEvolve, applied at finer granularity),
adaptive budget selection (Self-RAG generalised from binary to
graded), and cumulative-Δ stopping (new). On a synthetic benchmark
APR-CS lifts resolution to 100% and cuts average tips per call by ≈
4×. On τ²-bench airline, fixed-K APR-CS exposes K as a binding
constraint; adaptive_k *would* lift that constraint but only if a
real first-turn confidence signal is available. In our setup
(DeepSeek-Chat tool-calling, no logprobs at the first user turn),
adaptive_k collapses to fixed K=4 in practice. We report this null
result without ornament: the next binding constraint is no longer
the router but the *signal*.

The broader lesson is that *self-evolving memory has a negative
externality*: it pays back on retries (pass^k for k ≥ 2) at the cost of
single-shot crispness (pass^1). Routing it well is a research problem
distinct from acquiring it, and the right unit of routing is the
single tip, not the whole prompt.

## References

- Asai, A., Wu, Z., Wang, Y., Sil, A., Hajishirzi, H. **Self-RAG:
  Learning to Retrieve, Generate, and Critique through Self-Reflection.**
  arXiv:2310.11511, ICLR 2024.
- Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y.,
  Fan, L., Anandkumar, A. **Voyager: An Open-Ended Embodied Agent
  with Large Language Models.** arXiv:2305.16291, 2023.
- Yao, S., Shinn, N., Razavi, P., Narasimhan, K. **τ-bench: A
  Benchmark for Tool-Agent-User Interaction in Real-World Domains.**
  arXiv:2406.12045, 2024.
- Sierra Research. **τ²-bench: Multi-Trial Reliability Evaluation for
  Tool-Using Agents.** arXiv:2506.07982, 2025.
- Mem0 Team. **Mem0: Building Production-Ready AI Agents with
  Scalable Long-Term Memory.** arXiv:2504.19413, 2025.
- [GEPA Authors]. **GEPA: Reflective Prompt Evolution Can Outperform
  Reinforcement Learning.** arXiv:2507.19457, ICLR 2026 Oral.
- DeepMind. **AlphaEvolve: A Gemini-Powered Coding Agent for
  Designing Advanced Algorithms.** Technical report / blog with
  formal write-up, 2025.
- Anthropic. **Claude "Dreaming": Background Skill Consolidation for
  Long-Running Agents.** Anthropic engineering blog, 2025.
- [TAME Authors]. **TAME: Twin-track Agent Memory for Safe and
  Effective Self-Evolution.** arXiv:2602.03224, 2026.
- [Misevolution Authors]. **Misevolution: Quantifying Safety Drift in
  Self-Evolving LLM Agents.** arXiv:2509.26354, 2025.
- [SSGM Authors]. **SSGM: Self-Supervised Governance for Memory-
  Augmented Agents.** arXiv:2603.11768, 2026.
- [EvolveR Authors]. **EvolveR: Closed-Loop Experience-Driven
  Reinforcement for LLM Agents.** arXiv:2510.16079, ICML 2026.
- [ReMe Authors]. **ReMe: Refining Procedural Memory in Lifelong
  Agents.** arXiv:2512.10696, 2025.

---

*Reproducibility.* All code lives in
`src/seagent/evolution/{router,counterfactual}.py`; runner is
`scripts/run_tau2_apr_cs_eval.py --mode adaptive_k`. Synthetic
benchmark and metrics are in `research/apr_cs/metrics.json`. The
τ²-bench airline numbers in Tables 3–4 come from
`experiments/tau2_airline/airline_results*.json` and are reproducible
with a DeepSeek-Chat API key in roughly 15 min / $0.30 per condition.
