#!/usr/bin/env python
"""Exp E_v3 — v2.8 per-sub aggregated guardrail (Subagent + Handoff, 3rd attempt).

Background
----------
Three iterations on the multi_intent guardrail story:

  * Exp E (core)      — mode='core', no per-sub guard.  multi_intent res 46.8%,
                         overall esc 36.6%.  No safety on merged answer.
  * Exp E (observed)  — mode='observed', per-sub guard fires for every sub.
                         multi_intent res 0%, overall esc 85.2%.  ANY sub-fail
                         poisons the bundle.
  * Exp E_v2 (merged) — v2.7: specialists mode='core' + ONE check_output on
                         merged answer.  multi_intent res 0%, overall esc 45%,
                         block 15.2%.  Concatenated answer is too long for
                         groundedness against the union of unrelated contexts;
                         PII spans accumulate across subs -> bundle BLOCK.

v2.8 fix (this experiment)
--------------------------
Specialists still run in mode='core' (raw sub-answer).  Orchestrator now runs
``guardrail.check_output`` **per (sub_answer, sub_contexts)** and aggregates:

  - groundedness:  any-supported = supported  (each sub judged against its OWN
                   contexts; merged answer is a structural join of N
                   self-consistent pieces).
  - PII:           per-sub redaction; merged answer is stitched from per-sub
                   ``redacted_answer`` so surface PII can't accumulate.
  - policy:        ANY BLOCK -> BLOCK; else ANY REWRITE -> REWRITE; else ALLOW.
  - escalate:      majority vote (>50% of subs voting ESCALATE) OR groundedness
                   failure (no sub supported) OR any BLOCK.

Hypothesis: multi_intent res >= 30% AND overall esc <= 60% AND block_rate > 0%
(i.e. safety is preserved — PII subs still trigger block when warranted).

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
        return f"  {name:22s} : (missing)"
    esc = summary.get("escalation_rate", 0.0)
    blk = summary.get("block_rate", 0.0)
    p50 = summary.get("p50_latency_ms", 0.0)
    by = summary.get("by_category") or {}
    mi = by.get("multi_intent") or {}
    mi_res = mi.get("resolution_rate", 0.0)
    mi_n = mi.get("n", 0)
    return (f"  {name:22s} : esc={_fmt_pct(esc)}  block={_fmt_pct(blk)}  "
            f"mi_res={_fmt_pct(mi_res)} (n={mi_n})  p50={p50:.0f}ms")


def main():
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(here, "..", "data", "kb_expanded", "index.jsonl"))
    ap.add_argument("--tickets", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_b", "tickets.jsonl"))
    ap.add_argument("--thresholds", default=os.path.join(here, "..", "experiments", "calibration", "thresholds.json"))
    ap.add_argument("--workdir", default=os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_e_v3"))
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
    # Single guardrail instance shared by:
    #   - the base SupportAgent (single-intent fast path)
    #   - the orchestrator (multi-intent: N per-sub check_output calls,
    #     aggregated into one bundle verdict)
    guard = GuardrailPipeline(
        pii_precision_mode=args.pii_mode,
        groundedness_checker=judge,
    )

    tickets = _load_tickets(args.tickets)
    print(f"[exp-e-v3] kb={len(kb)} tickets={len(tickets)} concurrency={args.concurrency}")
    print(f"[exp-e-v3] groundedness=LLM-judge({cfg.model})  judge_tau={args.judge_tau}")
    print(f"[exp-e-v3] pii_mode={args.pii_mode}  calibrator_domains={list(cal.domain_thresholds.keys())}")
    print(f"[exp-e-v3] orchestrator=MultiAgentOrchestrator(guardrail_mode='per_sub_aggregated')")
    print(f"[exp-e-v3] specialists=[billing,account,technical,refund,general]  mode='core'")

    tracer = Tracer(workdir=args.workdir, filename="stress_trace.jsonl")
    open(tracer.path, "w").close()

    router_backend = build_backend(cfg)
    router = IntentRouter(backend=router_backend, cache=not args.no_router_cache)

    def agent_factory():
        backend = build_backend(cfg)
        base = SupportAgent(cfg, backend, sem, guardrail=guard, tracer=tracer, calibrator=cal)
        specialists = {
            "billing":   SpecialistAgent.for_domain("billing",   base, mode="core"),
            "account":   SpecialistAgent.for_domain("account",   base, mode="core"),
            "technical": SpecialistAgent.for_domain("technical", base, mode="core"),
            "refund":    SpecialistAgent.for_domain("refund",    base, mode="core"),
            "general":   SpecialistAgent.for_domain("general",   base, mode="core"),
        }
        return MultiAgentOrchestrator(
            router, specialists,
            default_specialist="general",
            guardrail=guard,
            guardrail_mode="per_sub_aggregated",
        )

    t0 = time.perf_counter()
    records = run_load(tickets, agent_factory, max_concurrency=args.concurrency, tracer=tracer)
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
    summary["guardrail_mode"] = "per_sub_aggregated"
    with open(os.path.join(args.workdir, "load_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n=====  Exp E_v3 (v2.8 per-sub aggregated guardrail)  =====")
    print(f"  n={summary.get('n', 0)}  err={summary.get('error_rate', 0):.3f}")
    print(f"  escalation={summary.get('escalation_rate', 0):.4f}  block={summary.get('block_rate', 0):.4f}")
    print(f"  p50={summary.get('p50_latency_ms', 0):.0f}ms  p95={summary.get('p95_latency_ms', 0):.0f}ms")
    print(f"  wallclock={wall:.1f}s")
    print(f"  router: calls={router.n_calls} cache_hits={router.n_cache_hits} parse_fail={router.n_parse_fail}")
    for cat, m in (summary.get("by_category") or {}).items():
        print(f"    {cat:14s} n={m.get('n', 0):3d} res={m.get('resolution_rate', 0):.3f} "
              f"esc={m.get('escalation_rate', 0):.3f} block={m.get('block_rate', 0):.3f}")

    # 8-column comparison
    print("\n=====  Comparison: Original / B / C / D / E core / E observed / E_v2 merged / E_v3 per-sub-agg  =====")
    paths = {
        "Original":             os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_a", "load_summary.json"),
        "Exp B":                os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_b", "load_summary.json"),
        "Exp C":                os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_c", "load_summary.json"),
        "Exp D":                os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_d", "load_summary.json"),
        "Exp E (core)":         os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_e", "load_summary.json"),
        "Exp E (observed)":     os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_e_observed", "load_summary.json"),
        "Exp E_v2 (merged)":    os.path.join(here, "..", "experiments", "stress_test_expanded", "exp_e_v2", "load_summary.json"),
        "Exp E_v3 (per_sub_agg)": os.path.join(args.workdir, "load_summary.json"),
    }
    summaries = {name: _load_summary(p) for name, p in paths.items()}
    for name in (
        "Original", "Exp B", "Exp C", "Exp D",
        "Exp E (core)", "Exp E (observed)", "Exp E_v2 (merged)",
        "Exp E_v3 (per_sub_agg)",
    ):
        print(_comparison_row(name, summaries[name]))

    _write_report_md(args.workdir, summaries, router_stats=summary["router_stats"], wallclock_s=wall)
    print(f"\n[exp-e-v3] summary -> {os.path.join(args.workdir, 'load_summary.json')}")
    print(f"[exp-e-v3] report  -> {os.path.join(args.workdir, 'report.md')}")


def _write_report_md(workdir, summaries, router_stats, wallclock_s):
    rows = []
    ordered = (
        "Original", "Exp B", "Exp C", "Exp D",
        "Exp E (core)", "Exp E (observed)", "Exp E_v2 (merged)",
        "Exp E_v3 (per_sub_agg)",
    )
    for name in ordered:
        s = summaries.get(name)
        if s is None:
            rows.append(f"| {name} | – | – | – | – | – |")
            continue
        by = s.get("by_category") or {}
        mi = by.get("multi_intent") or {}
        rows.append(
            f"| {name} | {100*s.get('escalation_rate', 0):.1f}% | "
            f"{100*s.get('block_rate', 0):.1f}% | "
            f"{100*mi.get('resolution_rate', 0):.1f}% ({mi.get('n', 0)}) | "
            f"{s.get('p50_latency_ms', 0):.0f}ms | {s.get('n', 0)} |"
        )
    e_v3 = summaries.get("Exp E_v3 (per_sub_agg)") or {}
    e_v3_mi = (e_v3.get("by_category") or {}).get("multi_intent") or {}
    success_mi = e_v3_mi.get("resolution_rate", 0.0) >= 0.30
    success_esc = e_v3.get("escalation_rate", 1.0) <= 0.60
    success_blk = e_v3.get("block_rate", 0.0) > 0.0
    success_overall = success_mi and success_esc and success_blk

    md = [
        "# Exp E_v3 — v2.8 per-sub aggregated guardrail",
        "",
        "Third iteration on the multi_intent guardrail design.",
        "Specialists run in ``mode='core'`` (raw sub-answer).  Orchestrator",
        "runs ``GuardrailPipeline.check_output`` **per (sub_answer, sub_contexts)**",
        "and aggregates:",
        "",
        "  - groundedness: any-supported = supported (the bundle is grounded if",
        "    at least one sub is fully supported by its own contexts).",
        "  - PII: per-sub redaction, merged answer stitched from per-sub",
        "    ``redacted_answer`` so surface PII can't accumulate.",
        "  - policy: ANY BLOCK -> BLOCK; else ANY REWRITE -> REWRITE; else ALLOW.",
        "  - escalate: majority vote (> 50% subs voting ESCALATE) OR any BLOCK",
        "    OR groundedness failure (no sub supported).",
        "",
        "## Headline comparison",
        "",
        "| Config | escalation | block | multi_intent res (n) | p50 | n total |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "## v2.8 success criterion",
        "",
        f"- multi_intent res >= 30%: **{'PASS' if success_mi else 'FAIL'}** "
        f"({100*e_v3_mi.get('resolution_rate', 0):.1f}%)",
        f"- overall escalation <= 60%: **{'PASS' if success_esc else 'FAIL'}** "
        f"({100*e_v3.get('escalation_rate', 0):.1f}%)",
        f"- overall block_rate > 0% (safety preserved): "
        f"**{'PASS' if success_blk else 'FAIL'}** "
        f"({100*e_v3.get('block_rate', 0):.1f}%)",
        f"- overall: **{'PASS' if success_overall else 'FAIL'}**",
        "",
        "## Router stats (Exp E_v3)",
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
