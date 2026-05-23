"""规模化压测工具包(LLM 生成工单 + 并发压测 + 记忆膨胀)。

该子包**只新增、不修改**任何现有 seagent 源码。设计目标：

  1. 用真实 LLM 在零模板束缚下批量生成贴近真实业务分布的客服工单
     (含 PII / prompt-injection / 多意图 / 中英混合等"硬骨头")；
  2. 用 stdlib threading 把工单并发跑过 production-path SupportAgent，
     落 trace + 聚合 latency/cost/escalation/guardrail；
  3. 用合成方式把 EpisodicMemory 膨胀到不同规模(10/100/1k/5k)，测检
     索延迟与解决率的拐点 — 给"TTL/淘汰策略何时触发"提供工程依据。

全模块零强制第三方依赖：openai SDK 与 matplotlib 走 guarded import；
LLM 不可用时落到 deterministic stub generator，方便 CI 单元测试。
"""
from __future__ import annotations

from .generator import (
    DEFAULT_DISTRIBUTION,
    TicketSpec,
    generate_tickets,
    load_tickets,
    sample_categories,
)
from .load_runner import LoadRecord, run_load, summarize_load
from .memory_scaling import MemoryScalePoint, scale_memory

__all__ = [
    "DEFAULT_DISTRIBUTION",
    "TicketSpec",
    "generate_tickets",
    "load_tickets",
    "sample_categories",
    "LoadRecord",
    "run_load",
    "summarize_load",
    "MemoryScalePoint",
    "scale_memory",
]
