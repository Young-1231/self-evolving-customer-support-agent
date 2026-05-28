"""Handoff protocol — inter-agent transfer records.

Two artefacts live here:

1. ``HandoffProtocol`` (v2.3) — passive data record used by specialists to
   *annotate* their AgentResult ("this should have gone to <target_domain>").
   Kept untouched for byte-level backwards compatibility.  Consumers (Exp E
   reports, future chat-loop UIs) can read
   ``getattr(result, 'handoff_protocol', None)``.

2. ``HandoffRequest`` (v3.2, **NEW**) — OpenAI Agents SDK 2026-Q1 style
   "tool-call shaped" handoff that a specialist emits **mid-flight** when it
   discovers it cannot answer (low confidence / KB topic mismatch).  The
   orchestrator detects it in ``AgentResult.metadata['handoff_request']`` and
   may dispatch the query to ``target_domain``.

The realistic-LITE scope (2026-05): our SupportAgent does not actually do
LLM tool-calling — specialists decide whether to emit a HandoffRequest via a
heuristic (``SpecialistAgent._decide_handoff``).  The wire format
(``to_tool_call_format()``) is already SDK-compatible so a future LLM
tool-call backend can produce it directly.

OpenAI Agents SDK alignment
---------------------------
The Agents SDK 2026-Q1 represents an inter-agent handoff as a regular
function-tool call named ``handoff_to_<target>(context_summary, reason)`` —
no special type, just a tool whose execution swaps the active agent.  Our
``HandoffRequest.to_tool_call_format()`` produces exactly that shape so
traces and analytics stay vendor-neutral.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# v2.3 legacy data record (unchanged signature; consumers may still depend
# on this exact field set).
# ---------------------------------------------------------------------------
@dataclass
class HandoffProtocol:
    """Inter-agent transfer record (passive annotation)."""

    reason: str                          # human-readable why
    target_domain: Optional[str] = None  # 'refund' / 'billing' / 'human' / None
    context_summary: str = ""            # condensed state for the next agent
    urgent: bool = False                 # bypass queue?
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "target_domain": self.target_domain,
            "context_summary": self.context_summary,
            "urgent": self.urgent,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def to_human(cls, reason: str, context_summary: str = "", urgent: bool = False) -> "HandoffProtocol":
        return cls(reason=reason, target_domain="human", context_summary=context_summary, urgent=urgent)


# ---------------------------------------------------------------------------
# v3.2 OpenAI Agents SDK 2026 style tool-call shaped handoff
# ---------------------------------------------------------------------------
_VALID_URGENCY = ("low", "normal", "urgent")


@dataclass
class HandoffRequest:
    """OpenAI Agents SDK 2026 style mid-flight handoff request.

    A specialist emits this when, **during** its own ``handle()``, it decides
    another specialist (or a human) is a better fit.  The orchestrator
    detects it post-fan-out and may re-dispatch.

    Attributes
    ----------
    from_domain:
        The specialist that emitted the request.
    target_domain:
        Where to route next: another specialist label (``"billing"``,
        ``"account"``, ``"technical"``, ``"refund"``, ``"general"``) or the
        sentinel ``"human"`` to force overall escalation.
    context_summary:
        Short text the next specialist should treat as ground-truth context
        for the user's situation so it doesn't have to re-derive it.
    reason:
        Why the handoff is being requested (``"low_confidence"``,
        ``"topic_mismatch"``, ``"out_of_scope"``, …) — meant for traces.
    confidence:
        Emitter's own confidence at handoff time.  Useful for trace
        analytics and for downstream loop-suppression.
    urgency:
        ``"low"`` / ``"normal"`` / ``"urgent"``.  ``"urgent"`` lets the
        orchestrator treat the target as a hard escalation when it is
        ``"human"``.
    """

    from_domain: str
    target_domain: str
    context_summary: str
    reason: str
    confidence: float = 0.0
    urgency: str = "normal"

    def __post_init__(self) -> None:
        if self.urgency not in _VALID_URGENCY:
            # don't raise — we never want a heuristic mis-spelling to crash
            # the agent.  Normalise instead.
            self.urgency = "normal"
        try:
            self.confidence = float(self.confidence)
        except (TypeError, ValueError):
            self.confidence = 0.0

    # -- OpenAI Agents SDK 2026-Q1 wire format ------------------------------
    def to_tool_call_format(self) -> Dict[str, Any]:
        """Render as an OpenAI Agents SDK compatible function-tool call.

        Shape::

            {
              "type": "function",
              "function": {
                "name": "handoff_to_<target>",
                "arguments": {"context_summary": "...", "reason": "..."}
              }
            }

        The SDK's runtime treats any tool whose name starts with
        ``handoff_to_`` as a handoff trigger and swaps the active agent to
        the matching specialist.  We emit the same shape so traces are
        portable.
        """
        return {
            "type": "function",
            "function": {
                "name": f"handoff_to_{self.target_domain}",
                "arguments": {
                    "context_summary": self.context_summary,
                    "reason": self.reason,
                },
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_domain": self.from_domain,
            "target_domain": self.target_domain,
            "context_summary": self.context_summary,
            "reason": self.reason,
            "confidence": float(self.confidence),
            "urgency": self.urgency,
        }


def make_handoff_tool_schemas(available_domains: List[str]) -> List[Dict[str, Any]]:
    """Build a list of OpenAI-tool-schema dicts a specialist's LLM could be
    given so its tool-call decoder can directly emit ``handoff_to_<target>``.

    For the realistic-LITE scope this output is not wired into a backend yet
    — it exists so a future LLM tool-call integration can be done without
    touching the protocol again.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": f"handoff_to_{d}",
                "description": (
                    f"Hand this customer off to the {d} domain specialist. "
                    f"Use when the current specialist cannot resolve the "
                    f"issue (out-of-scope, low confidence, or the user's "
                    f"question is primarily about {d})."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "context_summary": {
                            "type": "string",
                            "description": "Short ground-truth summary of "
                                           "the user's situation for the "
                                           "next specialist.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why this handoff is being "
                                           "requested (e.g. 'topic_mismatch',"
                                           " 'low_confidence').",
                        },
                    },
                    "required": ["context_summary", "reason"],
                },
            },
        }
        for d in available_domains
    ]
