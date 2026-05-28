"""v2.3 R2 — Subagent + Handoff multi-specialist routing.

This package is *fully external* to the rest of ``seagent``: nothing under
``seagent.agent`` / ``seagent.memory`` / ``seagent.guardrails`` imports from
here.  That keeps the controlled-ablation harness (Exp A→D) reproducible
bit-for-bit, while adding a drop-in replacement for ``SupportAgent.handle``
that fixes the multi_intent 0%-resolution wall observed in c21 Exp D.

Architecture (OpenAI cs-agents demo + Claude Code Subagents pattern):

    query ── IntentRouter ──┬─ [single intent]  ─► Specialist.handle()
                            │
                            └─ [N intents]     ─► fan-out ──► merge

The router is one LLM call returning structured JSON; each Specialist wraps a
shared SupportAgent but filters retrieved contexts to its own KB topic set
(``kb_filter``).  Merge is deterministic — answers prefixed by 第 X 个问题,
escalate = any(sub.escalate), confidence = min(sub.confidence).

Public API:
    IntentRouter, SubIntent
    SpecialistAgent
    HandoffProtocol
    MultiAgentOrchestrator
"""
from .router import IntentRouter, SubIntent
from .specialist import SpecialistAgent
from .handoff import HandoffProtocol, HandoffRequest, make_handoff_tool_schemas
from .orchestrator import MultiAgentOrchestrator

__all__ = [
    "IntentRouter",
    "SubIntent",
    "SpecialistAgent",
    "HandoffProtocol",
    "HandoffRequest",
    "make_handoff_tool_schemas",
    "MultiAgentOrchestrator",
]
