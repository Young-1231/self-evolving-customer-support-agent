"""Built-in hooks — thin adapters over existing c21 modules.

These ship as runnable examples of the hook API and as a regression-safe path
for customers who want Exp D behaviour without hand-wiring the pieces.

  * :func:`llm_judge_groundedness_hook` — POST_OUTPUT_GUARD: rewrite the
    guardrail report's groundedness sub-result using
    :class:`~seagent.guardrails.groundedness_llm.LLMJudgeGroundedness`.
  * :func:`escalation_vote_hook` — ON_ESCALATE: convert the OR-of-signals
    escalation decision into majority / weighted / unanimous via
    :class:`~seagent.agent.escalation_voting.EscalationVoter`.
  * :func:`audit_log_hook` — POST_OUTPUT_GUARD: append a one-line JSON record
    per turn to a JSONL audit file.

All three are *factories* — they take their config at registration time and
return a closure conforming to ``Callable[[HookContext], Optional[HookResult]]``.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from .types import HookContext, HookResult


# ----------------------- 1) LLM-judge groundedness -----------------------
def make_llm_judge_groundedness_hook(
    judge: Optional[object] = None,
    **judge_kwargs,
) -> Callable[[HookContext], Optional[HookResult]]:
    """Build a hook that re-evaluates groundedness with an LLM judge.

    Parameters
    ----------
    judge:
        An object exposing ``.check(answer, contexts) -> GroundednessResult``.
        Pass ``None`` to lazy-construct
        :class:`~seagent.guardrails.groundedness_llm.LLMJudgeGroundedness`
        with ``judge_kwargs`` on first call.

    The hook only fires when ``ctx.guardrail_report`` already exists (i.e. the
    output pipeline did run); otherwise it no-ops.  It rewrites the
    ``groundedness`` field in place and flips ``escalate`` to match.
    """
    _state = {"judge": judge}

    def _hook(ctx: HookContext) -> Optional[HookResult]:
        rpt = ctx.guardrail_report
        if rpt is None or not ctx.answer or not ctx.contexts:
            return None
        if _state["judge"] is None:
            from ..guardrails.groundedness_llm import LLMJudgeGroundedness
            _state["judge"] = LLMJudgeGroundedness(**judge_kwargs)
        j = _state["judge"]
        try:
            new_ground = j.check(ctx.answer, ctx.contexts)
        except Exception as e:  # pragma: no cover — soft fail
            return HookResult(add_reason=f"llm_judge_failed:{type(e).__name__}")
        # mutate the existing report so downstream code paths still work
        try:
            rpt.groundedness = new_ground
        except Exception:
            return HookResult(add_reason="llm_judge_report_immutable")
        # if judge says ungrounded, ensure escalate; if grounded, do NOT
        # auto-clear (other signals may still warrant escalation).
        if not getattr(new_ground, "supported", True):
            return HookResult(
                force_escalate=True,
                add_reason=f"llm_judge_ungrounded:score={getattr(new_ground, 'score', 0.0):.2f}",
            )
        return HookResult(add_reason=f"llm_judge_grounded:score={getattr(new_ground, 'score', 0.0):.2f}")

    _hook.__name__ = "llm_judge_groundedness_hook"
    return _hook


# ---------------------- 2) Escalation-voter override ---------------------
def make_escalation_vote_hook(
    mode: str = "majority",
    weights: Optional[dict] = None,
    threshold: float = 0.5,
) -> Callable[[HookContext], Optional[HookResult]]:
    """Build a hook that re-decides ``escalate`` via :class:`EscalationVoter`.

    Reads three signals out of ``ctx`` / ``ctx.guardrail_report``:

      * ``critic``       — ``ctx.confidence < 0.5`` (proxy; cfg threshold-free)
      * ``groundedness`` — ``not guardrail_report.groundedness.supported``
      * ``policy``       — any policy violation present

    Then runs the voter and writes its decision back via ``force_escalate``
    (or simply observes if the voter says no-escalate, *without* clearing
    a hard block).
    """
    from ..agent.escalation_voting import EscalationVoter

    voter = EscalationVoter(
        mode=mode,
        weights=dict(weights) if weights else None or {"critic": 0.4, "groundedness": 0.4, "policy": 0.2},
        threshold=threshold,
    )

    def _hook(ctx: HookContext) -> Optional[HookResult]:
        rpt = ctx.guardrail_report
        if rpt is None:
            return None
        crit = bool(ctx.confidence < 0.5)
        ground = rpt.groundedness
        ground_bad = bool(ground is not None and not getattr(ground, "supported", True))
        pol_bad = bool(getattr(rpt, "violations", None))
        decision = voter.vote(critic=crit, groundedness=ground_bad, policy=pol_bad)
        meta = {
            "vote_mode": decision.mode,
            "vote_signals": dict(decision.signals),
            "vote_escalate": bool(decision.escalate),
        }
        if decision.weighted_sum is not None:
            meta["vote_weighted_sum"] = decision.weighted_sum
        # We can *raise* escalation but never silently lower a BLOCK; the
        # registry's force_block metadata wins.
        if decision.escalate:
            return HookResult(
                force_escalate=True,
                add_reason=f"vote_{decision.mode}:{decision.reason}",
                add_metadata=meta,
            )
        return HookResult(add_reason=f"vote_{decision.mode}:{decision.reason}",
                          add_metadata=meta)

    _hook.__name__ = "escalation_vote_hook"
    return _hook


# --------------------------- 3) JSONL audit log --------------------------
def make_audit_log_hook(
    path: str = "experiments/hooks_demo/audit.jsonl",
) -> Callable[[HookContext], Optional[HookResult]]:
    """Build a hook that appends one JSON record per turn to ``path``.

    Captures everything an auditor needs:
      * timestamp, trace_id, hook point
      * raw + safe query, final answer, escalate/confidence
      * guardrail action + groundedness score (if any)
      * accumulated ``reasons`` and ``metadata`` from prior hooks
    """
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    def _hook(ctx: HookContext) -> Optional[HookResult]:
        rpt = ctx.guardrail_report
        ground = getattr(rpt, "groundedness", None) if rpt else None
        record = {
            "ts": round(time.time(), 3),
            "trace_id": ctx.trace_id,
            "point": ctx.point.value,
            "query": ctx.query,
            "query_safe": ctx.query_safe,
            "answer": ctx.answer[:500],
            "confidence": round(float(ctx.confidence), 4),
            "escalate": bool(ctx.escalate),
            "guardrail_action": str(getattr(rpt, "action", "")) if rpt else "",
            "groundedness_score": (
                round(float(getattr(ground, "score", 0.0)), 4) if ground is not None else None
            ),
            "groundedness_supported": (
                bool(getattr(ground, "supported", False)) if ground is not None else None
            ),
            "reasons": list(ctx.reasons),
            "metadata": dict(ctx.metadata),
        }
        try:
            with open(abs_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # pragma: no cover — best-effort
            return None
        return None  # observe-only

    _hook.__name__ = "audit_log_hook"
    return _hook


# ------------- Convenience direct hooks (no factory closure) -------------
def llm_judge_groundedness_hook(ctx: HookContext) -> Optional[HookResult]:
    """Default-config wrapper; lazy-builds ``LLMJudgeGroundedness()``."""
    # cache the inner hook on the function to avoid rebuilding per call
    inner = getattr(llm_judge_groundedness_hook, "_inner", None)
    if inner is None:
        inner = make_llm_judge_groundedness_hook()
        llm_judge_groundedness_hook._inner = inner  # type: ignore[attr-defined]
    return inner(ctx)


def escalation_vote_hook(ctx: HookContext) -> Optional[HookResult]:
    """Default ``majority`` voter wrapper."""
    inner = getattr(escalation_vote_hook, "_inner", None)
    if inner is None:
        inner = make_escalation_vote_hook(mode="majority")
        escalation_vote_hook._inner = inner  # type: ignore[attr-defined]
    return inner(ctx)


def audit_log_hook(ctx: HookContext) -> Optional[HookResult]:
    """Default-path audit-log wrapper (``experiments/hooks_demo/audit.jsonl``)."""
    inner = getattr(audit_log_hook, "_inner", None)
    if inner is None:
        inner = make_audit_log_hook()
        audit_log_hook._inner = inner  # type: ignore[attr-defined]
    return inner(ctx)
