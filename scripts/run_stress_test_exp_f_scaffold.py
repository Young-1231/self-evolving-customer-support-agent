#!/usr/bin/env python
"""Exp F (SCAFFOLD ONLY) — OpenViking-style FS episodic store on real LLM stress run.

THIS SCRIPT DOES NOT RUN BY DEFAULT.  It is wired end-to-end against the
DeepSeek backend exactly like Exp E (multi-agent + LLM-judge groundedness +
balanced PII), with the SINGLE difference that episodic memory is the new
:class:`seagent.memory.fs_store.FsEpisodicStore` (scheme='topic_date') instead
of the legacy flat jsonl :class:`EpisodicMemory`.

Why it does not auto-execute:
    The DeepSeek account is out of credits at the time of writing
    (2026-05-28).  Running this script as-is would call into ``build_backend``
    and fail on the first request.  When the balance is topped up, remove the
    sys.exit() guard below to execute.

Expected outcome (based on OpenViking self-reported numbers in
``research/05_2026_github_radar.md`` — NOT yet validated on this codebase):

    metric                 Exp E (jsonl ep)   Exp F (fs topic_date)   Δ
    -------------------------------------------------------------------
    multi_intent res       ~22%               +6 to +12 pp            ↑
    escalation rate        ~mid               slightly lower          ↓
    p50 latency            ~2.5s              within 10%              ~

These deltas are extrapolations from OpenViking's published tau2-bench
numbers (retail +6.87pp, airline +11.87pp).  Mark every cell as "OpenViking
self-reported" until Exp F runs on this codebase.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))


_BLOCKING_NOTE = """\
[exp-f] SCAFFOLD ONLY — refusing to spend LLM credits.

Steps to unlock when budget is restored:
  1. Confirm DEEPSEEK_API_KEY is set and the account has > $1 balance.
  2. Run pre-flight smoke:
       PYTHONPATH=src python -c \\
         "from seagent.llm.factory import build_backend; from seagent.config \\
          import Config; c=Config().resolve(); c.backend='openai'; \\
          c.model='deepseek-chat'; c.api_base='https://api.deepseek.com'; \\
          c.api_key_env='DEEPSEEK_API_KEY'; \\
          print(build_backend(c).generate_answer('ping', []))"
  3. Re-run this script with --i-have-budget to bypass the guard.

