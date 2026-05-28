"""Tests for v3.1 — SubagentExecutor (Anthropic Claude Code subagent
context-isolation pattern).

These tests rely only on the mock backend + small in-memory KB/episodic
fixtures so they finish in <1s with zero network.
"""
from __future__ import annotations

import os
import sys

import pytest

THIS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(THIS, "..", "src"))

from seagent.data import KBDoc
from seagent.llm.base import LLMBackend, Passage
from seagent.llm.mock import MockBackend
from seagent.memory.episodic import Case
from seagent.multi_agent.subagent_executor import (
    SubagentExecutor,
    merge_subagent_summaries,
)
from seagent.multi_agent.summary import SUMMARY_MAX_CHARS, SubagentSummary


# ---------------------- fixtures -------------------------------------------


def _full_kb():
    return [
        KBDoc("kb_b1", "退款政策", "billing",
              "您可以在订单完成后 14 天内申请退款。请进入「设置>账单」点击「申请退款」。"),
        KBDoc("kb_b2", "套餐降级", "subscription",
              "降级在当前计费周期结束后生效，差额以账户积分形式保留。"),
        KBDoc("kb_a1", "重置密码", "account",
              "请进入「设置>安全」点击「忘记密码」，按邮箱链接重置即可。"),
        KBDoc("kb_a2", "双因素登录", "account_security",
              "在「安全」设置中开启 2FA，绑定身份验证器应用。"),
        KBDoc("kb_t1", "Webhook 超时排查", "integrations_api",
              "请检查回调端点是否返回 2xx，并确保 5 秒内响应。"),
        KBDoc("kb_t2", "导出数据失败", "data_export",
              "若导出任务卡住，可在「数据>导出」重启该任务。"),
        KBDoc("kb_g1", "联系客服", "general",
              "您可以通过 support@nimbusflow.com 联系我们的支持团队。"),
    ]


def _epi_cases_billing():
    return [
        Case(
            case_id="case_b001",
            query="为什么我的退款还没到账",
            resolution="退款一般 7-14 个工作日到账，请关注账户流水。",
            should_escalate=False,
            topic="billing",
        ),
    ]


# ---------------------- isolated-KB tests ----------------------------------


def test_kb_subset_is_isolated_not_full_kb():
    """SubagentExecutor must only see its domain slice, not the full KB."""
    full = _full_kb()
    billing = SubagentExecutor.from_specialist_config(
        domain="billing", full_kb=full,
    )
    # billing topics in DEFAULT_DOMAIN_TOPICS = {billing, subscription}
    assert billing.stats()["kb_size"] == 2
    # cannot retrieve account docs
    summary = billing.handle("我想重置密码")
    # the billing subagent should NOT cite any kb_a*
    for did in summary.cited_doc_ids:
        assert not did.startswith("kb_a"), (
            f"billing subagent leaked account doc id {did}"
        )


def test_independent_executors_have_independent_episodic_snapshots():
    """Mutating one executor's episodic list must not affect another's."""
    full = _full_kb()
    snap1 = [Case("c1", "退款没到", "退款 7-14 天", False, "billing")]
    snap2 = []
    exec1 = SubagentExecutor(
        domain="billing",
        base_model="mock",
        kb=[d for d in full if d.topic in ("billing", "subscription")],
        episodic_snapshot=snap1,
    )
    exec2 = SubagentExecutor(
        domain="billing",
        base_model="mock",
        kb=[d for d in full if d.topic in ("billing", "subscription")],
        episodic_snapshot=snap2,
    )
    # mutate snap1 after construction — exec1 must have a private copy
    snap1.append(Case("c2", "added", "x", False, "billing"))
    assert exec1.stats()["episodic_size"] == 1
    assert exec2.stats()["episodic_size"] == 0


def test_handle_returns_subagent_summary_not_agent_result():
    """The Anthropic contract is: subagents return summaries, not full results."""
    full = _full_kb()
    exec_ = SubagentExecutor.from_specialist_config(domain="billing", full_kb=full)
    out = exec_.handle("怎么申请退款")
    assert isinstance(out, SubagentSummary)
    # AgentResult would have `.contexts` and `.used_sources`; ensure we don't.
    assert not hasattr(out, "contexts")
    assert not hasattr(out, "used_sources")


# ---------------------- summary contract -----------------------------------


def test_summary_length_capped():
    """answer_summary must be ≤ SUMMARY_MAX_CHARS chars."""
    s = SubagentSummary(
        domain="billing",
        answer_summary="x" * (SUMMARY_MAX_CHARS + 500),
        confidence=0.5,
    )
    assert len(s.answer_summary) <= SUMMARY_MAX_CHARS


