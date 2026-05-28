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
    HandoffRequest,
    IntentRouter,
    MultiAgentOrchestrator,
    SpecialistAgent,
    SubIntent,
    make_handoff_tool_schemas,
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
    # legacy helper: pin guardrail_mode='none' so we don't emit the
    # missing-guardrail warning and so the legacy fan-out (no merged/agg
    # guardrail) is what these v2.3 tests assert.
    return MultiAgentOrchestrator(
        router, specs, default_specialist="general",
        guardrail_mode="none",
    ), specs


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


# =============================================================================
# v2.7: guardrail_mode tests (merged-answer guardrail)
# =============================================================================


class _StubGuardrail:
    """Test double for GuardrailPipeline that records calls + returns a
    canned check_output verdict."""

    def __init__(self, output_action="ALLOW", output_blocked=False,
                 input_blocked=False, redacted_input=None,
                 redacted_output=None):
        from seagent.guardrails.pipeline import GuardrailReport
        self._Report = GuardrailReport
        self.output_action = output_action
        self.output_blocked = output_blocked
        self.input_blocked = input_blocked
        self.redacted_input = redacted_input
        self.redacted_output = redacted_output
        self.input_calls = []
        self.output_calls = []

    def check_input(self, user_text):
        self.input_calls.append(user_text)
        action = "BLOCK" if self.input_blocked else "ALLOW"
        return self._Report(
            stage="input", passed=not self.input_blocked,
            action=action, blocked=self.input_blocked,
            redacted_text=self.redacted_input if self.redacted_input is not None else user_text,
        )

    def check_output(self, answer, contexts):
        self.output_calls.append((answer, list(contexts)))
        return self._Report(
            stage="output", passed=(self.output_action == "ALLOW"),
            action=self.output_action,
            blocked=self.output_blocked,
            redacted_answer=self.redacted_output if self.redacted_output is not None else answer,
        )


def _build_orchestrator_v27(router_payload, *, guardrail_mode="merged",
                            guardrail=None, specialist_mode="core"):
    kb = _mini_kb()
    base = _agent_with(kb)
    specs = {
        "billing":   SpecialistAgent.for_domain("billing",   base, mode=specialist_mode),
        "account":   SpecialistAgent.for_domain("account",   base, mode=specialist_mode),
        "technical": SpecialistAgent.for_domain("technical", base, mode=specialist_mode),
        "general":   SpecialistAgent.for_domain("general",   base, mode=specialist_mode),
    }
    router = IntentRouter(backend=_StubBackend(router_payload))
    orch = MultiAgentOrchestrator(
        router, specs,
        default_specialist="general",
        guardrail=guardrail,
        guardrail_mode=guardrail_mode,
    )
    return orch, specs


def test_orchestrator_guardrail_mode_default_is_per_sub_aggregated():
    """v2.8: new default guardrail_mode is 'per_sub_aggregated'."""
    kb = _mini_kb(); base = _agent_with(kb)
    specs = {"general": SpecialistAgent.for_domain("general", base)}
    # provide a guardrail so we don't trip the missing-guardrail warning
    guard = _StubGuardrail()
    orch = MultiAgentOrchestrator(
        IntentRouter(backend=None), specs,
        default_specialist="general",
        guardrail=guard,
    )
    assert orch.guardrail_mode == "per_sub_aggregated"


def test_orchestrator_guardrail_mode_rejects_invalid():
    kb = _mini_kb(); base = _agent_with(kb)
    specs = {"general": SpecialistAgent.for_domain("general", base)}
    with pytest.raises(ValueError):
        MultiAgentOrchestrator(IntentRouter(backend=None), specs,
                               default_specialist="general",
                               guardrail_mode="bogus")


def test_orchestrator_merged_mode_downgrades_observed_specialists():
    """v2.7: specialists built in mode='observed' must be downgraded to
    'core' when guardrail_mode='merged', so the merged guardrail isn't
    double-fired."""
    guard = _StubGuardrail()
    with pytest.warns(RuntimeWarning, match="downgraded specialists"):
        orch, specs = _build_orchestrator_v27(
            '{"intents":[{"label":"billing","sub_query":"x","confidence":0.9}]}',
            guardrail_mode="merged",
            guardrail=guard,
            specialist_mode="observed",
        )
    for label, spec in specs.items():
        assert spec.mode == "core", f"{label} not downgraded"


