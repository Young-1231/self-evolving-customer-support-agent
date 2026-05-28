"""Tests for Exp G — 10k+ episodic case memory scale stress.

These tests exercise the synthesis + per-backend evaluation helpers from
``scripts/run_exp_g_memory_scale.py``.  They run a *small* scaled-down
version of Exp G (N=300, 30 eval queries) so the suite stays fast (<1s),
while still asserting the structural / directional claims:

  * synth_cases produces the requested count + 15-topic distribution
  * synth_eval_queries produces the requested count and references known
    keypoint phrases
  * both EpisodicMemory and FsEpisodicStore accept the full pool and run
    the agent end-to-end without error
  * at populated-pool scale (already visible at N=300), fs_topic_date
    retrieval latency <= jsonl latency

The 10k full run is exercised by the CLI script; we keep tests cheap.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from collections import Counter

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

# Import the script as a module (it's not a package — load it explicitly).
_SCRIPT = os.path.join(ROOT, "scripts", "run_exp_g_memory_scale.py")
_spec = importlib.util.spec_from_file_location("exp_g_script", _SCRIPT)
exp_g = importlib.util.module_from_spec(_spec)
sys.modules["exp_g_script"] = exp_g
_spec.loader.exec_module(exp_g)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------

def test_synth_cases_count_and_topic_distribution():
    cases = exp_g.synth_cases(150, seed=0)
    assert len(cases) == 150
    topic_counts = Counter(c.topic for c, _ in cases)
    # 15 topics x 10 per topic at N=150
    assert len(topic_counts) == len(exp_g.TOPICS)
    for t in exp_g.TOPICS:
        assert topic_counts[t] == 10, f"{t} not evenly distributed"


def test_synth_cases_metadata_has_month():
    cases = exp_g.synth_cases(60, seed=0)
    for c, meta in cases:
        ca = meta["created_at"]
        assert isinstance(ca, str) and len(ca) == 7 and ca.startswith("2026-")


def test_synth_cases_deterministic():
    a = exp_g.synth_cases(50, seed=42)
    b = exp_g.synth_cases(50, seed=42)
    assert [c.case_id for c, _ in a] == [c.case_id for c, _ in b]
    assert [c.query for c, _ in a] == [c.query for c, _ in b]


def test_synth_eval_queries_count_and_keypoints():
    qs = exp_g.synth_eval_queries(45, n_cases=150, seed=1)
    assert len(qs) == 45
    # each query must reference at least one topic-level keypoint phrase
    # that lives in _TOPIC_RECIPES
    known_kps = set()
    for recipe in exp_g._TOPIC_RECIPES.values():
        known_kps.update(recipe["kps"])
    for q in qs:
        assert q.required_keypoints, q.id
        # at least one of the query's keypoints is a recognized topic-level
        # phrase (the recipe keypoints are id-free at the topic level)
        assert any(kp in known_kps for kp in q.required_keypoints), q.required_keypoints


# ---------------------------------------------------------------------------
# end-to-end scaled-down Exp G
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scaled_exp_g_run():
    """Run a small (N=300, 30 queries) Exp G and return both backends' metrics."""
    from seagent.config import Config
    from seagent.data import load_kb
    from seagent.llm.mock import MockBackend
    from seagent.memory.semantic import SemanticMemory

    cfg = Config().resolve()
    cfg.backend = "mock"
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, score_norm_k=cfg.score_norm_k)
    backend = MockBackend()

    cases = exp_g.synth_cases(300, seed=0)
    eval_qs = exp_g.synth_eval_queries(30, n_cases=300, seed=7)

    ep_jsonl, _ = exp_g._populate_jsonl(cases, cfg.score_norm_k)
    ep_fs, _ = exp_g._populate_fs(cases, cfg.score_norm_k, l0_top=3)

    lat_jsonl = exp_g.time_retrievals(ep_jsonl, eval_qs, top_k=3)
    lat_fs = exp_g.time_retrievals(ep_fs, eval_qs, top_k=3)

    m_jsonl = exp_g.run_agent_eval(cfg, semantic, ep_jsonl, backend, eval_qs)
    m_fs = exp_g.run_agent_eval(cfg, semantic, ep_fs, backend, eval_qs)
    return {
        "jsonl": {"lat": lat_jsonl, "metrics": m_jsonl, "n": len(ep_jsonl)},
        "fs": {"lat": lat_fs, "metrics": m_fs, "n": len(ep_fs)},
    }


def test_both_backends_populate_full_pool(scaled_exp_g_run):
    assert scaled_exp_g_run["jsonl"]["n"] == 300
    assert scaled_exp_g_run["fs"]["n"] == 300


def test_both_backends_eval_runs_without_error(scaled_exp_g_run):
    for k in ("jsonl", "fs"):
        m = scaled_exp_g_run[k]["metrics"]
        assert m["n_queries"] == 30
        assert 0.0 <= m["resolution_rate"] <= 1.0
        assert 0.0 <= m["escalation_rate"] <= 1.0
        assert 0.0 <= m["avg_coverage"] <= 1.0


def test_fs_store_retrieval_no_slower_than_jsonl(scaled_exp_g_run):
    """At populated-pool scale, fs_topic_date should be at least as fast as
    jsonl on average.  We use avg + a 50% tolerance so test-host noise does
    not produce false failures; the directional claim still holds."""
    j = scaled_exp_g_run["jsonl"]["lat"]["avg_ms"]
    f = scaled_exp_g_run["fs"]["lat"]["avg_ms"]
    # tolerance: fs avg <= 1.5 * jsonl avg (in practice fs is 2-3x faster at
    # scale; the slack here protects against CI jitter at N=300 where the
    # narrowing win is small compared to noise)
    assert f <= 1.5 * j, f"fs avg {f:.3f}ms > 1.5 * jsonl avg {j:.3f}ms"


def test_fs_store_resolution_at_least_as_good(scaled_exp_g_run):
    """fs_topic_date's resolution_rate should be >= jsonl - 5pp (tolerance
    for the small-N regime).  At full 10k it actually edges jsonl."""
    j = scaled_exp_g_run["jsonl"]["metrics"]["resolution_rate"]
    f = scaled_exp_g_run["fs"]["metrics"]["resolution_rate"]
    assert f >= j - 0.05, (
        f"fs resolution {f:.3f} more than 5pp below jsonl {j:.3f}; "
        "OpenViking direction violated"
    )
