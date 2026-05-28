"""Three-signal escalation voter (P1, c18 真瓶颈修复).

§4h 通过 Exp C 证伪了 "critic 阈值 + PII 精度是瓶颈" 假设，定位真瓶颈在
guardrail 的 groundedness check：当前 OR 逻辑下任一信号 escalate 即整体
escalate，导致 stiff template 回答的 groundedness false-fail 完全主导决策。

本模块把"OR 任一→escalate"升级为可配置 vote：

  - ``any``         : 任一信号 -> escalate (旧行为, 默认, 向后兼容)
  - ``majority``    : ≥2 信号 -> escalate
  - ``weighted``    : 加权和 ≥ threshold -> escalate (critic 0.4 / ground 0.4 / policy 0.2)
  - ``unanimous``   : 全部 3 信号 -> escalate (最激进, 几乎不转人工)

block (硬拦) 始终走 OR (任一硬拦即拦), 不参与 vote。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

VALID_MODES = ("any", "majority", "weighted", "unanimous")

DEFAULT_WEIGHTS: Dict[str, float] = {
    "critic": 0.4,
    "groundedness": 0.4,
    "policy": 0.2,
}


@dataclass
class VoteResult:
    """voter 决策结果, 便于 trace / 调试."""

    escalate: bool
    mode: str
    signals: Dict[str, bool] = field(default_factory=dict)
    weighted_sum: Optional[float] = None
    threshold: Optional[float] = None
    reason: str = ""


@dataclass
class EscalationVoter:
    """聚合 (critic, groundedness, policy) 三信号 -> 单一 escalate 决策.

    Parameters
    ----------
    mode:
        投票模式; 默认 'any' 即旧 OR 行为, 保证现有 agent 不回退.
    weights:
        ``mode='weighted'`` 用; 默认 ``DEFAULT_WEIGHTS``.
    threshold:
        ``mode='weighted'`` 用; 加权和 >= threshold 触发 escalate. 默认 0.5.
    """

    mode: str = "any"
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    threshold: float = 0.5

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            # 不抛 — 保护性降级到 'any', 避免配置 typo 把 agent 拖崩
            self.mode = "any"

    def vote(self, critic: bool, groundedness: bool, policy: bool) -> VoteResult:
        signals = {"critic": bool(critic), "groundedness": bool(groundedness), "policy": bool(policy)}
        n = sum(signals.values())

        if self.mode == "any":
            return VoteResult(escalate=n > 0, mode="any", signals=signals,
                              reason=f"any-OR: {n}/3 signals fired")

        if self.mode == "majority":
            return VoteResult(escalate=n >= 2, mode="majority", signals=signals,
                              reason=f"majority: {n}/3 fired (need ≥2)")

        if self.mode == "unanimous":
            return VoteResult(escalate=n == 3, mode="unanimous", signals=signals,
                              reason=f"unanimous: {n}/3 fired (need 3)")

        # weighted
        ws = self.weights
        total = (ws.get("critic", 0) * int(signals["critic"])
                 + ws.get("groundedness", 0) * int(signals["groundedness"])
                 + ws.get("policy", 0) * int(signals["policy"]))
        return VoteResult(
            escalate=total >= self.threshold,
            mode="weighted",
            signals=signals,
            weighted_sum=round(total, 4),
            threshold=self.threshold,
            reason=f"weighted sum {total:.2f} {'≥' if total >= self.threshold else '<'} {self.threshold}",
        )
