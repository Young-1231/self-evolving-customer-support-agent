"""从一批 trace 记录聚合运营指标(agent-ops / SRE 视角)。

这些指标是评估一个生产客服 Agent 是否"健康、划算、可信"的核心面板，对应业界
agent-ops 实践(Langfuse 的 dashboards、Phoenix 的 evals 聚合)：

  - deflection_rate     自助解决率(未转人工占比)——衡量 Agent 替人省了多少工单；
  - escalation_rate     转人工率(= 1 - deflection)；
  - avg/p50/p95 latency 时延分布——p95 是 SLA 关注点；
  - total/avg cost       成本——直接对应 LLM 账单；
  - avg_hits             平均检索命中数——RAG 召回体量；
  - guardrail_block_rate guardrail 拦截率——出站安全/合规命中比例；
  - error_rate           异常 turn 占比。

纯 stdlib 实现；百分位用最近秩(nearest-rank)法，样本少也稳定。
"""
from __future__ import annotations

from typing import Any, Dict, List


def _percentile(sorted_vals: List[float], q: float) -> float:
    """最近秩百分位。q 为 0~100。空列表返回 0.0。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    # nearest-rank: ceil(q/100 * N)，索引从 1 开始
    import math

    rank = max(1, math.ceil(q / 100.0 * len(sorted_vals)))
    return float(sorted_vals[min(rank, len(sorted_vals)) - 1])


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把若干 turn 记录聚合成一份运营指标 dict。"""
    n = len(records)
    if n == 0:
        return {
            "n_turns": 0,
            "deflection_rate": 0.0,
            "escalation_rate": 0.0,
            "avg_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "total_cost_usd": 0.0,
            "avg_cost_usd": 0.0,
            "avg_hits": 0.0,
            "guardrail_block_rate": 0.0,
            "error_rate": 0.0,
            "total_tokens": 0,
        }

    escalated = sum(1 for r in records if r.get("escalate"))
    blocked = sum(1 for r in records if r.get("guardrail_blocked"))
    errors = sum(1 for r in records if r.get("error"))

    latencies = sorted(float(r.get("latency_ms", 0.0) or 0.0) for r in records)
    costs = [float(r.get("cost_usd", 0.0) or 0.0) for r in records]
    hits = [int(r.get("n_hits", 0) or 0) for r in records]
    tokens = sum(
        int(r.get("in_tokens", 0) or 0) + int(r.get("out_tokens", 0) or 0) for r in records
    )

    total_cost = sum(costs)
    return {
        "n_turns": n,
        "deflection_rate": round((n - escalated) / n, 4),
        "escalation_rate": round(escalated / n, 4),
        "avg_latency_ms": round(sum(latencies) / n, 3),
        "p50_latency_ms": round(_percentile(latencies, 50), 3),
        "p95_latency_ms": round(_percentile(latencies, 95), 3),
        "total_cost_usd": round(total_cost, 8),
        "avg_cost_usd": round(total_cost / n, 8),
        "avg_hits": round(sum(hits) / n, 3),
        "guardrail_block_rate": round(blocked / n, 4),
        "error_rate": round(errors / n, 4),
        "total_tokens": tokens,
    }


def summary_table(records: List[Dict[str, Any]]) -> str:
    """把聚合指标渲染成一张 markdown 表(便于贴进 PR / 报告)。"""
    m = aggregate(records)
    rows = [
        ("回合数 (turns)", m["n_turns"]),
        ("自助解决率 deflection", "%.1f%%" % (m["deflection_rate"] * 100)),
        ("转人工率 escalation", "%.1f%%" % (m["escalation_rate"] * 100)),
        ("平均时延 avg latency (ms)", m["avg_latency_ms"]),
        ("p50 时延 (ms)", m["p50_latency_ms"]),
        ("p95 时延 (ms)", m["p95_latency_ms"]),
        ("平均检索命中 avg hits", m["avg_hits"]),
        ("guardrail 拦截率", "%.1f%%" % (m["guardrail_block_rate"] * 100)),
        ("异常率 error rate", "%.1f%%" % (m["error_rate"] * 100)),
        ("总 token", m["total_tokens"]),
        ("总成本 (USD)", "$%.6f" % m["total_cost_usd"]),
        ("平均成本/回合 (USD)", "$%.6f" % m["avg_cost_usd"]),
    ]
    lines = ["| 指标 | 值 |", "| --- | --- |"]
    for k, v in rows:
        lines.append("| %s | %s |" % (k, v))
    return "\n".join(lines)
