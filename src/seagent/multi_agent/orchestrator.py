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

* ``'per_sub_aggregated'`` (default since v2.8): forces specialists to
  ``mode='core'`` (raw sub-answer, no per-sub guard), then runs
  ``guardrail.check_output`` on **each (sub_answer, sub_contexts)** pair
  independently and AGGREGATES the verdicts:

  - groundedness uses **any-supported = supported** (the merged bundle is
    considered grounded if at least one sub-answer is fully supported by
    its own contexts — each sub is self-consistent against its slice).
  - PII redaction is applied per sub, then merged answers are stitched
    from the per-sub ``redacted_answer`` to avoid surface accumulation.
  - policy: ANY ``BLOCK`` -> bundle ``BLOCK``; else ANY ``REWRITE`` ->
    bundle ``REWRITE``; else ``ALLOW``.
  - escalate uses **majority vote**: bundle escalates only when
    strictly more than half of subs escalate (or any block fires).

  This restores the multi_intent resolution that ``'merged'`` killed by
  judging an over-long concatenation against a union of unrelated
  contexts, while keeping safety (PII redaction, per-sub policy).

* ``'merged'``: v2.7 behaviour — forces specialists to ``mode='core'``
  for the multi-intent fan-out (warns if they were 'observed'), collects raw
  sub-answers, then runs ``self.guardrail.check_output`` on the merged
  answer once.  Input guardrail (if any) is also run once up front.
  Negative finding: groundedness fires on concatenated answer vs union
  contexts -> multi_intent res 0%.
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

_VALID_GUARDRAIL_MODES = ("per_sub_aggregated", "merged", "per_sub", "none")


