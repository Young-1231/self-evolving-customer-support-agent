"""v2.6 R5 — Trace exporters: 把 seagent.obs.Tracer 写的 JSONL trace 推送到
通用可视化后端（Langfuse / OTLP-compatible 后端）。

设计原则：
  - **零硬依赖**：langfuse / opentelemetry-sdk 都是 try-import；缺失则走自实现
    HTTP fallback。pip install seagent 默认仍 stdlib-only。
  - **不动现有 obs 模块**：本目录是纯增量，trace.py / dashboard.py / cost.py /
    metrics.py 不变。
  - **错误隔离**：批量 export 时单条失败不阻断其他 trace。

入口：
  - :class:`LangfuseExporter` — 推到 Langfuse self-host 或 cloud。
  - :class:`OtelExporter`     — 推到任何 OTLP-compatible 后端
    （Phoenix / Datadog / Jaeger / Tempo）。

字段映射详见 ``docs/langfuse_integration.md``。
"""
from .langfuse import LangfuseExporter, trace_to_langfuse_payload
from .otel import OtelExporter, trace_to_otel_payload

__all__ = [
    "LangfuseExporter",
    "OtelExporter",
    "trace_to_langfuse_payload",
    "trace_to_otel_payload",
]
