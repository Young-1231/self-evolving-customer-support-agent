"""Aggregate evaluation metrics over a list of VerdictItem."""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from .verifier import VerdictItem


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0:
        return 0.0
    prec = tp / (tp + fp)
    rec = tp / (tp + fn)
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def aggregate(verdicts: List[VerdictItem], baseline_failed_groups: Optional[Set[str]] = None) -> Dict[str, float]:
    n = len(verdicts) or 1
    resolved = sum(v.resolved for v in verdicts)
    coverage = sum(v.coverage for v in verdicts) / n
    esc_correct = sum(v.escalation_correct for v in verdicts) / n
    intervention = sum(v.pred_escalate for v in verdicts) / n

    # escalation F1 (positive class = should_escalate)
    tp = sum(1 for v in verdicts if v.pred_escalate and v.gold_escalate)
    fp = sum(1 for v in verdicts if v.pred_escalate and not v.gold_escalate)
    fn = sum(1 for v in verdicts if not v.pred_escalate and v.gold_escalate)

    metrics = {
        "resolution_rate": resolved / n,
        "keypoint_coverage": coverage,
        "escalation_accuracy": esc_correct,
        "escalation_f1": _f1(tp, fp, fn),
        "human_intervention_rate": intervention,
        "n": float(n),
    }

    # repeated-error rate: of the groups that failed at the cold-start baseline,
    # how many still fail now. The headline "learning" signal.
    if baseline_failed_groups:
        still = sum(
            1 for v in verdicts if v.group in baseline_failed_groups and not v.resolved
        )
        metrics["repeated_error_rate"] = still / len(baseline_failed_groups)
    return metrics


def failed_groups(verdicts: List[VerdictItem]) -> Set[str]:
    return {v.group for v in verdicts if not v.resolved}