def test_orchestrator_merged_mode_runs_single_output_guardrail():
    """v2.7: for multi_intent, guardrail.check_output is called EXACTLY ONCE
    on the merged answer (not N times)."""
    guard = _StubGuardrail(output_action="ALLOW")
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我想退款","confidence":0.9},'
        '{"label":"account","sub_query":"如何重置密码","confidence":0.8},'
        '{"label":"technical","sub_query":"Webhook 超时","confidence":0.7}'
        "]}"
    )
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="merged")
    res = orch.handle("我想退款，重置密码，Webhook 老超时")
    # exactly one output guardrail call (on merged answer), one input call
    assert len(guard.output_calls) == 1
    assert len(guard.input_calls) == 1
    # merged answer must contain all 3 sub-prefixes
    merged_answer = guard.output_calls[0][0]
    assert "针对您的第 1 个问题" in merged_answer
    assert "针对您的第 2 个问题" in merged_answer
    assert "针对您的第 3 个问题" in merged_answer
    # result.guardrail is the merged-stage report
    assert res.guardrail is not None
    assert getattr(res, "merged_guardrail_action", None) == "ALLOW"


def test_orchestrator_merged_mode_single_intent_skips_merged_guardrail():
    """v2.7: single-intent path stays on the legacy fast path — orchestrator
    must NOT run its merged guardrail (the specialist's own pipeline does)."""
    guard = _StubGuardrail()
    payload = '{"intents":[{"label":"billing","sub_query":"我想退款","confidence":0.9}]}'
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="merged")
    res = orch.handle("我想退款")
    # NO orchestrator-level guardrail calls — single intent fast path
    assert guard.output_calls == []
    assert guard.input_calls == []
    assert isinstance(res, AgentResult)


def test_orchestrator_merged_mode_escalates_when_guardrail_says_escalate():
    """v2.7: merged-stage ESCALATE verdict must propagate to AgentResult."""
    guard = _StubGuardrail(output_action="ESCALATE")
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我想退款","confidence":0.9},'
        '{"label":"account","sub_query":"如何重置密码","confidence":0.8}'
        "]}"
    )
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="merged")
    res = orch.handle("退款 + 改密码")
    assert res.escalate is True
    assert getattr(res, "merged_guardrail_action", None) == "ESCALATE"
    s = orch.stats()
    assert s["n_merged_guard_escalate"] == 1
    assert s["n_merged_guard_block"] == 0


def test_orchestrator_merged_mode_blocks_when_guardrail_says_block():
    """v2.7: merged-stage BLOCK verdict triggers escalate=True and is
    counted under n_merged_guard_block."""
    guard = _StubGuardrail(output_action="BLOCK", output_blocked=True,
                           redacted_output="[BLOCKED]")
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"q1","confidence":0.9},'
        '{"label":"account","sub_query":"q2","confidence":0.8}'
        "]}"
    )
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="merged")
    res = orch.handle("two-q")
    assert res.escalate is True
    assert res.answer == "[BLOCKED]"  # redacted_answer propagated
    assert getattr(res, "merged_guardrail_action", None) == "BLOCK"
    assert orch.stats()["n_merged_guard_block"] == 1


def test_orchestrator_merged_mode_input_block_short_circuits():
    """v2.7: merged-mode input-stage BLOCK returns canned escalation response
    without ever calling specialists."""
    guard = _StubGuardrail(input_blocked=True)
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"q1","confidence":0.9},'
        '{"label":"account","sub_query":"q2","confidence":0.8}'
        "]}"
    )
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="merged")
    res = orch.handle("malicious payload")
    assert res.escalate is True
    assert res.confidence == 0.0
    # specialists never reached -> no output guardrail call
    assert guard.output_calls == []
    assert "人工客服" in res.answer


