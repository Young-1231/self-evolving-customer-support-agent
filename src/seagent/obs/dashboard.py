"""运营报告渲染(agent-ops 看板的 markdown 版本)。

读取一个 JSONL trace 文件，产出一份给值班/运营看的 markdown 报告：体量、成功/转
人工、时延分布、成本、guardrail 命中 Top、近期异常 turn。风格对标 SRE/agent-ops
看板(Langfuse dashboards / Phoenix project overview)的离线快照。

matplotlib 为可选增强：装了就额外画 latency/cost 趋势图(guarded import)，没装则
报告照样完整产出，绝不因为缺图而失败。
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List, Optional

from .metrics import aggregate, summary_table
from .trace import read_traces


def _guardrail_top(records: List[Dict[str, Any]], top: int = 5) -> List[tuple]:
    """统计 guardrail verdict 分布(非 allow 视为命中)，返回 Top-N (verdict, count)。"""
    c: Counter = Counter()
    for r in records:
        v = r.get("guardrail_verdict", "allow") or "allow"
        if v != "allow":
            c[v] += 1
    return c.most_common(top)


def _recent_anomalies(records: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """近期异常 turn：有 error，或被 guardrail 拦截，或被转人工。按 ts 倒序取前 N。"""
    anomalies = [
        r
        for r in records
        if r.get("error") or r.get("guardrail_blocked") or r.get("escalate")
    ]
    anomalies.sort(key=lambda r: r.get("ts", 0.0), reverse=True)
    return anomalies[:limit]


def _maybe_plot(records: List[Dict[str, Any]], out_dir: str) -> Optional[str]:
    """可选：用 matplotlib 画 latency/cost 趋势图，返回图片相对路径或 None。"""
    try:
        import matplotlib

        matplotlib.use("Agg")  # 无显示环境
        import matplotlib.pyplot as plt
    except Exception:
        return None
    if not records:
        return None
    ordered = sorted(records, key=lambda r: (r.get("turn", 0), r.get("ts", 0.0)))
    turns = [r.get("turn", i) for i, r in enumerate(ordered)]
    lat = [float(r.get("latency_ms", 0.0) or 0.0) for r in ordered]
    cost = [float(r.get("cost_usd", 0.0) or 0.0) for r in ordered]
    fig, ax1 = plt.subplots(figsize=(8, 3))
    ax1.plot(turns, lat, color="tab:blue", label="latency_ms")
    ax1.set_xlabel("turn")
    ax1.set_ylabel("latency_ms", color="tab:blue")
    ax2 = ax1.twinx()
    ax2.plot(turns, cost, color="tab:red", label="cost_usd")
    ax2.set_ylabel("cost_usd", color="tab:red")
    fig.tight_layout()
    img_path = os.path.join(out_dir, "ops_trend.png")
    fig.savefig(img_path, dpi=110)
    plt.close(fig)
    return os.path.basename(img_path)


def render_ops_report(traces_path: str, with_plot: bool = True) -> str:
    """读 JSONL trace，产出一份完整的 markdown 运营报告字符串。"""
    records = read_traces(traces_path)
    m = aggregate(records)

    parts: List[str] = []
    parts.append("# Agent 运营报告 (Ops Report)")
    parts.append("")
    parts.append("- 数据源: `%s`" % traces_path)
    parts.append("- 回合数: **%d**" % m["n_turns"])
    parts.append("")

    if m["n_turns"] == 0:
        parts.append("> 暂无 trace 记录。")
        return "\n".join(parts)

    # --- 核心指标表 ---
    parts.append("## 核心指标")
    parts.append("")
    parts.append(summary_table(records))
    parts.append("")

    # --- 成功 / 转人工拆分 ---
    escalated = sum(1 for r in records if r.get("escalate"))
    parts.append("## 处置拆分")
    parts.append("")
    parts.append("- 自助解决 (deflected): **%d** (%.1f%%)" % (
        m["n_turns"] - escalated, m["deflection_rate"] * 100))
    parts.append("- 转人工 (escalated): **%d** (%.1f%%)" % (
        escalated, m["escalation_rate"] * 100))
    parts.append("")

    # --- guardrail 命中 Top ---
    parts.append("## Guardrail 命中 Top")
    parts.append("")
    top = _guardrail_top(records)
    if top:
        parts.append("| verdict | 次数 |")
        parts.append("| --- | --- |")
        for verdict, cnt in top:
            parts.append("| %s | %d |" % (verdict, cnt))
    else:
        parts.append("> 无 guardrail 命中(全部 allow)。")
    parts.append("")

    # --- 近期异常 turn ---
    parts.append("## 近期异常 Turn")
    parts.append("")
    anomalies = _recent_anomalies(records)
    if anomalies:
        parts.append("| turn | 时延ms | conf | escalate | verdict | error |")
        parts.append("| --- | --- | --- | --- | --- | --- |")
        for r in anomalies:
            parts.append("| %s | %s | %s | %s | %s | %s |" % (
                r.get("turn", "-"),
                r.get("latency_ms", "-"),
                r.get("confidence", "-"),
                "Y" if r.get("escalate") else "-",
                r.get("guardrail_verdict", "-"),
                (r.get("error") or "-"),
            ))
    else:
        parts.append("> 无异常 turn。")
    parts.append("")

    # --- 可选趋势图 ---
    if with_plot:
        img = _maybe_plot(records, os.path.dirname(os.path.abspath(traces_path)))
        if img:
            parts.append("## 趋势图")
            parts.append("")
            parts.append("![latency / cost 趋势](%s)" % img)
            parts.append("")

    return "\n".join(parts)
