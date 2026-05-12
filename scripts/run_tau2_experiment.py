#!/usr/bin/env python
"""Self-evolution on the REAL τ²-bench benchmark.

Pipeline (one command):
  A. Baseline: run stock `llm_agent` on the TRAIN split, collect failures.
  B. Reflect : distill a domain playbook (auditable tips) from the failures.
  C. Evaluate: on the held-out TEST split, run with memory OFF (`llm_agent`) vs
               ON (`memory_agent` + playbook), and compare pass^1.

Requires a .env with DEEPSEEK_API_KEY (or any litellm-compatible key) and
TAU2_MODEL. Run inside the tau2 venv:

  set -a; . ./.env; set +a
  .venv-tau2/bin/python scripts/run_tau2_experiment.py --domain retail \
      --train-tasks 12 --test-tasks 16 --max-steps 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.tau2_ext.experience import PLAYBOOK_ENV, save_playbook, load_playbook
from seagent.tau2_ext.memory_agent import register as register_memory_agent
from seagent.tau2_ext import reflect


def force_judge_model(model: str) -> None:
    """Route tau2's auxiliary LLM calls (NL-assertion judge, auth classifier,
    NL env interface) to our available model.

    These default to an OpenAI gpt-4.1 model, which fails without an OpenAI key.
    Their model name is bound in different ways (module constant vs. frozen
    default arg), so the robust fix is to wrap each consumer module's `generate`
    and redirect any OpenAI model to ours. The agent/user already pass our model
    explicitly, so only judge/aux calls are redirected -- scoring stays faithful.
    """
    import importlib

    def make_redirect(orig):
        def wrapped(*a, **kw):
            m = kw.get("model", "")
            if m and ("gpt-" in m or str(m).startswith("openai") or "o1" in m or "o3" in m):
                kw["model"] = model
            return orig(*a, **kw)
        return wrapped

    for mn in (
        "tau2.evaluator.evaluator_nl_assertions",
        "tau2.evaluator.auth_classifier",
        "tau2.environment.utils.interface_agent",
    ):
        try:
            mod = importlib.import_module(mn)
        except Exception:
            continue
        if hasattr(mod, "generate"):
            mod.generate = make_redirect(mod.generate)
        for attr in list(vars(mod)):
            if attr.startswith("DEFAULT_LLM") and not attr.endswith("ARGS"):
                setattr(mod, attr, model)


def _run(domain, agent, split, n_tasks, max_steps, model, user_model, trials, seed):
    from tau2.data_model.simulation import TextRunConfig
    from tau2.run import run_domain

    cfg = TextRunConfig(
        domain=domain, agent=agent, user="user_simulator",
        llm_agent=model, llm_args_agent={"temperature": 0.0},
        llm_user=user_model, llm_args_user={"temperature": 0.0},
        task_split_name=split, num_tasks=n_tasks, num_trials=trials,
        max_steps=max_steps, max_errors=8, seed=seed,
    )
    return run_domain(cfg)


def _success(sim) -> bool:
    return bool(sim.reward_info and sim.reward_info.reward is not None and sim.reward_info.reward >= 1.0)


def pass1(res) -> float:
    sims = res.simulations
    return sum(_success(s) for s in sims) / len(sims) if sims else 0.0


def official_metrics(res) -> dict:
    """Use tau2's own metric (pass^k = C(c,k)/C(n,k), arXiv 2406.12045) so the
    numbers are directly comparable to the public leaderboard."""
    from tau2.metrics.agent_metrics import compute_metrics
    m = compute_metrics(res)
    out = {"avg_reward": m.avg_reward, "avg_agent_cost": getattr(m, "avg_agent_cost", None),
           "total_tasks": getattr(m, "total_tasks", None), "total_sims": getattr(m, "total_simulations", None)}
    for k, v in m.pass_hat_ks.items():
        out[f"pass^{k}"] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", default="retail")
    ap.add_argument("--train-tasks", type=int, default=12)
    ap.add_argument("--test-tasks", type=int, default=16)
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--max-tips", type=int, default=8)
    ap.add_argument("--trials", type=int, default=4)  # τ-bench standard for pass^k
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(__file__), "..", "experiments", "tau2"))
    args = ap.parse_args()

    model = os.environ.get("TAU2_MODEL", "deepseek/deepseek-chat")
    user_model = os.environ.get("TAU2_USER_MODEL", model)
    os.makedirs(args.workdir, exist_ok=True)
    playbook_path = os.path.join(args.workdir, f"{args.domain}_playbook.json")
    register_memory_agent()
    force_judge_model(model)

    print(f"[tau2] domain={args.domain} model={model} train={args.train_tasks} test={args.test_tasks}")

    # --- A. baseline on TRAIN, collect failures ---
    print("[tau2] Phase A: baseline on train split ...")
    os.environ.pop(PLAYBOOK_ENV, None)
    train_res = _run(args.domain, "llm_agent", "train", args.train_tasks, args.max_steps, model, user_model, 1, args.seed)
    tasks_by_id = {t.id: t for t in train_res.tasks}
    failed = []
    for s in train_res.simulations:
        if _success(s):
            continue
        t = tasks_by_id.get(s.task_id)
        failed.append({
            "task_id": s.task_id,
            "reward": s.reward_info.reward if s.reward_info else None,
            "goal": reflect.task_goal(t) if t else "",
            "expected_actions": reflect.expected_actions(t) if t else [],
            "trajectory": reflect.render_trajectory(s.messages),
        })
    train_p1 = pass1(train_res)
    print(f"[tau2] train pass^1={train_p1:.3f}  failures={len(failed)}/{len(train_res.simulations)}")

    # --- B. reflect -> playbook ---
    print("[tau2] Phase B: distilling playbook from failures ...")
    policy = train_res.tasks and getattr(train_res, "info", None)
    domain_policy = ""
    try:
        from tau2.run import get_environment_info
        domain_policy = get_environment_info(args.domain).policy or ""
    except Exception:
        domain_policy = ""
    distilled = reflect.distill_playbook(domain_policy, failed, model, max_tips=args.max_tips)
    save_playbook(playbook_path, distilled["tips"],
                  meta={"domain": args.domain, "n_failed_cases": distilled["n_cases"],
                        "train_pass1": train_p1, "model": model, "version": 1})
    print(f"[tau2] playbook tips={len(distilled['tips'])} -> {playbook_path}")
    for t in distilled["tips"]:
        print("   •", t)

    # --- C. evaluate on TEST: memory OFF vs ON (official pass^k, num_trials trials) ---
    n_test = args.test_tasks if args.test_tasks and args.test_tasks > 0 else None  # None = full split
    print(f"[tau2] Phase C: eval test split memory OFF (tasks={n_test or 'ALL'}, trials={args.trials}) ...")
    os.environ.pop(PLAYBOOK_ENV, None)
    off = _run(args.domain, "llm_agent", "test", n_test, args.max_steps, model, user_model, args.trials, args.seed)
    off_m = official_metrics(off)

    print(f"[tau2] Phase C: eval test split memory ON (trials={args.trials}) ...")
    os.environ[PLAYBOOK_ENV] = playbook_path
    on = _run(args.domain, "memory_agent", "test", n_test, args.max_steps, model, user_model, args.trials, args.seed)
    on_m = official_metrics(on)
    os.environ.pop(PLAYBOOK_ENV, None)

    ks = sorted(int(k.split("^")[1]) for k in off_m if k.startswith("pass^"))
    out = {
        "domain": args.domain, "model": model, "num_trials": args.trials,
        "train_tasks": len(train_res.simulations), "train_pass1": train_p1,
        "n_failed_distilled": distilled["n_cases"], "n_tips": len(distilled["tips"]),
        "test_tasks": on_m.get("total_tasks"),
        "metric": "official tau2 pass^k (arXiv 2406.12045), reward==1.0 = success",
        "memory_off": off_m, "memory_on": on_m,
        "delta_pass^1": on_m.get("pass^1", 0) - off_m.get("pass^1", 0),
        "delta_avg_reward": on_m.get("avg_reward", 0) - off_m.get("avg_reward", 0),
        "tips": distilled["tips"],
    }
    with open(os.path.join(args.workdir, f"{args.domain}_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n========  τ²-bench self-evolution result (official pass^k)  ========")
    print(f" domain={args.domain} model={model} test_tasks={on_m.get('total_tasks')} trials={args.trials}")
    print(f" {'metric':<12}{'OFF':>10}{'ON':>10}{'Δ':>10}")
    print(f" {'avg_reward':<12}{off_m['avg_reward']:>10.3f}{on_m['avg_reward']:>10.3f}{on_m['avg_reward']-off_m['avg_reward']:>+10.3f}")
    for k in ks:
        key = f"pass^{k}"
        print(f" {key:<12}{off_m.get(key,0):>10.3f}{on_m.get(key,0):>10.3f}{on_m.get(key,0)-off_m.get(key,0):>+10.3f}")
    if off_m.get("avg_agent_cost") is not None:
        print(f" {'avg_cost$':<12}{off_m['avg_agent_cost']:>10.4f}{on_m['avg_agent_cost']:>10.4f}")
    print("====================================================================")
    print(f"[tau2] results -> {os.path.join(args.workdir, args.domain + '_results.json')}")


if __name__ == "__main__":
    main()
