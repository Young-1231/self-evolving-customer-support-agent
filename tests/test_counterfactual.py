"""Tests for the APR-CS CounterfactualEvaluator."""
from __future__ import annotations

from seagent.evolution.counterfactual import CounterfactualEvaluator


def _make_synthetic_eval_fn(weights, base=0.5, noise=0.0):
    """Build a deterministic eval_fn whose metric is the sum of per-tip weights.

    weights: dict[tip -> Delta_i_truth]. The metric is base + sum of active
    weights, so leave-one-out recovers exactly each tip's weight. We also keep
    a call counter to test caching.
    """
    state = {"calls": 0}

    def eval_fn(active):
        state["calls"] += 1
        score = base + sum(weights.get(t, 0.0) for t in active)
        return {"pass^1": score, "pass^2": score + 0.05}

    return eval_fn, state


def test_score_tips_recovers_known_contributions():
    weights = {"a": 0.10, "b": -0.05, "c": 0.20}
    eval_fn, _ = _make_synthetic_eval_fn(weights)
    ev = CounterfactualEvaluator(eval_fn)
    report = ev.score_tips(["a", "b", "c"], baseline_metric="pass^1")

    # Within floating-point tolerance, Delta_i must equal the synthetic weight.
    for tip, truth in weights.items():
        assert abs(report.scores[tip] - truth) < 1e-9, (tip, report.scores[tip], truth)

    # Ranked() puts the most contributing tip first.
    ranked_tips = [t for t, _ in report.ranked()]
    assert ranked_tips == ["c", "a", "b"]

    # positive_tips() drops the harmful tip.
    assert report.positive_tips() == ["c", "a"]


def test_off_anchor_matches_baseline_minus_sum_of_deltas():
    weights = {"x": 0.10, "y": 0.10, "z": -0.05}
    eval_fn, _ = _make_synthetic_eval_fn(weights, base=0.40)
    ev = CounterfactualEvaluator(eval_fn)
    rep = ev.score_tips(list(weights), baseline_metric="pass^1")
    # base(all) - sum(Delta_i) should approximate eval(off) up to interaction
    # terms; for the linear synthetic model interactions are zero.
    expected_off = rep.base - sum(rep.scores.values())
    assert abs(rep.off - expected_off) < 1e-9


def test_cache_avoids_redundant_calls():
    weights = {"a": 0.1, "b": 0.2}
    eval_fn, state = _make_synthetic_eval_fn(weights)
    ev = CounterfactualEvaluator(eval_fn, cache=True)
    ev.score_tips(["a", "b"], baseline_metric="pass^1")
    first = state["calls"]
    # Second call with identical input should re-use cache entries entirely.
    ev.score_tips(["a", "b"], baseline_metric="pass^1")
    second = state["calls"] - first
    assert second == 0, f"cache miss: {second} extra calls"


def test_missing_baseline_metric_raises():
    eval_fn, _ = _make_synthetic_eval_fn({"a": 0.1})
    ev = CounterfactualEvaluator(eval_fn)
    raised = False
    try:
        ev.score_tips(["a"], baseline_metric="not_a_metric")
    except KeyError:
        raised = True
    assert raised


def test_dedup_and_strip_in_tips():
    weights = {"hello": 0.1, "world": 0.2}
    eval_fn, _ = _make_synthetic_eval_fn(weights)
    ev = CounterfactualEvaluator(eval_fn)
    rep = ev.score_tips(["hello", "  hello  ", "", "world"], baseline_metric="pass^1")
    assert set(rep.scores.keys()) == {"hello", "world"}


def test_override_eval_fn_does_not_pollute_cache():
    weights_a = {"a": 0.1, "b": 0.2}
    eval_fn_a, _ = _make_synthetic_eval_fn(weights_a)
    ev = CounterfactualEvaluator(eval_fn_a, cache=True)
    ev.score_tips(["a", "b"], baseline_metric="pass^1")
    size_after_first = ev.cache_size

    weights_b = {"a": 0.9, "b": 0.9}
    eval_fn_b, _ = _make_synthetic_eval_fn(weights_b)
    rep = ev.score_tips(["a", "b"], eval_fn=eval_fn_b, baseline_metric="pass^1")
    # The override should reflect weights_b, not weights_a.
    assert abs(rep.scores["a"] - 0.9) < 1e-9
    # And the persistent cache must be unchanged.
    assert ev.cache_size == size_after_first
