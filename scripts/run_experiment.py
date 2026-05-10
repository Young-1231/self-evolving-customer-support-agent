#!/usr/bin/env python
"""Run the self-evolution experiment and emit metrics + a markdown report.

Usage:
    python scripts/run_experiment.py                 # mock backend (offline)
    python scripts/run_experiment.py --backend openai --model qwen2.5-7b-instruct \
        --api-base http://localhost:8000/v1
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config
from seagent.eval.harness import Experiment, save_results
from seagent.llm.factory import build_backend

METRIC_KEYS = [
    "resolution_rate", "keypoint_coverage", "escalation_f1",
    "human_intervention_rate", "repeated_error_rate",
]


def fmt_table(results) -> str:
    lines = []
    for cond, recs in results.items():
        lines.append(f"\n### condition = {cond}\n")
        header = ["round", "cases", "pb"] + METRIC_KEYS
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for r in recs:
            row = [str(r["round"]), str(r["learned_cases"]), str(r["playbooks"])]
            row += [f"{r.get(k, float('nan')):.3f}" for k in METRIC_KEYS]
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def delta_summary(results) -> str:
    lines = ["\n## 进化效果汇总（round0 冷启动 → 末轮）\n"]
    for cond, recs in results.items():
        a, b = recs[0], recs[-1]
        lines.append(f"- **{cond}**: "
                     f"解决率 {a['resolution_rate']:.1%}→{b['resolution_rate']:.1%}; "
                     f"keypoint覆盖 {a['keypoint_coverage']:.1%}→{b['keypoint_coverage']:.1%}; "
                     f"重复错误率 {a.get('repeated_error_rate',0):.1%}→{b.get('repeated_error_rate',0):.1%}; "
                     f"人工介入 {a['human_intervention_rate']:.1%}→{b['human_intervention_rate']:.1%}; "
                     f"转人工F1 {a['escalation_f1']:.2f}→{b['escalation_f1']:.2f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "..", "configs", "default.yaml"))
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-base", dest="api_base", default=None)
    ap.add_argument("--rounds", type=int, default=None)
    args = ap.parse_args()

    cfg = Config.load(args.config, backend=args.backend, model=args.model,
                      api_base=args.api_base, train_rounds=args.rounds)
    backend = build_backend(cfg)
    print(f"[seagent] backend={backend.name} rounds={cfg.train_rounds}")

    exp = Experiment(cfg, backend)
    print(f"[seagent] kb={len(exp.kb)} train={len(exp.train)} eval={len(exp.eval)}")
    results = exp.run()

    path = save_results(results, cfg.workdir)
    report = "# 自进化客服 Agent — 实验报告\n" + delta_summary(results) + "\n\n## 逐轮指标\n" + fmt_table(results)
    rp = os.path.join(cfg.workdir, "report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(report)

    print(fmt_table(results))
    print(delta_summary(results))
    print(f"\n[seagent] metrics -> {path}\n[seagent] report  -> {rp}")

    try:
        from plot_evolution import plot  # type: ignore
        png = plot(path, cfg.workdir)
        print(f"[seagent] curve   -> {png}")
    except Exception as e:
        print(f"[seagent] (plot skipped: {e})")


if __name__ == "__main__":
    main()
