"""OpenTelemetry trace exporter (v2.6 R5)。

把 seagent trace 推到任何 OTLP-compatible 后端（Phoenix / Datadog / Jaeger /
Tempo / Honeycomb / SigNoz）。对齐 OpenTelemetry GenAI 语义约定：

  - gen_ai.system        = "deepseek"   (或 record.model 的厂商)
  - gen_ai.request.model = record.model
  - gen_ai.usage.input_tokens / output_tokens
  - gen_ai.usage.total_cost   (Anthropic / OpenLLMetry 扩展，非 GA 字段)
  - phase 名挂在 span name 上（retrieval / generation / critic / guardrail）

实现：
  - 优先 ``opentelemetry-sdk`` + OTLP HTTP exporter；
  - 缺失则自实现一个最小 OTLP/HTTP JSON encoder（POST /v1/traces）；
  - 都失败则可走 dry_run，只渲染 payload 不打网络。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..trace import read_traces


# ---------------------------------------------------------------------------
# 字段映射
# ---------------------------------------------------------------------------

def _ns_from_epoch(ts: float) -> int:
    """epoch 秒 -> OTLP 要求的纳秒整数。"""
    try:
        return int(float(ts) * 1_000_000_000)
    except Exception:
        return int(time.time() * 1_000_000_000)


def _vendor_from_model(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    return "unknown"


def trace_to_otel_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """seagent Trace dict -> OTLP/HTTP JSON payload（resourceSpans 结构）。

    返回的 dict 符合 OTLP/HTTP v1 JSON 编码（spec: opentelemetry-proto
    /collector/trace/v1/trace_service.proto 的 JSON binding）。
    """
    trace_id = record.get("trace_id") or uuid.uuid4().hex
    # OTLP 要求 16-byte hex（32 字符），seagent 的 uuid4().hex 正好满足
    if len(trace_id) != 32:
        trace_id = uuid.uuid4().hex

    ts = float(record.get("ts") or time.time())
    total_lat_ms = float(record.get("latency_ms") or 0.0)
    end_ns = _ns_from_epoch(ts + total_lat_ms / 1000.0)
    start_ns = _ns_from_epoch(ts)

    common_attrs = [
        {"key": "gen_ai.system", "value": {"stringValue": _vendor_from_model(record.get("model", ""))}},
        {"key": "gen_ai.request.model", "value": {"stringValue": record.get("model", "")}},
        {"key": "seagent.turn", "value": {"intValue": int(record.get("turn", 0) or 0)}},
        {"key": "seagent.confidence", "value": {"doubleValue": float(record.get("confidence", -1.0))}},
        {"key": "seagent.escalate", "value": {"boolValue": bool(record.get("escalate", False))}},
        {"key": "seagent.guardrail.verdict", "value": {"stringValue": record.get("guardrail_verdict", "allow")}},
        {"key": "seagent.guardrail.blocked", "value": {"boolValue": bool(record.get("guardrail_blocked", False))}},
        {"key": "seagent.n_hits", "value": {"intValue": int(record.get("n_hits", 0) or 0)}},
    ]
    if record.get("error"):
        common_attrs.append(
            {"key": "seagent.error", "value": {"stringValue": str(record["error"])}}
        )

    # 根 span：agent_turn
    root_span_id = uuid.uuid4().hex[:16]
    root_span = {
        "traceId": trace_id,
        "spanId": root_span_id,
        "name": "agent_turn",
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": list(common_attrs) + [
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": int(record.get("in_tokens", 0) or 0)}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": int(record.get("out_tokens", 0) or 0)}},
            {"key": "gen_ai.usage.total_cost", "value": {"doubleValue": float(record.get("cost_usd", 0.0) or 0.0)}},
            {"key": "seagent.query", "value": {"stringValue": record.get("query", "")[:500]}},
        ],
        "status": {"code": 2 if record.get("error") else 1},  # ERROR / OK
    }

    spans: List[Dict[str, Any]] = [root_span]

    # phase 子 span
    phase_ms: Dict[str, float] = record.get("phase_ms") or {}
    cursor = float(ts)
    for phase, dur_ms in phase_ms.items():
        dur_s = float(dur_ms or 0.0) / 1000.0
        child_attrs = list(common_attrs) + [
            {"key": "seagent.phase", "value": {"stringValue": phase}},
            {"key": "seagent.phase.latency_ms", "value": {"doubleValue": float(dur_ms or 0.0)}},
        ]
        if phase == "retrieval":
            hits = record.get("hits") or []
            child_attrs.append(
                {"key": "seagent.retrieval.hits", "value": {"stringValue": json.dumps(hits)}}
            )
        spans.append(
            {
                "traceId": trace_id,
                "spanId": uuid.uuid4().hex[:16],
                "parentSpanId": root_span_id,
                "name": phase,
                "kind": 1,
                "startTimeUnixNano": str(_ns_from_epoch(cursor)),
                "endTimeUnixNano": str(_ns_from_epoch(cursor + dur_s)),
                "attributes": child_attrs,
                "status": {"code": 1},
            }
        )
        cursor += dur_s

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "seagent"}},
                        {"key": "service.version", "value": {"stringValue": "v2.6"}},
                        {"key": "telemetry.sdk.name", "value": {"stringValue": "seagent-otel-exporter"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "seagent.obs", "version": "v2.6"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class OtelExporter:
    """OTLP/HTTP trace exporter。

    参数：
        endpoint: OTLP/HTTP base URL（不含 /v1/traces）。默认本地 collector。
        headers:  额外 HTTP 头（如 Datadog / Honeycomb 的 API token）
        use_sdk:  True=优先 opentelemetry-sdk；缺失自动 fallback 到 HTTP
        http_client: 注入 mock
        dry_run:  只返回 payload 不打网络
    """

    TRACES_PATH = "/v1/traces"

    def __init__(
        self,
        endpoint: str = "http://localhost:4318",
        headers: Optional[Dict[str, str]] = None,
        use_sdk: bool = True,
        http_client: Any = None,
        dry_run: bool = False,
    ):
        self.endpoint = (endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")).rstrip("/")
        self.headers = dict(headers or {})
        self.dry_run = bool(dry_run)
        self._http_client = http_client
        self._sdk_provider = None
        if use_sdk and not dry_run:
            try:
                from opentelemetry import trace as _otrace  # type: ignore
                from opentelemetry.sdk.trace import TracerProvider  # type: ignore
                from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                    OTLPSpanExporter,
                )

                provider = TracerProvider()
                provider.add_span_processor(
                    BatchSpanProcessor(
                        OTLPSpanExporter(endpoint=self.endpoint + self.TRACES_PATH, headers=self.headers)
                    )
                )
                self._sdk_provider = provider
                self._sdk_tracer = provider.get_tracer("seagent.obs")
            except Exception:
                self._sdk_provider = None

    @property
    def backend(self) -> str:
        if self.dry_run:
            return "dry_run"
        return "sdk" if self._sdk_provider is not None else "http"

    # ------------------------------------------------------------------
    def export_trace(self, record: Dict[str, Any]) -> Dict[str, Any]:
        payload = trace_to_otel_payload(record)
        if self.dry_run:
            return payload
        if self._sdk_provider is not None:
            # SDK 路径：用 OTel SDK 直接创建 span（不重新走 payload 路径，避免双倍）
            return self._sdk_emit(record)
        return self._http_post(payload)

    def export_traces_from_jsonl(
        self, path: str, on_error: str = "skip"
    ) -> Tuple[int, int]:
        records = read_traces(path)
        return self.export_traces(records, on_error=on_error)

    def export_traces(
        self, records: Iterable[Dict[str, Any]], on_error: str = "skip"
    ) -> Tuple[int, int]:
        ok, fail = 0, 0
        for r in records:
            try:
                self.export_trace(r)
                ok += 1
            except Exception:
                fail += 1
                if on_error == "raise":
                    raise
        return ok, fail

    # ------------------------------------------------------------------
    def _sdk_emit(self, record: Dict[str, Any]) -> Dict[str, Any]:
        # 简化：只创建根 span + 各 phase 子 span，依赖 SDK 自己批处理/编码
        with self._sdk_tracer.start_as_current_span("agent_turn") as root:
            root.set_attribute("gen_ai.request.model", record.get("model", ""))
            root.set_attribute("gen_ai.usage.input_tokens", int(record.get("in_tokens", 0) or 0))
            root.set_attribute("gen_ai.usage.output_tokens", int(record.get("out_tokens", 0) or 0))
            root.set_attribute("seagent.confidence", float(record.get("confidence", -1.0)))
            for phase, dur in (record.get("phase_ms") or {}).items():
                with self._sdk_tracer.start_as_current_span(phase) as ch:
                    ch.set_attribute("seagent.phase.latency_ms", float(dur or 0.0))
        return {"status": "ok", "via": "sdk"}

    def _http_post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.endpoint + self.TRACES_PATH
        headers = {"Content-Type": "application/json", "User-Agent": "seagent-otel-exporter/v2.6"}
        headers.update(self.headers)
        body = json.dumps(payload).encode("utf-8")

        if self._http_client is not None:
            resp = self._http_client.post(url, data=body, headers=headers)
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                raise RuntimeError(f"otel http {status}: {getattr(resp, 'text', '')!r}")
            return {"status": "ok", "via": "http", "code": status}

        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.getcode()
                if code >= 400:
                    raise RuntimeError(f"otel http {code}")
                return {"status": "ok", "via": "http", "code": code}
        except urllib.error.URLError as e:
            raise RuntimeError(f"otel http error: {e}") from e
