"""MultiAgentOrchestrator — drop-in replacement for ``SupportAgent.handle``
that routes multi-intent tickets to N specialist agents in parallel and
merges their answers.

Single-intent tickets follow a fast path (no fan-out, no merge overhead).

Public surface:
    handle(query) -> AgentResult        # same signature as SupportAgent

Merge policy
------------
* answer = "针对您的第 N 个问题（<domain>）：\\n<sub_answer>" joined by blank lines
* escalate = any(sub.escalate)
* confidence = min(sub.confidence)  (worst-case across the bundle)
* used_sources = union(sub.used_sources)
* contexts = concatenation (order = router output order, dedup by (source, ref))
* guardrail = first sub-result's guardrail (good enough for observability)
* trace_id = first sub-result's trace_id
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence

from ..agent.support_agent import AgentResult
from ..llm.base import Passage
from .router import IntentRouter, SubIntent
from .specialist import SpecialistAgent


_MAX_FANOUT_WORKERS = 4  # protect downstream provider QPS per ticket


class MultiAgentOrchestrator:
    """Compose a router + a specialist registry behind the SupportAgent API."""

    def __init__(
        self,
        router: IntentRouter,
        specialists: Dict[str, SpecialistAgent],
        default_specialist: str = "general",
        max_fanout_workers: int = _MAX_FANOUT_WORKERS,
    ) -> None:
        if not specialists:
            raise ValueError("specialists must be non-empty")
        if default_specialist not in specialists:
            raise ValueError(
                f"default_specialist={default_specialist!r} not in specialists "
                f"({list(specialists.keys())})"
            )
        self.router = router
        self.specialists = dict(specialists)
        self.default_specialist = default_specialist
        self.max_fanout_workers = max(1, int(max_fanout_workers))

        # lightweight observability counters
        self.n_routed = 0
        self.n_multi = 0
        self.n_specialist_errors = 0
        self._stats_lock = threading.Lock()

    # ---- main entrypoint (SupportAgent-compatible) ----
    def handle(self, query: str) -> AgentResult:
        intents = self.router.route(query)
        with self._stats_lock:
            self.n_routed += 1
            if len(intents) > 1:
                self.n_multi += 1

        # Defensive: router should never return [], but if it does, fall back.
        if not intents:
            intents = [SubIntent(label=self.default_specialist, sub_query=query, confidence=0.0)]

        if len(intents) == 1:
            return self._dispatch_single(query, intents[0])
        return self._dispatch_many(query, intents)

    # ---- single-intent path (no merge overhead) ----
    def _dispatch_single(self, query: str, si: SubIntent) -> AgentResult:
        spec = self._resolve_specialist(si.label)
        try:
            return spec.handle(si.sub_query or query)
        except Exception as e:
            with self._stats_lock:
                self.n_specialist_errors += 1
            return self._fallback_error_result(query, [si], str(e))

    # ---- multi-intent path: fan-out + merge ----
    def _dispatch_many(self, query: str, intents: List[SubIntent]) -> AgentResult:
        results: List[Optional[AgentResult]] = [None] * len(intents)
        errors: List[Optional[str]] = [None] * len(intents)

        n_workers = min(self.max_fanout_workers, len(intents))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futs = {
                pool.submit(self._run_one, si): i
                for i, si in enumerate(intents)
            }
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    errors[i] = f"{type(e).__name__}: {e}"
                    with self._stats_lock:
                        self.n_specialist_errors += 1

        return self._merge(query, intents, results, errors)

    def _run_one(self, si: SubIntent) -> AgentResult:
        spec = self._resolve_specialist(si.label)
        return spec.handle(si.sub_query)

    def _resolve_specialist(self, label: str) -> SpecialistAgent:
        if label in self.specialists:
            return self.specialists[label]
        # Refund tickets often map to billing flow if a dedicated refund
        # specialist isn't registered; otherwise fall back to default.
        if label == "refund" and "billing" in self.specialists:
            return self.specialists["billing"]
        return self.specialists[self.default_specialist]

    # ---- merge ----
    def _merge(
        self,
        query: str,
        intents: Sequence[SubIntent],
        results: Sequence[Optional[AgentResult]],
        errors: Sequence[Optional[str]],
    ) -> AgentResult:
        parts: List[str] = []
        all_contexts: List[Passage] = []
        seen_ctx = set()
        used_sources: set = set()
        confs: List[float] = []
        escalate_any = False
        guard: Any = None
        trace_id: Optional[str] = None

        n_ok = 0
        for idx, (si, r, err) in enumerate(zip(intents, results, errors), start=1):
            label = si.label
            if r is None:
                # specialist crashed — surface that as an escalation marker.
                # We do NOT fail the whole ticket; other sub-results still merge.
                msg = (
                    f"针对您的第 {idx} 个问题（{label}）：很抱歉，处理时遇到内部错误，"
                    f"已为您转接人工客服。"
                )
                parts.append(msg)
                escalate_any = True
                confs.append(0.0)
                continue

            n_ok += 1
            ans = (r.answer or "").strip()
            parts.append(f"针对您的第 {idx} 个问题（{label}）：\n{ans}")
            if r.escalate:
                escalate_any = True
            confs.append(float(r.confidence))
            for src in r.used_sources or []:
                used_sources.add(src)
            for p in r.contexts or []:
                key = (p.source, p.ref)
                if key not in seen_ctx:
                    seen_ctx.add(key)
                    all_contexts.append(p)
            if guard is None and getattr(r, "guardrail", None) is not None:
                guard = r.guardrail
            if trace_id is None and getattr(r, "trace_id", None):
                trace_id = r.trace_id

        merged_answer = "\n\n".join(parts) if parts else ""
        # If every specialist crashed, force escalation w/ empty confidence.
        confidence = min(confs) if confs else 0.0
        if n_ok == 0:
            escalate_any = True

        merged = AgentResult(
            query=query,
            answer=merged_answer,
            escalate=escalate_any,
            confidence=confidence,
            contexts=all_contexts,
            used_sources=sorted(used_sources),
            guardrail=guard,
            trace_id=trace_id,
        )
        # Attach the routing decision for observability (duck-typed extension).
        try:
            setattr(merged, "sub_intents", [si.to_dict() for si in intents])
            setattr(merged, "n_sub_errors", sum(1 for e in errors if e is not None))
        except Exception:
            pass
        return merged

    # ---- fallback ----
    def _fallback_error_result(self, query: str, intents: List[SubIntent], err: str) -> AgentResult:
        return AgentResult(
            query=query,
            answer="抱歉，我们暂时无法处理您的请求，已为您转接人工客服。",
            escalate=True,
            confidence=0.0,
            contexts=[],
            used_sources=[],
        )

    # ---- introspection ----
    def stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return {
                "n_routed": self.n_routed,
                "n_multi": self.n_multi,
                "n_specialist_errors": self.n_specialist_errors,
                "router_calls": int(getattr(self.router, "n_calls", 0)),
                "router_cache_hits": int(getattr(self.router, "n_cache_hits", 0)),
                "router_parse_fail": int(getattr(self.router, "n_parse_fail", 0)),
            }
