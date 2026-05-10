#!/usr/bin/env python
"""Plot the self-evolution curves from metrics.json (requires matplotlib)."""
from __future__ import annotations

import json
import os
import sys

PANELS = [
    ("resolution_rate", "Resolution rate"),
    ("keypoint_coverage", "Keypoint coverage"),
    ("repeated_error_rate", "Repeated-error rate"),
    ("human_intervention_rate", "Human-intervention rate"),
    ("escalation_f1", "Escalation F1"),
]


def plot(metrics_path: str, workdir: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(metrics_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    fig, axes = plt.subplots(1, len(PANELS), figsize=(4 * len(PANELS), 3.6))
    for ax, (key, title) in zip(axes, PANELS):
        for cond, recs in results.items():
            xs = [r["round"] for r in recs]
            ys = [r.get(key, float("nan")) for r in recs]
            ax.plot(xs, ys, marker="o", label=cond)
        ax.set_title(title)
        ax.set_xlabel("training round")
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-0.05, 1.05)
    axes[0].legend(loc="best", fontsize=8)
    fig.suptitle("Self-Evolving Support Agent — evolution curves")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(workdir, "evolution_curve.png")
    fig.savefig(out, dpi=130)
    return out


if __name__ == "__main__":
    mp = sys.argv[1] if len(sys.argv) > 1 else "experiments/metrics.json"
    wd = os.path.dirname(mp) or "."
    print(plot(mp, wd))