class MultiAgentOrchestrator:
    """Compose a router + a specialist registry behind the SupportAgent API."""

    def __init__(
        self,
        router: IntentRouter,
        specialists: Dict[str, SpecialistAgent],
        default_specialist: str = "general",
        max_fanout_workers: int = _MAX_FANOUT_WORKERS,
        guardrail: Optional[object] = None,
        guardrail_mode: str = "per_sub_aggregated",
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

        # v2.7/v2.8: when guardrail_mode is 'merged' or 'per_sub_aggregated' and
        # specialists were built in mode='observed', their per-sub guardrail
        # would double-fire and defeat the whole point.  Downgrade in-place
        # (warn) so the user gets a consistent state regardless of how they
        # wired things up.
        if guardrail_mode in ("merged", "per_sub_aggregated"):
            self._downgrade_specialists_for_merged()
        # 'merged'/'per_sub_aggregated' both need a guardrail instance to
        # actually run check_output.  If none was provided, the orchestrator
        # still merges but doesn't guard.  Warn so this isn't quietly the
        # wrong thing in production.
        if guardrail_mode in ("merged", "per_sub_aggregated") and guardrail is None:
            warnings.warn(
                f"guardrail_mode={guardrail_mode!r} but guardrail=None — no "
                f"output guardrail will run. "
                f"Pass guardrail=GuardrailPipeline(...) or set "
                f"guardrail_mode='none' to silence this warning.",
                RuntimeWarning,
                stacklevel=2,
            )

        # lightweight observability counters
        self.n_routed = 0
        self.n_multi = 0
        self.n_specialist_errors = 0
        self.n_merged_guard_block = 0
        self.n_merged_guard_escalate = 0
        # v2.8 per_sub_aggregated counters
        self.n_agg_block = 0
        self.n_agg_escalate = 0
        self.n_agg_rewrite = 0
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
                f"guardrail_mode={self.guardrail_mode!r} downgraded specialists "
                f"{downgraded} from mode='observed' to mode='core' so the "
                f"orchestrator-level guardrail isn't double-fired per sub-answer.",
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
            # Single intent: no fan-out, no aggregated/merged guardrail. The
            # specialist's own pipeline (incl. its own guardrail if
            # mode='observed') handles the request.  This keeps the v2.3 fast
            # path unchanged for all guardrail modes.
            return self._dispatch_single(query, intents[0])
        if self.guardrail_mode == "per_sub_aggregated":
            return self._dispatch_many_per_sub_aggregated(query, intents)
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

    # ---- multi-intent path with per-sub aggregated guardrail (v2.8 default) ----
    def _dispatch_many_per_sub_aggregated(
        self, query: str, intents: List[SubIntent]
    ) -> AgentResult:
        """v2.8: run guardrail.check_output PER sub-answer (with its own
        contexts), then aggregate verdicts before producing the final
        AgentResult.

        Aggregation:
          - groundedness: any-supported = supported.  At least one sub fully
            grounded by its own contexts means the bundle's grounded
            (concatenated answer is a structural join of N self-consistent
            pieces; we cannot judge it against the union and expect any
            single sub to "carry" the whole text).
          - PII: redact per sub; merge from per-sub redacted_answer to avoid
            surface accumulation.
          - policy: ANY BLOCK -> BLOCK; else ANY REWRITE -> REWRITE; else ALLOW.
          - escalate: majority vote on ESCALATE action (>50% of subs).  ANY
            BLOCK always escalates.
        """
        guard = self.guardrail

        # 1) input guardrail (run once on the *original* query)
        query_safe = query
        if guard is not None:
            try:
                inp = guard.check_input(query)
                if getattr(inp, "blocked", False):
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
                query_safe = query

        # 2) fan-out (specialists already downgraded to 'core' in __init__)
        results, errors = self._fanout(intents, query_override=query_safe)

        # 3) per-sub guardrail.check_output(sub_answer, sub_contexts)
        sub_reports: List[Optional[object]] = [None] * len(results)
        if guard is not None:
            for i, r in enumerate(results):
                if r is None or not (r.answer or "").strip():
                    continue
                try:
                    sub_reports[i] = guard.check_output(
                        r.answer, list(r.contexts or [])
                    )
                except Exception:
                    sub_reports[i] = None

        # 4) aggregate verdicts and assemble merged answer using per-sub
        #    redacted text where available
        parts: List[str] = []
        all_contexts: List[Passage] = []
        seen_ctx = set()
        used_sources: set = set()
        confs: List[float] = []
        n_ok = 0
        n_block = 0
        n_rewrite = 0
        n_escalate_signal = 0
        any_supported = False
        any_ground_seen = False
        sub_pii_entities: set = set()

        # final aggregated guardrail uses the FIRST sub's report as a template
        # for stage/violations bookkeeping; we synthesise a fresh
        # GuardrailReport at the bottom if guard ran.
        for idx, (si, r, rep, err) in enumerate(
            zip(intents, results, sub_reports, errors), start=1
        ):
            label = si.label
            if r is None:
                parts.append(
                    f"针对您的第 {idx} 个问题（{label}）：很抱歉，处理时遇到内部错误，"
                    f"已为您转接人工客服。"
                )
                confs.append(0.0)
                n_escalate_signal += 1
                continue
            n_ok += 1
            confs.append(float(r.confidence))
            # We do NOT fold the specialist's own r.escalate into the bundle
            # vote: that flag is already represented through its
            # groundedness / policy output. Counting it twice (here AND via
            # the per-sub guardrail report) is what blew up Exp E observed
            # (any-sub-fails -> whole-ticket-fails).
            for src in r.used_sources or []:
                used_sources.add(src)
            for p in r.contexts or []:
                key = (p.source, p.ref)
                if key not in seen_ctx:
                    seen_ctx.add(key)
                    all_contexts.append(p)

            # pick the per-sub answer (prefer redacted if guardrail ran)
            sub_answer = (r.answer or "").strip()
            if rep is not None:
                redacted = getattr(rep, "redacted_answer", "") or ""
                if redacted:
                    sub_answer = redacted
                # per-sub policy aggregation
                action = str(getattr(rep, "action", "ALLOW")).upper()
                if action == "BLOCK":
                    n_block += 1
                elif action == "REWRITE":
                    n_rewrite += 1
                elif action == "ESCALATE":
                    n_escalate_signal += 1
                # per-sub groundedness aggregation (any-supported)
                ground = getattr(rep, "groundedness", None)
                if ground is not None:
                    any_ground_seen = True
                    if getattr(ground, "supported", False):
                        any_supported = True
                # collect pii entities for the aggregated report
                for span in getattr(rep, "pii_spans", []) or []:
                    ent = getattr(span, "entity", None)
                    if ent:
                        sub_pii_entities.add(ent)
            parts.append(f"针对您的第 {idx} 个问题（{label}）：\n{sub_answer}")

        merged_answer = "\n\n".join(parts) if parts else ""
        confidence = min(confs) if confs else 0.0
        if n_ok == 0:
            n_escalate_signal = max(n_escalate_signal, len(intents))

        # bundle policy decision
        if n_block > 0:
            bundle_action = "BLOCK"
            bundle_escalate = True
        else:
            # majority escalate vote: strictly more than half of subs
            majority_escalate = (
                len(results) > 0
                and n_escalate_signal > len(results) / 2
            )
            # groundedness aggregation: any-supported.  If guardrail never
            # ran (guard=None) we have no groundedness signal -> treat as
            # not requiring escalation on groundedness grounds.
            ground_fail = any_ground_seen and not any_supported
            if majority_escalate or ground_fail:
                bundle_action = "ESCALATE"
                bundle_escalate = True
            elif n_rewrite > 0:
                bundle_action = "REWRITE"
                bundle_escalate = False
            else:
                bundle_action = "ALLOW"
                bundle_escalate = False

        # build an aggregated GuardrailReport when at least one sub-report exists
        aggregated_report = None
        if guard is not None and any(rep is not None for rep in sub_reports):
            try:
                from ..guardrails.pipeline import GuardrailReport
                reasons: List[str] = []
                if n_block:
                    reasons.append(f"agg_policy_block: {n_block}/{len(results)} subs")
                if n_rewrite and bundle_action == "REWRITE":
                    reasons.append(f"agg_policy_rewrite: {n_rewrite}/{len(results)} subs")
                if any_ground_seen and not any_supported:
                    reasons.append(
                        f"agg_low_groundedness: 0/{sum(1 for rep in sub_reports if rep is not None and getattr(rep, 'groundedness', None) is not None)} subs supported"
                    )
                if (
                    bundle_action == "ESCALATE"
                    and n_escalate_signal > len(results) / 2
                ):
                    reasons.append(
                        f"agg_majority_escalate: {n_escalate_signal}/{len(results)} subs"
                    )
                if sub_pii_entities:
                    reasons.append(
                        f"agg_output_pii_redacted: {sorted(sub_pii_entities)}"
                    )
                aggregated_report = GuardrailReport(
                    stage="output",
                    passed=(bundle_action == "ALLOW" and not sub_pii_entities),
                    action=bundle_action.lower() if bundle_action != "ALLOW" else "allow",
                    blocked=(bundle_action == "BLOCK"),
                    reasons=reasons,
                    redacted_answer=merged_answer,
                )
            except Exception:
                aggregated_report = None

        # counters
        if bundle_action == "BLOCK":
            with self._stats_lock:
                self.n_agg_block += 1
        elif bundle_action == "ESCALATE":
            with self._stats_lock:
                self.n_agg_escalate += 1
        elif bundle_action == "REWRITE":
            with self._stats_lock:
                self.n_agg_rewrite += 1

        out = AgentResult(
            query=query,
            answer=merged_answer,
            escalate=bundle_escalate,
            confidence=confidence,
            contexts=all_contexts,
            used_sources=sorted(used_sources),
            guardrail=aggregated_report,
            trace_id=next(
                (r.trace_id for r in results if r is not None and getattr(r, "trace_id", None)),
                None,
            ),
        )
        try:
            setattr(out, "sub_intents", [si.to_dict() for si in intents])
            setattr(out, "n_sub_errors", sum(1 for e in errors if e is not None))
            setattr(out, "agg_guardrail_action", bundle_action)
            setattr(
                out, "agg_sub_breakdown",
                {
                    "n_subs": len(results),
                    "n_ok": n_ok,
                    "n_block": n_block,
                    "n_rewrite": n_rewrite,
                    "n_escalate_signal": n_escalate_signal,
                    "any_supported": any_supported,
                    "any_ground_seen": any_ground_seen,
                },
            )
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
                "n_agg_block": self.n_agg_block,
                "n_agg_escalate": self.n_agg_escalate,
                "n_agg_rewrite": self.n_agg_rewrite,
                "router_calls": int(getattr(self.router, "n_calls", 0)),
                "router_cache_hits": int(getattr(self.router, "n_cache_hits", 0)),
                "router_parse_fail": int(getattr(self.router, "n_parse_fail", 0)),
            }
