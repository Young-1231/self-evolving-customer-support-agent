#!/usr/bin/env python
"""端到端规模化压测编排：generate -> load -> memory -> report。

用法:

  # 全部跑(默认 N=500, concurrency=20, 默认 DeepSeek V4-Flash)
  python scripts/run_stress_test.py all

  # 只生成工单(缓存到 experiments/stress_test/tickets.jsonl)
  python scripts/run_stress_test.py generate --n 500

  # 只跑并发压测(用已有 tickets.jsonl)
  python scripts/run_stress_test.py load --concurrency 20

  # 只跑记忆膨胀
  python scripts/run_stress_test.py memory

预算保护：开跑前会估算 token / USD; 超过 $5 阈值需 --yes 显式确认。

完全不修改 src/seagent 任何文件; 走 production path(SupportAgent +
GuardrailPipeline + Tracer)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# 让脚本既能从仓库根直接 `python scripts/...` 跑也能从 PYTHONPATH=src 跑
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config
from seagent.data import load_kb
from seagent.memory.semantic import SemanticMemory
from seagent.memory.episodic import EpisodicMemory
from seagent.agent.support_agent import SupportAgent
from seagent.guardrails import GuardrailPipeline
from seagent.obs import Tracer, estimate_cost, estimate_tokens
from seagent.stress import (
    DEFAULT_DISTRIBUTION,
    TicketSpec,
    generate_tickets,
    load_tickets,
    run_load,
    summarize_load,
    scale_memory,
)
from seagent.stress.generator import estimate_generation_cost
from seagent.stress.memory_scaling import find_knee


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORKDIR = os.path.join(ROOT, "experiments", "stress_test")
TICKETS_PATH = os.path.join(WORKDIR, "tickets.jsonl")
LOAD_RECORDS_PATH = os.path.join(WORKDIR, "load_records.jsonl")
MEMORY_POINTS_PATH = os.path.join(WORKDIR, "memory_points.jsonl")
REPORT_PATH = os.path.join(WORKDIR, "report.md")
TRACE_FILENAME = "stress_trace.jsonl"


# ============================================================================
# 公共：构造 agent factory(线程局部, 每 worker 一份)
# ============================================================================
def _make_cfg() -> Config:
    cfg = Config.load(os.path.join(ROOT, "configs", "default.yaml"))
    cfg.workdir = WORKDIR
    cfg.backend = "openai"
    cfg.model = os.environ.get("STRESS_MODEL", "deepseek-chat")
    cfg.api_base = os.environ.get("STRESS_API_BASE", "https://api.deepseek.com")
    cfg.api_key_env = os.environ.get("STRESS_API_KEY_ENV", "DEEPSEEK_API_KEY")
    cfg.temperature = float(os.environ.get("STRESS_TEMPERATURE", "0.0"))
    return cfg


def _build_agent_factory(cfg: Config, tracer: Tracer):
    """SemanticMemory 在所有线程共享(只读); EpisodicMemory + agent 每线程独立。"""
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, cfg.score_norm_k)

    def factory():
        from seagent.llm.factory import build_backend
        backend = build_backend(cfg)
        epi = EpisodicMemory(path=None, score_norm_k=cfg.score_norm_k)
        guardrail = GuardrailPipeline()
        return SupportAgent(cfg, backend, semantic, epi, guardrail=guardrail, tracer=tracer)

    return factory


# ============================================================================
# 预算 gate
# ============================================================================
def _budget_gate(n_tickets: int, model: str, *, threshold_usd: float = 5.0,
                 force: bool = False) -> bool:
    """打印预算估算; 超阈值要求 --yes。返回 True 表示通过。

    估算：
      - generation: 用 generator.estimate_generation_cost
      - load: 每条 ticket 约 5 次 LLM 调用 (retrieve 不需 LLM; generation +
        critic 1~2 次), 单次 in~600 tok / out~150 tok (含 KB context)
      - memory scaling: 每点 eval~30 条 * 1 LLM call = 极少
    """
    gen_cost = estimate_generation_cost(n_tickets, model=model)
    per_ticket_in, per_ticket_out = 600, 150
    load_in = n_tickets * per_ticket_in
    load_out = n_tickets * per_ticket_out
    load_usd = estimate_cost(model, load_in, load_out)
    total_usd = gen_cost["usd_estimate"] + load_usd

    print("=" * 60)
    print(f"[budget] model = {model}")
    print(f"[budget] generation: ~{int(gen_cost['in_tokens'])}/{int(gen_cost['out_tokens'])} tok  -> ${gen_cost['usd_estimate']:.4f}")
    print(f"[budget] load     : ~{load_in}/{load_out} tok  -> ${load_usd:.4f}")
    print(f"[budget] TOTAL    : ~${total_usd:.4f}")
    print("=" * 60)
    if total_usd > threshold_usd and not force:
        print(f"[budget] ESTIMATE > ${threshold_usd}; rerun with --yes to proceed.")
        return False
    return True


# ============================================================================
# 子命令: generate
# ============================================================================
def cmd_generate(args) -> List[TicketSpec]:
    os.makedirs(WORKDIR, exist_ok=True)
    cfg = _make_cfg()
    distribution = dict(DEFAULT_DISTRIBUTION)
    print(f"[generate] n={args.n}  concurrency={args.gen_concurrency}  cache={TICKETS_PATH}")

    def _progress(done, total):
        if done % max(1, total // 10) == 0 or done == total:
            print(f"  generating ... {done}/{total}")

    tickets = generate_tickets(
        n=args.n,
        model=cfg.model,
        api_base=cfg.api_base,
        api_key_env=cfg.api_key_env,
        distribution=distribution,
        seed=args.seed,
        cache_path=TICKETS_PATH,
        concurrency=args.gen_concurrency,
        progress=_progress,
    )
    # 输出分布统计
    from collections import Counter
    counts = Counter(t.category for t in tickets)
    print(f"[generate] got {len(tickets)} tickets; distribution = {dict(counts)}")
    return tickets


# ============================================================================
# 子命令: load
# ============================================================================
def cmd_load(args, tickets: Optional[List[TicketSpec]] = None):
    os.makedirs(WORKDIR, exist_ok=True)
    if tickets is None:
        tickets = load_tickets(TICKETS_PATH)
        if not tickets:
            print(f"[load] no tickets found at {TICKETS_PATH}; run `generate` first")
            sys.exit(2)
    print(f"[load] tickets={len(tickets)}  concurrency={args.concurrency}")

    cfg = _make_cfg()
    tracer = Tracer(workdir=WORKDIR, filename=TRACE_FILENAME)
    # 新一轮 trace 清零
    open(tracer.path, "w").close()
    factory = _build_agent_factory(cfg, tracer)

    def _progress(done, total):
        if done % max(1, total // 10) == 0 or done == total:
            print(f"  load running ... {done}/{total}")

    t0 = time.perf_counter()
    records = run_load(
        tickets,
        agent_factory=factory,
        max_concurrency=args.concurrency,
        tracer=tracer,
        progress=_progress,
    )
    wallclock = time.perf_counter() - t0
    # 把每条 record 写盘
    with open(LOAD_RECORDS_PATH, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_record(), ensure_ascii=False) + "\n")

    summary = summarize_load(records)
    summary["wallclock_s"] = round(wallclock, 3)
    summary["qps"] = round(summary.get("n_success", 0) / max(wallclock, 1e-6), 3)
    print(f"[load] done in {wallclock:.2f}s, QPS={summary['qps']}, "
          f"p50={summary.get('p50_latency_ms')}ms p95={summary.get('p95_latency_ms')}ms "
          f"err={summary.get('error_rate')}")
    with open(os.path.join(WORKDIR, "load_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return records, summary


# ============================================================================
# 子命令: memory
# ============================================================================
def cmd_memory(args, eval_tickets: Optional[List[TicketSpec]] = None):
    os.makedirs(WORKDIR, exist_ok=True)
    if eval_tickets is None:
        tickets = load_tickets(TICKETS_PATH)
        if not tickets:
            print(f"[memory] no tickets at {TICKETS_PATH}; run `generate` first")
            sys.exit(2)
        eval_tickets = tickets[: args.memory_eval_n]
    print(f"[memory] sizes={args.memory_sizes}  eval_n={len(eval_tickets)}")

    cfg = _make_cfg()
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, cfg.score_norm_k)

    from seagent.llm.factory import build_backend
    backend = build_backend(cfg)

    def factory_with_epi(epi):
        # 注意：此处用单 agent + 单 backend(顺序跑各 size 点, 不并发)
        return SupportAgent(cfg, backend, semantic, epi)

    def _progress(done, total):
        print(f"  memory point ... {done}/{total}")

    points = scale_memory(
        sizes=args.memory_sizes,
        eval_tickets=eval_tickets,
        agent_factory=factory_with_epi,
        backend=backend,
        score_norm_k=cfg.score_norm_k,
        progress=_progress,
    )
    with open(MEMORY_POINTS_PATH, "w", encoding="utf-8") as f:
        for p in points:
            f.write(json.dumps(p.to_record(), ensure_ascii=False) + "\n")
    knee = find_knee(points)
    print(f"[memory] points written -> {MEMORY_POINTS_PATH}; knee≈{knee}")
    return points, knee


# ============================================================================
# 子命令: all (+ 报告 + 三张图)
# ============================================================================
def _try_plots(records, points, out_dir):
    """matplotlib guarded import — 没装就跳过画图, 不抛错。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[plot] matplotlib not available ({e}); skipping figures")
        return []
    paths: List[str] = []

    # 1) latency 分布(柱状直方)
    try:
        lats = [r.latency_ms for r in records if r.error is None]
        if lats:
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.hist(lats, bins=30, color="#4c78a8", edgecolor="white")
            ax.set_xlabel("latency (ms)"); ax.set_ylabel("count")
            ax.set_title("Per-ticket latency distribution")
            fig.tight_layout()
            p = os.path.join(out_dir, "fig_latency_hist.png")
            fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)
    except Exception as e:
        print(f"[plot] latency hist failed: {e}")

    # 2) 按 category 拆 escalation/error/block
    try:
        from collections import Counter
        cats = sorted({r.category for r in records})
        if cats:
            by_cat = {c: {"resolved": 0, "escalate": 0, "block": 0, "error": 0}
                      for c in cats}
            for r in records:
                if r.error is not None:
                    by_cat[r.category]["error"] += 1
                elif r.guardrail_blocked:
                    by_cat[r.category]["block"] += 1
                elif r.escalate:
                    by_cat[r.category]["escalate"] += 1
                else:
                    by_cat[r.category]["resolved"] += 1
            import numpy as np
            xs = np.arange(len(cats))
            width = 0.2
            fig, ax = plt.subplots(figsize=(8, 4))
            for i, key in enumerate(["resolved", "escalate", "block", "error"]):
                ys = [by_cat[c][key] for c in cats]
                ax.bar(xs + (i - 1.5) * width, ys, width=width, label=key)
            ax.set_xticks(xs); ax.set_xticklabels(cats, rotation=20, ha="right")
            ax.set_ylabel("count"); ax.set_title("Outcome breakdown by ticket category")
            ax.legend(); fig.tight_layout()
            p = os.path.join(out_dir, "fig_category_breakdown.png")
            fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)
    except Exception as e:
        print(f"[plot] category breakdown failed: {e}")

    # 3) memory 规模 vs (retrieval latency, resolution rate) 双轴
    try:
        if points:
            sizes = [p.size for p in points]
            lats = [p.avg_retrieval_ms for p in points]
            res = [p.resolution_rate for p in points]
            fig, ax1 = plt.subplots(figsize=(6, 4))
            ax1.plot(sizes, lats, "-o", color="#e45756", label="avg retrieval ms")
            ax1.set_xscale("log"); ax1.set_xlabel("episodic memory size")
            ax1.set_ylabel("avg retrieval (ms)", color="#e45756")
            ax2 = ax1.twinx()
            ax2.plot(sizes, res, "-s", color="#4c78a8", label="resolution rate")
            ax2.set_ylabel("resolution rate", color="#4c78a8")
            ax2.set_ylim(0, 1.0)
            ax1.set_title("Memory scaling: retrieval latency vs quality")
            fig.tight_layout()
            p = os.path.join(out_dir, "fig_memory_scaling.png")
            fig.savefig(p, dpi=150); plt.close(fig); paths.append(p)
    except Exception as e:
        print(f"[plot] memory scaling failed: {e}")
    return paths


