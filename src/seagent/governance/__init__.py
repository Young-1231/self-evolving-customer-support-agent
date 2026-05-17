"""记忆与 Playbook 治理(governance)模块。

把"自进化"做成真实业务里**安全可控的发布流程**：自进化产出的 playbook 不是
黑盒权重更新，而是一份可审计、可灰度、可回滚的**受治理资产**。本模块提供三块能力：

  - ``lifecycle``        : playbook 发布生命周期状态机(proposed→approved→canary
                           →active→rolled_back/deprecated) + 审计日志；
  - ``regression_gate``  : 发布门禁，启用前后跑同一 eval 集对比关键指标，回退超阈
                           值则拒绝 activate(SLO 回归即拦截)；
  - ``memory_hygiene``   : 经验池治理(dedup / TTL 遗忘 / 冲突消解 / 入库 PII 脱敏)。

对齐 2026 实际做法：Mem0 / Letta / Zep / MemArchitect(arXiv 2603.18330) 的记忆
TTL/遗忘/冲突消解/PII 治理；以及对 misevolution(arXiv 2509.26354) 的防护——
任何自生成的行为变更都要经"提案→人审→灰度→可回滚"才能上线。
"""
from __future__ import annotations

from .lifecycle import (
    LifecycleState,
    PlaybookRecord,
    PlaybookRegistry,
)
from .regression_gate import GateResult, RegressionGate, evaluate_playbook
from .memory_hygiene import (
    ConflictPair,
    dedup,
    detect_conflicts,
    scrub_case,
    ttl_filter,
)

__all__ = [
    "LifecycleState",
    "PlaybookRecord",
    "PlaybookRegistry",
    "GateResult",
    "RegressionGate",
    "evaluate_playbook",
    "ConflictPair",
    "dedup",
    "detect_conflicts",
    "scrub_case",
    "ttl_filter",
]
