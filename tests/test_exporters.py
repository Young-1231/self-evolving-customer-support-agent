"""v2.6 R5 — tests for seagent.obs.exporters (Langfuse / OTLP)。

约束：完全 mock HTTP；不引入 langfuse / opentelemetry 真实依赖；不修改
现有 obs 模块。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Dict, List

import pytest

from seagent.obs.exporters import (
    LangfuseExporter,
    OtelExporter,
    trace_to_langfuse_payload,
    trace_to_otel_payload,
)


# ---------------------------------------------------------------------------
# 测试夹具：一条接近真实 exp_d trace 的记录
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_trace() -> Dict[str, Any]:
    return {
        "trace_id": "dcace0f994b74d159c81fd31c0573945",
        "turn": 1,
        "ts": 1779964134.764096,
        "latency_ms": 4192.08,
        "phase_ms": {
            "retrieval": 25.632,
            "generation": 38834.392,
            "critic": 5001.86,
            "guardrail": 943.441,
        },
        "hits": [
            {"source": "kb", "ref": "bx_set_up_shipping_address_03", "score": 0.0561},
            {"source": "kb", "ref": "bx_complaint_03", "score": 0.0561},
        ],
        "n_hits": 2,
        "confidence": 1.0,
        "escalate": False,
        "guardrail_verdict": "allow",
        "guardrail_blocked": False,
        "model": "deepseek-chat",
        "in_tokens": 453,
        "out_tokens": 108,
        "cost_usd": 0.00024111,
        "query": "I placed an order...",
        "error": None,
    }


@pytest.fixture
def sample_jsonl(tmp_path, sample_trace) -> str:
    p = tmp_path / "traces.jsonl"
    rows = [sample_trace, {**sample_trace, "trace_id": "b" * 32, "turn": 2}]
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return str(p)


# ---------------------------------------------------------------------------
# Mock HTTP client（duck-typed requests-like）
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status: int = 200, text: str = "OK"):
        self.status_code = status
        self.text = text


class MockHTTP:
    """记录所有 POST 调用，按 fail_codes 注入失败。"""

    def __init__(self, fail_indices: List[int] = None, fail_status: int = 500):
        self.calls: List[Dict[str, Any]] = []
        self.fail_indices = set(fail_indices or [])
        self.fail_status = fail_status

    def post(self, url: str, data=None, headers=None, **kw):
        idx = len(self.calls)
        self.calls.append({"url": url, "data": data, "headers": headers or {}})
        if idx in self.fail_indices:
            return _Resp(self.fail_status, "boom")
        return _Resp(200, "OK")


# ===========================================================================
# Langfuse — 字段映射
# ===========================================================================

class TestLangfusePayload:
    def test_payload_has_trace_create(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        types = [item["type"] for item in p["batch"]]
        assert types[0] == "trace-create"
        # 4 phases -> 4 child observations
        assert types.count("generation-create") == 1  # only generation phase
        # retrieval / critic / guardrail = 3 spans
        assert types.count("span-create") == 3

    def test_trace_id_preserved(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        trace_body = p["batch"][0]["body"]
        assert trace_body["id"] == sample_trace["trace_id"]

    def test_metadata_round_trip(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        meta = p["batch"][0]["body"]["metadata"]
        assert meta["turn"] == 1
        assert meta["confidence"] == 1.0
        assert meta["guardrail_verdict"] == "allow"
        assert meta["n_hits"] == 2

    def test_generation_usage_mapping(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        gen = next(it for it in p["batch"] if it["type"] == "generation-create")
        assert gen["body"]["model"] == "deepseek-chat"
        assert gen["body"]["usage"]["input"] == 453
        assert gen["body"]["usage"]["output"] == 108
        assert gen["body"]["usage"]["total"] == 561
        assert gen["body"]["usage"]["totalCost"] == pytest.approx(0.00024111)

    def test_retrieval_hits_preserved(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        retr = next(
            it for it in p["batch"]
            if it["type"] == "span-create" and it["body"]["name"] == "retrieval"
        )
        assert len(retr["body"]["output"]) == 2
        assert retr["body"]["output"][0]["ref"] == "bx_set_up_shipping_address_03"
        assert retr["body"]["metadata"]["n_hits"] == 2

    def test_guardrail_verdict_attached(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        g = next(
            it for it in p["batch"]
            if it["type"] == "span-create" and it["body"]["name"] == "guardrail"
        )
        assert g["body"]["metadata"]["verdict"] == "allow"
        assert g["body"]["metadata"]["blocked"] is False

    def test_error_trace_tagged(self, sample_trace):
        bad = {**sample_trace, "error": "RetrievalTimeout"}
        p = trace_to_langfuse_payload(bad)
        tags = p["batch"][0]["body"]["tags"]
        assert "error" in tags
        assert p["batch"][0]["body"]["metadata"]["error"] == "RetrievalTimeout"

    def test_timestamp_is_iso8601(self, sample_trace):
        p = trace_to_langfuse_payload(sample_trace)
        ts = p["batch"][0]["body"]["timestamp"]
        # 形如 2026-XX-XXTHH:MM:SS.mmmZ
        assert ts.endswith("Z")
        assert "T" in ts


# ===========================================================================
# Langfuse — Exporter
# ===========================================================================

class TestLangfuseExporter:
    def test_dry_run_returns_payload(self, sample_trace):
        exp = LangfuseExporter(public_key="pk", secret_key="sk", dry_run=True)
        assert exp.backend == "dry_run"
        out = exp.export_trace(sample_trace)
        assert "batch" in out

    def test_http_fallback_when_no_sdk(self, sample_trace, monkeypatch):
        # 强制 SDK 不可用
        monkeypatch.setitem(sys.modules, "langfuse", None)
        mock = MockHTTP()
        exp = LangfuseExporter(
            public_key="pk", secret_key="sk", host="http://lf.local",
            http_client=mock,
        )
        assert exp.backend == "http"
        r = exp.export_trace(sample_trace)
        assert r["status"] == "ok"
        assert len(mock.calls) == 1
        assert mock.calls[0]["url"] == "http://lf.local/api/public/ingestion"
        # Basic auth header 存在
        assert mock.calls[0]["headers"]["Authorization"].startswith("Basic ")

    def test_batch_error_isolation(self, sample_jsonl, monkeypatch):
        monkeypatch.setitem(sys.modules, "langfuse", None)
        mock = MockHTTP(fail_indices=[0])  # 第 1 条失败
        exp = LangfuseExporter(
            public_key="pk", secret_key="sk",
            host="http://lf.local", http_client=mock,
        )
        ok, fail = exp.export_traces_from_jsonl(sample_jsonl)
        assert ok == 1
        assert fail == 1
        # 没有 raise，第二条继续走
        assert len(mock.calls) == 2

    def test_on_error_raise(self, sample_jsonl, monkeypatch):
        monkeypatch.setitem(sys.modules, "langfuse", None)
        mock = MockHTTP(fail_indices=[0])
        exp = LangfuseExporter(
            public_key="pk", secret_key="sk",
            host="http://lf.local", http_client=mock,
        )
        with pytest.raises(RuntimeError):
            exp.export_traces_from_jsonl(sample_jsonl, on_error="raise")

    def test_sdk_path_when_available(self, sample_trace):
        """模拟 langfuse SDK 可用：注入 fake module。"""

        class FakeLangfuse:
            def __init__(self, **kw):
                self.kw = kw
                self.traces = []
                self.gens = []
                self.spans = []

            def trace(self, **kw): self.traces.append(kw)
            def generation(self, **kw): self.gens.append(kw)
            def span(self, **kw): self.spans.append(kw)

        fake_mod = type(sys)("langfuse")
        fake_mod.Langfuse = FakeLangfuse
        sys.modules["langfuse"] = fake_mod
        try:
            exp = LangfuseExporter(public_key="pk", secret_key="sk")
            assert exp.backend == "sdk"
            r = exp.export_trace(sample_trace)
            assert r["status"] == "ok"
            assert r["via"] == "sdk"
            sdk: FakeLangfuse = exp._sdk
            assert len(sdk.traces) == 1
            assert len(sdk.gens) == 1     # 一条 generation
            assert len(sdk.spans) == 3    # retrieval / critic / guardrail
        finally:
            sys.modules.pop("langfuse", None)


# ===========================================================================
# OTel — 字段映射
# ===========================================================================

class TestOtelPayload:
    def test_resource_spans_structure(self, sample_trace):
        p = trace_to_otel_payload(sample_trace)
        assert "resourceSpans" in p
        rs = p["resourceSpans"][0]
        attrs = {a["key"]: a["value"] for a in rs["resource"]["attributes"]}
        assert attrs["service.name"]["stringValue"] == "seagent"

    def test_root_and_child_spans(self, sample_trace):
        p = trace_to_otel_payload(sample_trace)
        spans = p["resourceSpans"][0]["scopeSpans"][0]["spans"]
        # 1 root + 4 phases
        assert len(spans) == 5
        names = [s["name"] for s in spans]
        assert names[0] == "agent_turn"
        assert set(names[1:]) == {"retrieval", "generation", "critic", "guardrail"}

    def test_parent_links(self, sample_trace):
        p = trace_to_otel_payload(sample_trace)
        spans = p["resourceSpans"][0]["scopeSpans"][0]["spans"]
        root = spans[0]
        for child in spans[1:]:
            assert child["parentSpanId"] == root["spanId"]
            assert child["traceId"] == root["traceId"]

    def test_genai_semantic_attrs(self, sample_trace):
        p = trace_to_otel_payload(sample_trace)
        root = p["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        attrs = {a["key"]: a["value"] for a in root["attributes"]}
        assert attrs["gen_ai.system"]["stringValue"] == "deepseek"
        assert attrs["gen_ai.request.model"]["stringValue"] == "deepseek-chat"
        assert attrs["gen_ai.usage.input_tokens"]["intValue"] == 453
        assert attrs["gen_ai.usage.output_tokens"]["intValue"] == 108

    def test_error_sets_error_status(self, sample_trace):
        bad = {**sample_trace, "error": "Timeout"}
        p = trace_to_otel_payload(bad)
        root = p["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert root["status"]["code"] == 2

    def test_trace_id_format_is_32_hex(self, sample_trace):
        p = trace_to_otel_payload({**sample_trace, "trace_id": "too-short"})
        spans = p["resourceSpans"][0]["scopeSpans"][0]["spans"]
        assert len(spans[0]["traceId"]) == 32


# ===========================================================================
# OTel — Exporter
# ===========================================================================

class TestOtelExporter:
    def test_dry_run(self, sample_trace):
        exp = OtelExporter(endpoint="http://otel.local", dry_run=True)
        assert exp.backend == "dry_run"
        out = exp.export_trace(sample_trace)
        assert "resourceSpans" in out

    def test_http_fallback(self, sample_trace, monkeypatch):
        # 模拟 opentelemetry 不可用
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        mock = MockHTTP()
        exp = OtelExporter(
            endpoint="http://otel.local",
            http_client=mock,
            use_sdk=True,
        )
        assert exp.backend == "http"
        r = exp.export_trace(sample_trace)
        assert r["status"] == "ok"
        assert mock.calls[0]["url"] == "http://otel.local/v1/traces"

    def test_batch_error_isolation(self, sample_jsonl, monkeypatch):
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        mock = MockHTTP(fail_indices=[1])
        exp = OtelExporter(
            endpoint="http://otel.local",
            http_client=mock,
        )
        ok, fail = exp.export_traces_from_jsonl(sample_jsonl)
        assert ok == 1
        assert fail == 1

    def test_headers_propagated(self, sample_trace, monkeypatch):
        monkeypatch.setitem(sys.modules, "opentelemetry", None)
        mock = MockHTTP()
        exp = OtelExporter(
            endpoint="http://otel.local",
            headers={"X-Honeycomb-Team": "tok"},
            http_client=mock,
        )
        exp.export_trace(sample_trace)
        assert mock.calls[0]["headers"]["X-Honeycomb-Team"] == "tok"


# ===========================================================================
# 集成：从真实 JSONL 文件批量导出
# ===========================================================================

class TestBatchFromJsonl:
    def test_dry_run_batch(self, sample_jsonl):
        exp = LangfuseExporter(public_key="pk", secret_key="sk", dry_run=True)
        ok, fail = exp.export_traces_from_jsonl(sample_jsonl)
        assert ok == 2
        assert fail == 0

    def test_skips_missing_file(self, tmp_path):
        exp = LangfuseExporter(public_key="pk", secret_key="sk", dry_run=True)
        ok, fail = exp.export_traces_from_jsonl(str(tmp_path / "nope.jsonl"))
        assert ok == 0
        assert fail == 0
