"""APR-CS: Counterfactual self-scoring for playbook tips.

This is the *training-time* component of APR-CS. Given a set of distilled
playbook tips, we estimate each tip's marginal contribution Delta_i by
leave-one-out evaluation:

    base   = eval_fn(all tips)
    LOO_i  = eval_fn(all tips except tip_i)
    Delta_i = base - LOO_i

A positive Delta_i means dropping the tip *hurts*, i.e. the tip is paying its
prompt-budget rent on the eval distribution. A non-positive Delta_i means the
tip is inert or actively harmful and should be down-weighted at inference time
by the :class:`PlaybookRouter`.

The design is intentionally agent-agnostic: ``eval_fn`` is an injected callable
that maps ``active_tips -> {metric: float}``. The caller wires it -- it might
run the agent on a synthetic NimbusFlow batch, on a tau^2-bench subset, or on
any other evaluation harness. This module never imports tau2 directly, so it
stays in the zero-dependency core.

2026 lineage
------------
The counterfactual / leave-one-out attribution pattern is the same one used by
AlphaEvolve and GEPA to credit-assign across components of an evolved program
or prompt. We adopt it at the level of *individual playbook tips* (the unit at
which the Reflector emits guidance), which is the smallest auditable unit in
our procedural memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple


EvalFn = Callable[[List[str]], Dict[str, float]]


@dataclass
class CounterfactualReport:
    """Structured result of a leave-one-out attribution sweep."""

    baseline_metric: str
    base: float
    off: float  # all-OFF sanity anchor: eval_fn([])
    scores: Dict[str, float] = field(default_factory=dict)
    loo_metrics: Dict[str, float] = field(default_factory=dict)

    def ranked(self) -> List[Tuple[str, float]]:
        """Tips sorted by Delta_i descending (most useful first)."""
        return sorted(self.scores.items(), key=lambda kv: -kv[1])

    def positive_tips(self) -> List[str]:
        """Tips with strictly positive marginal contribution."""
        return [t for t, d in self.ranked() if d > 0.0]


class CounterfactualEvaluator:
    """Compute Delta_i for each tip via leave-one-out evaluation.

    Parameters
    ----------
    eval_fn:
        Callable ``active_tips -> {metric_name: float}``. Must be deterministic
        for a given input (or already aggregated over enough trials), otherwise
        Delta_i is noisy.
    cache:
        When ``True`` (default), memoise ``eval_fn`` on the *frozen* set of
        active tips, so swapping back to a previously-seen configuration costs
        zero extra calls. Useful when running multiple metrics over the same
        playbook, or when the evaluator is re-invoked after a small edit.
    """

    def __init__(self, eval_fn: EvalFn, cache: bool = True):
        self._eval_fn = eval_fn
        self._use_cache = cache
        self._cache: Dict[frozenset, Dict[str, float]] = {}

    # --------------------------------------------------------------- core
    def _eval(self, active: Sequence[str]) -> Dict[str, float]:
        key = frozenset(active)
        if self._use_cache and key in self._cache:
            return self._cache[key]
        result = dict(self._eval_fn(list(active)))
        if self._use_cache:
            self._cache[key] = result
        return result

    def score_tips(
        self,
        tips: List[str],
        eval_fn: Optional[EvalFn] = None,
        baseline_metric: str = "pass^1",
    ) -> CounterfactualReport:
        """Run the leave-one-out sweep.

        ``eval_fn`` overrides the constructor-supplied callable for this call
        only (cache is bypassed in that case to avoid cross-contamination).
        """
        if eval_fn is not None:
            # Temporarily swap eval_fn; do not pollute the cache.
            prev_fn, prev_cache = self._eval_fn, self._cache
            self._eval_fn = eval_fn
            self._cache = {}
            try:
                return self._score_tips_inner(tips, baseline_metric)
            finally:
                self._eval_fn = prev_fn
                self._cache = prev_cache
        return self._score_tips_inner(tips, baseline_metric)

    def _score_tips_inner(self, tips: List[str], baseline_metric: str) -> CounterfactualReport:
        # Deduplicate while preserving order; mirrors PlaybookRouter contract.
        seen: set[str] = set()
        clean: List[str] = []
        for t in tips:
            ts = (t or "").strip()
            if ts and ts not in seen:
                seen.add(ts)
                clean.append(ts)

        base_metrics = self._eval(clean)
        off_metrics = self._eval([])
        if baseline_metric not in base_metrics:
            raise KeyError(
                f"eval_fn did not return baseline_metric={baseline_metric!r}; "
                f"got keys={sorted(base_metrics)}"
            )
        if baseline_metric not in off_metrics:
            raise KeyError(
                f"eval_fn(off) did not return baseline_metric={baseline_metric!r}; "
                f"got keys={sorted(off_metrics)}"
            )

        base_val = float(base_metrics[baseline_metric])
        off_val = float(off_metrics[baseline_metric])

        scores: Dict[str, float] = {}
        loos: Dict[str, float] = {}
        for i, tip in enumerate(clean):
            loo = clean[:i] + clean[i + 1 :]
            m = self._eval(loo)
            loo_val = float(m.get(baseline_metric, 0.0))
            loos[tip] = loo_val
            scores[tip] = base_val - loo_val

        return CounterfactualReport(
            baseline_metric=baseline_metric,
            base=base_val,
            off=off_val,
            scores=scores,
            loo_metrics=loos,
        )

    # ----------------------------------------------------------- utilities
    def clear_cache(self) -> None:
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)


__all__ = ["CounterfactualEvaluator", "CounterfactualReport", "EvalFn"]
