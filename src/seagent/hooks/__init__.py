"""v2.1 R1 — Lifecycle hooks, modeled after Claude Code's 25-lifecycle hooks.

This package exposes a pluggable hook system so customers can inject their own
guardrail / audit / logging / compliance logic at 8 well-defined lifecycle
points inside ``SupportAgent`` without forking the core code.

Design goals:
  * **Regression-safe by default.** ``SupportAgent`` defaults ``hook_registry``
    to ``None`` and the c21 Exp D behaviour is bit-for-bit preserved.
  * **Zero third-party deps.** All built-in hooks live in
    :mod:`seagent.hooks.builtin` and reuse existing c21 modules
    (``LLMJudgeGroundedness``, ``EscalationVoter``, JSONL writer).
  * **Failure-isolated.** A buggy hook never crashes the agent: exceptions are
    logged and swallowed inside :class:`HookRegistry.fire`.

Usage (production)::

    from seagent.hooks import HookRegistry, HookPoint
    from seagent.hooks.builtin import audit_log_hook

    reg = HookRegistry()
    reg.register(HookPoint.POST_OUTPUT_GUARD, audit_log_hook, priority=10)
    agent = SupportAgent(cfg, backend, sem, guardrail=gp, hook_registry=reg)
"""
from __future__ import annotations

from .registry import HookRegistry, default_registry, get_registry, set_registry
from .types import HookContext, HookPoint, HookResult

__all__ = [
    "HookPoint",
    "HookContext",
    "HookResult",
    "HookRegistry",
    "default_registry",
    "get_registry",
    "set_registry",
]
