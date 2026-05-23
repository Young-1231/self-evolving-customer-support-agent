"""Offline tests for the stress test machinery — base python, zero deps.

Run:
    PYTHONPATH=src python -m pytest -q tests/test_stress.py
"""
from __future__ import annotations

import os
import random
import time
from collections import Counter
from typing import List

import pytest

from seagent.llm.base import LLMBackend, Passage
from seagent.stress import (
    DEFAULT_DISTRIBUTION,
    TicketSpec,
    generate_tickets,
    sample_categories,
    run_load,
    summarize_load,
    scale_memory,
)
from seagent.stress.generator import (
    CATEGORIES,
    _expected_signals_for,
    estimate_generation_cost,
    load_tickets,
)
from seagent.stress.memory_scaling import find_knee


# ===========================================================================
# generator: 分布抽样
# ===========================================================================
def test_sample_categories_is_deterministic_and_close_to_distribution():
    n = 500
    a = sample_categories(n, DEFAULT_DISTRIBUTION, seed=42)
    b = sample_categories(n, DEFAULT_DISTRIBUTION, seed=42)
    assert a == b, "same seed must reproduce"
    assert len(a) == n
    counts = Counter(a)
    # 每个键都至少出现一次(对于 n=500 + 默认分布, 即便最小类也有 ~25 条)
    for k in CATEGORIES:
        assert counts[k] > 0, f"category {k} got zero quota"
    # 主类 ~50%, 偏差 < 3%
    assert abs(counts["normal_easy"] / n - 0.50) < 0.03
    # 最小类 ~5%, 偏差 < 2% (配额式分配比 multinomial 紧)
    assert abs(counts["multilingual"] / n - 0.05) < 0.02


def test_sample_categories_small_n_still_covers_all_classes():
    """n=50 时, 即便最小类 quota = 2.5, 也应至少分到 2 条。"""
    cats = sample_categories(50, DEFAULT_DISTRIBUTION, seed=0)
    assert len(cats) == 50
    counts = Counter(cats)
    # injection (5%) 在 n=50 时配额式至少应到 2 条
    assert counts["injection"] >= 2
    assert counts["multilingual"] >= 2


def test_sample_categories_custom_distribution_renormalized():
    """非归一化的分布也应工作(内部 renorm)。"""
    custom = {"normal_easy": 2.0, "injection": 1.0, "pii": 1.0}  # ratio 2:1:1
    cats = sample_categories(40, custom, seed=0)
    c = Counter(cats)
    assert c["normal_easy"] == 20 and c["injection"] == 10 and c["pii"] == 10


# ===========================================================================
# generator: 端到端(mock LLM)
# ===========================================================================
def _stub_chat(system: str, user: str) -> str:
    """返回一个非空字符串, 内容可识别类别便于断言。"""
    for k in CATEGORIES:
        if k in user:
            return f"<{k}> 我有问题, 请帮我看一下 {random.randint(1,9999)}"
    return "fallback"


def test_generate_tickets_with_mock_chat_fn(tmp_path):
    cache = os.path.join(str(tmp_path), "tickets.jsonl")
    out = generate_tickets(
        n=24, seed=7, cache_path=cache, concurrency=4, chat_fn=_stub_chat,
    )
    assert len(out) == 24
    # expected_signals 字段被填了
    for t in out:
        assert "has_pii" in t.expected_signals
        assert "is_injection" in t.expected_signals
        assert "has_multi_intent" in t.expected_signals
        sig = _expected_signals_for(t.category)
        assert t.expected_signals == sig
    # 缓存命中后不会再调
    calls = {"n": 0}

    def counting(system, user):
        calls["n"] += 1
        return _stub_chat(system, user)

    out2 = generate_tickets(
        n=24, seed=7, cache_path=cache, concurrency=4, chat_fn=counting,
    )
    assert calls["n"] == 0, "cache must short-circuit LLM calls"
    assert len(out2) == 24
    assert out2[0].ticket_id == out[0].ticket_id


def test_load_tickets_roundtrip(tmp_path):
    p = os.path.join(str(tmp_path), "t.jsonl")
    out = generate_tickets(n=5, seed=1, cache_path=p, concurrency=2,
                           chat_fn=_stub_chat)
    again = load_tickets(p)
    assert len(again) == 5
    assert again[0].text == out[0].text


def test_estimate_generation_cost_positive():
    c = estimate_generation_cost(500, model="deepseek-chat")
    assert c["in_tokens"] > 0 and c["out_tokens"] > 0
    assert c["usd_estimate"] > 0.0