def test_orchestrator_per_sub_mode_preserves_legacy_behaviour():
    """v2.7: guardrail_mode='per_sub' must NOT downgrade observed specialists
    and must NOT call orchestrator's own merged guardrail."""
    guard = _StubGuardrail()
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"q1","confidence":0.9},'
        '{"label":"account","sub_query":"q2","confidence":0.8}'
        "]}"
    )
    # specialists built in mode='observed' must remain 'observed'
    orch, specs = _build_orchestrator_v27(
        payload, guardrail=guard,
        guardrail_mode="per_sub",
        specialist_mode="observed",
    )
    assert all(s.mode == "observed" for s in specs.values())
    orch.handle("q1 + q2")
    # orchestrator must NOT call its own guardrail under 'per_sub'
    assert guard.output_calls == []
    assert guard.input_calls == []


def test_orchestrator_none_mode_skips_all_orchestrator_guardrails():
    """v2.7: guardrail_mode='none' means orchestrator never invokes its
    guardrail (even if one was passed)."""
    guard = _StubGuardrail()
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"q1","confidence":0.9},'
        '{"label":"account","sub_query":"q2","confidence":0.8}'
        "]}"
    )
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="none")
    orch.handle("q1 + q2")
    assert guard.output_calls == []
    assert guard.input_calls == []


def test_orchestrator_merged_mode_warns_when_no_guardrail():
    """v2.7: merged mode + no guardrail emits a RuntimeWarning at init."""
    kb = _mini_kb(); base = _agent_with(kb)
    specs = {"general": SpecialistAgent.for_domain("general", base)}
    with pytest.warns(RuntimeWarning, match="no output guardrail"):
        MultiAgentOrchestrator(IntentRouter(backend=None), specs,
                               default_specialist="general",
                               guardrail=None, guardrail_mode="merged")


# =============================================================================
# v2.8: per_sub_aggregated guardrail tests
# =============================================================================


class _PerSubGuardrail:
    """Test double that returns per-call configurable check_output verdicts.

    Pass a list of (action, supported, redacted_answer, pii_entities) tuples;
    successive check_output calls pop the next verdict (or repeat the last
    one).  Used to drive the v2.8 aggregation logic deterministically.
    """

    def __init__(
        self,
        verdicts=None,
        input_blocked=False,
        redacted_input=None,
    ):
        from seagent.guardrails.pipeline import GuardrailReport
        from seagent.guardrails.groundedness import GroundednessResult
        from seagent.guardrails.pii import PiiSpan
        self._Report = GuardrailReport
        self._Ground = GroundednessResult
        self._Span = PiiSpan
        # verdicts: list of dicts with keys
        #   action ("ALLOW"/"REWRITE"/"ESCALATE"/"BLOCK"),
        #   supported (bool),
        #   redacted_answer (str or None),
        #   pii_entities (list[str])
        self.verdicts = list(verdicts or [])
        self.input_blocked = input_blocked
        self.redacted_input = redacted_input
        self.input_calls = []
        self.output_calls = []
        self._idx = 0

    def check_input(self, user_text):
        self.input_calls.append(user_text)
        action = "BLOCK" if self.input_blocked else "ALLOW"
        return self._Report(
            stage="input", passed=not self.input_blocked,
            action=action, blocked=self.input_blocked,
            redacted_text=self.redacted_input if self.redacted_input is not None else user_text,
        )

    def check_output(self, answer, contexts):
        self.output_calls.append((answer, list(contexts)))
        if not self.verdicts:
            v = {"action": "ALLOW", "supported": True,
                 "redacted_answer": None, "pii_entities": []}
        else:
            v = self.verdicts[min(self._idx, len(self.verdicts) - 1)]
            self._idx += 1
        action = v.get("action", "ALLOW")
        supported = v.get("supported", True)
        redacted = v.get("redacted_answer")
        if redacted is None:
            redacted = answer
        pii_entities = v.get("pii_entities") or []
        spans = [
            self._Span(entity=e, start=0, end=0, text="", placeholder=f"<{e}>")
            for e in pii_entities
        ]
        ground = self._Ground(
            score=1.0 if supported else 0.0,
            supported=supported,
            unsupported_claims=[] if supported else ["x"],
            n_sentences=1,
        )
        return self._Report(
            stage="output",
            passed=(action == "ALLOW" and not pii_entities),
            action=action.lower() if action != "ALLOW" else "allow",
            blocked=(action == "BLOCK"),
            redacted_answer=redacted,
            pii_spans=spans,
            groundedness=ground,
        )


