"""零依赖 demo 入口：用一个内置的桩 agent 装配 FastAPI app。

仅供 `run_server.sh` 快速起服务、看端点是否通。**不接任何真实模型**。
生产里你会写一个自己的装配模块，按 README 的注入点把真实 SupportAgent 传给 create_app。

注意：本模块在 import 时不触发 FastAPI(create_app 内部才检查)，
因此即使未装 FastAPI 也能被 import；只有访问 `app` 时才会要求依赖就位。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .app import create_app


@dataclass
class _StubResult:
    """鸭子类型匹配 agent.support_agent.AgentResult 的服务层读取字段。"""

    answer: str
    escalate: bool = False
    confidence: float = 0.7
    used_sources: List[str] = field(default_factory=lambda: ["kb"])


class _StubAgent:
    """回声式桩 agent：低置信度长问题升级，仅用于本地连通性验证。"""

    def handle(self, query: str):
        escalate = len(query) > 200  # 极简策略：超长问题转人工
        conf = 0.4 if escalate else 0.75
        return _StubResult(
            answer=f"[demo] received: {query[:120]}",
            escalate=escalate,
            confidence=conf,
        )


# uvicorn 通过 "seagent.serving._demo_app:app" 取这个对象
app = create_app(_StubAgent())
