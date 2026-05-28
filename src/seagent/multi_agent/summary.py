"""v3.1 — SubagentSummary: the compact return type of a context-isolated
subagent.

Background
----------
v2.3 introduced ``SpecialistAgent`` which wraps a *shared* ``SupportAgent``.
All N specialists therefore see the same KB instance, the same episodic
memory and the same context window — fan-out is conceptually parallel but
the agents are not isolated.

Anthropic's Claude Code Subagent pattern (2026) takes a different stance:
each subagent owns a **private context budget** (its own KB slice, its own
episodic snapshot, its own token budget), and when it finishes it returns
only a *summary* to the orchestrator.  The orchestrator never sees the
subagent's full retrieval context or chain-of-thought; that information
stays inside the subagent.

The key data structure for that handoff is :class:`SubagentSummary`.

Why a summary, not a full ``AgentResult``?
------------------------------------------
* The orchestrator's prompt would otherwise re-ingest every passage every
  subagent retrieved, blowing up the token budget for multi-intent tickets.
* The supervisor only needs:
    - the answer (short, customer-facing),
    - a confidence number,
    - whether the subagent is "tapping out" (handoff),
    - which docs it cited (for audit, not full text).
  Everything else is internal to the subagent.

This module is *additive*: it does not replace ``AgentResult`` and the
existing v2.3 ``SpecialistAgent`` continues to return ``AgentResult``
unchanged.  ``SubagentSummary`` is only emitted by the new
``SubagentExecutor`` (see ``subagent_executor.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Cap on the customer-facing answer surface that flows back to the
# orchestrator.  Subagents are free to *think* longer; only the summary
# is exported.  200 chars is a soft cap (we truncate, not error).
SUMMARY_MAX_CHARS = 200


@dataclass
class SubagentSummary:
    """The single artifact returned by a context-isolated subagent.

    Attributes
    ----------
    domain:
        Which domain handled the sub-query (``'billing'``, ``'account'``,
        ``'technical'``, ...).  Mirrors ``SpecialistAgent.domain`` so the
        orchestrator can route summaries by the same key.
    answer_summary:
        Compact, customer-facing reply.  Capped at
        :data:`SUMMARY_MAX_CHARS` chars at construction time.
    confidence:
        Subagent's self-reported confidence in ``[0, 1]``.
    needs_handoff:
        True iff the subagent decided it could not adequately handle this
        sub-query and is requesting the orchestrator hand it off to a
        different domain.  Set when confidence falls below the subagent's
        handoff threshold or when the retrieved KB slice was empty.
    handoff_to:
        Optional target domain string when ``needs_handoff`` is True.
        ``None`` means "give it to the default/general specialist".
    handoff_reason:
        Free-text rationale ("low_confidence", "kb_empty",
        "domain_mismatch", ...).  Used by the orchestrator for logging,
        not for routing decisions.
    cited_doc_ids:
        The KB doc ids the subagent actually grounded its answer on.  We
        intentionally do *not* include the doc text — that stays inside
        the subagent's private context.  The orchestrator can fetch full
        text on demand if it has its own KB handle.
    token_budget_used:
        Approximate token cost incurred by this subagent (input + output
        chars / 4 as a rough heuristic for the mock backend).  The
        orchestrator uses this to verify ``token_budget`` was respected
        and to report end-to-end savings vs the shared-context baseline.
    error:
        Non-``None`` iff the subagent crashed.  When set, the other
        fields hold conservative defaults (``confidence=0.0``,
        ``needs_handoff=True``).
    """

    domain: str
    answer_summary: str
    confidence: float = 0.0
    needs_handoff: bool = False
    handoff_to: Optional[str] = None
    handoff_reason: Optional[str] = None
    cited_doc_ids: List[str] = field(default_factory=list)
    token_budget_used: int = 0
    error: Optional[str] = None

    def __post_init__(self) -> None:
        # Enforce the summary length contract.  We truncate rather than
        # raise so a slightly verbose subagent never breaks the pipeline.
        if self.answer_summary and len(self.answer_summary) > SUMMARY_MAX_CHARS:
            self.answer_summary = self.answer_summary[: SUMMARY_MAX_CHARS - 1] + "…"
        # Clamp confidence to [0, 1] defensively.
        try:
            c = float(self.confidence)
        except Exception:
            c = 0.0
        if c < 0.0:
            c = 0.0
        elif c > 1.0:
            c = 1.0
        self.confidence = c

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "answer_summary": self.answer_summary,
            "confidence": self.confidence,
            "needs_handoff": self.needs_handoff,
            "handoff_to": self.handoff_to,
            "handoff_reason": self.handoff_reason,
            "cited_doc_ids": list(self.cited_doc_ids),
            "token_budget_used": self.token_budget_used,
            "error": self.error,
        }


__all__ = ["SubagentSummary", "SUMMARY_MAX_CHARS"]
