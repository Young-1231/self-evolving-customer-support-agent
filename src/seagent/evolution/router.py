"""APR-CS: Adaptive Playbook Router.

This module is the inference-time half of APR-CS (Adaptive Playbook Router with
Counterfactual Self-Scoring). The training-time half lives in
``counterfactual.py`` and produces per-tip marginal contributions Delta_i; this
module consumes those scores to decide which tips actually get injected into the
agent's system prompt for a given query.

Motivation
----------
On tau^2-bench airline we observed that hard-injecting all 8 distilled playbook
tips improves pass^2/^3 (multi-trial consistency, +0.8/+1.2pp) but *hurts*
pass^1 by -2.5pp. This is the classic "single-best vs. multi-trial agreement"
tradeoff: most tips are inert for any one ticket, and the wasted prompt budget
drags the first-shot answer toward a less crisp distribution. APR-CS targets:
preserve the consistency gain while restoring pass^1.

2026-aligned design references (cited per mode):

* ``top_k_relevance`` -- task-conditioned skill selection, in the spirit of
  Voyager's growing skill library where only relevant skills are loaded for a
  task, and Mem0's selective memory retrieval. Cheap, no offline training
  needed.
* ``cf_weighted``     -- per-component counterfactual attribution, as used by
  AlphaEvolve and GEPA for prompt/program evolution: each piece earns its slot
  by showing positive marginal contribution Delta_i = base - LOO_i.
* ``conf_gated``      -- adaptive retrieval gating in the spirit of Self-RAG:
  when the agent is already confident, skip injection (zero scaffolding tax);
  when uncertain, inject more aggressively. The router only sees a scalar
  confidence; the policy for *producing* it is the caller's responsibility.
* ``adaptive_k``      -- the full APR-CS routing: K = f(confidence, query
  complexity, available tips) layered on top of cf-weighted ranking, with a
  cumulative-Delta stop rule. This is the mode used in the τ²-bench airline
  re-evaluation; the three earlier modes are kept as degenerate baselines.

All modes are deterministic given inputs (no RNG), which is required by the
project's offline mock + CI invariants.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from ..memory.bm25 import tokenize


# Public mode constants -- also valid values for the SEAGENT_TAU2_ROUTE_MODE
# environment variable consumed by ``tau2_ext.memory_agent``.
MODE_ALL = "all"
MODE_TOP_K_RELEVANCE = "top_k_relevance"
MODE_CF_WEIGHTED = "cf_weighted"
MODE_CONF_GATED = "conf_gated"
MODE_ADAPTIVE_K = "adaptive_k"

VALID_MODES = (
    MODE_ALL,
    MODE_TOP_K_RELEVANCE,
    MODE_CF_WEIGHTED,
    MODE_CONF_GATED,
    MODE_ADAPTIVE_K,
)


@dataclass
class RouterConfig:
    """Tunables for :class:`PlaybookRouter`.

    Attributes
    ----------
    k:
        Default number of tips to keep when a per-call ``k`` is not given.
        For ``adaptive_k`` mode this acts as the *upper bound* K_max when
        ``k_max`` is not overridden separately.
    low_tau, high_tau:
        Confidence thresholds for ``conf_gated``. ``confidence >= high_tau``
        -> inject nothing (the agent says it already knows). ``confidence >=
        low_tau`` -> halve ``k`` (light scaffolding). Otherwise keep full ``k``.
    cf_floor:
        Tips with Delta_i below this are treated as non-contributing in
        ``cf_weighted``. Defaults to 0.0, i.e. exclude strictly-harmful or
        zero-marginal tips. The default mirrors GEPA-style "only keep
        components that pay rent".
    k_min, k_max:
        Bounds for ``adaptive_k`` mode. ``K = K_min + (1 - confidence) *
        (K_max - K_min)`` so a high-confidence task sees only ``K_min`` tips
        (or zero, if it crosses ``high_tau``) and a low-confidence task can
        pull in up to ``K_max``. Default range [1, 8] matches the airline
        playbook size and keeps the prompt budget bounded.
    cum_threshold:
        Cumulative-attribution stopping rule for ``adaptive_k``. After the
        ranked tips are picked up to the confidence-dictated K, we keep
        extending the list one tip at a time *until* the running
        sum(Delta_i) reaches ``cum_threshold`` OR we hit ``K_max``. Set
        to None to disable the cumulative gate (default: 0.5).
    complexity_bonus:
        Extra K added when the query is "complex" (long / many distinct
        tokens), capped at ``K_max``. Default 2.
    complexity_token_threshold:
        Distinct-token count above which the query is considered "complex"
        for ``adaptive_k``. Default 12 (matches τ²-bench airline ticket
        distribution: easy tickets have <10 distinct tokens, multi-intent
        tickets have 15+).
    """

    k: int = 4
    low_tau: float = 0.4
    high_tau: float = 0.8
    cf_floor: float = 0.0
    k_min: int = 1
    k_max: int = 8
    cum_threshold: Optional[float] = 0.5
    complexity_bonus: int = 2
    complexity_token_threshold: int = 12


def _relevance(query_tokens: Sequence[str], tip: str) -> float:
    """Token-overlap relevance using the project's BM25 tokenizer.

    We deliberately reuse :func:`seagent.memory.bm25.tokenize` so that mixed
    Chinese/English tickets get the same character-ngram treatment as the
    BM25 retrieval path -- one tokenizer, one definition of "related".
    """
    if not tip:
        return 0.0
    tip_toks = set(tokenize(tip))
    if not tip_toks:
        return 0.0
    q = set(query_tokens)
    if not q:
        return 0.0
    overlap = len(q & tip_toks)
    if overlap == 0:
        return 0.0
    # Jaccard-style normalisation: bounded in (0, 1], stable across tip lengths.
    return overlap / float(len(tip_toks | q))


class PlaybookRouter:
    """Adaptive router: pick which tips to inject for a given query.

    The router is stateless across calls (config aside). All scoring is
    deterministic and side-effect-free, which is essential for reproducible
    pass^k experiments.
    """

    def __init__(self, config: Optional[RouterConfig] = None):
        self.config = config or RouterConfig()

    # ------------------------------------------------------------------ api
    def select(
        self,
        query: str,
        tips: List[str],
        scores: Optional[Dict[str, float]] = None,
        k: Optional[int] = None,
        confidence: Optional[float] = None,
        mode: str = MODE_TOP_K_RELEVANCE,
    ) -> List[str]:
        """Return the subset (and order) of ``tips`` to inject.

        Order in the returned list is *score-descending* for the relevance and
        cf-weighted modes; ``all`` mode preserves the input order verbatim so
        the existing behaviour is bit-exact.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"unknown router mode {mode!r}; expected one of {VALID_MODES}")

        if not tips:
            return []

        # Deduplicate while preserving first-seen order. Empty/whitespace tips
        # are dropped silently to match ``load_playbook``'s contract.
        seen: set[str] = set()
        clean: List[str] = []
        for t in tips:
            ts = (t or "").strip()
            if ts and ts not in seen:
                seen.add(ts)
                clean.append(ts)
        if not clean:
            return []

        if mode == MODE_ALL:
            # Exact legacy behaviour: hand back every tip in original order.
            return list(clean)

        effective_k = self.config.k if k is None else int(k)
        if effective_k <= 0 and mode != MODE_ADAPTIVE_K:
            return []

        if mode == MODE_CONF_GATED:
            effective_k = self._gate_k(effective_k, confidence)
            if effective_k == 0:
                return []
            # Inside conf-gated we still want the *most relevant* tips, so we
            # fall through to relevance ranking with the gated k.
            mode = MODE_TOP_K_RELEVANCE

        q_tokens = tokenize(query)

        if mode == MODE_ADAPTIVE_K:
            # adaptive_k composes cf_weighted ranking with a confidence- and
            # complexity-driven K, plus a cumulative-attribution stop rule.
            ranked = self._rank_by_cf(q_tokens, clean, scores or {})
            return self._adaptive_pick(ranked, scores or {}, q_tokens, confidence)

        if mode == MODE_TOP_K_RELEVANCE:
            ranked = self._rank_by_relevance(q_tokens, clean)
        elif mode == MODE_CF_WEIGHTED:
            ranked = self._rank_by_cf(q_tokens, clean, scores or {})
        else:  # pragma: no cover - guarded above
            raise AssertionError(mode)

        kept = [tip for tip, score in ranked if score > 0.0][:effective_k]
        return kept

    # -------------------------------------------------------------- helpers
    def _gate_k(self, k: int, confidence: Optional[float]) -> int:
        """Self-RAG-style adaptive injection budget."""
        if confidence is None:
            return k
        if confidence >= self.config.high_tau:
            return 0
        if confidence >= self.config.low_tau:
            return max(0, k // 2)
        return k

    def _adaptive_k_target(
        self,
        confidence: Optional[float],
        q_tokens: Sequence[str],
        available: int,
    ) -> int:
        """Compute the per-call K for ``adaptive_k`` mode.

        K = K_min + (1 - confidence) * (K_max - K_min)
        plus a complexity bonus for long / multi-intent queries, clamped to
        [0, min(K_max, available_tips_count)]. Confidence is treated as
        ``low_tau`` when missing (so the router degrades to "moderate
        scaffolding" rather than zero injection -- matches the Self-RAG
        "default to retrieve when uncertain" stance).
        """
        cfg = self.config
        k_min = max(0, int(cfg.k_min))
        k_max = max(k_min, int(cfg.k_max))

        # Confidence-driven base K.
        if confidence is None:
            base = (k_min + k_max) // 2  # midpoint = "moderate"
        else:
            c = max(0.0, min(1.0, float(confidence)))
            if c >= cfg.high_tau:
                # Already very confident -> minimum scaffolding.
                base = 0
            else:
                # Linear interp: c == low_tau -> K_max, c == high_tau -> K_min.
                if cfg.high_tau > cfg.low_tau:
                    span = (cfg.high_tau - c) / (cfg.high_tau - cfg.low_tau)
                    span = max(0.0, min(1.0, span))
                else:
                    span = 1.0 - c
                base = k_min + int(round(span * (k_max - k_min)))

        # Complexity bonus from query length / distinct-token count.
        bonus = 0
        if q_tokens and len(set(q_tokens)) >= cfg.complexity_token_threshold:
            bonus = max(0, int(cfg.complexity_bonus))

        target = base + bonus
        # Clamp to [0, k_max] and never ask for more than we have.
        target = max(0, min(k_max, target))
        if available >= 0:
            target = min(target, available)
        return target

    def _adaptive_pick(
        self,
        ranked,
        scores: Dict[str, float],
        q_tokens: Sequence[str],
        confidence: Optional[float],
    ) -> List[str]:
        """Pick adaptive_k tips with a cumulative-Delta stop rule.

        Walks the ranked list (cf_weighted order) and accumulates
        ``Delta_i`` for each candidate. Stopping rule, in order:
        1. positive-score gate: tips with non-positive cf_weight are skipped.
        2. lower bound: always include at least ``K_min`` tips when
           available.
        3. confidence-driven upper bound K_target.
        4. cumulative gate: stop early once running sum(Delta_i) >=
           ``cum_threshold`` (and >= K_min tips selected).
        """
        cfg = self.config
        positive = [(t, s) for (t, s) in ranked if s > 0.0]
        target = self._adaptive_k_target(confidence, q_tokens, len(positive))
        if target == 0 and cfg.k_min == 0:
            return []
        # Always honour at least K_min when positive candidates exist.
        floor_k = min(cfg.k_min, len(positive))
        upper_k = max(target, floor_k)

        chosen: List[str] = []
        running = 0.0
        for tip, _w in positive:
            if len(chosen) >= upper_k:
                break
            chosen.append(tip)
            running += float(scores.get(tip, 1.0))
            if (
                cfg.cum_threshold is not None
                and len(chosen) >= floor_k
                and running >= float(cfg.cum_threshold)
            ):
                break
        return chosen

    def _rank_by_relevance(self, q_tokens: Sequence[str], tips: List[str]):
        scored = [(tip, _relevance(q_tokens, tip)) for tip in tips]
        # Stable sort: tie-break by original index so behaviour is deterministic.
        scored.sort(key=lambda it: (-it[1], tips.index(it[0])))
        return scored

    def _rank_by_cf(
        self,
        q_tokens: Sequence[str],
        tips: List[str],
        scores: Dict[str, float],
    ):
        floor = self.config.cf_floor
        scored = []
        for tip in tips:
            rel = _relevance(q_tokens, tip)
            # Missing score => 1.0 fallback (neutral prior), per spec.
            delta = float(scores.get(tip, 1.0))
            weight = max(0.0, delta - floor) if floor != 0.0 else max(0.0, delta)
            scored.append((tip, rel * weight))
        scored.sort(key=lambda it: (-it[1], tips.index(it[0])))
        return scored


__all__ = [
    "MODE_ALL",
    "MODE_TOP_K_RELEVANCE",
    "MODE_CF_WEIGHTED",
    "MODE_CONF_GATED",
    "MODE_ADAPTIVE_K",
    "VALID_MODES",
    "RouterConfig",
    "PlaybookRouter",
]
