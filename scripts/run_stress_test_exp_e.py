#!/usr/bin/env python
"""Exp E — Subagent + Handoff multi-specialist routing (v2.3 R2).

Reuses Exp B mixed tickets + expanded KB + Exp D calibration / LLM-judge
groundedness / balanced PII guardrail.  The ONLY thing changing vs Exp D is
the top-level agent: instead of a single SupportAgent, we wire a
``MultiAgentOrchestrator`` with five specialists (billing / account /
technical / refund / general) behind an ``IntentRouter``.

Hypothesis: multi_intent tickets (single SupportAgent: 0% resolution in Exp
D) can be split into N focused sub-questions, each answered by the relevant
specialist, then merged.  Target: multi_intent resolution >= 20%.

Cost budget: ~$0.30 (DeepSeek). Real cost printed at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.agent.support_agent import SupportAgent
from seagent.calibration import DomainCalibrator
from seagent.config import Config
from seagent.data import load_kb
from seagent.guardrails import GuardrailPipeline
from seagent.guardrails.groundedness_llm import LLMJudgeGroundedness
from seagent.llm.factory import build_backend
from seagent.memory.semantic import SemanticMemory
from seagent.multi_agent import (
    IntentRouter,
    MultiAgentOrchestrator,
    SpecialistAgent,
)
from seagent.obs import Tracer
from seagent.stress.generator import TicketSpec
from seagent.stress.load_runner import run_load, summarize_load


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


def _load_summary(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_pct(x: float) -> str:
    return f"{100 * float(x):.1f}%"


def _comparison_row(name: str, summary):
    if summary is None:
        return f"  {name:10s} : (missing)"
    esc = summary.get("escalation_rate", 0.0)
    p50 = summary.get("p50_latency_ms", 0.0)
    by = summary.get("by_category") or {}
    mi = by.get("multi_intent") or {}
    mi_res = mi.get("resolution_rate", 0.0)
    mi_n = mi.get("n", 0)
    return f"  {name:10s} : esc={_fmt_pct(esc)}  multi_intent_res={_fmt_pct(mi_res)} (n={mi_n})  p50={p50:.0f}ms"


def main():
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(here, "..", "data", "kb_expanded", "index.jsonl"))
    ap.add_argument("--tickets", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_b", "tickets.jsonl"))
    ap.add_argument("--thresholds", default=os.path.join(here, "..", "experiments", "calibration", "thresholds.json"))
    ap.add_argument("--workdir", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_e"))
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--pii-mode", default="balanced")
    ap.add_argument("--judge-tau", type=float, default=0.5)
    ap.add_argument("--no-router-cache", action="store_true",
                    help="disable router cache (for ablation only).")
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

    judge = LLMJudgeGroundedness(
        model=cfg.model, api_base=cfg.api_base, api_key_env=cfg.api_key_env,
        confidence_threshold=args.judge_tau,
    )
    guard = GuardrailPipeline(
        pii_precision_mode=args.pii_mode,
        groundedness_checker=judge,
    )

    tickets = _load_tickets(args.tickets)
    print(f"[exp-e] kb={len(kb)} tickets={len(tickets)} concurrency={args.concurrency}")
    print(f"[exp-e] groundedness=LLM-judge({cfg.model})  judge_tau={args.judge_tau}")
    print(f"[exp-e] pii_mode={args.pii_mode}  calibrator_domains={list(cal.domain_thresholds.keys())}")
    print(f"[exp-e] orchestrator=MultiAgentOrchestrator domains=[billing,account,technical,refund,general]")

    tracer = Tracer(workdir=args.workdir, filename="stress_trace.jsonl")
    open(tracer.path, "w").close()

    # Build a single shared router (its backend is the cheapest deepseek-chat
    # call, and we want cache hits across workers).
    router_backend = build_backend(cfg)
    router = IntentRouter(backend=router_backend, cache=not args.no_router_cache)

    def agent_factory():
        backend = build_backend(cfg)
        base = SupportAgent(cfg, backend, sem, guardrail=guard, tracer=tracer, calibrator=cal)
        # mode='observed' so each sub-query runs through the FULL Exp-D
        # pipeline (input guard -> retrieve -> generate -> critic -> output
        # guard -> tracer), with topic-filtered KB.  Apples-to-apples vs Exp D.
        specialists = {
            "billing":   SpecialistAgent.for_domain("billing",   base, mode="observed"),
            "account":   SpecialistAgent.for_domain("account",   base, mode="observed"),
            "technical": SpecialistAgent.for_domain("technical", base, mode="observed"),
            "refund":    SpecialistAgent.for_domain("refund",    base, mode="observed"),
            "general":   SpecialistAgent.for_domain("general",   base, mode="observed"),
        }
        return MultiAgentOrchestrator(router, specialists, default_specialist="general")

    t0 = time.perf_counter()
    records = run_load(tickets, agent_factory, max_concurrency=args.concurrency, tracer=tracer)
    wall = time.perf_counter() - t0

    with open(os.path.join(args.workdir, "load_records.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    summary = summarize_load(records)
    # attach router/orchestrator stats for postmortem
    summary["router_stats"] = {
        "n_calls": int(router.n_calls),
        "n_cache_hits": int(router.n_cache_hits),
        "n_parse_fail": int(router.n_parse_fail),
    }
    summary["wallclock_s"] = round(wall, 2)
    with open(os.path.join(args.workdir, "load_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n=====  Exp E (Subagent + Handoff multi-specialist routing)  =====")
    print(f"  n={summary.get('n', 0)}  err={summary.get('error_rate', 0):.3f}")
    print(f"  escalation={summary.get('escalation_rate', 0):.4f}  block={summary.get('block_rate', 0):.4f}")
    print(f"  p50={summary.get('p50_latency_ms', 0):.0f}ms  p95={summary.get('p95_latency_ms', 0):.0f}ms")
    print(f"  wallclock={wall:.1f}s")
    print(f"  router: calls={router.n_calls} cache_hits={router.n_cache_hits} parse_fail={router.n_parse_fail}")
    for cat, m in (summary.get("by_category") or {}).items():
        print(f"    {cat:14s} n={m.get('n', 0):3d} res={m.get('resolution_rate', 0):.3f} esc={m.get('escalation_rate', 0):.3f} block={m.get('block_rate', 0):.3f}")

    # comparison vs Original / Exp B / Exp C / Exp D
    print("\n=====  Comparison: Original / Exp B / Exp C / Exp D / Exp E  =====")
    paths = {
        "Original": os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_a", "load_summary.json"),
        "Exp B":    os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_b", "load_summary.json"),
        "Exp C":    os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_c", "load_summary.json"),
        "Exp D":    os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_d", "load_summary.json"),
        "Exp E":    os.path.join(args.workdir, "load_summary.json"),
    }
    summaries = {name: _load_summary(p) for name, p in paths.items()}
    for name in ("Original", "Exp B", "Exp C", "Exp D", "Exp E"):
        print(_comparison_row(name, summaries[name]))

    # also emit a short markdown report
    _write_report_md(args.workdir, summaries, router_stats=summary["router_stats"], wallclock_s=wall)
    print(f"\n[exp-e] summary -> {os.path.join(args.workdir, 'load_summary.json')}")
    print(f"[exp-e] report  -> {os.path.join(args.workdir, 'report.md')}")


def _write_report_md(workdir, summaries, router_stats, wallclock_s):
    rows = []
    for name in ("Original", "Exp B", "Exp C", "Exp D", "Exp E"):
        s = summaries.get(name)
        if s is None:
            rows.append(f"| {name} | – | – | – | – |")
            continue
        by = s.get("by_category") or {}
        mi = by.get("multi_intent") or {}
        rows.append(
            f"| {name} | {100*s.get('escalation_rate', 0):.1f}% | "
            f"{100*mi.get('resolution_rate', 0):.1f}% ({mi.get('n', 0)}) | "
            f"{s.get('p50_latency_ms', 0):.0f}ms | {s.get('n', 0)} |"
        )
    md = [
        "# Exp E — Subagent + Handoff multi-specialist routing (v2.3 R2)",
        "",
        "Drop-in MultiAgentOrchestrator replaces SupportAgent at the top level.",
        "Router (1 LLM call/ticket, cached) splits multi_intent tickets into N",
        "focused sub-queries; each is dispatched to a domain specialist that",
        "filters retrieved contexts to its KB topic set.  Merge: per-question",
        "prefixed answer, escalate=any, confidence=min.",
        "",
        "## Headline comparison",
        "",
        "| Config | escalation | multi_intent res (n) | p50 | n total |",
        "|---|---|---|---|---|",
        *rows,
        "",
        "## Router stats (Exp E)",
        "",
        f"- LLM calls: {router_stats['n_calls']}",
        f"- cache hits: {router_stats['n_cache_hits']}",
        f"- parse failures: {router_stats['n_parse_fail']}",
        f"- wallclock: {wallclock_s:.1f}s",
        "",
    ]
    with open(os.path.join(workdir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))


if __name__ == "__main__":
    main()
