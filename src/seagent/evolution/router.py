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

VALID_MODES = (MODE_ALL, MODE_TOP_K_RELEVANCE, MODE_CF_WEIGHTED, MODE_CONF_GATED)


@dataclass
class RouterConfig:
    """Tunables for :class:`PlaybookRouter`.

    Attributes
    ----------
    k:
        Default number of tips to keep when a per-call ``k`` is not given.
    low_tau, high_tau:
        Confidence thresholds for ``conf_gated``. ``confidence >= high_tau``
        -> inject nothing (the agent says it already knows). ``confidence >=
        low_tau`` -> halve ``k`` (light scaffolding). Otherwise keep full ``k``.
    cf_floor:
        Tips with Delta_i below this are treated as non-contributing in
        ``cf_weighted``. Defaults to 0.0, i.e. exclude strictly-harmful or
        zero-marginal tips. The default mirrors GEPA-style "only keep
        components that pay rent".
    """

    k: int = 4
    low_tau: float = 0.4
    high_tau: float = 0.8
    cf_floor: float = 0.0


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
        if effective_k <= 0:
            return []

        if mode == MODE_CONF_GATED:
            effective_k = self._gate_k(effective_k, confidence)
            if effective_k == 0:
                return []
            # Inside conf-gated we still want the *most relevant* tips, so we
            # fall through to relevance ranking with the gated k.
            mode = MODE_TOP_K_RELEVANCE

        q_tokens = tokenize(query)

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
    "VALID_MODES",
    "RouterConfig",
    "PlaybookRouter",
]
