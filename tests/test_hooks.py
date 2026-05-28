"""Tests for v2.1 R1 lifecycle hooks (seagent.hooks).

Covers:
  * registry: register / fire / priority sort / exception isolation / singleton
  * HookResult merging: rewrite_answer, force_escalate, force_block, add_reason,
    add_metadata, rewrite_guardrail_report
  * SupportAgent integration: POST_OUTPUT_GUARD audit hook is invoked
  * Regression: SupportAgent(hook_registry=None) byte-equivalent to the
    pre-hook path on a deterministic mock query.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from seagent.config import Config
from seagent.data import KBDoc
from seagent.hooks import (
    HookContext,
    HookPoint,
    HookRegistry,
    HookResult,
    default_registry,
    get_registry,
    set_registry,
)
from seagent.hooks.builtin import (
    make_audit_log_hook,
    make_escalation_vote_hook,
)
from seagent.llm.mock import MockBackend
from seagent.llm.base import Passage
from seagent.memory.semantic import SemanticMemory


# ----------------------------- registry ----------------------------------
def test_register_and_fire_runs_hook():
    reg = HookRegistry()
    seen = []

    def hk(ctx: HookContext):
        seen.append(ctx.point)
        return None

    reg.register(HookPoint.PRE_INPUT, hk, name="probe")
    ctx = HookContext(point=HookPoint.PRE_INPUT, query="hi")
    out = reg.fire(HookPoint.PRE_INPUT, ctx)
    assert seen == [HookPoint.PRE_INPUT]
    assert out is ctx


def test_priority_order_high_first():
    reg = HookRegistry()
    order = []
    reg.register(HookPoint.POST_INPUT, lambda c: order.append("low") or None, priority=1)
    reg.register(HookPoint.POST_INPUT, lambda c: order.append("hi") or None, priority=10)
    reg.register(HookPoint.POST_INPUT, lambda c: order.append("mid") or None, priority=5)
    reg.fire(HookPoint.POST_INPUT, HookContext(point=HookPoint.POST_INPUT))
    assert order == ["hi", "mid", "low"]


def test_tie_break_by_registration_order():
    reg = HookRegistry()
    order = []
    reg.register(HookPoint.PRE_INPUT, lambda c: order.append("a") or None, priority=5)
    reg.register(HookPoint.PRE_INPUT, lambda c: order.append("b") or None, priority=5)
    reg.fire(HookPoint.PRE_INPUT, HookContext(point=HookPoint.PRE_INPUT))
    assert order == ["a", "b"]


def test_exception_in_hook_does_not_break_chain():
    reg = HookRegistry()
    calls = []

    def bad(ctx):
        raise RuntimeError("boom")

    def good(ctx):
        calls.append("good")
        return None

    reg.register(HookPoint.POST_GENERATION, bad, name="bad", priority=10)
    reg.register(HookPoint.POST_GENERATION, good, name="good", priority=1)
    reg.fire(HookPoint.POST_GENERATION, HookContext(point=HookPoint.POST_GENERATION))
    assert calls == ["good"]


def test_unknown_point_register_raises():
    reg = HookRegistry()
    with pytest.raises(ValueError):
        reg.register("not_a_point", lambda c: None)  # type: ignore[arg-type]


def test_clear_one_point_keeps_others():
    reg = HookRegistry()
    reg.register(HookPoint.PRE_INPUT, lambda c: None)
    reg.register(HookPoint.POST_INPUT, lambda c: None)
    reg.clear(HookPoint.PRE_INPUT)
    assert reg.hooks_at(HookPoint.PRE_INPUT) == []
    assert len(reg.hooks_at(HookPoint.POST_INPUT)) == 1


def test_default_registry_singleton_swap():
    orig = get_registry()
    new = HookRegistry()
    try:
        set_registry(new)
        assert get_registry() is new
    finally:
        set_registry(orig)
    assert get_registry() is orig


# ----------------------- HookResult merging ------------------------------
def test_rewrite_answer_applied():
    reg = HookRegistry()
    reg.register(
        HookPoint.POST_GENERATION,
        lambda c: HookResult(rewrite_answer="rewritten"),
    )
    ctx = HookContext(point=HookPoint.POST_GENERATION, answer="orig")
    out = reg.fire(HookPoint.POST_GENERATION, ctx)
    assert out.answer == "rewritten"


def test_force_escalate_applied():
    reg = HookRegistry()
    reg.register(
        HookPoint.ON_ESCALATE,
        lambda c: HookResult(force_escalate=True, add_reason="manual"),
    )
    ctx = HookContext(point=HookPoint.ON_ESCALATE, escalate=False)
    out = reg.fire(HookPoint.ON_ESCALATE, ctx)
    assert out.escalate is True
    assert any("manual" in r for r in out.reasons)


def test_force_block_sets_escalate_and_metadata():
    reg = HookRegistry()
    reg.register(HookPoint.POST_OUTPUT_GUARD, lambda c: HookResult(force_block=True))
    ctx = HookContext(point=HookPoint.POST_OUTPUT_GUARD)
    out = reg.fire(HookPoint.POST_OUTPUT_GUARD, ctx)
    assert out.escalate is True
    assert out.metadata.get("force_block") is True


def test_add_metadata_merges():
    reg = HookRegistry()
    reg.register(HookPoint.POST_INPUT,
                 lambda c: HookResult(add_metadata={"k1": 1, "k2": "v"}))
    ctx = HookContext(point=HookPoint.POST_INPUT, metadata={"existing": True})
    out = reg.fire(HookPoint.POST_INPUT, ctx)
    assert out.metadata == {"existing": True, "k1": 1, "k2": "v"}


def test_rewrite_guardrail_report_applied():
    reg = HookRegistry()
    sentinel = object()
    reg.register(HookPoint.POST_OUTPUT_GUARD,
                 lambda c: HookResult(rewrite_guardrail_report=sentinel))
    ctx = HookContext(point=HookPoint.POST_OUTPUT_GUARD)
    out = reg.fire(HookPoint.POST_OUTPUT_GUARD, ctx)
    assert out.guardrail_report is sentinel


def test_chained_results_compose():
    """First hook rewrites answer, second hook escalates — both applied."""
    reg = HookRegistry()
    reg.register(HookPoint.POST_OUTPUT_GUARD,
                 lambda c: HookResult(rewrite_answer="r"), priority=10)
    reg.register(HookPoint.POST_OUTPUT_GUARD,
                 lambda c: HookResult(force_escalate=True, add_reason="2nd"),
                 priority=1)
    ctx = HookContext(point=HookPoint.POST_OUTPUT_GUARD, answer="o")
    out = reg.fire(HookPoint.POST_OUTPUT_GUARD, ctx)
    assert out.answer == "r"
    assert out.escalate is True


# --------------------- SupportAgent integration --------------------------
def _build_agent(hook_registry=None):
    from seagent.agent.support_agent import SupportAgent
    cfg = Config()
    backend = MockBackend()
    sem = SemanticMemory([
        KBDoc(doc_id="kb1", title="Refund policy", topic="billing",
              text="To request a refund, file a ticket within 30 days."),
        KBDoc(doc_id="kb2", title="Password reset", topic="account",
              text="Reset your password via the account settings page."),
    ])
    return SupportAgent(cfg, backend, sem, hook_registry=hook_registry)


def test_agent_with_no_registry_is_regression_safe():
    """SupportAgent(hook_registry=None) === pre-hook behaviour on a fixed query."""
    a_old = _build_agent(hook_registry=None)
    a_new = _build_agent(hook_registry=HookRegistry())  # empty registry
    q = "How do I get a refund?"
    r1 = a_old.handle(q)
    r2 = a_new.handle(q)
    assert r1.answer == r2.answer
    assert r1.escalate == r2.escalate
    assert abs(r1.confidence - r2.confidence) < 1e-9


def test_agent_fires_post_generation_hook():
    reg = HookRegistry()
    seen = []

    def cap(ctx):
        seen.append((ctx.point, ctx.query_safe, ctx.answer))
        return None

    reg.register(HookPoint.PRE_INPUT, cap)
    reg.register(HookPoint.POST_INPUT, cap)
    reg.register(HookPoint.PRE_GENERATION, cap)
    reg.register(HookPoint.POST_GENERATION, cap)

    agent = _build_agent(hook_registry=reg)
    agent.handle("How do I get a refund?")
    points = [s[0] for s in seen]
    assert HookPoint.PRE_INPUT in points
    assert HookPoint.POST_INPUT in points
    assert HookPoint.PRE_GENERATION in points
    assert HookPoint.POST_GENERATION in points
    # POST_GENERATION sees the answer
    post_gen = next(s for s in seen if s[0] == HookPoint.POST_GENERATION)
    assert post_gen[2]  # non-empty answer


def test_agent_post_generation_hook_can_rewrite_answer():
    reg = HookRegistry()
    reg.register(HookPoint.POST_GENERATION,
                 lambda c: HookResult(rewrite_answer="OVERRIDE_BY_HOOK"))
    agent = _build_agent(hook_registry=reg)
    r = agent.handle("any query")
    assert r.answer == "OVERRIDE_BY_HOOK"


def test_agent_on_escalate_hook_fires_when_escalating():
    reg = HookRegistry()
    fired = []
    reg.register(HookPoint.ON_ESCALATE,
                 lambda c: (fired.append(c.point), None)[1])
    # empty KB + no contexts → low confidence → escalate
    from seagent.agent.support_agent import SupportAgent
    cfg = Config()
    backend = MockBackend()
    agent = SupportAgent(cfg, backend, SemanticMemory([]), hook_registry=reg)
    r = agent.handle("totally unrelated query")
    assert r.escalate is True
    assert HookPoint.ON_ESCALATE in fired


def test_audit_log_hook_writes_jsonl(tmp_path):
    audit = make_audit_log_hook(path=str(tmp_path / "audit.jsonl"))
    reg = HookRegistry()
    reg.register(HookPoint.POST_GENERATION, audit)
    agent = _build_agent(hook_registry=reg)
    agent.handle("How do I get a refund?")
    p = tmp_path / "audit.jsonl"
    assert p.exists()
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["query"] == "How do I get a refund?"
    assert "answer" in record
    assert record["point"] == "post_generation"


def test_escalation_vote_hook_majority_no_op_when_no_report():
    """ON_ESCALATE: voter hook with no guardrail report -> no-op (returns None)."""
    hook = make_escalation_vote_hook(mode="majority")
    ctx = HookContext(point=HookPoint.ON_ESCALATE, confidence=0.3)
    result = hook(ctx)
    assert result is None


def test_escalation_vote_hook_with_fake_report():
    """voter reads (critic, groundedness, policy) signals from ctx + report."""
    class _Ground:
        supported = False
        score = 0.2

    class _Report:
        groundedness = _Ground()
        violations = []

    hook = make_escalation_vote_hook(mode="majority")
    # critic bad (conf<0.5) + groundedness bad + policy ok = 2/3 → majority escalates
    ctx = HookContext(point=HookPoint.POST_OUTPUT_GUARD,
                      confidence=0.3, guardrail_report=_Report())
    res = hook(ctx)
    assert res is not None
    assert res.force_escalate is True
    assert res.add_metadata["vote_mode"] == "majority"
    assert res.add_metadata["vote_signals"]["critic"] is True
    assert res.add_metadata["vote_signals"]["groundedness"] is True
    assert res.add_metadata["vote_signals"]["policy"] is False
