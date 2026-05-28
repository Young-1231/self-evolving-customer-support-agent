#!/usr/bin/env python
"""Exp G — 10k+ episodic case memory scale stress (v2.5 R4).

Validates the OpenViking-style FsEpisodicStore vs the legacy flat jsonl
EpisodicMemory at populated-pool scale (>=10k cases).  Exp F established
parity at *cold start* (0 cases); Exp G is the populated-pool regime where
OpenViking self-reports +6 / +12pp on tau2-bench retail / airline.

No LLM call.  Deterministic.  Does not touch any existing src/seagent file.

Pipeline:
  1. synth N cases (default 10000) over 15 topics x 12 months, mixing
     NimbusFlow KB topic labels and Bitext-style fillers (synthetic — no
     real Bitext data is downloaded).  Each case has a deterministic
     resolution string that contains a small set of keypoints (3-4 short
     phrases).  Keypoints are recoverable by both BM25 jsonl and the
     L0/L1 fs_store.
  2. build a 200-query held-out eval set.  Each query is paraphrased from
     a stored case so retrieval has a non-trivial ground-truth target.
     Verifier scores resolution_rate via keypoint coverage (Section 5.4
     of the design doc — gold labels, agent never sees them).
  3. for each backend (jsonl, fs_topic_date) populate the store with the
     full 10k cases (timed), run the 200 queries (timed per-call via
     time.perf_counter wrapping store.retrieve), then run the SupportAgent
     end-to-end with a MockBackend to compute resolution_rate /
     escalation_rate via the existing verifier.

Outputs to experiments/exp_g/:
  - results.json        : machine-readable metrics
  - report.md           : human-readable comparison
  - synth_meta.json     : synth parameters for reproducibility
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from dataclasses import asdict
from typing import Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from seagent.agent.support_agent import SupportAgent  # noqa: E402
from seagent.config import Config  # noqa: E402
from seagent.data import Query, load_kb  # noqa: E402
from seagent.eval.verifier import verify  # noqa: E402
from seagent.llm.mock import MockBackend  # noqa: E402
from seagent.memory.episodic import Case, EpisodicMemory  # noqa: E402
from seagent.memory.fs_store import FsEpisodicStore  # noqa: E402
from seagent.memory.semantic import SemanticMemory  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic topic / template catalog
# ---------------------------------------------------------------------------

# 15 topics: 8 NimbusFlow KB topics + 7 Bitext-style synthetic fillers.
# Bitext-style labels mirror the categories from the real Bitext customer
# support intent dataset (orders / cancellation / shipping / refunds / ...).
TOPICS: List[str] = [
    # NimbusFlow KB topics (from data/kb/index.jsonl)
    "billing", "account_security", "data_export", "integrations_api",
    "mobile_app", "permissions", "troubleshooting", "general",
    # Bitext-style synthetic filler topics
    "cancel_order", "track_order", "refund", "shipping",
    "create_account", "delete_account", "invoice",
]

# Per-topic (paraphrase_query_template, resolution_template, keypoints_template).
# The keypoint phrases are intentionally short and distinctive so verifier
# substring matching is reliable.  {n} is the variant id so we generate many
# unique-but-related cases under each topic.
#
# NOTE on keypoint design (load-bearing):
#   - We use TOPIC-LEVEL keypoints (same across all cases under a topic),
#     not case-id-specific phrases.  That means resolution_rate measures
#     "did retrieval surface a same-topic case", which is exactly the
#     property fs_store's L0/L1 narrowing should improve at scale.  An
#     id-specific keypoint would only test exact-case precision, which is
#     a different (and at 10k uniformly noisy) signal and is captured by
#     `avg_coverage` separately.
#   - Each topic still embeds an id-tag in the case query/resolution body
#     to keep cases unique (so the synth pool has 10k distinct documents,
#     not 15 duplicated 666x).
_TOPIC_RECIPES: Dict[str, Dict[str, str]] = {
    "billing": {
        "q": "我的工单{n}号订阅账单出问题了，怎么办",
        "r": "针对账单问题(BILL-{n:05d})，请进入 设置>账单 检查付款方式，如重复扣费可在3-5个工作日内自动撤销。",
        "kps": ["设置>账单", "付款方式", "3-5个工作日"],
    },
    "account_security": {
        "q": "我账号工单{n}号的两步验证不工作了怎么办",
        "r": "针对账号安全(SEC-{n:05d})，请进入 设置>安全 重新绑定 TOTP，备份恢复码链接15分钟内有效。",
        "kps": ["设置>安全", "TOTP", "15分钟内有效"],
    },
    "data_export": {
        "q": "我想导出工单{n}号工作区的数据，怎么操作",
        "r": "数据导出(EXP-{n:05d})请进入 设置>数据导出，导出包通常24小时内可下载，格式为CSV。",
        "kps": ["设置>数据导出", "CSV", "24小时内可下载"],
    },
    "integrations_api": {
        "q": "API集成工单{n}号一直401，怎么排查",
        "r": "API 401(API-{n:05d}) 通常因 Token 过期，请重新生成密钥，并检查请求头 Authorization Bearer 格式。",
        "kps": ["Token", "Authorization", "Bearer"],
    },
    "mobile_app": {
        "q": "手机App工单{n}号打不开闪退怎么办",
        "r": "App闪退(APP-{n:05d})请清理缓存并升级至最新版本，如仍异常请提交日志包。",
        "kps": ["清理缓存", "升级至最新版本", "日志包"],
    },
    "permissions": {
        "q": "工作区工单{n}号的成员权限怎么改",
        "r": "成员权限(PERM-{n:05d})请进入 设置>成员，仅管理员可调整角色为 Owner/Admin/Member。",
        "kps": ["设置>成员", "管理员", "Owner/Admin/Member"],
    },
    "troubleshooting": {
        "q": "工单{n}号工作区一直加载不出来，是bug吗",
        "r": "加载异常(TRB-{n:05d})请刷新并清空Cookie，如5分钟内仍异常请上报。",
        "kps": ["清空Cookie", "刷新", "上报"],
    },
    "general": {
        "q": "我有个工单{n}号一般问题想咨询",
        "r": "一般咨询(GEN-{n:05d})请走帮助中心，工作日4小时内回复。",
        "kps": ["帮助中心", "工作日", "4小时内回复"],
    },
    "cancel_order": {
        "q": "订单工单{n}号怎么取消啊",
        "r": "取消订单(ORD-{n:05d})请进入 订单列表 选择对应订单，取消后退款3-7个工作日到账。",
        "kps": ["订单列表", "取消", "3-7个工作日"],
    },
    "track_order": {
        "q": "我想查工单{n}号订单到哪了",
        "r": "查询物流(TRK-{n:05d})请进入 我的订单>查看物流，可点击物流公司链接查看实时位置。",
        "kps": ["我的订单>查看物流", "物流公司", "实时位置"],
    },
    "refund": {
        "q": "工单{n}号退款怎么还没到账",
        "r": "退款查询(RFD-{n:05d})请进入 设置>退款，原路退回通常需3-5个工作日。",
        "kps": ["设置>退款", "原路退回", "3-5个工作日"],
    },
    "shipping": {
        "q": "工单{n}号快递怎么改地址",
        "r": "修改收货地址(SHP-{n:05d})请在订单未发货前进入 订单详情，已发货订单需联系物流方。",
        "kps": ["订单详情", "已发货订单", "物流方"],
    },
    "create_account": {
        "q": "怎么注册工单{n}号新账号",
        "r": "注册账号(REG-{n:05d})请进入官网注册页，密码至少8位且需含字母与数字。",
        "kps": ["官网注册页", "至少8位", "字母与数字"],
    },
    "delete_account": {
        "q": "我想注销工单{n}号账号怎么办",
        "r": "账号注销(DEL-{n:05d})请进入 设置>账户>注销账号，注销后数据保留30天可恢复。",
        "kps": ["设置>账户>注销账号", "30天可恢复", "注销"],
    },
    "invoice": {
        "q": "我想下载工单{n}号发票",
        "r": "发票下载(INV-{n:05d})请进入 设置>账单>发票历史，电子发票24小时内可下载。",
        "kps": ["设置>账单>发票历史", "电子发票", "24小时内可下载"],
    },
}

# Paraphrase wording for the eval queries — must differ from synth case
# query wording so retrieval has to do real matching (not exact echo).
_PARAPHRASE_PREFIX = [
    "你好，请问", "麻烦帮我看下", "客服你好", "请问一下",
    "急，", "在线等：", "想咨询一下，", "",
]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

def _month_for(i: int) -> str:
    # 12 months, deterministic — yields ~833 cases per L1 bucket at n=10k.
    return f"2026-{((i // 1000) % 12) + 1:02d}"


def synth_cases(n: int, seed: int = 0) -> List[Tuple[Case, Dict[str, object]]]:
    """Generate ``n`` cases, returning list of (Case, metadata)."""
    rng = random.Random(seed)
    out: List[Tuple[Case, Dict[str, object]]] = []
    for i in range(n):
        topic = TOPICS[i % len(TOPICS)]
        recipe = _TOPIC_RECIPES[topic]
        query = recipe["q"].format(n=i)
        resolution = recipe["r"].format(n=i)
        # Slight noise in case_id to mimic real ticket-ID variety.
        case_id = f"case_{i:05d}"
        c = Case(
            case_id=case_id,
            query=query,
            resolution=resolution,
            should_escalate=False,
            topic=topic,
            source_query_id="g",
            learned_round=0,
        )
        meta = {"created_at": _month_for(i)}
        out.append((c, meta))
    rng.shuffle(out)  # de-cluster contiguous topic runs
    return out


def synth_eval_queries(n: int, n_cases: int, seed: int = 7) -> List[Query]:
    """Build ``n`` eval queries.  Each query is a paraphrase of a stored
    case (sampled uniformly across topics and indices)."""
    rng = random.Random(seed)
    out: List[Query] = []
    for k in range(n):
        # uniform topic coverage; sample a case index that lies in that topic
        topic = TOPICS[k % len(TOPICS)]
        # the synth pool deterministically assigns case i -> TOPICS[i % 15]
        # so any i with i % 15 == TOPICS.index(topic) belongs to topic.
        ti = TOPICS.index(topic)
        # pick the m'th case under this topic (cycling)
        m = k // len(TOPICS)
        # we have n_cases // 15 cases per topic; clamp m
        per_topic = n_cases // len(TOPICS)
        m = m % max(1, per_topic)
        i = ti + m * len(TOPICS)
        if i >= n_cases:
            i = ti
        recipe = _TOPIC_RECIPES[topic]
        # paraphrase: change wording style but keep {n} marker so the matching
        # case (by id index i) is the gold-target case
        pref = _PARAPHRASE_PREFIX[k % len(_PARAPHRASE_PREFIX)]
        # Wording variation: swap '号' for '的' and drop some particles
        raw_q = recipe["q"].format(n=i)
        paraphrased = pref + raw_q.replace("号", "的").replace("怎么办", "如何处理")
        kps_raw = [s.format(n=i) for s in recipe["kps"]]
        out.append(Query(
            id=f"evg_{k:04d}",
            split="eval",
            group=f"g_{topic}",
            query=paraphrased,
            required_keypoints=kps_raw,
            gold_doc_ids=[],
            should_escalate=False,
            difficulty="medium",
            resolution=recipe["r"].format(n=i),
        ))
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------------------
# Run one backend
# ---------------------------------------------------------------------------

def _populate_jsonl(cases: List[Tuple[Case, Dict[str, object]]],
                    k: float) -> Tuple[EpisodicMemory, float]:
    t0 = time.perf_counter()
    # Build in O(N) by appending then reindexing once at the end — the
    # default EpisodicMemory.add reindexes on every call (O(N^2) total).
    # We respect "no source edit" by manipulating only public attributes
    # plus the internal _reindex(); both already exist.
    ep = EpisodicMemory(path=None, score_norm_k=k)
    for c, _ in cases:
        ep.cases.append(c)
    ep._reindex()
    dt = time.perf_counter() - t0
    return ep, dt


def _populate_fs(cases: List[Tuple[Case, Dict[str, object]]],
                 k: float, l0_top: int) -> Tuple[FsEpisodicStore, float]:
    t0 = time.perf_counter()
    # root_dir=None keeps the store fully in memory (still exercises L0/L1
    # bucketing and reranker).  Disk I/O is not the metric of interest here.
    st = FsEpisodicStore(root_dir=None, scheme="topic_date",
                         score_norm_k=k, l0_top=l0_top)
    for c, meta in cases:
        st.add(c, metadata=meta)
    dt = time.perf_counter() - t0
    return st, dt


def time_retrievals(store, queries: List[Query], top_k: int) -> Dict[str, float]:
    """Time per-call store.retrieve and return latency stats."""
    times_ms: List[float] = []
    for q in queries:
        t0 = time.perf_counter()
        store.retrieve(q.query, top_k=top_k)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        times_ms.append(dt_ms)
    times_sorted = sorted(times_ms)
    n = len(times_sorted)

    def _p(p: float) -> float:
        if n == 0:
            return 0.0
        idx = min(n - 1, int(p * n))
        return times_sorted[idx]

    return {
        "avg_ms": statistics.fmean(times_ms),
        "p50_ms": _p(0.5),
        "p95_ms": _p(0.95),
        "p99_ms": _p(0.99),
        "max_ms": max(times_ms),
        "n_queries": n,
    }


def run_agent_eval(cfg: Config, semantic: SemanticMemory, episodic,
                   backend: MockBackend, queries: List[Query]) -> Dict[str, float]:
    """Run SupportAgent end-to-end and compute resolution / escalation."""
    agent = SupportAgent(cfg, backend, semantic, episodic, procedural=None)
    n_resolved = 0
    n_escalated = 0
    coverages: List[float] = []
    for q in queries:
        res = agent.handle(q.query)
        v = verify(q, res, cfg.coverage_threshold)
        if v.resolved:
            n_resolved += 1
        if v.pred_escalate:
            n_escalated += 1
        coverages.append(v.coverage)
    n = len(queries)
    return {
        "resolution_rate": n_resolved / n if n else 0.0,
        "escalation_rate": n_escalated / n if n else 0.0,
        "avg_coverage": statistics.fmean(coverages) if coverages else 0.0,
        "n_queries": n,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-cases", type=int, default=10000)
    ap.add_argument("--n-eval", type=int, default=200)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--l0-top", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--outdir", default=os.path.join(HERE, "..", "experiments", "exp_g"))
    args = ap.parse_args()

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    cfg = Config().resolve()
    cfg.backend = "mock"
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, score_norm_k=cfg.score_norm_k)
    backend = MockBackend()

    t_total0 = time.perf_counter()
    print(f"[exp-g] synthesizing {args.n_cases} cases ...")
    t0 = time.perf_counter()
    cases = synth_cases(args.n_cases, seed=args.seed)
    t_synth = time.perf_counter() - t0
    print(f"[exp-g] synth done in {t_synth:.2f}s")

    print(f"[exp-g] building {args.n_eval} held-out eval queries ...")
    eval_qs = synth_eval_queries(args.n_eval, args.n_cases, seed=args.seed + 7)

    results: Dict[str, object] = {
        "config": {
            "n_cases": args.n_cases,
            "n_eval": args.n_eval,
            "top_k": args.top_k,
            "l0_top": args.l0_top,
            "seed": args.seed,
            "score_norm_k": cfg.score_norm_k,
            "coverage_threshold": cfg.coverage_threshold,
            "n_topics": len(TOPICS),
        },
        "synth_seconds": t_synth,
        "backends": {},
    }

    # -- jsonl baseline --
    print("[exp-g] populating jsonl EpisodicMemory ...")
    ep_jsonl, pop_jsonl = _populate_jsonl(cases, cfg.score_norm_k)
    print(f"[exp-g] jsonl populated ({len(ep_jsonl)}) in {pop_jsonl:.2f}s")
    print(f"[exp-g] timing jsonl retrievals ({len(eval_qs)} queries) ...")
    lat_jsonl = time_retrievals(ep_jsonl, eval_qs, args.top_k)
    print(f"[exp-g] jsonl retrieval avg={lat_jsonl['avg_ms']:.2f}ms p95={lat_jsonl['p95_ms']:.2f}ms")
    print("[exp-g] jsonl agent eval ...")
    t0 = time.perf_counter()
    metrics_jsonl = run_agent_eval(cfg, semantic, ep_jsonl, backend, eval_qs)
    eval_t_jsonl = time.perf_counter() - t0
    print(f"[exp-g] jsonl eval done in {eval_t_jsonl:.2f}s  resolution={metrics_jsonl['resolution_rate']*100:.1f}%")

    results["backends"]["jsonl"] = {
        "n_cases": len(ep_jsonl),
        "populate_seconds": pop_jsonl,
        "retrieval_latency": lat_jsonl,
        "agent_eval": metrics_jsonl,
        "agent_eval_seconds": eval_t_jsonl,
    }

    # -- fs_topic_date --
    print("[exp-g] populating FsEpisodicStore(topic_date) ...")
    ep_fs, pop_fs = _populate_fs(cases, cfg.score_norm_k, args.l0_top)
    print(f"[exp-g] fs populated ({len(ep_fs)}) in {pop_fs:.2f}s; stats:")
    fs_stats = ep_fs.stats()
    print(f"        n_l0={fs_stats['n_l0']} n_l1={fs_stats['n_l1']}")
    print(f"[exp-g] timing fs retrievals ({len(eval_qs)} queries) ...")
    lat_fs = time_retrievals(ep_fs, eval_qs, args.top_k)
    print(f"[exp-g] fs retrieval avg={lat_fs['avg_ms']:.2f}ms p95={lat_fs['p95_ms']:.2f}ms")
    print("[exp-g] fs agent eval ...")
    t0 = time.perf_counter()
    metrics_fs = run_agent_eval(cfg, semantic, ep_fs, backend, eval_qs)
    eval_t_fs = time.perf_counter() - t0
    print(f"[exp-g] fs eval done in {eval_t_fs:.2f}s  resolution={metrics_fs['resolution_rate']*100:.1f}%")

    results["backends"]["fs_topic_date"] = {
        "n_cases": len(ep_fs),
        "populate_seconds": pop_fs,
        "retrieval_latency": lat_fs,
        "agent_eval": metrics_fs,
        "agent_eval_seconds": eval_t_fs,
        "fs_stats": {"n_l0": fs_stats["n_l0"], "n_l1": fs_stats["n_l1"]},
    }

    results["total_seconds"] = time.perf_counter() - t_total0

    # ---- write outputs ----
    with open(os.path.join(outdir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(os.path.join(outdir, "synth_meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "topics": TOPICS,
            "n_cases": args.n_cases,
            "n_eval": args.n_eval,
            "seed": args.seed,
            "topic_recipe_keys": list(_TOPIC_RECIPES.keys()),
        }, f, ensure_ascii=False, indent=2)

    _write_report(outdir, results)
    print(f"[exp-g] done in {results['total_seconds']:.1f}s -> {outdir}")
    return 0


def _write_report(outdir: str, results: Dict[str, object]) -> None:
    cfg = results["config"]
    j = results["backends"]["jsonl"]
    f = results["backends"]["fs_topic_date"]

    lines: List[str] = []
    lines.append("# Exp G — 10k+ episodic memory scale stress (v2.5 R4)")
    lines.append("")
    lines.append(
        f"Synthetic NimbusFlow-style benchmark, mock backend, deterministic, "
        f"zero LLM cost.  N={cfg['n_cases']} cases across {cfg['n_topics']} "
        f"topics, eval set = {cfg['n_eval']} held-out paraphrased queries."
    )
    lines.append("")
    lines.append("## Head-to-head: jsonl vs fs_topic_date")
    lines.append("")
    lines.append("| store | n_cases | populate (s) | avg retr (ms) | p50 | p95 | p99 | resolution | escalation |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    def _row(name, payload):
        la = payload["retrieval_latency"]; me = payload["agent_eval"]
        return ("| {name} | {n} | {pop:.2f} | {avg:.2f} | {p50:.2f} | {p95:.2f} | {p99:.2f} "
                "| {res:.1f}% | {esc:.1f}% |").format(
            name=name, n=payload["n_cases"], pop=payload["populate_seconds"],
            avg=la["avg_ms"], p50=la["p50_ms"], p95=la["p95_ms"], p99=la["p99_ms"],
            res=me["resolution_rate"] * 100, esc=me["escalation_rate"] * 100,
        )
    lines.append(_row("EpisodicMemory (jsonl)", j))
    lines.append(_row("FsEpisodicStore (topic_date)", f))
    lines.append("")
    j_res, f_res = j["agent_eval"]["resolution_rate"], f["agent_eval"]["resolution_rate"]
    dpp = (f_res - j_res) * 100
    speedup = j["retrieval_latency"]["avg_ms"] / max(1e-9, f["retrieval_latency"]["avg_ms"])
    lines.append(f"**Delta (fs - jsonl):** resolution {dpp:+.2f} pp; "
                 f"retrieval avg speedup = {speedup:.2f}x  "
                 f"({j['retrieval_latency']['avg_ms']:.2f} -> {f['retrieval_latency']['avg_ms']:.2f} ms).")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Synthetic data; absolute numbers are NOT directly comparable to "
        "OpenViking's tau2-bench retail (+6.87pp) / airline (+11.87pp) "
        "self-reports, but the **direction** (fs_store gains as the pool "
        "grows) is the falsifiable claim."
    )
    lines.append("- jsonl populate uses a single end-of-load `_reindex()` to "
                 "avoid the default O(N^2) add-then-reindex; this is a "
                 "**read-only** access of an existing private method, no source "
                 "is modified.")
    lines.append(f"- fs_store buckets: n_l0={f['fs_stats']['n_l0']}, "
                 f"n_l1={f['fs_stats']['n_l1']}  "
                 f"(l0_top={cfg['l0_top']} kept per query).")
    lines.append("")
    with open(os.path.join(outdir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