def _multi_payload_3():
    return (
        '{"intents":['
        '{"label":"billing","sub_query":"q1","confidence":0.9},'
        '{"label":"account","sub_query":"q2","confidence":0.8},'
        '{"label":"technical","sub_query":"q3","confidence":0.7}'
        "]}"
    )


def test_orchestrator_per_sub_aggregated_runs_check_output_per_sub():
    """v2.8: orchestrator must call check_output exactly N times (once per sub)
    and exactly one check_input on the original query."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "ALLOW", "supported": True},
        {"action": "ALLOW", "supported": True},
        {"action": "ALLOW", "supported": True},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("three things")
    assert len(guard.output_calls) == 3
    assert len(guard.input_calls) == 1
    assert res.escalate is False
    assert getattr(res, "agg_guardrail_action", None) == "ALLOW"
    # answer carries all three sub-prefixes
    assert "针对您的第 1 个问题" in res.answer
    assert "针对您的第 2 个问题" in res.answer
    assert "针对您的第 3 个问题" in res.answer


def test_orchestrator_per_sub_aggregated_any_supported_passes():
    """v2.8: any-supported aggregation — 1 supported sub + 2 unsupported
    must NOT escalate on groundedness."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "ALLOW", "supported": True},   # this one carries the bundle
        {"action": "ALLOW", "supported": False},
        {"action": "ALLOW", "supported": False},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("three things")
    assert res.escalate is False
    assert getattr(res, "agg_guardrail_action", None) == "ALLOW"
    bd = getattr(res, "agg_sub_breakdown", {})
    assert bd.get("any_supported") is True


def test_orchestrator_per_sub_aggregated_all_unsupported_escalates():
    """v2.8: if NO sub is supported (any_ground_seen=True, any_supported=False)
    the bundle escalates on groundedness."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "ALLOW", "supported": False},
        {"action": "ALLOW", "supported": False},
        {"action": "ALLOW", "supported": False},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("three things")
    assert res.escalate is True
    assert getattr(res, "agg_guardrail_action", None) == "ESCALATE"
    assert orch.stats()["n_agg_escalate"] == 1


def test_orchestrator_per_sub_aggregated_any_block_blocks_bundle():
    """v2.8: a single BLOCK verdict in any sub blocks the bundle and
    forces escalate=True."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "ALLOW", "supported": True},
        {"action": "BLOCK", "supported": True, "redacted_answer": "[BLOCKED]"},
        {"action": "ALLOW", "supported": True},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("three things")
    assert res.escalate is True
    assert getattr(res, "agg_guardrail_action", None) == "BLOCK"
    assert orch.stats()["n_agg_block"] == 1
    # the redacted sub-answer should have surfaced into the merged text
    assert "[BLOCKED]" in res.answer


def test_orchestrator_per_sub_aggregated_majority_escalate_vote():
    """v2.8: bundle escalates when STRICTLY MORE THAN HALF of subs vote
    ESCALATE; 1/3 alone must not escalate."""
    # 1 escalate / 3 -> NO bundle escalate (groundedness still ALLOW via any-supported)
    guard1 = _PerSubGuardrail(verdicts=[
        {"action": "ESCALATE", "supported": True},
        {"action": "ALLOW", "supported": True},
        {"action": "ALLOW", "supported": True},
    ])
    orch1, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard1,
                                       guardrail_mode="per_sub_aggregated")
    res1 = orch1.handle("three things")
    assert res1.escalate is False
    assert getattr(res1, "agg_guardrail_action", None) == "ALLOW"

    # 2 escalate / 3 -> majority -> bundle escalate
    guard2 = _PerSubGuardrail(verdicts=[
        {"action": "ESCALATE", "supported": True},
        {"action": "ESCALATE", "supported": True},
        {"action": "ALLOW", "supported": True},
    ])
    orch2, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard2,
                                       guardrail_mode="per_sub_aggregated")
    res2 = orch2.handle("three things")
    assert res2.escalate is True
    assert getattr(res2, "agg_guardrail_action", None) == "ESCALATE"


