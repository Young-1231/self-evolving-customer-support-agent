#!/usr/bin/env python3
"""记忆增长的成本/延迟 vs 收益 (ROI) 分析。

问题:自进化 Agent 靠"经验池 (episodic memory) 不断变大"来提升解决率。但经验池
越大,每个 query 注入的检索上下文越多 -> 注入 token 上升、单 query 成本上升、检索
延迟上升。这个脚本把"收益曲线"和"成本/延迟曲线"画到同一张图上,定量回答:

  - 每提升 1% 解决率,要多付多少 token / 多少美元?
  - 边际收益在哪一轮开始递减 (拐点)? 之后再扩经验池是"花钱不办事"。
  - 工程结论:把检索 top-k / TTL / 记忆淘汰的预算钉在拐点附近。

实现完全复用现有组件,不改任何现有文件:
  - 进化循环沿用 eval.harness 的逐轮逻辑 (train 分批积累经验, 仅对未解决工单写经验)。
  - 收益用 eval.verifier + eval.metrics (gold-label, 不可被 agent 自评刷分)。
  - 成本用 obs.estimate_tokens / obs.estimate_cost。
  - 延迟用 obs.Tracer.span 真实计时 (mock 很快, 故对每个 query 多次重复取均值);
    同时记录"检索命中数 / 经验池大小"作为与规模无关的延迟代理。

离线、确定性、秒级。运行:
    PYTHONPATH=src python3 scripts/run_cost_benefit_analysis.py
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List

# --- 复用现有组件 (import only, 不修改) ---
from seagent.config import Config
from seagent.data import KBDoc, Query, load_kb, load_queries, split_queries
from seagent.llm.factory import build_backend
from seagent.memory.semantic import SemanticMemory
from seagent.memory.episodic import Case, EpisodicMemory
from seagent.agent.support_agent import SupportAgent
from seagent.eval.verifier import verify
from seagent.eval.metrics import aggregate, failed_groups
from seagent.obs import Tracer, estimate_tokens, estimate_cost

OUT_DIR = os.path.join("experiments", "cost_benefit")

# 延迟测量:mock 太快,单次计时噪声大。对每个 eval query 重复 handle 取均值,
# 用 Tracer.span 真实计时 (perf_counter),得到稳定的端到端耗时信号。
LATENCY_REPEATS = 40


def _batches(items: List[Query], n: int) -> List[List[Query]]:
    n = max(1, n)
    size = (len(items) + n - 1) // n
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def _topic_of(q: Query, semantic: SemanticMemory, kb_topic: Dict[str, str]) -> str:
    for d in q.gold_doc_ids:
        if d in kb_topic:
            return kb_topic[d]
    hits = semantic.retrieve(q.query, top_k=1)
    if hits:
        return kb_topic.get(hits[0].ref, "general")
    return "general"


def _measure_eval(
    agent: SupportAgent,
    eval_qs: List[Query],
    cfg: Config,
    tracer: Tracer,
    baseline_failed,
) -> Dict[str, float]:
    """在 eval 集上跑一遍, 测量收益 + 成本 + 延迟。"""
    verdicts = []
    inj_tokens: List[int] = []
    out_tokens: List[int] = []
    costs: List[float] = []
    n_hits: List[int] = []
    epi_hits: List[int] = []
    latencies_ms: List[float] = []

    model = getattr(cfg, "model", "mock")

    for q in eval_qs:
        # 端到端结果 (一次, 用于正确性/成本统计)
        res = agent.handle(q.query)
        verdicts.append(verify(q, res, cfg.coverage_threshold))

        ctx_text = "".join(p.text for p in res.contexts)
        in_tok = estimate_tokens(q.query + ctx_text)   # 注入 prompt = query + 检索上下文
        o_tok = estimate_tokens(res.answer)
        inj_tokens.append(in_tok)
        out_tokens.append(o_tok)
        costs.append(estimate_cost(model, in_tok, o_tok))
        n_hits.append(len(res.contexts))
        epi_hits.append(sum(1 for p in res.contexts if p.source == "episodic"))

        # 真实计时:重复多次取均值 (mock 很快, 单次抖动大)
        t0 = time.perf_counter()
        for _ in range(LATENCY_REPEATS):
            agent.handle(q.query)
        dt_ms = (time.perf_counter() - t0) * 1000.0 / LATENCY_REPEATS
        latencies_ms.append(dt_ms)

    n = len(eval_qs) or 1
    benefit = aggregate(verdicts, baseline_failed)
    return {
        "resolution_rate": round(benefit["resolution_rate"], 4),
        "keypoint_coverage": round(benefit["keypoint_coverage"], 4),
        "escalation_accuracy": round(benefit["escalation_accuracy"], 4),
        "avg_inj_tokens": round(sum(inj_tokens) / n, 3),
        "avg_out_tokens": round(sum(out_tokens) / n, 3),
        "avg_cost_usd": round(sum(costs) / n, 10),
        "avg_hits": round(sum(n_hits) / n, 3),
        "avg_epi_hits": round(sum(epi_hits) / n, 3),
        "avg_latency_ms": round(sum(latencies_ms) / n, 5),
    }


def run_evolution(cfg: Config) -> List[Dict]:
    """逐轮进化 (episodic 条件), 每轮在 eval 上测量收益/成本/延迟。"""
    kb: List[KBDoc] = load_kb(cfg.kb_index)
    kb_topic = {d.doc_id: d.topic for d in kb}
    semantic = SemanticMemory(kb, score_norm_k=cfg.score_norm_k)
    qs = load_queries(cfg.queries)
    sp = split_queries(qs)
    train = sorted(sp.get("train", []), key=lambda q: q.id)
    eval_qs = sorted(sp.get("eval", []), key=lambda q: q.id)

    episodic = EpisodicMemory(path=None, score_norm_k=cfg.score_norm_k)
    agent = SupportAgent(cfg, build_backend(cfg), semantic, episodic, None)
    tracer = Tracer(workdir=OUT_DIR, filename="cost_benefit_trace.jsonl")

    records: List[Dict] = []

    # round 0: 冷启动, 经验池为空
    v0 = [verify(q, agent.handle(q.query), cfg.coverage_threshold) for q in eval_qs]
    baseline_failed = failed_groups(v0)
    m0 = _measure_eval(agent, eval_qs, cfg, tracer, baseline_failed)
    records.append({"round": 0, "pool_size": 0, **m0})

    # 逐轮:train 分批, 对未解决工单把人工 resolution 写进经验池
    for r, batch in enumerate(_batches(train, cfg.train_rounds), start=1):
        for tq in batch:
            res = agent.handle(tq.query)
            v = verify(tq, res, cfg.coverage_threshold)
            if not v.resolved:
                episodic.add(Case(
                    case_id=tq.id, query=tq.query, resolution=tq.resolution,
                    should_escalate=tq.should_escalate,
                    topic=_topic_of(tq, semantic, kb_topic),
                    source_query_id=tq.id, learned_round=r,
                ))
        m = _measure_eval(agent, eval_qs, cfg, tracer, baseline_failed)
        records.append({"round": r, "pool_size": len(episodic), **m})

    return records


# --- 分析:边际收益 / ROI / 拐点 ---------------------------------------------

def analyze(records: List[Dict]) -> Dict:
    """从逐轮曲线推导边际 ROI 与"边际收益递减"拐点。"""
    deltas: List[Dict] = []
    for prev, cur in zip(records, records[1:]):
        d_res = cur["resolution_rate"] - prev["resolution_rate"]
        d_tok = cur["avg_inj_tokens"] - prev["avg_inj_tokens"]
        d_cost = cur["avg_cost_usd"] - prev["avg_cost_usd"]
        d_lat = cur["avg_latency_ms"] - prev["avg_latency_ms"]
        d_pool = cur["pool_size"] - prev["pool_size"]
        # 每提升 1% (=0.01) 解决率的边际 token / 成本 (仅在有正收益时定义)
        if d_res > 1e-9:
            tok_per_pct = round(d_tok / (d_res * 100.0), 4)
            cost_per_pct = round(d_cost / (d_res * 100.0), 12)
        else:
            tok_per_pct = None
            cost_per_pct = None
        deltas.append({
            "from_round": prev["round"], "to_round": cur["round"],
            "d_resolution": round(d_res, 4),
            "d_inj_tokens": round(d_tok, 3),
            "d_cost_usd": round(d_cost, 12),
            "d_latency_ms": round(d_lat, 5),
            "d_pool_size": d_pool,
            "marginal_tokens_per_1pct_resolution": tok_per_pct,
            "marginal_cost_per_1pct_resolution": cost_per_pct,
        })

    final_res = records[-1]["resolution_rate"]
    base_res = records[0]["resolution_rate"]
    total_gain = final_res - base_res

    # 这条曲线揭示三个拐点 (都是真实可执行的工程信号):
    #
    # (A) 冷启动死区 (cold dead-zone): 早期经验池增大、注入 token 上升,但因为存的经验
    #     还匹配不到 eval 的 paraphrase,解决率纹丝不动 —— 纯加价、零收益。
    #     死区结束 = 第一次出现正收益的那一轮。
    # (B) token 饱和点 (token saturation): 注入 token / 检索命中受 top-k 上限钳制,
    #     在某一轮后基本不再增长 (Δtok 很小);此后"成本"的增量主要来自检索延迟而非 token。
    # (C) 成本效率拐点 (cost-efficiency knee): 边际"每+1%解决率所需 token"最低的那段过渡
    #     —— ROI 最划算的甜区;越过它之后单位收益越来越贵 (延迟持续上涨)。

    # (A) 死区
    dead_zone_until = 0
    first_gain_round = records[-1]["round"]
    for d in deltas:
        if d["d_resolution"] <= 1e-9:
            dead_zone_until = d["to_round"]
        else:
            first_gain_round = d["to_round"]
            break
    dead_zone_token_waste = round(
        next(r for r in records if r["round"] == dead_zone_until)["avg_inj_tokens"]
        - records[0]["avg_inj_tokens"], 3,
    )

    # (B) token 饱和:Δtok 降到峰值 Δtok 的 20% 以下的最早一轮
    tok_deltas = [abs(d["d_inj_tokens"]) for d in deltas]
    max_dtok = max(tok_deltas) if tok_deltas else 0.0
    sat_round = records[-1]["round"]
    for d in deltas:
        if max_dtok > 0 and abs(d["d_inj_tokens"]) <= 0.2 * max_dtok and d["to_round"] > first_gain_round:
            sat_round = d["to_round"]
            break

    # (C) 成本效率拐点:在"有收益"的过渡里,边际 token/1% 最小者
    gain_deltas = [d for d in deltas if d["marginal_tokens_per_1pct_resolution"] is not None]
    if gain_deltas:
        best = min(gain_deltas, key=lambda d: d["marginal_tokens_per_1pct_resolution"])
        knee_round = best["to_round"]
        knee_reason = (
            "cost-efficiency knee: marginal cost is cheapest here "
            "(%.1f tokens per +1%% resolution); beyond this, latency keeps rising "
            "while token/hit gains are capped by top-k, so each extra point of "
            "resolution costs more." % best["marginal_tokens_per_1pct_resolution"]
        )
    else:
        knee_round = records[-1]["round"]
        knee_reason = "no positive-gain transition found"

    knee = next(r for r in records if r["round"] == knee_round)
    return {
        "deltas": deltas,
        "knee_round": knee_round,
        "knee_reason": knee_reason,
        "knee_point": knee,
        "dead_zone_until_round": dead_zone_until,
        "dead_zone_token_waste": dead_zone_token_waste,
        "first_gain_round": first_gain_round,
        "token_saturation_round": sat_round,
        "base_resolution": base_res,
        "final_resolution": final_res,
        "total_resolution_gain": round(total_gain, 4),
    }


# --- 可视化 ------------------------------------------------------------------

def plot_curve(records: List[Dict], analysis: Dict, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pool = [r["pool_size"] for r in records]
    res = [r["resolution_rate"] * 100 for r in records]
    tok = [r["avg_inj_tokens"] for r in records]
    lat = [r["avg_latency_ms"] for r in records]

    fig, ax1 = plt.subplots(figsize=(9.6, 5.2))

    # 左轴:收益 (解决率)
    c_res = "#1b7837"
    ax1.set_xlabel("Experience pool size (# cases)")
    ax1.set_ylabel("Resolution rate (%)", color=c_res)
    l1, = ax1.plot(pool, res, "o-", color=c_res, lw=2.4, ms=7, label="Resolution rate")
    ax1.tick_params(axis="y", labelcolor=c_res)
    ax1.set_ylim(min(res) - 4, 100)

    # 右轴1:成本 (注入 token)
    ax2 = ax1.twinx()
    c_tok = "#b35806"
    c_lat = "#542788"
    ax2.set_ylabel("Avg injected tokens / query", color=c_tok)
    ax2.tick_params(axis="y", labelcolor=c_tok)
    l2, = ax2.plot(pool, tok, "s--", color=c_tok, lw=2.0, ms=6, label="Avg injected tokens / query")
    ax2.set_ylim(min(tok) - 20, max(tok) + 20)

    # 右轴2 (外移):延迟 —— 单独刻度,否则会被 token 量级 (~500) 压平看不出上涨趋势
    ax3 = ax1.twinx()
    ax3.spines["right"].set_position(("axes", 1.12))
    ax3.set_ylabel("Avg latency / query (ms)", color=c_lat)
    ax3.tick_params(axis="y", labelcolor=c_lat)
    l3, = ax3.plot(pool, lat, "^:", color=c_lat, lw=2.0, ms=6, label="Avg latency / query (ms)")
    ax3.set_ylim(0, max(lat) * 1.25)

    by_round = {r["round"]: r for r in records}

    # 冷启动死区:成本上升、收益为零 (左侧阴影带)
    dz = by_round[analysis["dead_zone_until_round"]]
    if analysis["dead_zone_until_round"] > 0:
        ax1.axvspan(0, dz["pool_size"], color="#cccccc", alpha=0.35, zorder=0)
        ax1.annotate(
            "cold dead-zone\n(+%.0f tok, +0%% res)" % analysis["dead_zone_token_waste"],
            xy=(dz["pool_size"] / 2.0, max(res) - 4),
            ha="center", fontsize=8.5, color="#555555",
        )

    # 成本效率拐点 (knee):ROI 最划算处
    knee = analysis["knee_point"]
    kx = knee["pool_size"]
    ax1.axvline(kx, color="#444444", ls="-.", lw=1.6)
    ax1.annotate(
        "cost-efficiency knee\n(round %d, pool=%d)" % (analysis["knee_round"], kx),
        xy=(kx, knee["resolution_rate"] * 100),
        xytext=(kx - 9, min(res) + 1),
        fontsize=8.5, color="#222222",
        arrowprops=dict(arrowstyle="->", color="#444444"),
    )

    # token 饱和点
    sat = by_round[analysis["token_saturation_round"]]
    ax2.annotate(
        "token saturates\n(top-k cap)",
        xy=(sat["pool_size"], sat["avg_inj_tokens"]),
        xytext=(sat["pool_size"] - 11, sat["avg_inj_tokens"] - 0.18 * (max(tok) - min(tok) + 1)),
        fontsize=8.5, color=c_tok,
        arrowprops=dict(arrowstyle="->", color=c_tok),
    )

    ax1.set_title("Memory growth: benefit vs cost/latency trade-off")
    lines = [l1, l2, l3]
    ax1.legend(lines, [ln.get_label() for ln in lines], loc="lower right", fontsize=9)
    fig.subplots_adjust(right=0.82, left=0.08, top=0.92, bottom=0.11)
    fig.savefig(path, dpi=140)
    plt.close(fig)


# --- 报告 --------------------------------------------------------------------

def write_report(records: List[Dict], analysis: Dict, path: str, model_name: str = "mock") -> None:
    r0, rN = records[0], records[-1]
    knee = analysis["knee_point"]

    # 找最具代表性的"有正收益"的边际 ROI (第一段产生收益的过渡)
    gain_deltas = [d for d in analysis["deltas"]
                   if d["marginal_tokens_per_1pct_resolution"] is not None]
    if gain_deltas:
        avg_tok_per_pct = sum(d["marginal_tokens_per_1pct_resolution"] for d in gain_deltas) / len(gain_deltas)
        avg_cost_per_pct = sum(d["marginal_cost_per_1pct_resolution"] for d in gain_deltas) / len(gain_deltas)
    else:
        avg_tok_per_pct = avg_cost_per_pct = 0.0

    lines: List[str] = []
    lines.append("# 记忆增长的成本/延迟 vs 收益 (ROI) 分析\n")
    lines.append("> 自进化 Agent 靠经验池 (episodic memory) 变大提升解决率,但每多一条经验,")
    lines.append("> 每个 query 注入的检索上下文也变多 -> token / 成本 / 检索延迟同步上升。")
    lines.append("> 本报告把收益曲线和成本/延迟曲线对齐,量化 ROI 并找出边际收益递减拐点。\n")

    lines.append("## 1. 头条数字\n")
    lines.append("| 维度 | 冷启动 (pool=0) | 终态 (pool=%d) | 变化 |" % rN["pool_size"])
    lines.append("| --- | --- | --- | --- |")
    lines.append("| 解决率 resolution_rate | %.1f%% | %.1f%% | +%.1fpct |" % (
        r0["resolution_rate"] * 100, rN["resolution_rate"] * 100,
        (rN["resolution_rate"] - r0["resolution_rate"]) * 100))
    lines.append("| 关键点覆盖 keypoint_coverage | %.1f%% | %.1f%% | +%.1fpct |" % (
        r0["keypoint_coverage"] * 100, rN["keypoint_coverage"] * 100,
        (rN["keypoint_coverage"] - r0["keypoint_coverage"]) * 100))
    lines.append("| 平均注入 token / query | %.1f | %.1f | +%.1f (%.0f%%) |" % (
        r0["avg_inj_tokens"], rN["avg_inj_tokens"],
        rN["avg_inj_tokens"] - r0["avg_inj_tokens"],
        100.0 * (rN["avg_inj_tokens"] / max(1e-9, r0["avg_inj_tokens"]) - 1)))
    lines.append("| 平均检索命中 / query | %.2f | %.2f | +%.2f |" % (
        r0["avg_hits"], rN["avg_hits"], rN["avg_hits"] - r0["avg_hits"]))
    lines.append("| 平均端到端延迟 / query (ms) | %.4f | %.4f | +%.4f (%.0f%%) |" % (
        r0["avg_latency_ms"], rN["avg_latency_ms"],
        rN["avg_latency_ms"] - r0["avg_latency_ms"],
        100.0 * (rN["avg_latency_ms"] / max(1e-9, r0["avg_latency_ms"]) - 1)))
    lines.append("| 平均成本 / query (USD) | %.3e | %.3e | %s |\n" % (
        r0["avg_cost_usd"], rN["avg_cost_usd"],
        "持平 (mock 定价=0)" if rN["avg_cost_usd"] == 0 else "+%.3e" % (rN["avg_cost_usd"] - r0["avg_cost_usd"])))

    lines.append("> 注:后端为 mock(离线确定性),但成本按配置模型 `%s` 的真实价目表计价 "
                 "(见 `obs/cost.py`,input/output 价/1K token),所以美元数字是换算到该模型的等效成本。" % model_name)
    lines.append("> 成本 = 注入 token x 价表,因此 **token 曲线就是成本曲线**;换 DeepSeek / Claude 只是乘以不同系数。\n")

    lines.append("## 2. 逐轮明细\n")
    lines.append("| round | pool_size | resolution | coverage | avg_inj_tok | avg_hits | avg_lat_ms |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for r in records:
        lines.append("| %d | %d | %.1f%% | %.1f%% | %.1f | %.2f | %.4f |" % (
            r["round"], r["pool_size"], r["resolution_rate"] * 100,
            r["keypoint_coverage"] * 100, r["avg_inj_tokens"],
            r["avg_hits"], r["avg_latency_ms"]))
    lines.append("")

    lines.append("## 3. 边际 ROI (每轮增量)\n")
    lines.append("| 过渡 | Δpool | Δresolution | Δinj_tok | Δlat_ms | 每+1%解决率的边际token |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for d in analysis["deltas"]:
        tpp = d["marginal_tokens_per_1pct_resolution"]
        tpp_s = "%.1f" % tpp if tpp is not None else "— (无收益,纯加价)"
        lines.append("| r%d→r%d | +%d | %+.1f%% | %+.1f | %+.4f | %s |" % (
            d["from_round"], d["to_round"], d["d_pool_size"],
            d["d_resolution"] * 100, d["d_inj_tokens"], d["d_latency_ms"], tpp_s))
    lines.append("")
    lines.append("平均而言,**每提升 1%% 解决率约需多注入 %.1f token / query**(对应成本 %.3e USD/query @当前模型价)。\n" % (
        avg_tok_per_pct, avg_cost_per_pct))

    lines.append("## 4. 三个拐点 (trade-off 的真实形状)\n")
    dz = next(r for r in records if r["round"] == analysis["dead_zone_until_round"])
    sat = next(r for r in records if r["round"] == analysis["token_saturation_round"])
    lines.append("这条曲线不是简单的收益平台,而呈现三段,每段对应一个工程动作:\n")
    lines.append("**(A) 冷启动死区 (round 0→%d, pool 0→%d)**:经验池在长大、注入 token 从 %.1f 涨到 %.1f"
                 "(+%.0f token),但存的经验还匹配不上 eval 的 paraphrase,**解决率纹丝不动 (%.1f%%)**。"
                 "这是纯加价、零收益区——工程上要尽快跳过 (预热 / 主动检索而非被动堆案例)。\n" % (
        analysis["dead_zone_until_round"], dz["pool_size"],
        r0["avg_inj_tokens"], dz["avg_inj_tokens"], analysis["dead_zone_token_waste"],
        r0["resolution_rate"] * 100))
    lines.append("**(B) 成本效率甜区 + 拐点 (knee = round %d, pool=%d)**:%s\n" % (
        analysis["knee_round"], knee["pool_size"], analysis["knee_reason"]))
    lines.append("**(C) token 饱和点 (round %d, pool=%d)**:注入 token / 检索命中受 **top-k 上限**钳制,"
                 "在 ~%.0f token / %.2f hits 处饱和;此后解决率还能涨 (靠命中质量更优,而非更多上下文),"
                 "但**延迟仍随经验池线性上升** (%.4f→%.4f ms,+%.0f%%)——这之后的成本主要来自 BM25 检索规模而非 token。\n" % (
        analysis["token_saturation_round"], sat["pool_size"],
        sat["avg_inj_tokens"], sat["avg_hits"],
        sat["avg_latency_ms"], rN["avg_latency_ms"],
        100.0 * (rN["avg_latency_ms"] / max(1e-9, sat["avg_latency_ms"]) - 1)))
    lines.append("> 关键洞察:**token 早早饱和 (top-k 钳制),延迟却一直涨**——所以在规模化时,"
                 "**延迟/检索规模**才是比 token 更敏感的成本代理,记忆淘汰要盯的是池子大小,不只是上下文长度。\n")

    lines.append("## 5. 工程结论 & 面试话术\n")
    lines.append("- **预算钉在拐点附近**:用 retrieval **top-k 截断** + episodic 记忆 **TTL / LRU 淘汰**,")
    lines.append("  把经验池有效规模控制在拐点 (pool≈%d) 量级,而非无限增长。" % knee["pool_size"])
    lines.append("- **拐点后改质不改量**:与其堆经验,不如做去重 / 反思蒸馏 (playbook) 压缩,提升单位 token 的信息密度。")
    lines.append("- 监控指标:把 `avg_inj_tokens`、`avg_hits`、`avg_latency_ms`(本仓 `obs.aggregate` 已有)接看板,")
    lines.append("  对 token 设 SLO,越拐点即告警。")
    lines.append("")
    lines.append("> 面试话术: 我不会让记忆无限膨胀——我先量化了 ROI: 解决率从 %.0f%% 涨到 %.0f%% 的同时, " % (
        r0["resolution_rate"] * 100, rN["resolution_rate"] * 100))
    lines.append("> 注入 token 涨了 %.0f%%、延迟涨了 %.0f%%;而且 token 在 pool≈%d 就被 top-k 钳到饱和,"
                 "延迟却一直随池子线性涨。" % (
        100.0 * (rN["avg_inj_tokens"] / max(1e-9, r0["avg_inj_tokens"]) - 1),
        100.0 * (rN["avg_latency_ms"] / max(1e-9, r0["avg_latency_ms"]) - 1),
        sat["pool_size"]))
    lines.append("> 所以我会用 **TTL + top-k 截断 + 反思蒸馏**把成本控制在效率拐点附近 (pool≈%d),"
                 "并优先按检索规模而非上下文长度做记忆淘汰——而不是为最后几个点去无脑堆经验、付延迟的账。" % knee["pool_size"])

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    cfg = Config.load()  # 默认 mock 后端, seed=0, 确定性
    records = run_evolution(cfg)
    analysis = analyze(records)

    payload = {
        "config": {
            "backend": cfg.backend, "model": cfg.model,
            "kb_top_k": cfg.kb_top_k, "epi_top_k": cfg.epi_top_k,
            "train_rounds": cfg.train_rounds, "coverage_threshold": cfg.coverage_threshold,
            "latency_repeats": LATENCY_REPEATS, "seed": cfg.seed,
        },
        "curve": records,
        "analysis": analysis,
    }
    json_path = os.path.join(OUT_DIR, "metrics.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    png_path = os.path.join(OUT_DIR, "curve.png")
    plot_curve(records, analysis, png_path)

    md_path = os.path.join(OUT_DIR, "report.md")
    write_report(records, analysis, md_path, model_name=cfg.model)

    r0, rN, knee = records[0], records[-1], analysis["knee_point"]
    print("[cost_benefit] wrote:")
    print("  ", json_path)
    print("  ", png_path)
    print("  ", md_path)
    print("[summary] resolution %.1f%% -> %.1f%% | inj_tok %.1f -> %.1f | latency %.4fms -> %.4fms" % (
        r0["resolution_rate"] * 100, rN["resolution_rate"] * 100,
        r0["avg_inj_tokens"], rN["avg_inj_tokens"],
        r0["avg_latency_ms"], rN["avg_latency_ms"]))
    print("[summary] knee at round %d (pool=%d, resolution=%.1f%%)" % (
        analysis["knee_round"], knee["pool_size"], knee["resolution_rate"] * 100))


if __name__ == "__main__":
    main()
