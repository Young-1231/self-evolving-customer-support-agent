"""Offline tests for the LLM-judge groundedness (P1 scaffold).

The real LLM call is not exercised here (would need network + API key); we test
the parser + soft-fail fallback path, which is what the production agent relies
on when the judge is flaky.
"""
import pytest

from seagent.guardrails.groundedness_llm import (
    LLMJudgeGroundedness,
    _parse_verdict,
)


class _Ctx:
    def __init__(self, text, source="kb", ref="kb_001"):
        self.text = text
        self.source = source
        self.ref = ref


def test_parse_verdict_direct_json():
    raw = '{"supported": true, "confidence": 0.87, "missing_claims": []}'
    supported, conf, missing = _parse_verdict(raw)
    assert supported is True
    assert abs(conf - 0.87) < 1e-9
    assert missing == []


def test_parse_verdict_embedded_json_with_chatter():
    raw = 'Sure, here is the JSON:\n{"supported": false, "confidence": 0.3, "missing_claims": ["refund window"]}\nThat is my verdict.'
    supported, conf, missing = _parse_verdict(raw)
    assert supported is False
    assert abs(conf - 0.3) < 1e-9
    assert missing == ["refund window"]


def test_parse_verdict_garbage_falls_back_neutral():
    supported, conf, missing = _parse_verdict("LOL no json here")
    assert supported is False
    assert conf == 0.5
    assert "judge response not json" in missing[0]


def test_parse_verdict_clamps_confidence():
    raw = '{"supported": true, "confidence": 5.0, "missing_claims": []}'
    _, conf, _ = _parse_verdict(raw)
    assert conf == 1.0


def test_parse_verdict_handles_bad_confidence_type():
    raw = '{"supported": true, "confidence": "maybe", "missing_claims": null}'
    supported, conf, missing = _parse_verdict(raw)
    assert supported is True
    assert conf == 0.5
    assert missing == []


def test_check_empty_answer_returns_unsupported():
    g = LLMJudgeGroundedness()
    r = g.check("", [_Ctx("anything")])
    assert r.supported is False
    assert r.score == 0.0
    assert "empty answer" in r.unsupported_claims[0]


def test_check_soft_fails_without_api_key(monkeypatch):
    """No DEEPSEEK_API_KEY => OpenAI client will raise on first call; we should
    return a neutral verdict (judge_error:*) instead of crashing the agent."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # Stub the OpenAI client so we don't make a real network call
    class _BadClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("simulated network failure")
    g = LLMJudgeGroundedness()
    g._client = _BadClient()
    r = g.check("some answer", [_Ctx("some context")])
    assert r.supported is False
    assert r.score == 0.5
    assert r.unsupported_claims and r.unsupported_claims[0].startswith("judge_error:")


def test_confidence_threshold_gates_supported():
    """Even if the judge says supported=True, low confidence should flip the
    final supported flag to False (configurable threshold)."""
    g = LLMJudgeGroundedness(confidence_threshold=0.7)
    class _GoodClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    class _R:
                        choices = [type("M", (), {"message": type("X", (), {"content": '{"supported": true, "confidence": 0.5, "missing_claims": []}'})()})]
                    return _R()
    g._client = _GoodClient()
    r = g.check("answer", [_Ctx("ctx")])
    assert r.score == 0.5
    assert r.supported is False  # below threshold 0.7


def test_check_batch_returns_one_per_input():
    g = LLMJudgeGroundedness()
    class _StubClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("ignored")
    g._client = _StubClient()
    out = g.check_batch([("a1", [_Ctx("c1")]), ("a2", [_Ctx("c2")])])
    assert len(out) == 2
    assert all(r.score == 0.5 for r in out)
