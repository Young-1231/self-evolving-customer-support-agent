"""生产级 guardrails 安全模块。

把一个自进化客服 Agent 当成真实业务系统来加固：入站做提示注入/越狱检测与
PII 识别，出站做 groundedness(回答必须有检索证据支撑)、合规策略与 PII 脱敏。

设计原则：
  - 默认零第三方依赖，纯 Python/正则即可跑通(CI、离线 mock backend 都依赖这点)；
  - 重型能力(Microsoft Presidio、LLM-judge)做可选增强(try-import / 注入回调)，
    装了就用，没装则回退确定性实现；
  - 数据结构与 ``seagent.llm.base.Passage`` / ``AgentResult`` 风格对齐。
"""
from __future__ import annotations

from .injection import InjectionResult, detect_injection
from .groundedness import GroundednessResult, check_groundedness
from .pii import PiiSpan, redact_pii
from .pipeline import GuardrailPipeline, GuardrailReport
from .policy import Violation, check_output_policy

__all__ = [
    "redact_pii",
    "PiiSpan",
    "check_groundedness",
    "GroundednessResult",
    "detect_injection",
    "InjectionResult",
    "check_output_policy",
    "Violation",
    "GuardrailPipeline",
    "GuardrailReport",
]
