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

v2.7 — ``guardrail_mode`` (orchestrator-level merged guardrail)
---------------------------------------------------------------
Exp E in two variants exposed an architecture problem:

* ``mode='core'`` specialists (no per-sub guardrail) hit multi_intent
  resolution 46.8% but the merged answer is **never guarded** — output PII
  could leak, hallucinations get through.
* ``mode='observed'`` specialists run the full base.handle() pipeline (incl.
  guardrail) **on each sub-answer**, and any sub-fail (e.g. groundedness
  borderline) escalates the entire ticket: multi_intent 0%, overall esc 85.2%.

The fix: run specialists in ``mode='core'`` (raw answer) and let the
orchestrator run **one** guardrail.check_output(merged_answer, union_ctxs)
after merge.  One LLM-judge call instead of N, guardrail still fires on the
final user-facing text.

``guardrail_mode``:

* ``'merged'`` (default since v2.7): forces specialists to ``mode='core'``
  for the multi-intent fan-out (warns if they were 'observed'), collects raw
  sub-answers, then runs ``self.guardrail.check_output`` on the merged
  answer once.  Input guardrail (if any) is also run once up front.
* ``'per_sub'``: legacy v2.3 behaviour — specialists' own pipeline runs
  whatever guardrail they have wired in; orchestrator does not re-guard.
  Use to reproduce Exp E observed.
* ``'none'``: no guardrail anywhere in the orchestrator layer (specialists
  still do whatever they were configured to do, but for ``mode='core'``
  that means no output guardrail at all).