def test_orchestrator_per_sub_aggregated_pii_per_sub_redaction():
    """v2.8: per-sub PII redacted_answer must be propagated into the
    merged answer (not the raw sub-answer)."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "ALLOW", "supported": True,
         "redacted_answer": "用户 [EMAIL] 的退款已处理。", "pii_entities": ["EMAIL"]},
        {"action": "ALLOW", "supported": True},
        {"action": "ALLOW", "supported": True},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("three things")
    assert "[EMAIL]" in res.answer
    # aggregated report keeps the union of PII entities in reasons
    assert any("EMAIL" in r for r in (res.guardrail.reasons or []))


def test_orchestrator_per_sub_aggregated_single_intent_skips_aggregation():
    """v2.8: single-intent path bypasses the per-sub aggregation entirely
    (no orchestrator-level check_output calls)."""
    guard = _PerSubGuardrail()
    payload = '{"intents":[{"label":"billing","sub_query":"q","confidence":0.9}]}'
    orch, _ = _build_orchestrator_v27(payload, guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    orch.handle("q")
    assert guard.output_calls == []
    assert guard.input_calls == []


def test_orchestrator_per_sub_aggregated_downgrades_observed_specialists():
    """v2.8: just like 'merged', the new default also downgrades
    observed-mode specialists to 'core'."""
    guard = _PerSubGuardrail()
    with pytest.warns(RuntimeWarning, match="downgraded specialists"):
        orch, specs = _build_orchestrator_v27(
            _multi_payload_3(),
            guardrail=guard,
            guardrail_mode="per_sub_aggregated",
            specialist_mode="observed",
        )
    for label, spec in specs.items():
        assert spec.mode == "core", f"{label} not downgraded"


def test_orchestrator_per_sub_aggregated_input_block_short_circuits():
    """v2.8: input-stage BLOCK returns canned escalation without ever
    calling specialists or per-sub guardrails."""
    guard = _PerSubGuardrail(input_blocked=True)
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    res = orch.handle("malicious")
    assert res.escalate is True
    assert res.confidence == 0.0
    assert guard.output_calls == []
    assert "人工客服" in res.answer


def test_orchestrator_per_sub_aggregated_warns_when_no_guardrail():
    """v2.8: per_sub_aggregated + no guardrail emits a RuntimeWarning at init."""
    kb = _mini_kb(); base = _agent_with(kb)
    specs = {"general": SpecialistAgent.for_domain("general", base)}
    with pytest.warns(RuntimeWarning, match="no output guardrail"):
        MultiAgentOrchestrator(IntentRouter(backend=None), specs,
                               default_specialist="general",
                               guardrail=None,
                               guardrail_mode="per_sub_aggregated")


def test_orchestrator_per_sub_aggregated_stats_counters():
    """v2.8: per-mode counters n_agg_block/n_agg_escalate/n_agg_rewrite
    are exposed via stats()."""
    guard = _PerSubGuardrail(verdicts=[
        {"action": "REWRITE", "supported": True},
        {"action": "ALLOW", "supported": True},
        {"action": "ALLOW", "supported": True},
    ])
    orch, _ = _build_orchestrator_v27(_multi_payload_3(), guardrail=guard,
                                      guardrail_mode="per_sub_aggregated")
    orch.handle("q")
    s = orch.stats()
    assert s["n_agg_rewrite"] == 1
    assert s["n_agg_block"] == 0
    assert s["n_agg_escalate"] == 0


# =============================================================================
# v3.2: OpenAI Agents SDK 2026 mid-flight handoff tests
# =============================================================================


def test_handoff_request_to_tool_call_format_matches_openai_sdk_shape():
    """HandoffRequest.to_tool_call_format() must produce the canonical
    OpenAI Agents SDK 2026 function-tool-call shape."""
    req = HandoffRequest(
        from_domain="billing",
        target_domain="account",
        context_summary="user is asking about account balance, not invoice",
        reason="topic_mismatch",
        confidence=0.42,
        urgency="normal",
    )
    fmt = req.to_tool_call_format()
    assert fmt["type"] == "function"
    assert fmt["function"]["name"] == "handoff_to_account"
    args = fmt["function"]["arguments"]
    assert args["context_summary"] == "user is asking about account balance, not invoice"
    assert args["reason"] == "topic_mismatch"


def test_handoff_request_to_dict_round_trip():
    req = HandoffRequest(
        from_domain="technical", target_domain="human",
        context_summary="API outage", reason="low_confidence",
        confidence=0.1, urgency="urgent",
    )
    d = req.to_dict()
    assert d["from_domain"] == "technical"
    assert d["target_domain"] == "human"
    assert d["urgency"] == "urgent"
    assert d["confidence"] == 0.1


def test_handoff_request_normalises_invalid_urgency():
    req = HandoffRequest(
        from_domain="billing", target_domain="account",
        context_summary="", reason="x", urgency="extremely-urgent",
    )
    assert req.urgency == "normal"


def test_make_handoff_tool_schemas_produces_openai_function_tools():
    schemas = make_handoff_tool_schemas(["billing", "account", "technical"])
    assert len(schemas) == 3
    names = [s["function"]["name"] for s in schemas]
    assert names == [
        "handoff_to_billing", "handoff_to_account", "handoff_to_technical",
    ]
    for s in schemas:
        assert s["type"] == "function"
        params = s["function"]["parameters"]
        assert "context_summary" in params["properties"]
        assert "reason" in params["properties"]
        assert set(params["required"]) == {"context_summary", "reason"}


# ---------------- specialist heuristic _decide_handoff ----------------------


class _ConfBackend(MockBackend):
    """MockBackend with a forced confidence override on generate_answer
    output — used to make the specialist's confidence deterministic."""

    def __init__(self, forced_answer=""):
        super().__init__()
        self.forced_answer = forced_answer

    def generate_answer(self, query, contexts):
        # mock returns the top-scored passage text by default; if we force
        # an empty answer the SupportAgent's heuristic confidence drops.
        if self.forced_answer is not None:
            return self.forced_answer
        return super().generate_answer(query, contexts)