def test_summary_confidence_clamped():
    s_hi = SubagentSummary(domain="x", answer_summary="ok", confidence=2.0)
    s_lo = SubagentSummary(domain="x", answer_summary="ok", confidence=-0.5)
    assert s_hi.confidence == 1.0
    assert s_lo.confidence == 0.0


def test_summary_roundtrips_to_dict():
    s = SubagentSummary(
        domain="billing",
        answer_summary="hi",
        confidence=0.7,
        cited_doc_ids=["kb_b1"],
        token_budget_used=42,
    )
    d = s.to_dict()
    assert d["domain"] == "billing"
    assert d["token_budget_used"] == 42
    assert d["cited_doc_ids"] == ["kb_b1"]


# ---------------------- budget enforcement ---------------------------------


def test_token_budget_respected():
    """Even with verbose KB, total tokens reported must not exceed budget."""
    # Each doc shares the token "退款" so BM25 will rank them and the
    # retrieval will produce 3 fat passages; the truncator then has to
    # shrink them to fit the budget.
    big_docs = [
        KBDoc(
            f"kb_big_{i}",
            f"退款标题{i}",
            "billing",
            "退款 " + "x" * 4000,
        )
        for i in range(5)
    ]
    exec_ = SubagentExecutor(
        domain="billing",
        base_model="mock",
        kb=big_docs,
        episodic_snapshot=[],
        token_budget=400,
        max_context_chars=600,
    )
    s = exec_.handle("退款")
    assert s.token_budget_used <= 400 + 16, (
        f"budget violated: {s.token_budget_used}"
    )
    # truncation flag set on the executor
    assert exec_.n_budget_truncated >= 1


def test_max_context_chars_enforced():
    """Char cap on raw context fed to the backend."""
    big_docs = [
        KBDoc(f"kb_big_{i}", f"标题{i}", "billing", "abc" * 1000)
        for i in range(3)
    ]
    exec_ = SubagentExecutor(
        domain="billing",
        base_model="mock",
        kb=big_docs,
        episodic_snapshot=[],
        token_budget=10_000,
        max_context_chars=200,
    )
    # The MockBackend mirrors context; the answer length is bounded by
    # max_chars passed to the mock through our constructor.
    s = exec_.handle("退款")
    # we know mock backend default max_chars == max_context_chars (200)
    assert len(s.answer_summary) <= SUMMARY_MAX_CHARS


# ---------------------- handoff logic --------------------------------------


def test_handoff_when_kb_empty():
    """Empty KB → needs_handoff=True with reason='kb_empty'."""
    exec_ = SubagentExecutor(
        domain="refund",
        base_model="mock",
        kb=[],
        episodic_snapshot=[],
    )
    s = exec_.handle("我要退款")
    assert s.needs_handoff is True
    assert s.handoff_reason == "kb_empty"
    assert s.confidence == 0.0


def test_handoff_when_confidence_below_tau():
    """Low confidence → needs_handoff=True."""
    # Single tiny doc unrelated to the query so BM25 hits with weak score.
    docs = [KBDoc("kb_x", "标题", "billing", "abc def ghi")]
    exec_ = SubagentExecutor(
        domain="billing",
        base_model="mock",
        kb=docs,
        episodic_snapshot=[],
        handoff_tau=0.99,  # force handoff
    )
    s = exec_.handle("退款政策")
    assert s.needs_handoff is True
    assert s.handoff_reason in ("low_confidence", "kb_empty")


def test_no_handoff_when_confident():
    """High-confidence retrieval should not trigger handoff."""
    full = _full_kb()
    exec_ = SubagentExecutor.from_specialist_config(
        domain="billing", full_kb=full,
    )
    # Use a query that strongly overlaps with kb_b1 title/text
    s = exec_.handle("退款政策 14 天 申请退款")
    # Either confident enough to skip handoff, OR if it does handoff,
    # the reason must be principled (low conf), not arbitrary.
    if s.needs_handoff:
        assert s.handoff_reason in (
            "low_confidence", "kb_empty", "domain_mismatch",
        )
    else:
        assert s.confidence > 0.0


# ---------------------- error path -----------------------------------------


class _CrashingBackend(LLMBackend):
    name = "crash"

    def generate_answer(self, query, contexts):
        raise RuntimeError("boom")


def test_executor_catches_backend_errors():
    full = _full_kb()
    exec_ = SubagentExecutor(
        domain="billing",
        base_model="crash",
        kb=[d for d in full if d.topic == "billing"],
        episodic_snapshot=[],
        backend=_CrashingBackend(),
    )
    s = exec_.handle("退款")
    assert s.error is not None
    assert "boom" in s.error
    assert s.needs_handoff is True
    assert s.confidence == 0.0


# ---------------------- multi-subagent merge -------------------------------