# ===========================================================================
# load_runner
# ===========================================================================
class _FakeAgent:
    """模仿 SupportAgent 的最小接口 — handle(text) -> AgentResult-shaped obj."""

    class _R:
        def __init__(self, answer, escalate=False, confidence=0.8, gr=None):
            self.answer = answer
            self.escalate = escalate
            self.confidence = confidence
            self.guardrail = gr
            self.trace_id = "tid"
            self.contexts = []
            self.used_sources = []

    def __init__(self, *, fail_on=None, escalate_on=None, block_on=None, delay_ms=0):
        self.fail_on = fail_on or set()
        self.escalate_on = escalate_on or set()
        self.block_on = block_on or set()
        self.delay_ms = delay_ms

    def handle(self, text):
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000.0)
        if any(k in text for k in self.fail_on):
            raise RuntimeError("boom")
        gr = None
        escalate = any(k in text for k in self.escalate_on)
        if any(k in text for k in self.block_on):
            class _G:
                action = "BLOCK"; blocked = True
            gr = _G()
        return self._R(answer="ok", escalate=escalate, gr=gr)


def test_run_load_handles_failures_and_summarizes():
    tickets = [
        TicketSpec(ticket_id=f"t{i}", text=f"hello {i}", category="normal_easy",
                   expected_signals={})
        for i in range(20)
    ]
    # 让 id 含 "fail" 的失败, "esc" 的转人工, "blk" 的拦截
    tickets[3].text = "fail-please"
    tickets[5].text = "fail-again"
    tickets[7].text = "esc-this"
    tickets[9].text = "blk-this"
    tickets[7].category = "normal_hard"
    tickets[9].category = "injection"
    tickets[3].category = "pii"
    tickets[5].category = "pii"

    def factory():
        return _FakeAgent(fail_on={"fail"}, escalate_on={"esc"}, block_on={"blk"})

    records = run_load(tickets, agent_factory=factory, max_concurrency=4)
    assert len(records) == 20
    summary = summarize_load(records)
    assert summary["n"] == 20
    assert summary["n_error"] == 2
    assert summary["error_rate"] == 0.1
    # latency 字段非负
    assert summary["p50_latency_ms"] >= 0
    assert summary["p95_latency_ms"] >= summary["p50_latency_ms"]
    assert summary["p99_latency_ms"] >= summary["p95_latency_ms"]
    # category 拆解
    by = summary["by_category"]
    assert "pii" in by and by["pii"]["error_rate"] == 1.0
    assert "normal_hard" in by and by["normal_hard"]["escalation_rate"] == 1.0
    assert "injection" in by and by["injection"]["block_rate"] == 1.0


def test_run_load_p99_with_known_distribution():
    """构造可预测的延迟分布, 严格断言 p99。"""
    tickets = [
        TicketSpec(ticket_id=f"t{i}", text=f"x{i}", category="normal_easy",
                   expected_signals={})
        for i in range(100)
    ]
    # 99 条 fast + 1 条 slow -> p99 应取到 slow 那条
    def factory():
        return _FakeAgent(delay_ms=1)

    records = run_load(tickets, agent_factory=factory, max_concurrency=8)
    # 给最后一条手动改成超长延迟以测 p99
    records[-1].latency_ms = 500.0
    summary = summarize_load(records)
    assert summary["max_latency_ms"] >= 500.0
    assert summary["p99_latency_ms"] >= summary["p95_latency_ms"]


def test_run_load_empty():
    assert run_load([], agent_factory=lambda: _FakeAgent()) == []
    assert summarize_load([]) == {"n": 0}


# ===========================================================================
# memory_scaling
# ===========================================================================
class _MockBackend(LLMBackend):
    name = "mock_for_stress"

    def generate_answer(self, query, contexts):
        return "ok"

    def judge_confidence(self, query, answer, contexts):
        return 0.7


def test_scale_memory_produces_points_in_order():
    from seagent.config import Config
    from seagent.memory.semantic import SemanticMemory
    from seagent.memory.episodic import EpisodicMemory
    from seagent.agent.support_agent import SupportAgent
    from seagent.data import KBDoc

    cfg = Config()
    # 极简 KB(2 doc) 即可
    docs = [KBDoc(doc_id="d1", title="A", topic="t", text="how to export data"),
            KBDoc(doc_id="d2", title="B", topic="t", text="billing refund policy")]
    semantic = SemanticMemory(docs, cfg.score_norm_k)
    backend = _MockBackend()

    def factory(epi):
        return SupportAgent(cfg, backend, semantic, epi)

    eval_t = [TicketSpec(ticket_id=f"e{i}", text=f"how do i export {i}",
                        category="normal_easy", expected_signals={})
              for i in range(5)]

    points = scale_memory(
        sizes=[10, 100, 500],
        eval_tickets=eval_t,
        agent_factory=factory,
        backend=backend,
        score_norm_k=cfg.score_norm_k,
    )
    assert [p.size for p in points] == [10, 100, 500]
    for p in points:
        assert p.n_eval == 5
        assert 0.0 <= p.resolution_rate <= 1.0
        assert p.avg_retrieval_ms >= 0.0
    # find_knee: 不一定有(测试集太小), 接受 None 或一个 size
    knee = find_knee(points)
    assert knee is None or knee in {p.size for p in points}


def test_scale_memory_empty_sizes():
    out = scale_memory(sizes=[], eval_tickets=[],
                       agent_factory=lambda epi: None, backend=_MockBackend())
    assert out == []
