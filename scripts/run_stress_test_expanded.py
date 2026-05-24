#!/usr/bin/env python
"""500-ticket stress test on the **expanded KB** (NimbusFlow 30 + Bitext 150).

Two controlled experiments:

  Exp A — "KB augmentation only"
      Re-run the *existing* 500 NimbusFlow tickets against the expanded KB.
      Hypothesis: Bitext is e-commerce-flavored while NimbusFlow tickets are
      SaaS-flavored, so the additional 150 docs barely move the needle. This
      is the honest control — augmenting a KB without aligning to the ticket
      distribution is mostly cargo-cult.

  Exp B — "KB + ticket distribution aligned"
      Generate 500 fresh tickets with a 50/50 SaaS / e-commerce split (250
      reused NimbusFlow + 250 e-commerce generated via deepseek) and run them
      against the expanded KB. Hypothesis: when the KB and ticket
      distribution agree, resolution rate jumps and escalation collapses.

Outputs:
    experiments/stress_test_expanded/exp_a/{report.md, load_summary.json,
                                            load_records.jsonl,
                                            stress_trace.jsonl}
    experiments/stress_test_expanded/exp_b/{...same...}
    experiments/stress_test_expanded/exp_b/tickets.jsonl   (the new 500)
    experiments/stress_test_expanded/report_comparison.md  (three-way table)

Honors hard constraints:
  * Does not modify any existing file under src/ or scripts/.
  * Does not touch data/kb/ or experiments/stress_test/.
  * mock backend & DeepSeek both supported (mock = STRESS_BACKEND=mock).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

# Allow `python scripts/...` or PYTHONPATH=src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config  # noqa: E402
from seagent.data import load_kb  # noqa: E402
from seagent.memory.semantic import SemanticMemory  # noqa: E402
from seagent.memory.episodic import EpisodicMemory  # noqa: E402
from seagent.agent.support_agent import SupportAgent  # noqa: E402
from seagent.guardrails import GuardrailPipeline  # noqa: E402
from seagent.obs import Tracer  # noqa: E402
from seagent.stress import (  # noqa: E402
    TicketSpec,
    load_tickets,
    run_load,
    summarize_load,
)
from seagent.stress.generator import (  # noqa: E402
    _LLMCaller,
    _expected_signals_for,
)


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ORIG_TICKETS = os.path.join(ROOT, "experiments", "stress_test", "tickets.jsonl")
EXPANDED_KB = os.path.join(ROOT, "data", "kb_expanded", "index.jsonl")
OUT_DIR = os.path.join(ROOT, "experiments", "stress_test_expanded")
OUT_A = os.path.join(OUT_DIR, "exp_a")
OUT_B = os.path.join(OUT_DIR, "exp_b")
COMPARISON_REPORT = os.path.join(OUT_DIR, "report_comparison.md")

ORIG_SUMMARY_PATH = os.path.join(ROOT, "experiments", "stress_test", "load_summary.json")


# ===========================================================================
# config builder
# ===========================================================================
def _make_cfg(workdir: str, *, backend: str) -> Config:
    cfg = Config.load(os.path.join(ROOT, "configs", "default.yaml"))
    cfg.workdir = workdir
    cfg.kb_index = EXPANDED_KB
    if backend == "mock":
        cfg.backend = "mock"
        cfg.model = "mock"
        cfg.api_base = None
        cfg.api_key_env = "OPENAI_API_KEY"
    else:
        cfg.backend = "openai"
        cfg.model = os.environ.get("STRESS_MODEL", "deepseek-chat")
        cfg.api_base = os.environ.get("STRESS_API_BASE", "https://api.deepseek.com")
        cfg.api_key_env = os.environ.get("STRESS_API_KEY_ENV", "DEEPSEEK_API_KEY")
        cfg.temperature = float(os.environ.get("STRESS_TEMPERATURE", "0.0"))
    return cfg


def _build_agent_factory(cfg: Config, tracer: Tracer):
    """Shared SemanticMemory (read-only), per-thread Episodic + Agent."""
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, cfg.score_norm_k)

    def factory():
        from seagent.llm.factory import build_backend
        backend = build_backend(cfg)
        epi = EpisodicMemory(path=None, score_norm_k=cfg.score_norm_k)
        guardrail = GuardrailPipeline()
        return SupportAgent(
            cfg, backend, semantic, epi, guardrail=guardrail, tracer=tracer,
        )

    return factory, len(kb)


# ===========================================================================
# Exp B ticket generation: 250 NimbusFlow (reused) + 250 e-commerce (fresh)
# ===========================================================================
_ECOMM_CATEGORY_PROMPTS: Dict[str, str] = {
    "ecomm_easy": (
        "Generate a single short customer-support ticket for an "
        "**e-commerce store** (covers order placement, shipping, payment, "
        "refund, account, subscription, invoicing, contact, feedback, "
        "delivery, cancellation). User-voice, first person, 20-50 words, "
        "English. Single intent. Do NOT add any prefix, quotes, or "
        "explanation — output the ticket body only."
    ),
    "ecomm_hard": (
        "Generate a single **complex** e-commerce customer-support ticket: "
        "user describes prior steps tried, time line, error messages, and "
        "asks for resolution. The issue should require the agent to combine "
        "knowledge from multiple topics (e.g. refund + shipping + invoice). "
        "60-160 words, English. No prefix or quotes."
    ),
    "ecomm_pii": (
        "Generate a single e-commerce support ticket that contains **at "
        "least two of**: realistic-looking but fake email, US phone number, "
        "credit-card-last-4 or full PAN. The underlying request is normal "
        "(refund, address change, invoice resend). 40-120 words, English. "
        "No prefix or quotes."
    ),
    "ecomm_multi": (
        "Generate a single e-commerce support ticket where the user asks "
        "2 or 3 **independent** questions in one message (e.g. change "
        "shipping address + reorder a previous purchase + ask about a "
        "refund). 80-180 words, English. No prefix or quotes."
    ),
}

# Aligned distribution for Exp B (matches NimbusFlow generator weights but
# only over e-commerce categories; the other 250 come from the existing
# NimbusFlow file)
_ECOMM_DIST: Dict[str, float] = {
    "ecomm_easy":  0.60,
    "ecomm_hard":  0.20,
    "ecomm_pii":   0.10,
    "ecomm_multi": 0.10,
}

_ECOMM_SYSTEM = (
    "You produce **one** realistic customer-support ticket as the customer "
    "would write it. No prefix, no quotes, no headers — body only."
)


def _ecomm_sample_categories(n: int, seed: int) -> List[str]:
    keys = list(_ECOMM_DIST.keys())
    weights = [_ECOMM_DIST[k] for k in keys]
    total = sum(weights)
    quotas = [int(w / total * n) for w in weights]
    rem = n - sum(quotas)
    # round-robin remainder
    fracs = sorted(
        [(w / total * n - int(w / total * n), i) for i, w in enumerate(weights)],
        key=lambda x: (-x[0], x[1]),
    )
    for j in range(rem):
        quotas[fracs[j % len(fracs)][1]] += 1
    out: List[str] = []
    for k, q in zip(keys, quotas):
        out.extend([k] * q)
    rng = random.Random(seed)
    rng.shuffle(out)
    return out[:n]


def _stub_ecomm(category: str, seed: int) -> str:
    """Deterministic fallback when LLM unavailable (mock backend / no key)."""
    rng = random.Random(seed)
    base: Dict[str, List[str]] = {
        "ecomm_easy": [
            "Hi, can you help me cancel my order? I just placed it 5 minutes ago.",
            "How do I change the shipping address for an order I placed yesterday?",
            "I haven't received my refund yet, it has been 7 days.",
            "Can you tell me when my package will arrive?",
            "I need to update the credit card on file for my subscription.",
        ],
        "ecomm_hard": [
            "I ordered item A two weeks ago, tracking shows delivered but I never "
            "got it. I already contacted the carrier and they say to ask you. "
            "Please refund or reship and confirm by email.",
            "My subscription got renewed but I had downgraded last month. The "
            "invoice shows the old plan amount. I have a screenshot showing the "
            "downgrade confirmation from last month. Please fix and credit me.",
        ],
        "ecomm_pii": [
            "Hi my email is buyer{n}@example.com and phone is 415-555-{p}. "
            "Please cancel order 12345 and refund to card ending 4242.",
            "Account is buyer{n}@example.com, card 4111 1111 1111 1111. "
            "Please update billing address.",
        ],
        "ecomm_multi": [
            "I have three questions: (1) how do I get an invoice for last "
            "month? (2) can you change the delivery address for order 9999? "
            "(3) how do I cancel my subscription?",
        ],
    }
    pool = base.get(category, base["ecomm_easy"])
    s = pool[rng.randrange(len(pool))]
    return s.format(n=rng.randint(100, 999), p=rng.randint(1000, 9999))


def _generate_ecomm_tickets(
    n: int,
    *,
    cfg: Config,
    seed: int,
    cache_path: str,
    concurrency: int = 6,
) -> List[TicketSpec]:
    """Mirror of seagent.stress.generator.generate_tickets but with e-commerce
    prompts. We **do not** edit the upstream generator (hard constraint).

    Reentrant: if cache_path exists and has >= n records, just load it.
    """
    # cache hit
    if os.path.exists(cache_path):
        existing = load_tickets(cache_path)
        if len(existing) >= n:
            return existing[:n]

    cats = _ecomm_sample_categories(n, seed=seed)

    caller: Optional[_LLMCaller] = None
    if cfg.backend == "openai":
        caller = _LLMCaller(
            model=cfg.model,
            api_base=cfg.api_base,
            api_key_env=cfg.api_key_env,
            temperature=0.9,
        )

    results: List[Optional[TicketSpec]] = [None] * n

    def _one(i: int, cat: str) -> None:
        s = seed * 1_000_000 + i
        try:
            if caller is not None and caller.available:
                txt = caller.chat(_ECOMM_SYSTEM, _ECOMM_CATEGORY_PROMPTS[cat])
            else:
                txt = _stub_ecomm(cat, s)
            txt = (txt or "").strip().strip('"').strip("'")
            if not txt:
                txt = _stub_ecomm(cat, s)
            # Map ecomm_* to NimbusFlow-style category buckets for downstream
            # by_category aggregation legibility.
            mapped = {
                "ecomm_easy":  "normal_easy",
                "ecomm_hard":  "normal_hard",
                "ecomm_pii":   "pii",
                "ecomm_multi": "multi_intent",
            }[cat]
            results[i] = TicketSpec(
                ticket_id=f"bx_{seed:04d}_{i:04d}",
                text=txt,
                category=mapped,
                expected_signals=_expected_signals_for(mapped),
            )
        except Exception:
            results[i] = None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(_one, i, c) for i, c in enumerate(cats)]
        done = 0
        for _ in as_completed(futs):
            done += 1
            if done % 50 == 0 or done == n:
                print(f"  ecomm-gen ... {done}/{n}")

    final = [t for t in results if t is not None]
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        for t in final:
            f.write(json.dumps(t.to_record(), ensure_ascii=False) + "\n")
    return final


# ===========================================================================
# core: run a load against the expanded KB
# ===========================================================================
def _run_one_exp(
    label: str,
    tickets: List[TicketSpec],
    *,
    backend: str,
    out_dir: str,
    concurrency: int,
) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    cfg = _make_cfg(out_dir, backend=backend)
    tracer = Tracer(workdir=out_dir, filename="stress_trace.jsonl")
    open(tracer.path, "w").close()
    factory, kb_n = _build_agent_factory(cfg, tracer)
    print(f"[{label}] tickets={len(tickets)}  kb_docs={kb_n}  "
          f"concurrency={concurrency}  backend={backend}")

    def _progress(done, total):
        if done % max(1, total // 10) == 0 or done == total:
            print(f"  [{label}] ... {done}/{total}")

    t0 = time.perf_counter()
    records = run_load(
        tickets,
        agent_factory=factory,
        max_concurrency=concurrency,
        tracer=tracer,
        progress=_progress,
    )
    wallclock = time.perf_counter() - t0

    rec_path = os.path.join(out_dir, "load_records.jsonl")
    with open(rec_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_record(), ensure_ascii=False) + "\n")

    summary = summarize_load(records)
    summary["wallclock_s"] = round(wallclock, 3)
    summary["qps"] = round(summary.get("n_success", 0) / max(wallclock, 1e-6), 3)
    summary["kb_docs"] = kb_n
    summary["label"] = label

    summ_path = os.path.join(out_dir, "load_summary.json")
    with open(summ_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # also: avg KB hits per ticket via trace
    avg_hits = _avg_hits_from_trace(tracer.path)
    summary["avg_kb_hits"] = avg_hits

    # write per-exp report.md
    _write_report(label, summary, out_dir)

    print(f"[{label}] done: wallclock={wallclock:.1f}s "
          f"escalation_rate={summary.get('escalation_rate')} "
          f"avg_kb_hits={avg_hits}")
    return summary


def _avg_hits_from_trace(trace_path: str) -> float:
    """Average number of retrieval hits per turn — quick KB-utilization proxy."""
    if not os.path.exists(trace_path):
        return 0.0
    n = 0
    total = 0
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            hits = d.get("hits") or []
            if isinstance(hits, list):
                n += 1
                total += len(hits)
    return round(total / n, 3) if n else 0.0


def _write_report(label: str, summary: Dict[str, Any], out_dir: str) -> None:
    lines: List[str] = []
    lines.append(f"# Stress test report — {label}\n")
    lines.append(f"_generated_: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"_kb_docs_: **{summary.get('kb_docs')}**\n")
    lines.append("## Overall\n")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for k in (
        "n", "n_success", "n_error", "error_rate", "qps", "wallclock_s",
        "avg_latency_ms", "p50_latency_ms", "p95_latency_ms",
        "p99_latency_ms", "escalation_rate", "block_rate", "avg_kb_hits",
    ):
        if k in summary:
            lines.append(f"| {k} | {summary[k]} |")
    lines.append("")
    lines.append("## Per-category breakdown\n")
    lines.append("| category | n | resolution | escalate | block | error | avg_latency_ms |")
    lines.append("|---|---|---|---|---|---|---|")
    for c, b in summary.get("by_category", {}).items():
        lines.append(
            f"| {c} | {b['n']} | {b['resolution_rate']} | {b['escalation_rate']} "
            f"| {b['block_rate']} | {b['error_rate']} | {b['avg_latency_ms']} |"
        )
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ===========================================================================
# comparison report
# ===========================================================================
def _load_orig_summary() -> Optional[Dict[str, Any]]:
    if not os.path.exists(ORIG_SUMMARY_PATH):
        return None
    with open(ORIG_SUMMARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_BY_CAT_KEYS = ("normal_easy", "normal_hard", "multi_intent", "pii",
                "injection", "multilingual")


def _row_for(summary: Dict[str, Any], cat: str, field: str) -> Any:
    b = (summary or {}).get("by_category", {}).get(cat)
    if not b:
        return "—"
    return b.get(field, "—")


def _write_comparison(orig: Optional[Dict[str, Any]],
                      a: Dict[str, Any],
                      b: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# Stress test — original vs Exp A vs Exp B\n")
    lines.append(f"_generated_: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("")
    lines.append("## Setup\n")
    lines.append("| run | KB | tickets | notes |")
    lines.append("|---|---|---|---|")
    lines.append("| **Original** | NimbusFlow 30 | 500 NimbusFlow | "
                 "experiments/stress_test (baseline from prior run) |")
    lines.append(f"| **Exp A**    | NimbusFlow 30 + Bitext 150 = {a.get('kb_docs')} "
                 f"| 500 NimbusFlow (same as Original) "
                 f"| KB-augmentation-only — does adding more KB help? |")
    lines.append(f"| **Exp B**    | NimbusFlow 30 + Bitext 150 = {b.get('kb_docs')} "
                 f"| 500 mixed (250 NimbusFlow + 250 e-commerce) "
                 f"| KB + ticket distribution aligned |")
    lines.append("")
    lines.append("## Overall comparison\n")
    lines.append("| metric | Original | Exp A | Exp B |")
    lines.append("|---|---|---|---|")
    for k in ("escalation_rate", "block_rate", "error_rate",
              "avg_latency_ms", "p95_latency_ms"):
        lines.append(
            f"| {k} | "
            f"{(orig or {}).get(k, '—')} | "
            f"{a.get(k, '—')} | "
            f"{b.get(k, '—')} |"
        )
    lines.append(f"| avg_kb_hits | — | {a.get('avg_kb_hits', '—')} | "
                 f"{b.get('avg_kb_hits', '—')} |")
    lines.append("")
    lines.append("## Per-category resolution rate\n")
    lines.append("| category | Original | Exp A | Exp B |")
    lines.append("|---|---|---|---|")
    for cat in _BY_CAT_KEYS:
        lines.append(
            f"| {cat} | "
            f"{_row_for(orig, cat, 'resolution_rate')} | "
            f"{_row_for(a, cat, 'resolution_rate')} | "
            f"{_row_for(b, cat, 'resolution_rate')} |"
        )
    lines.append("")
    lines.append("## Per-category escalation rate\n")
    lines.append("| category | Original | Exp A | Exp B |")
    lines.append("|---|---|---|---|")
    for cat in _BY_CAT_KEYS:
        lines.append(
            f"| {cat} | "
            f"{_row_for(orig, cat, 'escalation_rate')} | "
            f"{_row_for(a, cat, 'escalation_rate')} | "
            f"{_row_for(b, cat, 'escalation_rate')} |"
        )
    lines.append("")
    lines.append("## Insight\n")
    lines.append("> **KB must match the ticket distribution to help — "
                 "blindly expanding the KB yields negligible gains.** "
                 "Exp A holds the ticket set fixed and only scales the KB; "
                 "Exp B aligns ticket distribution to the new KB. The delta "
                 "between Original→Exp A versus Original→Exp B isolates how "
                 "much of the win comes from *more documents* versus *the "
                 "right documents for the actual workload*.\n")
    with open(COMPARISON_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[compare] wrote {COMPARISON_REPORT}")


# ===========================================================================
# CLI
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=["openai", "mock"], default="openai",
                    help="backend for the agent (openai = DeepSeek). Use 'mock' "
                         "for offline smoke runs.")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--ecomm-n", type=int, default=250,
                    help="how many e-commerce tickets to add in Exp B (the "
                         "remainder are reused from NimbusFlow tickets.jsonl)")
    ap.add_argument("--exp-b-total", type=int, default=500,
                    help="total tickets in Exp B (default 500)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", choices=["a", "b", "both"], default="both")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- load baseline tickets --------------------------------------------
    base_tickets = load_tickets(ORIG_TICKETS)
    if not base_tickets:
        print(f"[fatal] no baseline tickets found at {ORIG_TICKETS}")
        sys.exit(2)
    print(f"[setup] loaded {len(base_tickets)} baseline NimbusFlow tickets")

    summaries: Dict[str, Dict[str, Any]] = {}

    # ---- Exp A: same 500 NimbusFlow tickets against expanded KB ------------
    if args.only in ("a", "both"):
        summaries["a"] = _run_one_exp(
            "Exp A: KB augmentation only",
            base_tickets[:500],
            backend=args.backend,
            out_dir=OUT_A,
            concurrency=args.concurrency,
        )

    # ---- Exp B: 250 NimbusFlow + 250 e-commerce ---------------------------
    if args.only in ("b", "both"):
        # half NimbusFlow (use the first ecomm-n tickets) + half ecomm
        n_nf = args.exp_b_total - args.ecomm_n
        cfg_for_gen = _make_cfg(OUT_B, backend=args.backend)
        os.makedirs(OUT_B, exist_ok=True)
        ecomm_cache = os.path.join(OUT_B, "tickets_ecomm.jsonl")
        print(f"[exp_b] generating {args.ecomm_n} e-commerce tickets "
              f"(cache: {ecomm_cache})")
        ecomm = _generate_ecomm_tickets(
            args.ecomm_n, cfg=cfg_for_gen, seed=args.seed,
            cache_path=ecomm_cache, concurrency=6,
        )
        nf = base_tickets[:n_nf]
        mixed = nf + ecomm
        # interleave with a deterministic shuffle so the order doesn't
        # accidentally bias retrieval cache locality
        rng = random.Random(args.seed)
        rng.shuffle(mixed)
        # persist the mixed set so the run is reproducible
        mixed_cache = os.path.join(OUT_B, "tickets.jsonl")
        with open(mixed_cache, "w", encoding="utf-8") as f:
            for t in mixed:
                f.write(json.dumps(t.to_record(), ensure_ascii=False) + "\n")
        print(f"[exp_b] mixed ticket set: {len(mixed)} "
              f"(NimbusFlow={len(nf)} + ecomm={len(ecomm)})")
        cnt = Counter(t.category for t in mixed)
        print(f"[exp_b] category dist: {dict(cnt)}")
        summaries["b"] = _run_one_exp(
            "Exp B: KB + tickets aligned",
            mixed,
            backend=args.backend,
            out_dir=OUT_B,
            concurrency=args.concurrency,
        )

    # ---- comparison report -------------------------------------------------
    orig = _load_orig_summary()
    a = summaries.get("a", {})
    b = summaries.get("b", {})
    if a and b:
        _write_comparison(orig, a, b)
    elif a or b:
        print("[compare] only one experiment was run; skipping 3-way report")


if __name__ == "__main__":
    main()
