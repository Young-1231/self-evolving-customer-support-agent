"""HookRegistry — priority-ordered, failure-isolated dispatcher.

Mirrors Claude Code's hook registry semantics but trimmed to the agent's 8
lifecycle points.  A registry is per-agent (passed to ``SupportAgent.__init__``)
or process-wide via the :data:`default_registry` singleton.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .types import HookContext, HookPoint, HookResult

log = logging.getLogger("seagent.hooks")

# A hook is any callable: (ctx) -> Optional[HookResult]
HookFn = Callable[[HookContext], Optional[HookResult]]


@dataclass(order=True)
class _Entry:
    # negative priority so heapq-like sort puts highest priority first; we
    # actually use a plain sorted() call, kept as a stable record here
    priority: int
    seq: int = field(compare=True)     # insertion order tie-breaker
    fn: HookFn = field(compare=False)
    name: str = field(default="", compare=False)


class HookRegistry:
    """Per-point list of hooks; deterministic firing order."""

    def __init__(self) -> None:
        self._hooks: Dict[HookPoint, List[_Entry]] = {p: [] for p in HookPoint}
        self._lock = threading.Lock()
        self._seq = 0

    # --------------------------- registration ---------------------------
    def register(
        self,
        point: HookPoint,
        hook_fn: HookFn,
        priority: int = 0,
        name: Optional[str] = None,
    ) -> None:
        """Register ``hook_fn`` to fire at ``point``.

        Higher ``priority`` fires earlier; ties break by registration order.
        ``name`` is purely cosmetic (shows up in logs).
        """
        if point not in self._hooks:
            raise ValueError(f"unknown hook point: {point!r}")
        with self._lock:
            self._seq += 1
            entry = _Entry(
                priority=-int(priority),    # invert: higher prio sorts first
                seq=self._seq,
                fn=hook_fn,
                name=name or getattr(hook_fn, "__name__", "hook"),
            )
            self._hooks[point].append(entry)
            # stable sort: highest priority (most negative inverted) first
            self._hooks[point].sort(key=lambda e: (e.priority, e.seq))

    def clear(self, point: Optional[HookPoint] = None) -> None:
        """Drop all hooks (or just one point's worth)."""
        with self._lock:
            if point is None:
                for p in self._hooks:
                    self._hooks[p].clear()
            else:
                self._hooks[point].clear()

    def hooks_at(self, point: HookPoint) -> List[str]:
        """Return registered hook names at ``point`` (debug helper)."""
        return [e.name for e in self._hooks.get(point, [])]

    # ----------------------------- firing -------------------------------
    def fire(self, point: HookPoint, ctx: HookContext) -> HookContext:
        """Run every hook at ``point`` in priority order, merging results.

        A hook that raises is logged-and-skipped; the chain continues so a
        single buggy hook can never crash the agent.  Returns the (possibly
        mutated) ``ctx`` so callers can rebind local variables.
        """
        ctx.point = point
        for entry in list(self._hooks.get(point, [])):
            try:
                result = entry.fn(ctx)
            except Exception as e:
                log.warning(
                    "hook %s at %s raised %s: %s — skipping",
                    entry.name, point.value, type(e).__name__, e,
                )
                continue
            if result is None:
                continue
            _apply_result(ctx, result, entry.name)
        return ctx


def _apply_result(ctx: HookContext, result: HookResult, hook_name: str) -> None:
    """Merge a :class:`HookResult` into ``ctx`` in place."""
    if result.rewrite_answer is not None:
        ctx.answer = result.rewrite_answer
    if result.force_escalate:
        ctx.escalate = True
    if result.force_block:
        ctx.escalate = True
        ctx.metadata["force_block"] = True
    if result.add_reason:
        ctx.reasons.append(f"[{hook_name}] {result.add_reason}")
    if result.add_metadata:
        for k, v in result.add_metadata.items():
            ctx.metadata[k] = v
    if result.rewrite_guardrail_report is not None:
        ctx.guardrail_report = result.rewrite_guardrail_report


# ------------------------- process-wide default --------------------------
default_registry = HookRegistry()


def get_registry() -> HookRegistry:
    """Return the process-wide :data:`default_registry`."""
    return default_registry


def set_registry(reg: HookRegistry) -> None:
    """Swap the process-wide default (mostly for tests)."""
    global default_registry
    default_registry = reg