Expected wallclock with concurrency=16, |tickets|=500: 6-10 minutes.
Expected DeepSeek spend: $0.30 - $0.40.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(
        HERE, "..", "data", "kb_expanded", "index.jsonl"))
    ap.add_argument("--tickets", default=os.path.join(
        HERE, "..", "experiments", "stress_test_expanded", "exp_b", "tickets.jsonl"))
    ap.add_argument("--thresholds", default=os.path.join(
        HERE, "..", "experiments", "calibration", "thresholds.json"))
    ap.add_argument("--workdir", default=os.path.join(
        HERE, "..", "experiments", "stress_test_expanded", "exp_f"))
    ap.add_argument("--fs-root", default=os.path.join(
        HERE, "..", "experiments", "stress_test_expanded", "exp_f", "episodic_store"))
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--pii-mode", default="balanced")
    ap.add_argument("--judge-tau", type=float, default=0.5)
    ap.add_argument("--i-have-budget", action="store_true",
                    help="bypass the no-LLM-credit guard; required to actually run.")
    args = ap.parse_args()

    if not args.i_have_budget:
        sys.stdout.write(_BLOCKING_NOTE)
        sys.exit(0)

    # --- the real wiring, executed only when the guard is removed ---
    from seagent.agent.support_agent import SupportAgent
    from seagent.calibration import DomainCalibrator
    from seagent.config import Config
    from seagent.data import load_kb
    from seagent.guardrails import GuardrailPipeline
    from seagent.guardrails.groundedness_llm import LLMJudgeGroundedness
    from seagent.llm.factory import build_backend
    from seagent.memory.fs_store import FsEpisodicStore  # ← the v2.5 R3 swap
    from seagent.memory.semantic import SemanticMemory
    from seagent.multi_agent import (
        IntentRouter,
        MultiAgentOrchestrator,
        SpecialistAgent,
    )
    from seagent.obs import Tracer
    from seagent.stress.generator import TicketSpec
    from seagent.stress.load_runner import run_load, summarize_load

    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(args.fs_root, exist_ok=True)

    cfg = Config().resolve()
    cfg.kb_index = args.kb
    cfg.backend = "openai"
    cfg.model = os.environ.get("STRESS_MODEL", "deepseek-chat")
    cfg.api_base = os.environ.get("STRESS_API_BASE", "https://api.deepseek.com")
    cfg.api_key_env = os.environ.get("STRESS_API_KEY_ENV", "DEEPSEEK_API_KEY")

    kb = load_kb(args.kb)
    sem = SemanticMemory(kb, cfg.score_norm_k)
    cal = DomainCalibrator.load(args.thresholds)
    judge = LLMJudgeGroundedness(
        model=cfg.model, api_base=cfg.api_base, api_key_env=cfg.api_key_env,
        confidence_threshold=args.judge_tau,
    )
    guard = GuardrailPipeline(pii_precision_mode=args.pii_mode,
                              groundedness_checker=judge)

    # >>> the v2.5 R3 swap: filesystem episodic store, shared across workers <<<
    fs_episodic = FsEpisodicStore(root_dir=args.fs_root, scheme="topic_date",
                                  score_norm_k=cfg.score_norm_k, l0_top=3)
    print(f"[exp-f] episodic = FsEpisodicStore(scheme=topic_date) "
          f"root={args.fs_root}  initial={len(fs_episodic)} cases")

    def _load_tickets(path: str):
        out = []
        for line in open(path):
            d = json.loads(line)
            out.append(TicketSpec(
                ticket_id=d["ticket_id"], text=d["text"],
                category=d.get("category", "normal_easy"),
                expected_signals=d.get("expected_signals", {}),
            ))
        return out

    tickets = _load_tickets(args.tickets)
    print(f"[exp-f] kb={len(kb)} tickets={len(tickets)} "
          f"concurrency={args.concurrency}  pii={args.pii_mode}")

    tracer = Tracer(workdir=args.workdir, filename="stress_trace.jsonl")
    open(tracer.path, "w").close()

    router_backend = build_backend(cfg)
    router = IntentRouter(backend=router_backend, cache=True)

    def agent_factory():
        backend = build_backend(cfg)
        base = SupportAgent(cfg, backend, sem, episodic=fs_episodic,
                            guardrail=guard, tracer=tracer, calibrator=cal)
        specialists = {
            "billing":   SpecialistAgent.for_domain("billing",   base, mode="observed"),
            "account":   SpecialistAgent.for_domain("account",   base, mode="observed"),
            "technical": SpecialistAgent.for_domain("technical", base, mode="observed"),
            "refund":    SpecialistAgent.for_domain("refund",    base, mode="observed"),
            "general":   SpecialistAgent.for_domain("general",   base, mode="observed"),
        }
        return MultiAgentOrchestrator(router, specialists, default_specialist="general")

    t0 = time.perf_counter()
    records = run_load(tickets, agent_factory,
                       max_concurrency=args.concurrency, tracer=tracer)
    wall = time.perf_counter() - t0

    with open(os.path.join(args.workdir, "load_records.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    summary = summarize_load(records)
    summary["router_stats"] = {
        "n_calls": int(router.n_calls),
        "n_cache_hits": int(router.n_cache_hits),
        "n_parse_fail": int(router.n_parse_fail),
    }
    summary["wallclock_s"] = round(wall, 2)
    summary["fs_store_stats"] = fs_episodic.stats()

    with open(os.path.join(args.workdir, "load_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n=====  Exp F (FsEpisodicStore, OpenViking-style L0/L1/L2)  =====")
    print(f"  n={summary.get('n', 0)}  err={summary.get('error_rate', 0):.3f}")
    print(f"  escalation={summary.get('escalation_rate', 0):.4f}")
    print(f"  p50={summary.get('p50_latency_ms', 0):.0f}ms  "
          f"p95={summary.get('p95_latency_ms', 0):.0f}ms  wallclock={wall:.1f}s")
    print(f"  fs_store: {summary['fs_store_stats']}")
    print(f"\n[exp-f] summary -> {os.path.join(args.workdir, 'load_summary.json')}")
    print(f"\n[exp-f] To compare against Exp E, run a side-by-side delta:")
    print(f"        diff <(jq . experiments/stress_test_expanded/exp_e/load_summary.json) \\")
    print(f"             <(jq . experiments/stress_test_expanded/exp_f/load_summary.json)")


if __name__ == "__main__":
    main()
