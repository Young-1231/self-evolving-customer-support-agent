#!/usr/bin/env python
"""Compute counterfactual Δᵢ for each tip in the airline playbook via
leave-one-out on the train split, then re-run eval with APR-CS cf_weighted.

LOO design (intentionally tight to bound LLM cost):
  - take first N train tasks, trials=1
  - for each tip i: build a playbook with all-except-i, run agent, score pass^1
  - base: all tips on; Δᵢ = pass^1_base − pass^1_loo_i
  - save scores back into the same playbook file (under meta.scores)

Then re-run held-out test with mode=cf_weighted so the router prefers tips
with positive Δᵢ. Output is compared against both prior baselines.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from run_tau2_experiment import _run, official_metrics, force_judge_model
from seagent.tau2_ext.experience import PLAYBOOK_ENV, load_playbook_with_scores, save_playbook
from seagent.tau2_ext.memory_agent import register as register_memory_agent


def _eval_with_tips(domain, model, user_model, train_n, max_steps, seed, tip_list, mode="all"):
    """Run a small eval with a custom tip subset and return pass^1."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"version": 1, "meta": {}, "tips": tip_list}, f, ensure_ascii=False)
        tmp = f.name
    try:
        os.environ[PLAYBOOK_ENV] = tmp
        os.environ["SEAGENT_TAU2_ROUTE_MODE"] = mode
        agent = "memory_agent" if tip_list else "llm_agent"
        res = _run(domain, agent, "train", train_n, max_steps, model, user_model, 1, seed)
        m = official_metrics(res)
        return m.get("pass^1", 0.0)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="airline")
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(__file__), "..", "experiments", "tau2_airline"))
    ap.add_argument("--cf-train-tasks", type=int, default=8,
                    help="train subset size for LOO scoring (cost = (n_tips+1) × N sims)")
    ap.add_argument("--test-tasks", type=int, default=20)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    model = os.environ.get("TAU2_MODEL", "deepseek/deepseek-chat")
    user_model = os.environ.get("TAU2_USER_MODEL", model)
    playbook = os.path.join(args.workdir, f"{args.domain}_playbook.json")
    register_memory_agent()
    force_judge_model(model)

    tips, _ = load_playbook_with_scores(playbook)
    print(f"[cf] {args.domain}  n_tips={len(tips)}  cf_train_tasks={args.cf_train_tasks}  trials_for_loo=1")
    print(f"[cf] LOO budget = {(len(tips)+1) * args.cf_train_tasks} sims")

    # Phase 1: base (all tips on)
    print("[cf] scoring: base (all tips)...")
    base = _eval_with_tips(args.domain, model, user_model, args.cf_train_tasks,
                           args.max_steps, args.seed, tips, mode="all")
    print(f"[cf]   base pass^1 = {base:.3f}")

    # Phase 2: leave-one-out
    deltas = {}
    for i, t in enumerate(tips):
        loo = [x for j, x in enumerate(tips) if j != i]
        p = _eval_with_tips(args.domain, model, user_model, args.cf_train_tasks,
                            args.max_steps, args.seed, loo, mode="all")
        d = base - p
        deltas[t] = d
        print(f"[cf]   LOO[{i+1}/{len(tips)}] -tip={t[:50]!r}  pass^1={p:.3f}  Δᵢ={d:+.3f}")

    # save scores back
    save_playbook(playbook, tips, meta={
        "domain": args.domain, "version": 2,
        "n_tips": len(tips), "cf_train_tasks": args.cf_train_tasks,
        "cf_base_pass1": base, "scores": deltas,
    })
    print(f"[cf] scores written -> {playbook}")

    # Phase 3: re-run held-out test with cf_weighted
    print(f"[cf] eval: held-out test with cf_weighted (top_k={args.top_k})...")
    os.environ[PLAYBOOK_ENV] = playbook
    os.environ["SEAGENT_TAU2_ROUTE_MODE"] = "cf_weighted"
    os.environ["SEAGENT_TAU2_TOP_K"] = str(args.top_k)
    on_cf = _run(args.domain, "memory_agent", "test", args.test_tasks,
                 args.max_steps, model, user_model, args.trials, args.seed)
    on_cf_m = official_metrics(on_cf)

    # gather comparisons
    prior = {}
    pf = os.path.join(args.workdir, f"{args.domain}_results.json")
    pf_apr = os.path.join(args.workdir, f"{args.domain}_results_apr_cs_top_k_relevance.json")
    if os.path.exists(pf):
        prior = json.load(open(pf))
    apr_prior = json.load(open(pf_apr)) if os.path.exists(pf_apr) else {}
    off_m = prior.get("memory_off", {})
    on_naive_m = prior.get("memory_on", {})
    on_topk_m = apr_prior.get("memory_on_apr_cs", {})

    out = {
        "domain": args.domain, "model": model, "route_mode": "cf_weighted",
        "top_k": args.top_k, "cf_train_tasks": args.cf_train_tasks,
        "cf_scores": deltas, "cf_base_pass1": base,
        "memory_off_baseline": off_m,
        "memory_on_naive_all_tips": on_naive_m,
        "memory_on_apr_cs_top_k_relevance": on_topk_m,
        "memory_on_apr_cs_cf_weighted": on_cf_m,
    }
    out_path = os.path.join(args.workdir, f"{args.domain}_results_apr_cs_cf_weighted.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    ks = sorted(int(k.split("^")[1]) for k in on_cf_m if k.startswith("pass^"))
    print(f"\n=====  APR-CS cf_weighted (k={args.top_k}) on {args.domain}  =====")
    print(f" {'metric':<12}{'OFF':>10}{'naive':>10}{'top-k':>10}{'CF-wt':>10}{'CF Δ vs naive':>16}")
    for k in ["avg_reward"] + [f"pass^{k}" for k in ks]:
        o = off_m.get(k, 0); n = on_naive_m.get(k, 0); t = on_topk_m.get(k, 0); c = on_cf_m.get(k, 0)
        print(f" {k:<12}{o:>10.3f}{n:>10.3f}{t:>10.3f}{c:>10.3f}{c - n:>+16.3f}")
    print(f"\n[cf] results -> {out_path}")


if __name__ == "__main__":
    main()
