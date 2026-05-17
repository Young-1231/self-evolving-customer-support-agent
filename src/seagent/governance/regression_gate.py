"""发布门禁(regression gate)：启用候选 playbook 前后跑同一 eval 集对比指标。

业务语义：自进化产出的 playbook 哪怕看着合理，也可能在真实分布上**让整体变差**
(经典 misevolution：局部修了一个 case，全局却退化)。所以在 activate 之前必须过
门禁——把候选 playbook 启用，在固定 eval 集上重测，与启用前的 baseline 指标逐项
对比；只要任一**关键指标回退超过阈值**(SLO 回归)，门禁判 FAIL，拒绝放行。

复用 ``eval/verifier.py`` 的 verify(产出 VerdictItem) 与 ``eval/metrics.py`` 的
aggregate(聚合解决率/keypoint 覆盖/转人工 F1 等)，不重复造轮子，也不修改它们。

判定逻辑(默认门控指标与方向)：
  - resolution_rate     ↑ 越大越好，回退 = baseline - candidate
  - keypoint_coverage   ↑ 越大越好
  - escalation_f1       ↑ 越大越好(转人工 F1)
  - human_intervention_rate ↓ 越小越好，回退 = candidate - baseline(转人工率反而升高算退化)
任一门控指标的回退量 > 对应 tolerance，则 gate FAIL。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..eval.metrics import aggregate
from ..eval.verifier import VerdictItem


# 默认门控指标 -> (方向, 容忍回退量)。
#   direction="up"   指标越大越好；回退 = baseline - candidate
#   direction="down" 指标越小越好；回退 = candidate - baseline
_DEFAULT_GATES: Dict[str, Dict[str, float]] = {
    "resolution_rate": {"direction": "up", "tolerance": 0.0},
    "keypoint_coverage": {"direction": "up", "tolerance": 0.0},
    "escalation_f1": {"direction": "up", "tolerance": 0.0},
    "human_intervention_rate": {"direction": "down", "tolerance": 0.05},
}


@dataclass
class GateResult:
    """门禁判定结果。"""

    passed: bool
    deltas: Dict[str, float]        # 每个门控指标的 candidate - baseline(原始差值，便于排查)
    reason: str                     # 人类可读的判定说明(FAIL 时点名是哪个指标退了多少)
    candidate_metrics: Dict[str, float] = field(default_factory=dict)
    baseline_metrics: Dict[str, float] = field(default_factory=dict)


def _regression(metric: str, baseline: float, candidate: float, direction: str) -> float:
    """计算"回退量"(>0 表示变差)。"""
    if direction == "up":
        return baseline - candidate     # 升向指标掉了多少
    return candidate - baseline         # 降向指标涨了多少


def evaluate_playbook(
    candidate: object,
    baseline_metrics: Dict[str, float],
    eval_fn: Callable[[object], List[VerdictItem]],
    gates: Optional[Dict[str, Dict[str, float]]] = None,
    baseline_failed_groups: Optional[set] = None,
) -> GateResult:
    """对单个候选 playbook 跑发布门禁。

    Args:
        candidate: 候选 playbook(透传给 eval_fn；门禁不关心其内部结构)。
        baseline_metrics: 启用前的 aggregate 指标字典(由调用方先在同一 eval 集上
            用"未启用该 playbook"的 agent 跑出来)。
        eval_fn: 注入的评测函数，签名 ``(candidate) -> List[VerdictItem]``。由调用
            方负责"把候选 playbook 启用 → 跑 harness/agent → verify 得 verdicts"。
            门禁只消费 verdicts，从而与具体 backend/harness 解耦、便于测试。
        gates: 门控指标配置；缺省用 ``_DEFAULT_GATES``。
        baseline_failed_groups: 透传给 aggregate，用于算 repeated_error_rate。

    Returns:
        GateResult(passed, deltas, reason, ...)。
    """
    gates = gates or _DEFAULT_GATES
    verdicts = eval_fn(candidate)
    candidate_metrics = aggregate(verdicts, baseline_failed_groups)

    deltas: Dict[str, float] = {}
    failures: List[str] = []
    for metric, cfg in gates.items():
        base = float(baseline_metrics.get(metric, 0.0))
        cand = float(candidate_metrics.get(metric, 0.0))
        deltas[metric] = cand - base
        regr = _regression(metric, base, cand, cfg.get("direction", "up"))
        tol = float(cfg.get("tolerance", 0.0))
        # 用极小 epsilon 吸收浮点误差，避免边界误判
        if regr > tol + 1e-9:
            failures.append(
                f"{metric} 回退 {regr:.3f} (baseline={base:.3f} -> candidate={cand:.3f}, "
                f"容忍={tol:.3f})"
            )

    if failures:
        reason = "FAIL: 关键指标回退超阈值 -> " + "; ".join(failures)
        return GateResult(False, deltas, reason, candidate_metrics, baseline_metrics)
    reason = "PASS: 所有门控指标未回退超阈值"
    return GateResult(True, deltas, reason, candidate_metrics, baseline_metrics)


class RegressionGate:
    """门禁的可配置封装(固定一套 gates 后复用于多个候选)。"""

    def __init__(self, gates: Optional[Dict[str, Dict[str, float]]] = None):
        self.gates = gates or _DEFAULT_GATES

    def evaluate(
        self,
        candidate: object,
        baseline_metrics: Dict[str, float],
        eval_fn: Callable[[object], List[VerdictItem]],
        baseline_failed_groups: Optional[set] = None,
    ) -> GateResult:
        return evaluate_playbook(
            candidate, baseline_metrics, eval_fn, self.gates, baseline_failed_groups
        )
