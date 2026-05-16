"""生产级可观测性(observability)模块。

把自进化客服 Agent 当真实业务系统来观测：一次对话/一次 LLM 调用记 trace/span、
token、cost、latency、检索命中来源、置信度、guardrail 结果、是否转人工，并能聚合
出运营指标。对齐 2026 实践：OpenTelemetry GenAI 语义约定 + Langfuse / Arize
Phoenix / OpenLLMetry 的 trace→聚合→看板范式。零第三方依赖即可跑通。
"""
from __future__ import annotations

from .trace import RetrievalHit, Span, Trace, Tracer, read_traces
from .cost import estimate_cost, estimate_tokens, lookup_price, PRICING_USD_PER_1K
from .metrics import aggregate, summary_table
from .dashboard import render_ops_report

__all__ = [
    "Tracer",
    "Trace",
    "Span",
    "RetrievalHit",
    "read_traces",
    "estimate_tokens",
    "estimate_cost",
    "lookup_price",
    "PRICING_USD_PER_1K",
    "aggregate",
    "summary_table",
    "render_ops_report",
]
