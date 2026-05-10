"""Deterministic, label-based verifier.

Scores an agent answer against gold annotations the agent never sees:
  - keypoint coverage : fraction of required keypoints present in the answer
                        (whitespace-insensitive substring match)
  - escalation correct: predicted escalate == gold should_escalate

A ticket is "resolved" iff coverage >= threshold AND the escalation decision is
correct. Because verification uses gold labels (not the model judging itself),
the self-evolution metrics cannot be gamed by the agent -- see design doc §5.4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


@dataclass
class VerdictItem:
    query_id: str
    group: str
    difficulty: str
    coverage: float
    answer_correct: bool
    escalation_correct: bool
    resolved: bool
    pred_escalate: bool
    gold_escalate: bool


def keypoint_coverage(answer: str, keypoints: List[str]) -> float:
    if not keypoints:
        return 1.0
    a = _norm(answer)
    hit = sum(1 for k in keypoints if _norm(k) in a)
    return hit / len(keypoints)


def verify(query, result, coverage_threshold: float = 1.0) -> VerdictItem:
    cov = keypoint_coverage(result.answer, query.required_keypoints)
    answer_ok = cov >= coverage_threshold
    esc_ok = bool(result.escalate) == bool(query.should_escalate)
    # When a human takeover is the correct outcome, surfacing the escalation
    # keypoints + escalating == resolved. Otherwise both must hold.
    resolved = answer_ok and esc_ok
    return VerdictItem(
        query_id=query.id,
        group=query.group,
        difficulty=query.difficulty,
        coverage=cov,
        answer_correct=answer_ok,
        escalation_correct=esc_ok,
        resolved=resolved,
        pred_escalate=bool(result.escalate),
        gold_escalate=bool(query.should_escalate),
    )