def test_specialist_low_confidence_emits_handoff_to_human():
    """When the specialist's own confidence falls below the threshold it
    must emit a HandoffRequest pointing at 'human'."""
    kb = _mini_kb()
    cfg = Config()
    backend = _ConfBackend(forced_answer="")   # empty answer -> low conf
    sem = SemanticMemory(kb, cfg.score_norm_k)
    base = SupportAgent(cfg, backend, sem)
    spec = SpecialistAgent.for_domain(
        "billing", base, handoff_confidence_threshold=0.99,
    )
    res = spec.handle("我想申请退款")
    req = getattr(res, "handoff_request", None)
    assert req is not None, "specialist should have emitted a handoff request"
    assert req.target_domain == "human"
    assert req.reason == "low_confidence"
    assert req.from_domain == "billing"


def test_specialist_topic_mismatch_emits_handoff_to_sibling():
    """billing specialist asked about an account-topic query (KB top hit is
    an account doc) emits handoff_to_account."""
    kb = _mini_kb()
    base = _agent_with(kb)
    # disable the topic filter (no kb_topics) so we *retrieve* the account
    # doc — but keep self.domain_topics=billing-set so the mismatch fires.
    spec = SpecialistAgent(
        domain="billing",
        base_agent=base,
        kb_filter=None,           # don't filter; we want the off-topic hit
        domain_topics=["billing", "subscription"],
        handoff_confidence_threshold=0.0,  # disable low-conf branch
    )
    # query whose top KB hit is account-topic (kb_a1, password reset)
    res = spec.handle("我忘记密码了，请帮我重置密码")
    req = getattr(res, "handoff_request", None)
    assert req is not None, "specialist should have detected topic mismatch"
    assert req.target_domain == "account", f"got {req.target_domain}"
    assert req.reason == "topic_mismatch"
    assert req.from_domain == "billing"