def _write_report(*, n_tickets: int, summary: Dict[str, Any],
                  points, knee, fig_paths) -> str:
    lines: List[str] = []
    lines.append(f"# Stress test report — N={n_tickets}\n")
    lines.append(f"_generated_: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## 1. 总体指标\n")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k in ("n", "n_success", "n_error", "error_rate", "qps", "wallclock_s",
              "avg_latency_ms", "p50_latency_ms", "p95_latency_ms",
              "p99_latency_ms", "escalation_rate", "block_rate",
              "avg_cost_usd", "total_cost_usd"):
        if k in summary:
            lines.append(f"| {k} | {summary[k]} |")
    lines.append("")
    lines.append("## 2. 按类别拆解\n")
    lines.append("| category | n | resolution | escalate | block | error | avg_latency_ms |")
    lines.append("|---|---|---|---|---|---|---|")
    for c, b in summary.get("by_category", {}).items():
        lines.append(f"| {c} | {b['n']} | {b['resolution_rate']} | {b['escalation_rate']} "
                     f"| {b['block_rate']} | {b['error_rate']} | {b['avg_latency_ms']} |")
    lines.append("")
    lines.append("## 3. 记忆膨胀\n")
    lines.append("| size | avg_retrieval_ms | p95_retrieval_ms | resolution_rate | escalation_rate |")
    lines.append("|---|---|---|---|---|")
    for p in points or []:
        lines.append(f"| {p.size} | {p.avg_retrieval_ms} | {p.p95_retrieval_ms} "
                     f"| {p.resolution_rate} | {p.escalation_rate} |")
    if knee:
        lines.append(f"\n**knee ≈ size={knee}** — 建议在此规模前启用 TTL / case dedup。\n")
    if fig_paths:
        lines.append("\n## 4. 图\n")
        for fp in fig_paths:
            lines.append(f"![{os.path.basename(fp)}]({os.path.basename(fp)})")
    return "\n".join(lines) + "\n"


