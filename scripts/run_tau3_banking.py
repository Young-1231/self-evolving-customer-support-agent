#!/usr/bin/env python
"""v3.3 — self-evolution on the **τ³-bench banking_knowledge** domain.

τ³-bench v1.0 (released after τ²-bench airline/retail) introduces the
``banking_knowledge`` domain: a knowledge-RAG-heavy customer-service scenario
with **agentic shell-based document search** (grep + embeddings retrieval
toolkit). This script reuses our existing APR-CS pipeline (Distill → Inject)
and ports it to banking_knowledge so we get a third cross-domain data point
alongside τ² retail / τ² airline.

Key difference from ``run_tau2_experiment.py``:

* banking_knowledge has **no** train/test split function in the τ³ registry
  (97 tasks total, single ``base`` set). We split it ourselves via
  ``task_ids`` — first ``--train-tasks`` ids → train, the next
  ``--test-tasks`` ids → test, deterministic by lexical id order.

Outputs (newly created, no existing src/ file modified):

* ``experiments/tau3_banking/banking_playbook.json`` — distilled tips
* ``experiments/tau3_banking/banking_results.json``  — pass^k OFF / ON / Δ
* ``docs/tau3_banking_knowledge.md``                 — write-up (separately)

Usage (inside the tau2 venv, after sourcing .env)::

  .venv-tau2/bin/python scripts/run_tau3_banking.py \
      --train-tasks 20 --test-tasks 20 --trials 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.tau2_ext.experience import PLAYBOOK_ENV, save_playbook
from seagent.tau2_ext.memory_agent import register as register_memory_agent
from seagent.tau2_ext import reflect


DOMAIN = "banking_knowledge"


def force_judge_model(model: str) -> None:
    """Route tau2's auxiliary LLM calls (judge/auth/NL env) to our model.

    Identical to the helper in ``run_tau2_experiment.py``; duplicated here
    because the hard constraint forbids touching the existing script.
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


def _all_task_ids() -> list[str]:
    from tau2.registry import registry

    loader = registry.get_tasks_loader(DOMAIN)
    tasks = loader()
    # Stable ordering by id keeps the split reproducible across runs.
    return sorted([t.id for t in tasks])


def _run(
    task_ids, agent, n_tasks, max_steps, model, user_model, trials, seed,
    retrieval_config="bm25_grep",
):
    """Run a τ³ banking_knowledge simulation batch.

    ``retrieval_config`` defaults to ``"bm25_grep"`` — the closest knowledge-RAG
    variant that does **not** require an OpenAI / OpenRouter embedding key (the
    upstream default ``"alltools"`` warms an OpenAI embedding cache at startup
    and crashes with ``OpenAIError: Missing credentials`` in our DeepSeek-only
    setup). ``bm25_grep`` keeps both knowledge-RAG (BM25 ranking) and agentic
    shell-based search (``grep``) — the two characteristics this experiment
    was designed to probe.
    """
    from tau2.data_model.simulation import TextRunConfig
    from tau2.run import run_domain

    cfg = TextRunConfig(
        domain=DOMAIN, agent=agent, user="user_simulator",
        llm_agent=model, llm_args_agent={"temperature": 0.0},
        llm_user=user_model, llm_args_user={"temperature": 0.0},
        task_split_name=None,         # banking_knowledge has no splits
        task_ids=task_ids,            # explicit subset is our split
        num_tasks=n_tasks,
        num_trials=trials,
        max_steps=max_steps, max_errors=8, seed=seed,
        retrieval_config=retrieval_config,
    )
    return run_domain(cfg)


def _success(sim) -> bool:
    return bool(
        sim.reward_info
        and sim.reward_info.reward is not None
        and sim.reward_info.reward >= 1.0
    )


def pass1(res) -> float:
    sims = res.simulations
    return sum(_success(s) for s in sims) / len(sims) if sims else 0.0