Single-intent tickets always take the legacy fast path (the specialist's
own pipeline handles guardrail).  Multi-intent is the only path that needs
the merged guardrail policy because that's where N×guardrail blows up.
"""
from __future__ import annotations

import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Sequence

from ..agent.support_agent import AgentResult
from ..llm.base import Passage
from .router import IntentRouter, SubIntent
from .specialist import SpecialistAgent


_MAX_FANOUT_WORKERS = 4  # protect downstream provider QPS per ticket

_VALID_GUARDRAIL_MODES = ("merged", "per_sub", "none")


class MultiAgentOrchestrator:
    """Compose a router + a specialist registry behind the SupportAgent API."""

    def __init__(
        self,
        router: IntentRouter,
        specialists: Dict[str, SpecialistAgent],
        default_specialist: str = "general",
        max_fanout_workers: int = _MAX_FANOUT_WORKERS,
        guardrail: Optional[object] = None,
        guardrail_mode: str = "merged",
    ) -> None:
        if not specialists:
            raise ValueError("specialists must be non-empty")
        if default_specialist not in specialists:
            raise ValueError(
                f"default_specialist={default_specialist!r} not in specialists "
                f"({list(specialists.keys())})"
            )
        if guardrail_mode not in _VALID_GUARDRAIL_MODES:
            raise ValueError(
                f"guardrail_mode must be one of {_VALID_GUARDRAIL_MODES}, "
                f"got {guardrail_mode!r}"
            )
        self.router = router
        self.specialists = dict(specialists)
        self.default_specialist = default_specialist
        self.max_fanout_workers = max(1, int(max_fanout_workers))
        self.guardrail = guardrail
        self.guardrail_mode = guardrail_mode

        # v2.7: when guardrail_mode='merged' and specialists were built in
        # mode='observed', their per-sub guardrail would double-fire and
        # defeat the whole point.  Downgrade in-place (warn) so the user
        # gets a consistent state regardless of how they wired things up.
        if guardrail_mode == "merged":
            self._downgrade_specialists_for_merged()
        # 'merged' mode requires a guardrail instance to actually run the
        # check.  If none was provided, fall back to 'none' silently — the
        # orchestrator still merges, just doesn't guard.  We warn so this
        # isn't quietly the wrong thing in production.
        if guardrail_mode == "merged" and guardrail is None:
            warnings.warn(
                "guardrail_mode='merged' but guardrail=None — no output "
                "guardrail will run on the merged answer. "
                "Pass guardrail=GuardrailPipeline(...) or set "
                "guardrail_mode='none' to silence this warning.",
                RuntimeWarning,
                stacklevel=2,
            )

        # lightweight observability counters
        self.n_routed = 0
        self.n_multi = 0
        self.n_specialist_errors = 0
        self.n_merged_guard_block = 0
        self.n_merged_guard_escalate = 0
        self._stats_lock = threading.Lock()

    def _downgrade_specialists_for_merged(self) -> None:
        downgraded: List[str] = []
        for label, spec in self.specialists.items():
            if getattr(spec, "mode", "core") == "observed":
                # downgrade: future fan-out calls will hit _handle_core,
                # skipping the per-sub guardrail.  We do not touch the
                # specialist's base agent; single-intent fast path is
                # unaffected (it still calls .handle() which now goes
                # through _handle_core, but the user opted-in by passing
                # guardrail_mode='merged').
                try:
                    spec.mode = "core"
                    downgraded.append(label)
                except Exception:
                    pass
        if downgraded:
            warnings.warn(
                f"guardrail_mode='merged' downgraded specialists "
                f"{downgraded} from mode='observed' to mode='core' so the "
                f"merged guardrail isn't double-fired per sub-answer.",
                RuntimeWarning,
                stacklevel=3,
            )

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
            # Single intent: no fan-out, no merged guardrail. The specialist's
            # own pipeline (incl. its own guardrail if mode='observed') handles
            # the request.  This keeps the v2.3 fast path unchanged.
            return self._dispatch_single(query, intents[0])
        if self.guardrail_mode == "merged":
            return self._dispatch_many_merged(query, intents)
        # 'per_sub' and 'none' both use the legacy fan-out (specialists do
        # whatever their own mode dictates).  'none' just means the
        # orchestrator itself never adds a guardrail layer — but that's
        # already the case for the legacy path.
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

    # ---- multi-intent path: fan-out + merge (legacy / per_sub / none) ----
    def _dispatch_many(self, query: str, intents: List[SubIntent]) -> AgentResult:
        results, errors = self._fanout(intents)
        return self._merge(query, intents, results, errors)

    # ---- multi-intent path with merged guardrail (v2.7 default) ----
    def _dispatch_many_merged(self, query: str, intents: List[SubIntent]) -> AgentResult:
        # 1) input guardrail: run once on the *original* query.  This is what
        #    the SupportAgent.handle() observed path would have done per sub,
        #    but we only need it once.
        guard = self.guardrail
        query_safe = query
        if guard is not None:
            try:
                inp = guard.check_input(query)
                if getattr(inp, "blocked", False):
                    # Hard input block: return immediately without touching
                    # specialists, mirroring SupportAgent._handle_observed.
                    return AgentResult(
                        query=query,
                        answer="抱歉，您的请求无法处理，已为您转接人工客服。",
                        escalate=True,
                        confidence=0.0,
                        contexts=[],
                        used_sources=[],
                        guardrail=inp,
                    )
                query_safe = getattr(inp, "redacted_text", "") or query
            except Exception:
                # input guard crashing should never break the whole pipeline
                query_safe = query

        # 2) fan-out: specialists run in mode='core' (raw answer, no guard)
        results, errors = self._fanout(intents, query_override=query_safe)

        # 3) merge sub-answers (same deterministic policy as legacy)
        merged = self._merge(query, intents, results, errors)

        # 4) run ONE output guardrail on the merged answer + union(contexts)
        if guard is None or not merged.answer:
            return merged
        try:
            report = guard.check_output(merged.answer, merged.contexts or [])
        except Exception:
            # don't crash the agent if the judge is flaky
            return merged

        # apply guardrail decision to the merged result
        action = str(getattr(report, "action", "ALLOW")).upper()
        new_answer = merged.answer
        if getattr(report, "redacted_answer", ""):
            new_answer = report.redacted_answer
        new_escalate = bool(merged.escalate)
        if action in ("ESCALATE", "BLOCK"):
            new_escalate = True
        if action == "BLOCK":
            with self._stats_lock:
                self.n_merged_guard_block += 1
        elif action == "ESCALATE":
            with self._stats_lock:
                self.n_merged_guard_escalate += 1

        out = AgentResult(
            query=query,
            answer=new_answer,
            escalate=new_escalate,
            confidence=merged.confidence,
            contexts=merged.contexts,
            used_sources=merged.used_sources,
            guardrail=report,
            trace_id=merged.trace_id,
        )
        # preserve duck-typed observability attrs from the inner merge
        for attr in ("sub_intents", "n_sub_errors"):
            val = getattr(merged, attr, None)
            if val is not None:
                try:
                    setattr(out, attr, val)
                except Exception:
                    pass
        try:
            setattr(out, "merged_guardrail_action", action)
        except Exception:
            pass
        return out

    # ---- shared fan-out worker ----
    def _fanout(
        self,
        intents: List[SubIntent],
        query_override: Optional[str] = None,
    ):
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
        return results, errors

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
                "n_merged_guard_block": self.n_merged_guard_block,
                "n_merged_guard_escalate": self.n_merged_guard_escalate,
                "router_calls": int(getattr(self.router, "n_calls", 0)),
                "router_cache_hits": int(getattr(self.router, "n_cache_hits", 0)),
                "router_parse_fail": int(getattr(self.router, "n_parse_fail", 0)),
            }
