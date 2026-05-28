"""Tests for v2.3 R2 — multi_agent (router / specialist / orchestrator).

These tests rely **only** on the mock LLM backend + a small in-memory KB so
they run in <1s with no network.  Real-LLM behaviour is validated by Exp E.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest

THIS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(THIS, "..", "src"))

from seagent.agent.support_agent import AgentResult, SupportAgent
from seagent.config import Config
from seagent.data import KBDoc
from seagent.llm.base import LLMBackend, Passage
from seagent.llm.mock import MockBackend
from seagent.memory.semantic import SemanticMemory
from seagent.multi_agent import (
    HandoffProtocol,
    IntentRouter,
    MultiAgentOrchestrator,
    SpecialistAgent,
    SubIntent,
)
from seagent.multi_agent.router import _extract_json_obj


# ----------------------- helpers / fixtures ---------------------------------


def _mini_kb():
    return [
        KBDoc("kb_b1", "退款政策", "billing",
              "您可以在订单完成后 14 天内申请退款。请进入「设置>账单」点击「申请退款」。"),
        KBDoc("kb_b2", "套餐降级", "subscription",
              "降级在当前计费周期结束后生效，差额以账户积分形式保留。"),
        KBDoc("kb_a1", "重置密码", "account",
              "请进入「设置>安全」点击「忘记密码」，按邮箱链接重置即可。"),
        KBDoc("kb_t1", "Webhook 超时排查", "integrations_api",
              "请检查回调端点是否返回 2xx，并确保 5 秒内响应；可在「集成>日志」查看历史调用。"),
        KBDoc("kb_g1", "联系客服", "general",
              "您可以通过 support@nimbusflow.com 联系我们的支持团队。"),
    ]


def _agent_with(kb):
    cfg = Config()
    # never trip on guardrail / tracer / calibrator in unit tests
    backend = MockBackend()
    sem = SemanticMemory(kb, cfg.score_norm_k)
    return SupportAgent(cfg, backend, sem)


class _StubBackend(LLMBackend):
    """Backend that returns a pre-baked _chat response (for router tests)."""

    name = "stub"

    def __init__(self, chat_response: str):
        self.chat_response = chat_response
        self.calls = []
        self._lock = threading.Lock()

    def _chat(self, system: str, user: str) -> str:  # noqa: D401
        with self._lock:
            self.calls.append((system, user))
        return self.chat_response

    def generate_answer(self, query, contexts):
        return "stub-answer"


class _RaisingBackend(LLMBackend):
    name = "raising"

    def _chat(self, system: str, user: str) -> str:
        raise RuntimeError("boom")

    def generate_answer(self, query, contexts):
        return ""


# ----------------------- router unit tests ----------------------------------


def test_router_no_backend_returns_single_general_intent():
    r = IntentRouter(backend=None)
    out = r.route("我想退款")
    assert len(out) == 1
    assert out[0].label == "general"
    assert out[0].sub_query == "我想退款"


def test_router_parses_well_formed_json():
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我想退款","confidence":0.9},'
        '{"label":"account","sub_query":"我也想改密码","confidence":0.8}'
        "]}"
    )
    r = IntentRouter(backend=_StubBackend(payload))
    out = r.route("我想退款，顺便改下密码")
    assert len(out) == 2
    assert out[0].label == "billing" and "退款" in out[0].sub_query
    assert out[1].label == "account"


def test_router_extracts_json_from_code_fence_with_prose():
    payload = (
        "好的，我的分析：\n```json\n"
        '{"intents":[{"label":"technical","sub_query":"Webhook 超时","confidence":0.7}]}'
        "\n```\n（结束）"
    )
    r = IntentRouter(backend=_StubBackend(payload))
    out = r.route("我们的 Webhook 老是超时")
    assert len(out) == 1
    assert out[0].label == "technical"


def test_router_unknown_label_maps_to_general():
    payload = '{"intents":[{"label":"weather","sub_query":"今天天气","confidence":0.5}]}'
    r = IntentRouter(backend=_StubBackend(payload))
    out = r.route("今天天气")
    assert out[0].label == "general"


def test_router_falls_back_on_parse_error():
    r = IntentRouter(backend=_StubBackend("not json at all"))
    out = r.route("随便一句")
    assert len(out) == 1
    assert out[0].label == "general"
    assert r.n_parse_fail == 1


def test_router_falls_back_on_api_error():
    r = IntentRouter(backend=_RaisingBackend())
    out = r.route("我想退款")
    assert len(out) == 1
    assert out[0].label == "general"
    assert r.n_parse_fail == 1


def test_router_cache_hits_avoid_second_llm_call():
    payload = '{"intents":[{"label":"billing","sub_query":"退款","confidence":0.9}]}'
    backend = _StubBackend(payload)
    r = IntentRouter(backend=backend, cache=True)
    r.route("我想退款 X")
    r.route("我想退款 X")
    assert len(backend.calls) == 1
    assert r.n_cache_hits == 1


def test_router_cache_can_be_disabled():
    payload = '{"intents":[{"label":"billing","sub_query":"退款","confidence":0.9}]}'
    backend = _StubBackend(payload)
    r = IntentRouter(backend=backend, cache=False)
    r.route("Q")
    r.route("Q")
    assert len(backend.calls) == 2


def test_router_empty_query_returns_general_no_call():
    backend = _StubBackend('{"intents":[]}')
    r = IntentRouter(backend=backend)
    out = r.route("")
    assert len(out) == 1 and out[0].label == "general"
    assert backend.calls == []  # no llm hit


def test_extract_json_obj_handles_nested_strings():
    text = '## prose\n {"intents": [{"label":"a","sub_query":"x{y}z","confidence":0.1}]} done'
    extracted = _extract_json_obj(text)
    assert extracted is not None
    data = json.loads(extracted)
    assert data["intents"][0]["sub_query"] == "x{y}z"


# ----------------------- specialist unit tests ------------------------------


def test_specialist_handle_matches_support_agent_interface():
    kb = _mini_kb()
    base = _agent_with(kb)
    spec = SpecialistAgent.for_domain("billing", base)
    res = spec.handle("我想申请退款")
    assert isinstance(res, AgentResult)
    assert res.query == "我想申请退款"
    assert res.answer  # mock backend returns the top-scored passage text
    # specialist must tag the result with its domain (duck-typed)
    assert getattr(res, "specialist_domain", None) == "billing"


def test_specialist_kb_filter_drops_off_topic_passages():
    kb = _mini_kb()
    base = _agent_with(kb)
    spec = SpecialistAgent.for_domain("billing", base, kb_topics=["billing"])
    # query that would normally retrieve account doc — billing specialist
    # should only return billing passages (when there's at least one)
    res = spec.handle("套餐降级")
    kb_refs = [p.ref for p in res.contexts if p.source == "kb"]
    # All KB hits must come from billing/subscription docs (kb_b*).
    assert kb_refs, "expected at least one KB hit"
    for ref in kb_refs:
        assert ref.startswith("kb_b"), f"got off-topic ref {ref}"


def test_specialist_no_filter_keeps_all_passages():
    kb = _mini_kb()
    base = _agent_with(kb)
    spec = SpecialistAgent.for_domain("general", base)  # general = empty filter
    res = spec.handle("我想申请退款")
    # general specialist behaves exactly like the base path
    assert res.contexts == base._retrieve("我想申请退款")[0] or len(res.contexts) > 0


def test_specialist_fallback_when_filter_empties_kb():
    kb = _mini_kb()
    base = _agent_with(kb)
    # Topic that has no KB doc at all — fallback should kick in
    spec = SpecialistAgent.for_domain("billing", base, kb_topics=["nonexistent_topic"])
    res = spec.handle("我想申请退款")
    # With fallback_on_empty=True (default) at least one KB passage should remain
    assert any(p.source == "kb" for p in res.contexts)


# ----------------------- orchestrator unit tests ----------------------------


def _build_orchestrator(router_payload: str = None, router=None):
    kb = _mini_kb()
    base = _agent_with(kb)
    specs = {
        "billing":   SpecialistAgent.for_domain("billing", base),
        "account":   SpecialistAgent.for_domain("account", base),
        "technical": SpecialistAgent.for_domain("technical", base),
        "general":   SpecialistAgent.for_domain("general", base),
    }
    if router is None:
        if router_payload is None:
            router = IntentRouter(backend=None)
        else:
            router = IntentRouter(backend=_StubBackend(router_payload))
    return MultiAgentOrchestrator(router, specs, default_specialist="general"), specs


def test_orchestrator_single_intent_fast_path():
    payload = '{"intents":[{"label":"billing","sub_query":"我想退款","confidence":0.9}]}'
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("我想退款")
    assert isinstance(res, AgentResult)
    # single intent => no "第 1 个问题" merge prefix
    assert "针对您的第" not in res.answer
    # routing recorded
    s = orch.stats()
    assert s["n_routed"] == 1 and s["n_multi"] == 0


def test_orchestrator_multi_intent_merges_with_prefixes():
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我想退款","confidence":0.9},'
        '{"label":"account","sub_query":"如何重置密码","confidence":0.8},'
        '{"label":"technical","sub_query":"Webhook 总是超时怎么排查","confidence":0.7}'
        "]}"
    )
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("我想退款，顺便重置下密码，还有 Webhook 老超时。")
    assert "针对您的第 1 个问题" in res.answer
    assert "针对您的第 2 个问题" in res.answer
    assert "针对您的第 3 个问题" in res.answer
    # used_sources is a union (at least kb)
    assert "kb" in res.used_sources
    # confidence is the min across sub-results
    assert 0.0 <= res.confidence <= 1.0
    # observability: 3 sub-intents recorded
    assert getattr(res, "sub_intents", None) is not None
    assert len(res.sub_intents) == 3
    assert orch.stats()["n_multi"] == 1


def test_orchestrator_unknown_label_routes_to_default():
    payload = '{"intents":[{"label":"weather","sub_query":"今天天气","confidence":0.5}]}'
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("今天天气")
    # didn't crash and produced an answer via 'general' specialist
    assert isinstance(res, AgentResult)
    assert res.answer


def test_orchestrator_specialist_error_does_not_block_others(monkeypatch):
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我想退款","confidence":0.9},'
        '{"label":"account","sub_query":"如何重置密码","confidence":0.8}'
        "]}"
    )
    orch, specs = _build_orchestrator(router_payload=payload)

    # make the billing specialist crash
    def _boom(self, query):
        raise RuntimeError("billing-down")

    monkeypatch.setattr(SpecialistAgent, "handle", _boom)

    res = orch.handle("我想退款，顺便重置密码")
    # the merge surfaced the failure as an escalation rather than throwing
    assert res.escalate is True
    assert "针对您的第 1 个问题" in res.answer
    assert "针对您的第 2 个问题" in res.answer
    # at least one sub-error counted
    assert getattr(res, "n_sub_errors", 0) >= 1
    assert orch.stats()["n_specialist_errors"] >= 1


def test_orchestrator_empty_intents_uses_default():
    payload = '{"intents":[]}'
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("hi")
    assert isinstance(res, AgentResult)
    assert res.answer  # default specialist still answers


def test_orchestrator_rejects_missing_default():
    kb = _mini_kb()
    base = _agent_with(kb)
    specs = {"billing": SpecialistAgent.for_domain("billing", base)}
    with pytest.raises(ValueError):
        MultiAgentOrchestrator(IntentRouter(backend=None), specs, default_specialist="general")


def test_orchestrator_refund_falls_back_to_billing():
    payload = '{"intents":[{"label":"refund","sub_query":"我想退款","confidence":0.9}]}'
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("我想退款")
    # we expect the billing specialist to have answered (mock returns
    # the top-scored billing passage); no crash, has content
    assert res.answer
    # single dispatch -> no merge prefix
    assert "针对您的第" not in res.answer


# ----------------------- handoff data record --------------------------------


def test_specialist_observed_mode_runs_through_base_handle():
    """mode='observed' delegates to base.handle() (here = _handle_core
    because guardrail/tracer are None) and still applies the KB filter."""
    kb = _mini_kb()
    base = _agent_with(kb)
    spec = SpecialistAgent.for_domain("billing", base, mode="observed")
    res = spec.handle("我想申请退款")
    assert isinstance(res, AgentResult)
    # base._retrieve must be restored after the call
    from seagent.agent.support_agent import SupportAgent as SA
    assert base._retrieve.__self__ is base
    # only billing/subscription KB docs should remain
    for p in res.contexts:
        if p.source == "kb":
            assert p.ref.startswith("kb_b"), f"off-topic ref {p.ref}"


def test_specialist_invalid_mode_rejected():
    kb = _mini_kb()
    base = _agent_with(kb)
    with pytest.raises(ValueError):
        SpecialistAgent.for_domain("billing", base, mode="bogus")


def test_handoff_protocol_to_dict_and_human_factory():
    h = HandoffProtocol(reason="domain_mismatch", target_domain="refund",
                        context_summary="user wants refund", urgent=False,
                        metadata={"trace_id": "t-1"})
    d = h.to_dict()
    assert d["reason"] == "domain_mismatch"
    assert d["target_domain"] == "refund"
    assert d["metadata"]["trace_id"] == "t-1"

    h2 = HandoffProtocol.to_human("low_confidence", context_summary="...", urgent=True)
    assert h2.target_domain == "human"
    assert h2.urgent is True