def official_metrics(res) -> dict:
    """Official tau2 pass^k = C(c,k)/C(n,k) (arXiv 2406.12045)."""
    from tau2.metrics.agent_metrics import compute_metrics

    m = compute_metrics(res)
    out = {
        "avg_reward": m.avg_reward,
        "avg_agent_cost": getattr(m, "avg_agent_cost", None),
        "total_tasks": getattr(m, "total_tasks", None),
        "total_sims": getattr(m, "total_simulations", None),
    }
    for k, v in m.pass_hat_ks.items():
        out[f"pass^{k}"] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-tasks", type=int, default=20)
    ap.add_argument("--test-tasks", type=int, default=20)
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--max-tips", type=int, default=8)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--retrieval-config",
        default="bm25_grep",
        help="Knowledge-RAG variant (default bm25_grep: BM25 + grep, no OpenAI dep).",
    )
    ap.add_argument(
        "--workdir",
        default=os.path.join(
            os.path.dirname(__file__), "..", "experiments", "tau3_banking"
        ),
    )
    args = ap.parse_args()

    model = os.environ.get("TAU2_MODEL", "deepseek/deepseek-chat")
    user_model = os.environ.get("TAU2_USER_MODEL", model)
    os.makedirs(args.workdir, exist_ok=True)
    playbook_path = os.path.join(args.workdir, "banking_playbook.json")
    results_path = os.path.join(args.workdir, "banking_results.json")
    register_memory_agent()
    force_judge_model(model)

    # ---- Build deterministic train / test id slices ----
    all_ids = _all_task_ids()
    print(f"[tau3] {DOMAIN} total tasks available = {len(all_ids)}")
    train_ids = all_ids[: args.train_tasks]
    test_ids = all_ids[args.train_tasks : args.train_tasks + args.test_tasks]
    print(
        f"[tau3] split — train {len(train_ids)} ids "
        f"({train_ids[0]}..{train_ids[-1]}), "
        f"test {len(test_ids)} ids ({test_ids[0]}..{test_ids[-1]})"
    )

    print(f"[tau3] domain={DOMAIN} model={model} trials={args.trials}")

    # --- A. baseline on TRAIN, collect failures ---
    print("[tau3] Phase A: baseline on train ids ...")
    os.environ.pop(PLAYBOOK_ENV, None)
    train_res = _run(
        train_ids, "llm_agent", len(train_ids), args.max_steps,
        model, user_model, 1, args.seed,
        retrieval_config=args.retrieval_config,
    )
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
    print(
        f"[tau3] train pass^1={train_p1:.3f}  "
        f"failures={len(failed)}/{len(train_res.simulations)}"
    )

    # --- B. reflect -> playbook ---
    print("[tau3] Phase B: distilling playbook from failures ...")
    domain_policy = ""
    try:
        from tau2.run import get_environment_info

        domain_policy = get_environment_info(DOMAIN).policy or ""
    except Exception:
        domain_policy = ""
    distilled = reflect.distill_playbook(
        domain_policy, failed, model, max_tips=args.max_tips
    )
    save_playbook(
        playbook_path,
        distilled["tips"],
        meta={
            "domain": DOMAIN,
            "n_failed_cases": distilled["n_cases"],
            "train_pass1": train_p1,
            "model": model,
            "version": 1,
            "tau_version": "tau3-v1.0",
            "split_method": "manual_task_ids",
            "train_ids": train_ids,
        },
    )
    print(f"[tau3] playbook tips={len(distilled['tips'])} -> {playbook_path}")
    for tip in distilled["tips"]:
        print("   *", tip)

    # --- C. evaluate on TEST ids: memory OFF vs ON ---
    print(
        f"[tau3] Phase C: eval test ids memory OFF "
        f"(tasks={len(test_ids)}, trials={args.trials}) ..."
    )
    os.environ.pop(PLAYBOOK_ENV, None)
    off = _run(
        test_ids, "llm_agent", len(test_ids), args.max_steps,
        model, user_model, args.trials, args.seed,
        retrieval_config=args.retrieval_config,
    )
    off_m = official_metrics(off)

    print(f"[tau3] Phase C: eval test ids memory ON (trials={args.trials}) ...")
    os.environ[PLAYBOOK_ENV] = playbook_path
    on = _run(
        test_ids, "memory_agent", len(test_ids), args.max_steps,
        model, user_model, args.trials, args.seed,
        retrieval_config=args.retrieval_config,
    )
    on_m = official_metrics(on)
    os.environ.pop(PLAYBOOK_ENV, None)

    ks = sorted(int(k.split("^")[1]) for k in off_m if k.startswith("pass^"))
    out = {
        "domain": DOMAIN,
        "tau_version": "tau3-v1.0",
        "retrieval_config": args.retrieval_config,
        "model": model,
        "num_trials": args.trials,
        "train_tasks": len(train_res.simulations),
        "train_pass1": train_p1,
        "n_failed_distilled": distilled["n_cases"],
        "n_tips": len(distilled["tips"]),
        "test_tasks": on_m.get("total_tasks"),
        "train_ids": train_ids,
        "test_ids": test_ids,
        "metric": "official tau2 pass^k (arXiv 2406.12045), reward==1.0 = success",
        "memory_off": off_m,
        "memory_on": on_m,
        "delta_pass^1": on_m.get("pass^1", 0) - off_m.get("pass^1", 0),
        "delta_avg_reward": on_m.get("avg_reward", 0) - off_m.get("avg_reward", 0),
        "tips": distilled["tips"],
    }
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n========  τ³-bench banking_knowledge result (official pass^k)  ========")
    print(
        f" domain={DOMAIN} model={model} "
        f"test_tasks={on_m.get('total_tasks')} trials={args.trials}"
    )
    print(f" {'metric':<12}{'OFF':>10}{'ON':>10}{'Δ':>10}")
    print(
        f" {'avg_reward':<12}{off_m['avg_reward']:>10.3f}"
        f"{on_m['avg_reward']:>10.3f}"
        f"{on_m['avg_reward']-off_m['avg_reward']:>+10.3f}"
    )
    for k in ks:
        key = f"pass^{k}"
        print(
            f" {key:<12}{off_m.get(key,0):>10.3f}"
            f"{on_m.get(key,0):>10.3f}"
            f"{on_m.get(key,0)-off_m.get(key,0):>+10.3f}"
        )
    if off_m.get("avg_agent_cost") is not None:
        print(
            f" {'avg_cost$':<12}{off_m['avg_agent_cost']:>10.4f}"
            f"{on_m['avg_agent_cost']:>10.4f}"
        )
    print("========================================================================")
    print(f"[tau3] results -> {results_path}")


if __name__ == "__main__":
    main()