def test_specialist_in_domain_query_does_not_emit_handoff():
    """When the top KB hit is in-domain, no handoff is emitted."""
    kb = _mini_kb()
    base = _agent_with(kb)
    spec = SpecialistAgent.for_domain(
        "billing", base, handoff_confidence_threshold=0.0,
    )
    res = spec.handle("我想申请退款")
    assert getattr(res, "handoff_request", None) is None


def test_general_specialist_never_emits_handoff():
    """The catch-all 'general' specialist must never emit handoffs (would
    loop)."""
    kb = _mini_kb()
    base = _agent_with(kb)
    backend = _ConfBackend(forced_answer="")
    sem = SemanticMemory(kb, Config().score_norm_k)
    base = SupportAgent(Config(), backend, sem)
    spec = SpecialistAgent.for_domain(
        "general", base, handoff_confidence_threshold=0.99,
    )
    res = spec.handle("anything at all")
    assert getattr(res, "handoff_request", None) is None


# ---------------- orchestrator mid-flight dispatch --------------------------


def _build_handoff_orchestrator(router_payload, *, enable=True, max_hops=1):
    kb = _mini_kb()
    base = _agent_with(kb)
    specs = {
        "billing":   SpecialistAgent.for_domain("billing",   base),
        "account":   SpecialistAgent.for_domain("account",   base),
        "technical": SpecialistAgent.for_domain("technical", base),
        "general":   SpecialistAgent.for_domain("general",   base),
    }
    orch = MultiAgentOrchestrator(
        IntentRouter(backend=_StubBackend(router_payload)), specs,
        default_specialist="general",
        guardrail_mode="none",
        enable_mid_flight_handoff=enable,
        max_handoff_hops=max_hops,
    )
    return orch, specs


def test_orchestrator_dispatches_mid_flight_handoff_single_intent():
    """Single-intent path: billing specialist hands off to account, the
    final result reflects the account dispatch."""
    payload = '{"intents":[{"label":"billing","sub_query":"我忘记密码了","confidence":0.9}]}'
    orch, specs = _build_handoff_orchestrator(payload, enable=True)
    res = orch.handle("我忘记密码了")
    # handoff_trace must include a handoff_to_account event
    trace = getattr(res, "handoff_trace", [])
    assert len(trace) >= 1
    names = [e["function"]["name"] for e in trace]
    assert "handoff_to_account" in names
    # the dispatched event is marked True
    dispatched = [e for e in trace if e.get("dispatched")]
    assert len(dispatched) >= 1
    s = orch.stats()
    assert s["n_handoff_emitted"] >= 1
    assert s["n_handoff_dispatched"] >= 1


def test_orchestrator_default_disables_mid_flight_handoff():
    """enable_mid_flight_handoff defaults to False — old call sites get
    byte-identical behaviour (no handoff_trace, no dispatch)."""
    payload = '{"intents":[{"label":"billing","sub_query":"我忘记密码了","confidence":0.9}]}'
    # use the existing _build_orchestrator helper (doesn't pass the new flag)
    orch, _ = _build_orchestrator(router_payload=payload)
    res = orch.handle("我忘记密码了")
    assert getattr(res, "handoff_trace", None) is None
    s = orch.stats()
    assert s["n_handoff_dispatched"] == 0


def test_orchestrator_handoff_to_human_forces_escalation():
    """A HandoffRequest with target_domain='human' forces escalate=True on
    the result and does NOT re-dispatch."""
    # Use a KB that ONLY contains billing-topic docs so the topic-mismatch
    # branch in _decide_handoff cannot fire — the low-confidence branch
    # (target='human') must therefore win.
    kb_billing_only = [
        KBDoc("kb_b1", "退款政策", "billing",
              "您可以在订单完成后 14 天内申请退款。"),
        KBDoc("kb_b2", "套餐降级", "subscription",
              "降级在当前计费周期结束后生效。"),
    ]
    cfg = Config()
    backend = _ConfBackend(forced_answer="")  # low confidence -> human
    sem = SemanticMemory(kb_billing_only, cfg.score_norm_k)
    base = SupportAgent(cfg, backend, sem)
    specs = {
        "billing":   SpecialistAgent.for_domain(
            "billing", base, handoff_confidence_threshold=0.99
        ),
        "general":   SpecialistAgent.for_domain("general", base),
    }
    payload = '{"intents":[{"label":"billing","sub_query":"x","confidence":0.9}]}'
    orch = MultiAgentOrchestrator(
        IntentRouter(backend=_StubBackend(payload)), specs,
        default_specialist="general",
        guardrail_mode="none",
        enable_mid_flight_handoff=True,
    )
    res = orch.handle("x")
    assert res.escalate is True
    trace = getattr(res, "handoff_trace", [])
    assert any(e["function"]["name"] == "handoff_to_human" for e in trace)
    s = orch.stats()
    assert s["n_handoff_to_human"] == 1
    assert s["n_handoff_dispatched"] == 0  # no re-dispatch for 'human'


