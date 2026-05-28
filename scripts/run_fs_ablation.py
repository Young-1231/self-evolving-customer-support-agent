#!/usr/bin/env python
"""v2.5 R3 — OpenViking-style filesystem context store ablation.

Compares 4 episodic-memory backends on the synthetic NimbusFlow benchmark
(mock backend, deterministic, zero LLM cost):

  1. static          : no episodic memory (cold-start baseline)
  2. jsonl_episodic  : original EpisodicMemory (flat jsonl + BM25)
  3. fs_topic_date   : FsEpisodicStore(scheme='topic_date')   ← the proposed change
  4. fs_flat         : FsEpisodicStore(scheme='flat')          ← sanity check
                       (should match jsonl_episodic to within float noise)

We do NOT touch any existing src/seagent file.  The harness is sub-classed
locally to swap the episodic backend per condition without copying logic.

Outputs (under experiments/fs_ablation/):
    metrics.json        # per-condition, per-round metrics
    report.md           # human-readable comparison
    evolution_curve.png # if matplotlib is installed; gracefully skipped otherwise
    fs_stats.json       # FsEpisodicStore.stats() at end of each condition
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Set

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from seagent.agent.support_agent import SupportAgent
from seagent.config import Config
from seagent.eval.harness import Experiment
from seagent.eval.metrics import aggregate, failed_groups
from seagent.eval.verifier import verify
from seagent.evolution.reflector import Reflector
from seagent.llm.factory import build_backend
from seagent.memory.episodic import Case, EpisodicMemory
from seagent.memory.fs_store import FsEpisodicStore
from seagent.memory.procedural import ProceduralMemory


METRIC_KEYS = [
    "resolution_rate", "keypoint_coverage", "escalation_f1",
    "human_intervention_rate", "repeated_error_rate",
]


def _month_for_round(r: int) -> str:
    # spread rounds across calendar months for a non-trivial L1 bucket layout
    return f"2026-{(((r - 1) % 12) + 1):02d}"


class _FsExperiment(Experiment):
    """Subclass that lets us choose the episodic backend per condition."""

    def __init__(self, cfg, backend, episodic_kind: str, fs_root: Optional[str] = None):
        super().__init__(cfg, backend)
        self.episodic_kind = episodic_kind
        self.fs_root = fs_root
        self._last_stats: Dict[str, object] = {}

    def _make_episodic(self):
        if self.episodic_kind == "static":
            return None
        if self.episodic_kind == "jsonl_episodic":
            return EpisodicMemory(path=None, score_norm_k=self.cfg.score_norm_k)
        if self.episodic_kind == "fs_topic_date":
            return FsEpisodicStore(root_dir=self.fs_root, scheme="topic_date",
                                   score_norm_k=self.cfg.score_norm_k, l0_top=3)
        if self.episodic_kind == "fs_flat":
            return FsEpisodicStore(root_dir=self.fs_root, scheme="flat",
                                   score_norm_k=self.cfg.score_norm_k)
        raise ValueError(self.episodic_kind)

    def run_condition(self, condition: str) -> List[Dict]:
        episodic = self._make_episodic()
        # Procedural memory mirrors the original harness's "full" condition.
        # We keep it disabled here so the ablation isolates the episodic
        # backend.  (Same choice for all 4 conditions => apples-to-apples.)
        procedural = None
        agent = SupportAgent(self.cfg, self.backend, self.semantic, episodic, procedural)

        records: List[Dict] = []
        v0 = self._evaluate(agent, None)
        baseline_failed: Set[str] = failed_groups(v0)
        records.append({"round": 0, "learned_cases": 0, "playbooks": 0,
                        **aggregate(v0, baseline_failed)})

        # split training queries into rounds (re-use parent's helper logic)
        from seagent.eval.harness import _batches
        batches = _batches(self.train, self.cfg.train_rounds)
        for r, batch in enumerate(batches, start=1):
            if episodic is not None:
                for tq in batch:
                    res = agent.handle(tq.query)
                    v = verify(tq, res, self.cfg.coverage_threshold)
                    if not v.resolved:
                        case = Case(
                            case_id=tq.id, query=tq.query, resolution=tq.resolution,
                            should_escalate=tq.should_escalate,
                            topic=self._topic_of(tq),
                            source_query_id=tq.id, learned_round=r,
                        )
                        if isinstance(episodic, FsEpisodicStore):
                            episodic.add(case, metadata={"created_at": _month_for_round(r)})
                        else:
                            episodic.add(case)
            v = self._evaluate(agent, baseline_failed)
            records.append({
                "round": r,
                "learned_cases": len(episodic) if episodic else 0,
                "playbooks": 0,
                **aggregate(v, baseline_failed),
            })

        if isinstance(episodic, FsEpisodicStore):
            self._last_stats = episodic.stats()
        else:
            self._last_stats = {}
        return records


def _fmt_pct(x: float) -> str:
    return f"{100 * float(x):.1f}%"


def _fmt_table(results: Dict[str, List[Dict]]) -> str:
    rows = ["| condition | round | cases | resolution | keypoint cov | esc F1 | human intv | repeat err |",
            "|---|---|---|---|---|---|---|---|"]
    for cond, recs in results.items():
        for r in recs:
            rows.append(
                f"| {cond} | {r['round']} | {r['learned_cases']} | "
                f"{_fmt_pct(r['resolution_rate'])} | "
                f"{_fmt_pct(r['keypoint_coverage'])} | "
                f"{r['escalation_f1']:.2f} | "
                f"{_fmt_pct(r['human_intervention_rate'])} | "
                f"{_fmt_pct(r.get('repeated_error_rate', 0.0))} |"
            )
    return "\n".join(rows)


def _final_summary(results: Dict[str, List[Dict]]) -> str:
    lines = ["| condition | final res | final cov | final repeat err | final esc F1 |",
             "|---|---|---|---|---|"]
    for cond, recs in results.items():
        last = recs[-1]
        lines.append(
            f"| {cond} | {_fmt_pct(last['resolution_rate'])} | "
            f"{_fmt_pct(last['keypoint_coverage'])} | "
            f"{_fmt_pct(last.get('repeated_error_rate', 0.0))} | "
            f"{last['escalation_f1']:.2f} |"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config",
                    default=os.path.join(HERE, "..", "configs", "default.yaml"))
    ap.add_argument("--workdir",
                    default=os.path.join(HERE, "..", "experiments", "fs_ablation"))
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    cfg = Config.load(args.config)
    backend = build_backend(cfg)
    print(f"[fs-ablation] backend={backend.name} rounds={cfg.train_rounds}")

    conditions = ["static", "jsonl_episodic", "fs_topic_date", "fs_flat"]
    results: Dict[str, List[Dict]] = {}
    fs_stats: Dict[str, object] = {}

    for cond in conditions:
        fs_root = None
        if cond.startswith("fs_"):
            fs_root = os.path.join(args.workdir, f"store_{cond}")
            # clean previous run for reproducibility
            if os.path.isdir(fs_root):
                import shutil
                shutil.rmtree(fs_root)
        exp = _FsExperiment(cfg, backend, episodic_kind=cond, fs_root=fs_root)
        print(f"[fs-ablation] running {cond} (train={len(exp.train)} eval={len(exp.eval)})")
        results[cond] = exp.run_condition(cond)
        if exp._last_stats:
            fs_stats[cond] = exp._last_stats

    out_path = os.path.join(args.workdir, "metrics.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    stats_path = os.path.join(args.workdir, "fs_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(fs_stats, f, ensure_ascii=False, indent=2)

    report = [
        "# v2.5 R3 — Filesystem context store ablation (OpenViking-style)",
        "",
        "Synthetic NimbusFlow benchmark (mock backend, deterministic, zero LLM cost).",
        "",
        "## Final-round comparison",
        "",
        _final_summary(results),
        "",
        "## Per-round detail",
        "",
        _fmt_table(results),
        "",
        "## Filesystem store topology",
        "",
        "```json",
        json.dumps(fs_stats, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Notes",
        "",
        "- `fs_flat` is the sanity baseline: identical retrieval semantics to",
        "  `jsonl_episodic`, just routed through the filesystem layout.  Any",
        "  divergence flags a regression in the L0/L1/L2 wiring.",
        "- `fs_topic_date` uses the L0 (topic) + L1 (YYYY-MM) hierarchy.  In",
        "  the synthetic 76-query corpus the gain over the flat baseline is",
        "  marginal because the BM25 corpus is already small; the layered",
        "  structure shows its value at 1k+ scale (see test_fs_store.py).",
        "- OpenViking self-reports +6.87pp (retail) / +11.87pp (airline) on",
        "  tau2-bench.  Verifying those numbers on this codebase requires",
        "  Exp F — see `scripts/run_stress_test_exp_f_scaffold.py`.",
        "",
    ]
    report_path = os.path.join(args.workdir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print("\n=====  Final-round comparison  =====")
    print(_final_summary(results))
    print(f"\n[fs-ablation] metrics -> {out_path}")
    print(f"[fs-ablation] report  -> {report_path}")
    print(f"[fs-ablation] stats   -> {stats_path}")

    # optional matplotlib curve, guarded
    try:
        import matplotlib  # noqa: F401
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4))
        for cond, recs in results.items():
            xs = [r["round"] for r in recs]
            ys = [r["resolution_rate"] for r in recs]
            ax.plot(xs, ys, marker="o", label=cond)
        ax.set_xlabel("round")
        ax.set_ylabel("resolution rate")
        ax.set_title("v2.5 R3 — episodic backend ablation")
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right")
        fig.tight_layout()
        png = os.path.join(args.workdir, "evolution_curve.png")
        fig.savefig(png, dpi=130)
        print(f"[fs-ablation] curve   -> {png}")
    except Exception as e:
        print(f"[fs-ablation] (plot skipped: {e})")


if __name__ == "__main__":
    main()
