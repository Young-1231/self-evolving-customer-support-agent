#!/usr/bin/env python
"""Exp D — close the §4h loop: LLM-judge groundedness + per-domain calibration
+ balanced PII. Reuses Exp B tickets so the only thing changing vs Exp C is the
groundedness checker (deterministic n-gram -> LLM judge).

Hypothesis (§4h): real bottleneck is groundedness false-fail on stiff English
template answers. If true, swapping to LLM-judge should drop escalation
materially (target: <= 70%).

Cost: 500 agent LLM calls + ~500 judge LLM calls ≈ $0.30 (DeepSeek)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config
from seagent.data import load_kb
from seagent.llm.factory import build_backend
from seagent.memory.semantic import SemanticMemory
from seagent.agent.support_agent import SupportAgent
from seagent.guardrails import GuardrailPipeline
from seagent.guardrails.groundedness_llm import LLMJudgeGroundedness
from seagent.obs import Tracer
from seagent.calibration import DomainCalibrator
from seagent.stress.load_runner import run_load, summarize_load
from seagent.stress.generator import TicketSpec


def main():
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(here, "..", "data", "kb_expanded", "index.jsonl"))
    ap.add_argument("--tickets", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_b", "tickets.jsonl"))
    ap.add_argument("--thresholds", default=os.path.join(here, "..", "experiments", "calibration", "thresholds.json"))
    ap.add_argument("--workdir", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_d"))
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--pii-mode", default="balanced")
    ap.add_argument("--judge-tau", type=float, default=0.5)
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    cfg = Config().resolve()
    cfg.kb_index = args.kb
    cfg.backend = "openai"
    cfg.model = os.environ.get("STRESS_MODEL", "deepseek-chat")
    cfg.api_base = os.environ.get("STRESS_API_BASE", "https://api.deepseek.com")
    cfg.api_key_env = os.environ.get("STRESS_API_KEY_ENV", "DEEPSEEK_API_KEY")

    kb = load_kb(args.kb)
    sem = SemanticMemory(kb, cfg.score_norm_k)
    cal = DomainCalibrator.load(args.thresholds)

    # NEW: LLM-judge groundedness (key change vs Exp C)
    judge = LLMJudgeGroundedness(
        model=cfg.model, api_base=cfg.api_base, api_key_env=cfg.api_key_env,
        confidence_threshold=args.judge_tau,
    )
    guard = GuardrailPipeline(
        pii_precision_mode=args.pii_mode,
        groundedness_checker=judge,
    )

    tickets = []
    for line in open(args.tickets):
        d = json.loads(line)
        tickets.append(TicketSpec(ticket_id=d["ticket_id"], text=d["text"],
                                  category=d.get("category", "normal_easy"),
                                  expected_signals=d.get("expected_signals", {})))
    print(f"[exp-d] kb={len(kb)} tickets={len(tickets)} concurrency={args.concurrency}")
    print(f"[exp-d] groundedness=LLM-judge({cfg.model})  judge_tau={args.judge_tau}")
    print(f"[exp-d] pii_mode={args.pii_mode}  calibrator_domains={list(cal.domain_thresholds.keys())}")

    tracer = Tracer(workdir=args.workdir, filename="stress_trace.jsonl")
    open(tracer.path, "w").close()

    def agent_factory():
        backend = build_backend(cfg)
        return SupportAgent(cfg, backend, sem, guardrail=guard, tracer=tracer, calibrator=cal)

    records = run_load(tickets, agent_factory, max_concurrency=args.concurrency, tracer=tracer)
    with open(os.path.join(args.workdir, "load_records.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    summary = summarize_load(records)
    with open(os.path.join(args.workdir, "load_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n=====  Exp D (LLM-judge groundedness + balanced PII + calibration)  =====")
    print(f"  n={summary.get('n', 0)}  err={summary.get('error_rate', 0):.3f}")
    print(f"  escalation={summary.get('escalation_rate', 0):.4f}  block={summary.get('block_rate', 0):.4f}")
    print(f"  p50={summary.get('p50_latency_ms', 0):.0f}ms  p95={summary.get('p95_latency_ms', 0):.0f}ms")
    for cat, m in (summary.get("by_category") or {}).items():
        print(f"  {cat:14s} n={m.get('n', 0):3d} res={m.get('resolution_rate', 0):.3f} esc={m.get('escalation_rate', 0):.3f} block={m.get('block_rate', 0):.3f}")
    print(f"\n[exp-d] summary -> {os.path.join(args.workdir, 'load_summary.json')}")


if __name__ == "__main__":
    main()