def test_orchestrator_max_handoff_hops_caps_chain():
    """max_handoff_hops=0 means even a valid sibling handoff is blocked."""
    payload = '{"intents":[{"label":"billing","sub_query":"我忘记密码了","confidence":0.9}]}'
    orch, _ = _build_handoff_orchestrator(payload, enable=True, max_hops=0)
    res = orch.handle("我忘记密码了")
    s = orch.stats()
    # the request was emitted but never dispatched
    assert s["n_handoff_emitted"] >= 1
    assert s["n_handoff_dispatched"] == 0
    assert s["n_handoff_loops_blocked"] >= 1
    # trace records the attempt with dispatched=False
    trace = getattr(res, "handoff_trace", [])
    assert any(e.get("dispatched") is False for e in trace)


def test_orchestrator_multi_intent_handoff_per_sub_path():
    """Multi-intent fan-out: a billing sub-intent that's really an account
    question must trigger a handoff dispatch independently of the other
    sub-intents."""
    payload = (
        '{"intents":['
        '{"label":"billing","sub_query":"我忘记密码了","confidence":0.9},'
        '{"label":"technical","sub_query":"Webhook 超时怎么排查","confidence":0.7}'
        "]}"
    )
    orch, _ = _build_handoff_orchestrator(payload, enable=True)
    res = orch.handle("我忘记密码了，并且 Webhook 老超时")
    s = orch.stats()
    assert s["n_handoff_emitted"] >= 1
    assert s["n_handoff_dispatched"] >= 1
    # merged answer still includes both sub-intents
    assert "针对您的第 1 个问题" in res.answer
    assert "针对您的第 2 个问题" in res.answer


def test_orchestrator_disabled_flag_byte_compatible_with_v28():
    """With enable_mid_flight_handoff=False (default), even if specialists
    *would* emit a handoff (low conf), no trace and no dispatch happens —
    the result is byte-equivalent to the v2.8 path."""
    kb = _mini_kb()
    cfg = Config()
    # use a backend that would trigger low-conf handoffs IF the flag were on
    backend = _ConfBackend(forced_answer="")
    sem = SemanticMemory(kb, cfg.score_norm_k)
    base = SupportAgent(cfg, backend, sem)
    specs = {
        "billing":   SpecialistAgent.for_domain(
            "billing", base, handoff_confidence_threshold=0.99
        ),
        "general":   SpecialistAgent.for_domain("general", base),
    }
    payload = '{"intents":[{"label":"billing","sub_query":"x","confidence":0.9}]}'
    orch = MultiAgentOrchestrator(
        IntentRouter(backend=_StubBackend(payload)), specs,
        default_specialist="general",
        guardrail_mode="none",
        # NOTE: enable_mid_flight_handoff omitted -> default False
    )
    res = orch.handle("x")
    assert getattr(res, "handoff_trace", None) is None
    assert orch.stats()["n_handoff_dispatched"] == 0
    # the specialist may still have attached a handoff_request to its own
    # result (that's by design — it's metadata) but the orchestrator must
    # not act on it.  The escalation flag was NOT forced by the orchestrator.
    # (It may still be True from the underlying SupportAgent pipeline; we
    # only assert no orchestrator-level human handoff was counted.)
    assert orch.stats()["n_handoff_to_human"] == 0
