"""Guardrail 编排管线：把四个 guard 组合成入站/出站两道闸。

业务用途：给 ``SupportAgent`` 提供一个统一入口——

  - ``check_input(user_text)``  : 在 handle() 最开始调用。检测提示注入/越狱,
    并识别(可脱敏)用户 PII。命中强攻击 -> 直接拦截; 仅含 PII -> 放行但回传
    脱敏文本供入库/日志使用。
  - ``check_output(answer, contexts)`` : 在生成 answer 之后、返回之前调用。
    跑 groundedness(证据支撑) + 合规策略 + 出站 PII 脱敏, 给出最终动作:
      allow(放行) / rewrite(改写, 一般指降级为保守话术或追加免责) /
      escalate(转人工) / block(拦截)。

借鉴：NeMo Guardrails 的 input rails / output rails 分层编排, 以及 guardrails-ai
"校验失败后 fix/reask/escalate" 的动作语义。整条管线默认零依赖、确定性。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..llm.base import Passage
from .groundedness import GroundednessResult, check_groundedness
from .injection import InjectionResult, detect_injection
from .pii import PiiSpan, redact_pii
from .policy import Violation, check_output_policy

# 最终决策动作
ALLOW = "allow"
REWRITE = "rewrite"
ESCALATE = "escalate"
BLOCK = "block"


@dataclass
class GuardrailReport:
    stage: str                  # "input" | "output"
    passed: bool                # 是否无需任何干预即可放行
    action: str                 # ALLOW | REWRITE | ESCALATE | BLOCK
    blocked: bool = False       # 是否硬拦截(action==BLOCK)
    reasons: List[str] = field(default_factory=list)  # 触发原因(可读)
    redacted_text: str = ""     # 入站: 脱敏后的用户文本
    redacted_answer: str = ""   # 出站: 脱敏后的回答
    pii_spans: List[PiiSpan] = field(default_factory=list)
    injection: Optional[InjectionResult] = None
    groundedness: Optional[GroundednessResult] = None
    violations: List[Violation] = field(default_factory=list)


@dataclass
class GuardrailPipeline:
    """可配置的 guardrail 管线。阈值默认对齐各 guard 的默认值。"""

    # 入站: 注入风险分达到该值即硬拦截(低于则仅告警放行)
    injection_block_score: float = 0.34   # 约等于"命中 >=1 条强规则"
    # 出站 groundedness 阈值
    ground_tau: float = 0.6
    ground_min_supported: float = 0.8
    # 出站合规
    refund_cap: float = 1000.0
    # 出站是否对回答做 PII 脱敏
    redact_output_pii: bool = True
    use_presidio: bool = True
    # PII 精度档位: 'strict'(默认, 保持历史行为, 召回优先) | 'balanced' | 'relaxed'.
    # 入站(用户消息)与出站(模型回答)可独立配置; 出站若为 None 则跟随入站。
    pii_precision_mode: str = "strict"
    pii_precision_mode_output: Optional[str] = None

    # ---------------- 入站 ----------------
    def check_input(self, user_text: str) -> GuardrailReport:
        """入站闸: 注入/越狱检测 + 用户 PII 识别与脱敏。"""
        inj = detect_injection(user_text)
        redacted, spans = redact_pii(
            user_text, use_presidio=self.use_presidio,
            precision_mode=self.pii_precision_mode,
        )

        reasons: List[str] = []
        action = ALLOW
        if inj.flagged:
            reasons.append(f"prompt_injection: {', '.join(inj.patterns)}")
            # 强攻击直接拦截, 弱可疑放行但已记录
            if inj.score >= self.injection_block_score:
                action = BLOCK
        if spans:
            reasons.append(f"pii_detected: {sorted({s.entity for s in spans})}")

        blocked = action == BLOCK
        return GuardrailReport(
            stage="input",
            passed=not reasons,
            action=action,
            blocked=blocked,
            reasons=reasons,
            redacted_text=redacted,
            pii_spans=spans,
            injection=inj,
        )

    # ---------------- 出站 ----------------
    def check_output(self, answer: str, contexts: List[Passage]) -> GuardrailReport:
        """出站闸: groundedness + 合规策略 + PII 脱敏, 并给出最终动作。"""
        ground = check_groundedness(
            answer, contexts, tau=self.ground_tau,
            min_supported_ratio=self.ground_min_supported,
        )
        violations = check_output_policy(answer, refund_cap=self.refund_cap)

        redacted_answer = answer
        out_spans: List[PiiSpan] = []
        if self.redact_output_pii:
            out_mode = self.pii_precision_mode_output or self.pii_precision_mode
            redacted_answer, out_spans = redact_pii(
                answer, use_presidio=self.use_presidio,
                precision_mode=out_mode,
            )

        reasons: List[str] = []
        action = ALLOW

        # 1) 合规硬红线: 任一 block 级违规 -> 拦截
        if any(v.severity == "block" for v in violations):
            action = BLOCK
            reasons.append("policy_block: " + ", ".join(
                v.rule for v in violations if v.severity == "block"))
        # 2) 需要改写的合规问题(如法律保证) -> rewrite
        elif any(v.severity == "rewrite" for v in violations):
            action = REWRITE
            reasons.append("policy_rewrite: " + ", ".join(
                v.rule for v in violations if v.severity == "rewrite"))

        # 3) groundedness 不足 -> 潜在幻觉, 转人工(优先级低于硬红线)
        if not ground.supported and action != BLOCK:
            action = ESCALATE if action == ALLOW else action
            reasons.append(
                f"low_groundedness: score={ground.score:.2f}, "
                f"unsupported={len(ground.unsupported_claims)}")

        if out_spans:
            reasons.append(f"output_pii_redacted: {sorted({s.entity for s in out_spans})}")

        blocked = action == BLOCK
        passed = action == ALLOW and not out_spans
        return GuardrailReport(
            stage="output",
            passed=passed,
            action=action,
            blocked=blocked,
            reasons=reasons,
            redacted_answer=redacted_answer,
            pii_spans=out_spans,
            groundedness=ground,
            violations=violations,
        )