def cmd_all(args):
    cfg = _make_cfg()
    if not _budget_gate(args.n, cfg.model, threshold_usd=args.budget_usd,
                        force=args.yes):
        sys.exit(3)

    tickets = cmd_generate(args)
    records, summary = cmd_load(args, tickets=tickets)
    points, knee = cmd_memory(args, eval_tickets=tickets[: args.memory_eval_n])
    fig_paths = _try_plots(records, points, WORKDIR)
    md = _write_report(n_tickets=len(tickets), summary=summary, points=points,
                       knee=knee, fig_paths=fig_paths)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[all] report -> {REPORT_PATH}")
    if fig_paths:
        print(f"[all] figures -> {fig_paths}")


# ============================================================================
# CLI
# ============================================================================
def _add_common(p):
    p.add_argument("--n", type=int, default=500)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=20,
                   help="load runner concurrency")
    p.add_argument("--gen-concurrency", type=int, default=8,
                   help="ticket generator concurrency")
    p.add_argument("--memory-sizes", type=int, nargs="+",
                   default=[10, 100, 1000, 5000])
    p.add_argument("--memory-eval-n", type=int, default=30)
    p.add_argument("--budget-usd", type=float, default=5.0)
    p.add_argument("--yes", action="store_true",
                   help="bypass budget confirmation when over threshold")


def main():
    parser = argparse.ArgumentParser(description="LLM-driven stress test for the self-evolving support agent")
    subs = parser.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "load", "memory", "all"):
        sp = subs.add_parser(name)
        _add_common(sp)
    args = parser.parse_args()

    if args.cmd == "generate":
        cmd_generate(args)
    elif args.cmd == "load":
        cmd_load(args)
    elif args.cmd == "memory":
        cmd_memory(args)
    elif args.cmd == "all":
        cmd_all(args)


if __name__ == "__main__":
    main()
