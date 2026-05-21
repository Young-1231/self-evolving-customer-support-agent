#!/usr/bin/env python
"""Re-evaluate τ²-bench with APR-CS adaptive routing, keeping the OFF baseline
from the previous run for a clean apples-to-apples comparison.

Hypothesis test:
  on airline (where injecting all 8 tips drops pass^1 by -2.5pp), does
  *routing-only-the-relevant-top-K-tips* recover pass^1 while keeping the
  pass^2/^3 reliability gains?

Re-uses the already-distilled playbook on disk; only the ON eval is rerun.

Usage:
    .venv-tau2/bin/python scripts/run_tau2_apr_cs_eval.py \
        --domain airline --test-tasks 20 --trials 4 --max-steps 80 \
        --workdir experiments/tau2_airline --mode top_k_relevance --top-k 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Reuse the canonical runner machinery so the wiring is identical.
from run_tau2_experiment import _run, official_metrics, force_judge_model
from seagent.tau2_ext.experience import PLAYBOOK_ENV
from seagent.tau2_ext.memory_agent import register as register_memory_agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="airline")
    ap.add_argument("--test-tasks", type=int, default=20)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(__file__), "..", "experiments", "tau2_airline"))
    ap.add_argument("--mode", default="top_k_relevance",
                    choices=["top_k_relevance", "cf_weighted", "conf_gated", "all"])
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    model = os.environ.get("TAU2_MODEL", "deepseek/deepseek-chat")
    user_model = os.environ.get("TAU2_USER_MODEL", model)
    playbook = os.path.join(args.workdir, f"{args.domain}_playbook.json")
    prior_results = os.path.join(args.workdir, f"{args.domain}_results.json")
    assert os.path.exists(playbook), f"missing playbook: {playbook}"

    register_memory_agent()
    force_judge_model(model)

    # APR-CS routing controls (read by MemoryAugmentedLLMAgent at construction)
    os.environ[PLAYBOOK_ENV] = playbook
    os.environ["SEAGENT_TAU2_ROUTE_MODE"] = args.mode
    os.environ["SEAGENT_TAU2_TOP_K"] = str(args.top_k)

    n_test = args.test_tasks if args.test_tasks > 0 else None
    print(f"[apr-cs] {args.domain}  mode={args.mode}  top_k={args.top_k}  "
          f"test={n_test or 'ALL'}  trials={args.trials}  model={model}")

    on_res = _run(args.domain, "memory_agent", "test", n_test,
                  args.max_steps, model, user_model, args.trials, args.seed)
    on_m = official_metrics(on_res)

    # pull prior OFF (and naive ON, if present) for clean A/B
    prior = {}
    if os.path.exists(prior_results):
        with open(prior_results, "r", encoding="utf-8") as f:
            prior = json.load(f)
    off_m = prior.get("memory_off", {})
    on_naive_m = prior.get("memory_on", {})

    out = {
        "domain": args.domain, "model": model, "route_mode": args.mode, "top_k": args.top_k,
        "test_tasks": on_m.get("total_tasks"), "num_trials": args.trials,
        "memory_off_baseline": off_m,
        "memory_on_naive_all_tips": on_naive_m,
        "memory_on_apr_cs": on_m,
    }
    fname = f"{args.domain}_results_apr_cs_{args.mode}.json"
    with open(os.path.join(args.workdir, fname), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    ks = sorted(int(k.split("^")[1]) for k in on_m if k.startswith("pass^"))
    print(f"\n=====  APR-CS {args.mode} (k={args.top_k}) on {args.domain}  =====")
    print(f" {'metric':<12}{'OFF':>10}{'ON-naive':>12}{'ON-APR-CS':>12}{'Δ vs naive':>14}{'Δ vs OFF':>12}")
    for k in ["avg_reward"] + [f"pass^{k}" for k in ks]:
        o = off_m.get(k, 0.0); n = on_naive_m.get(k, 0.0); a = on_m.get(k, 0.0)
        print(f" {k:<12}{o:>10.3f}{n:>12.3f}{a:>12.3f}{a - n:>+14.3f}{a - o:>+12.3f}")
    print(f"\n[apr-cs] results -> {os.path.join(args.workdir, fname)}")


if __name__ == "__main__":
    main()
