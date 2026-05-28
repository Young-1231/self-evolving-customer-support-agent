"""Hook lifecycle types: enum of points, context payload, optional result.

Mirrors Claude Code's hooks contract:
  * Each hook receives a single :class:`HookContext` snapshot of the agent's
    in-flight state at that lifecycle point.
  * It returns either ``None`` (no change) or a :class:`HookResult` carrying
    deterministic overrides (rewrite_answer / force_escalate / force_block /
    add_reason / add_metadata).  Other hooks in the chain see the merged state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class HookPoint(str, Enum):
    """The 8 lifecycle points exposed by :class:`SupportAgent`.

    Ordered roughly by when they fire inside a single ``handle()`` call::

        PRE_INPUT          – before input guardrail
        POST_INPUT         – after input guardrail (sees redacted query)
        PRE_GENERATION     – after retrieval, before backend.generate_answer
        POST_GENERATION    – after generation, before critic / escalation
        PRE_OUTPUT_GUARD   – before output guardrail (groundedness/policy/pii)
        POST_OUTPUT_GUARD  – after output guardrail (sees full guardrail_report)
        ON_ESCALATE        – when escalate==True is about to be returned
        ON_BLOCK           – when guardrail says BLOCK (terminal)
    """

    PRE_INPUT = "pre_input"
    POST_INPUT = "post_input"
    PRE_GENERATION = "pre_generation"
    POST_GENERATION = "post_generation"
    PRE_OUTPUT_GUARD = "pre_output_guard"
    POST_OUTPUT_GUARD = "post_output_guard"
    ON_ESCALATE = "on_escalate"
    ON_BLOCK = "on_block"


@dataclass
class HookContext:
    """Snapshot of agent state passed to every hook.

    Fields are populated progressively as the lifecycle advances; e.g. at
    ``PRE_INPUT`` only ``query`` is set, while at ``POST_OUTPUT_GUARD``
    everything down to ``guardrail_report`` is filled in.
    """

    point: HookPoint
    query: str = ""
    query_safe: str = ""
    contexts: List[Any] = field(default_factory=list)  # List[Passage]
    answer: str = ""
    confidence: float = 0.0
    escalate: bool = False
    guardrail_report: Any = None        # GuardrailReport | None
    trace_id: Optional[str] = None
    # free-form bag for hooks to read/write cross-stage signals
    metadata: Dict[str, Any] = field(default_factory=dict)
    # accumulated human-readable reasons (audit trail)
    reasons: List[str] = field(default_factory=list)


@dataclass
class HookResult:
    """Optional deterministic overrides a hook can request.

    Hooks return ``None`` for the common "observe-only" case.  When any field
    is non-default it is merged into the next ``HookContext`` and propagated
    back to the agent loop.
    """

    rewrite_answer: Optional[str] = None
    force_escalate: bool = False
    force_block: bool = False
    add_reason: Optional[str] = None
    add_metadata: Optional[Dict[str, Any]] = None
    # advanced: override the guardrail report (used by llm_judge groundedness hook)
    rewrite_guardrail_report: Any = None
