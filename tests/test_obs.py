"""obs 可观测性模块测试(零第三方依赖，base python 即可跑)。"""
from __future__ import annotations

import json
import os

from seagent.obs import (
    Tracer,
    aggregate,
    estimate_cost,
    estimate_tokens,
    render_ops_report,
    summary_table,
)
from seagent.obs.cost import lookup_price
from seagent.llm.base import Passage


# --- cost.py ---------------------------------------------------------------
def test_estimate_tokens_monotonic_and_cjk():
    assert estimate_tokens("") == 0
    # 非空至少 1 token
    assert estimate_tokens("a") >= 1
    # 更长文本 token 更多
    assert estimate_tokens("hello world " * 10) > estimate_tokens("hello world")
    # CJK 按更密的比例切，同字符数下 token 不少于 ASCII
    cjk = estimate_tokens("退款政策是什么呢请告诉我详细流程")
    ascii_ = estimate_tokens("a" * 16)
    assert cjk >= ascii_


def test_estimate_cost_known_and_unknown():
    # mock 免费
    assert estimate_cost("mock", 1000, 1000) == 0.0
    # 已知模型成本 > 0 且随 token 单调
    c1 = estimate_cost("gpt-4o-mini", 1000, 0)
    c2 = estimate_cost("gpt-4o-mini", 2000, 0)
    assert c1 > 0 and c2 > c1
    # 前缀匹配带版本后缀
    assert lookup_price("gpt-4o-mini-2024-07-18") == lookup_price("gpt-4o-mini")
    # 未知模型回退默认价(非零)
    assert estimate_cost("totally-unknown-model", 1000, 1000) > 0


# --- trace.py：span 计时 + 落盘 -------------------------------------------
def test_tracer_span_timing_with_fake_clock(tmp_path):
    # 注入假时钟：每次调用 +1.0 秒，便于断言耗时
    ticks = iter([0.0, 0.0, 0.5, 0.5, 1.5, 2.0])  # turn_t0, span_start, span_end, ...
    seq = [0.0, 0.0, 0.5, 1.5, 2.0]
    state = {"i": 0}

    def clock():
        v = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return v

    tracer = Tracer(workdir=str(tmp_path), filename="t.jsonl", clock=clock)
    tracer.start_turn(turn=0, query="退款多久到账", model="deepseek-chat")  # t0 = 0.0
    with tracer.span("retrieval"):  # start 0.0 end 0.5 -> 500ms
        pass
    with tracer.span("generation"):  # start 1.5 end 2.0 -> 500ms
        pass
    rec = tracer.end_turn(confidence=0.8, escalate=False, guardrail_verdict="allow")

    assert rec.phase_ms["retrieval"] == 500.0
    assert rec.phase_ms["generation"] == 500.0
    assert rec.latency_ms >= 0.0
    # 落盘文件存在且可解析
    path = os.path.join(str(tmp_path), "traces", "t.jsonl")
    assert os.path.exists(path)
    lines = open(path, encoding="utf-8").read().strip().splitlines()
    assert len(lines) == 1
    on_disk = json.loads(lines[0])
    assert on_disk["trace_id"] and on_disk["model"] == "deepseek-chat"


def test_tracer_set_hits_from_passages(tmp_path):
    tracer = Tracer(workdir=str(tmp_path), filename="h.jsonl")
    tracer.start_turn(turn=1, query="q", model="mock")
    hits = [
        Passage(source="kb", text="...", score=0.9, ref="doc-1"),
        Passage(source="episodic", text="...", score=0.6, ref="case-7"),
    ]
    tracer.set_hits(hits)
    tracer.set_usage(in_tokens=100, out_tokens=50, cost_usd=0.0)
    rec = tracer.end_turn(confidence=0.7, escalate=True, guardrail_verdict="redact",
                          guardrail_blocked=False)
    assert rec.n_hits == 2
    assert rec.hits[0] == {"source": "kb", "ref": "doc-1", "score": 0.9}
    assert rec.escalate is True


def _fake_records():
    """构造若干假 turn 记录(覆盖转人工/拦截/异常/不同时延成本)。"""
    return [
        {"turn": 0, "ts": 1.0, "latency_ms": 100.0, "n_hits": 3, "confidence": 0.9,
         "escalate": False, "guardrail_verdict": "allow", "guardrail_blocked": False,
         "in_tokens": 100, "out_tokens": 40, "cost_usd": 0.001, "error": None},
        {"turn": 1, "ts": 2.0, "latency_ms": 200.0, "n_hits": 2, "confidence": 0.3,
         "escalate": True, "guardrail_verdict": "allow", "guardrail_blocked": False,
         "in_tokens": 120, "out_tokens": 0, "cost_usd": 0.0005, "error": None},
        {"turn": 2, "ts": 3.0, "latency_ms": 300.0, "n_hits": 4, "confidence": 0.8,
         "escalate": False, "guardrail_verdict": "block", "guardrail_blocked": True,
         "in_tokens": 90, "out_tokens": 30, "cost_usd": 0.002, "error": None},
        {"turn": 3, "ts": 4.0, "latency_ms": 400.0, "n_hits": 1, "confidence": 0.1,
         "escalate": True, "guardrail_verdict": "allow", "guardrail_blocked": False,
         "in_tokens": 50, "out_tokens": 10, "cost_usd": 0.0003, "error": "timeout"},
    ]


# --- metrics.py ------------------------------------------------------------
def test_aggregate_metrics():
    m = aggregate(_fake_records())
    assert m["n_turns"] == 4
    # 2/4 转人工
    assert m["escalation_rate"] == 0.5
    assert m["deflection_rate"] == 0.5
    # 时延：avg=250, p50(nearest-rank ceil(.5*4)=2 -> 第2小=200), p95(ceil(.95*4)=4 -> 400)
    assert m["avg_latency_ms"] == 250.0
    assert m["p50_latency_ms"] == 200.0
    assert m["p95_latency_ms"] == 400.0
    # 成本
    assert abs(m["total_cost_usd"] - 0.0038) < 1e-9
    assert abs(m["avg_cost_usd"] - 0.00095) < 1e-9
    # 检索命中均值 (3+2+4+1)/4
    assert m["avg_hits"] == 2.5
    # 1/4 拦截，1/4 异常
    assert m["guardrail_block_rate"] == 0.25
    assert m["error_rate"] == 0.25
    assert m["total_tokens"] == (140 + 120 + 120 + 60)


def test_aggregate_empty():
    m = aggregate([])
    assert m["n_turns"] == 0
    assert m["deflection_rate"] == 0.0


def test_summary_table_markdown():
    t = summary_table(_fake_records())
    assert t.startswith("| 指标 | 值 |")
    assert "deflection" in t and "p95" in t


# --- dashboard.py ----------------------------------------------------------
def test_render_ops_report_nonempty(tmp_path):
    path = os.path.join(str(tmp_path), "trace.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for r in _fake_records():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = render_ops_report(path, with_plot=False)
    assert "Agent 运营报告" in report
    assert "核心指标" in report
    assert "Guardrail 命中 Top" in report
    # block 命中应出现在 Top
    assert "block" in report
    # 近期异常应包含 timeout error 的 turn
    assert "timeout" in report
    assert len(report) > 200


def test_render_ops_report_empty(tmp_path):
    path = os.path.join(str(tmp_path), "empty.jsonl")
    open(path, "w").close()
    report = render_ops_report(path)
    assert "暂无 trace 记录" in report
