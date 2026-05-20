"""Tests for scripts/run_cost_benefit_analysis.py (offline, deterministic, seconds).

These guard the ROI analysis invariants without touching any production file:
  - the evolution curve is monotone-ish: resolution ends strictly above cold start;
  - cost (injected tokens) and latency rise as the experience pool grows;
  - the marginal-ROI / knee analysis is internally consistent;
  - resolution & token numbers are deterministic across runs (latency may jitter).
"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

_spec = importlib.util.spec_from_file_location(
    "cba", os.path.join(ROOT, "scripts", "run_cost_benefit_analysis.py")
)
cba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cba)

from seagent.config import Config


def _records():
    cfg = Config.load()
    cfg.backend = "mock"
    return cba.run_evolution(cfg), cfg


def test_curve_shape_benefit_and_cost_rise():
    records, _ = _records()
    assert len(records) >= 3
    r0, rN = records[0], records[-1]
    # benefit grows
    assert rN["resolution_rate"] > r0["resolution_rate"]
    assert rN["keypoint_coverage"] >= r0["keypoint_coverage"]
    # cost & retrieval grow with the pool
    assert rN["pool_size"] > r0["pool_size"]
    assert rN["avg_inj_tokens"] > r0["avg_inj_tokens"]
    assert rN["avg_hits"] >= r0["avg_hits"]
    # latency is non-trivially measured and rises end-to-end
    assert rN["avg_latency_ms"] > 0
    assert rN["avg_latency_ms"] >= r0["avg_latency_ms"]


def test_pool_size_monotone_nondecreasing():
    records, _ = _records()
    sizes = [r["pool_size"] for r in records]
    assert sizes == sorted(sizes)


def test_analysis_is_internally_consistent():
    records, _ = _records()
    a = cba.analyze(records)
    rounds = {r["round"] for r in records}
    assert a["knee_round"] in rounds
    assert a["dead_zone_until_round"] in rounds
    assert a["token_saturation_round"] in rounds
    assert 0.0 <= a["base_resolution"] <= a["final_resolution"] <= 1.0
    # one delta per round transition
    assert len(a["deltas"]) == len(records) - 1
    # cold dead-zone wastes tokens for zero benefit (this dataset has one)
    dz = next(r for r in records if r["round"] == a["dead_zone_until_round"])
    assert dz["resolution_rate"] == records[0]["resolution_rate"]


def test_marginal_roi_defined_only_on_positive_gain():
    records, _ = _records()
    a = cba.analyze(records)
    for d in a["deltas"]:
        if d["d_resolution"] > 1e-9:
            assert d["marginal_tokens_per_1pct_resolution"] is not None
        else:
            assert d["marginal_tokens_per_1pct_resolution"] is None


def test_benefit_and_cost_are_deterministic():
    r1, _ = _records()
    r2, _ = _records()
    for a, b in zip(r1, r2):
        assert a["resolution_rate"] == b["resolution_rate"]
        assert a["keypoint_coverage"] == b["keypoint_coverage"]
        assert a["avg_inj_tokens"] == b["avg_inj_tokens"]
        assert a["pool_size"] == b["pool_size"]


def test_end_to_end_produces_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(cba, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(cba, "LATENCY_REPEATS", 5)  # keep the test fast
    cba.main()
    for fn in ("metrics.json", "curve.png", "report.md"):
        p = tmp_path / fn
        assert p.exists() and p.stat().st_size > 0