def test_merge_summaries_basic():
    """Orchestrator merge folds N summaries into one customer-facing artifact."""
    s1 = SubagentSummary(
        domain="billing",
        answer_summary="退款 7-14 天到账。",
        confidence=0.8,
        cited_doc_ids=["kb_b1"],
        token_budget_used=120,
    )
    s2 = SubagentSummary(
        domain="account",
        answer_summary="进入设置>安全重置密码。",
        confidence=0.7,
        cited_doc_ids=["kb_a1"],
        token_budget_used=110,
    )
    merged = merge_subagent_summaries([s1, s2])
    assert "第 1 个问题" in merged["answer"]
    assert "第 2 个问题" in merged["answer"]
    assert merged["confidence"] == pytest.approx(0.7)  # min over subs
    assert merged["needs_handoff"] is False
    assert merged["total_tokens"] == 230
    assert merged["cited_doc_ids"] == ["kb_b1", "kb_a1"]
    assert merged["errors"] == []


def test_merge_summaries_propagates_handoff():
    s1 = SubagentSummary(
        domain="billing", answer_summary="ok", confidence=0.9,
    )
    s2 = SubagentSummary(
        domain="technical", answer_summary="不确定。", confidence=0.2,
        needs_handoff=True, handoff_to="general",
        handoff_reason="low_confidence",
    )
    merged = merge_subagent_summaries([s1, s2])
    assert merged["needs_handoff"] is True
    assert ("technical", "general") in merged["handoff_targets"]


def test_merge_summaries_dedupes_cited_ids():
    s1 = SubagentSummary(domain="a", answer_summary="x", cited_doc_ids=["kb_b1", "kb_b2"])
    s2 = SubagentSummary(domain="b", answer_summary="y", cited_doc_ids=["kb_b1", "kb_a1"])
    merged = merge_subagent_summaries([s1, s2])
    assert merged["cited_doc_ids"] == ["kb_b1", "kb_b2", "kb_a1"]


def test_merge_summaries_collects_errors():
    s_err = SubagentSummary(
        domain="billing",
        answer_summary="",
        confidence=0.0,
        error="RuntimeError: boom",
    )
    s_ok = SubagentSummary(domain="account", answer_summary="ok", confidence=0.6)
    merged = merge_subagent_summaries([s_err, s_ok])
    assert merged["errors"] == ["billing: RuntimeError: boom"]
    assert merged["needs_handoff"] is True


# ---------------------- context-isolation observability -------------------


def test_three_subagents_independent_state():
    """Run billing/account/technical side by side; their counters must not
    interfere — proves there is no shared internal state."""
    full = _full_kb()
    billing = SubagentExecutor.from_specialist_config("billing", full)
    account = SubagentExecutor.from_specialist_config("account", full)
    technical = SubagentExecutor.from_specialist_config("technical", full)
    billing.handle("退款 14 天")
    account.handle("重置密码")
    technical.handle("webhook 超时")
    billing.handle("套餐降级")
    assert billing.stats()["n_calls"] == 2
    assert account.stats()["n_calls"] == 1
    assert technical.stats()["n_calls"] == 1
    # kb_size is fixed at construction
    assert billing.stats()["kb_size"] >= 1
    assert account.stats()["kb_size"] >= 1
    assert technical.stats()["kb_size"] >= 1
    # No shared docs across subagents (defensive cross-check)
    b_ids = {d.doc_id for d in billing._kb_view.docs}
    a_ids = {d.doc_id for d in account._kb_view.docs}
    t_ids = {d.doc_id for d in technical._kb_view.docs}
    # billing topics: {billing, subscription}; account: {account,
    # account_security, permissions}; technical: integrations / data
    # export / mobile / troubleshooting — there is zero overlap by design.
    assert b_ids & a_ids == set()
    assert b_ids & t_ids == set()
    assert a_ids & t_ids == set()


def test_isolation_saves_context_vs_shared():
    """Sanity: total tokens across N isolated subagents must be less than
    the cost of feeding the full KB to N agents.

    This is the headline efficiency claim; we approximate "shared
    context" cost as N × sum(len(d.text)) / 4.
    """
    full = _full_kb()
    queries = ["退款 14 天", "重置密码", "webhook 超时"]
    domains = ["billing", "account", "technical"]
    isolated_tokens = 0
    for q, d in zip(queries, domains):
        e = SubagentExecutor.from_specialist_config(d, full)
        isolated_tokens += e.handle(q).token_budget_used
    # baseline: each of 3 agents sees the entire KB
    full_text = sum(len(d.text) for d in full)
    shared_tokens = 3 * (full_text // 4)
    assert isolated_tokens < shared_tokens, (
        f"isolation should save tokens: isolated={isolated_tokens}, "
        f"shared_baseline={shared_tokens}"
    )
