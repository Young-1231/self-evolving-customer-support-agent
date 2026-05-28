"""Handoff protocol — minimal data record for specialist→specialist / human
transfers.

The orchestrator does not currently chain specialists (we fan-out + merge
instead), but specialists may *emit* a ``HandoffProtocol`` alongside their
``AgentResult`` to signal "this should have gone to <target_domain>" or
"escalate with this context summary".  Consumers (Exp E reports, future
chat-loop UIs) can read ``getattr(result, 'handoff_protocol', None)`` to
surface routing failures.

Kept intentionally tiny — it is a contract, not a logic module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HandoffProtocol:
    """Inter-agent transfer record."""

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
