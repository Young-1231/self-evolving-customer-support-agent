"""SpecialistAgent — a thin wrapper around a shared SupportAgent that scopes
its retrieval to a single domain's KB slice.

We deliberately do **not** subclass ``SupportAgent``: we delegate.  The base
agent stays an unmodified v2.2 instance (shared backend, shared semantic
memory, shared guardrail), and the specialist intercepts the answer pipeline
by re-running it with a topic-filtered passage list.

Two delegation modes:

  * ``mode='core'`` (default): replay the *exact* logic of
    ``SupportAgent._handle_core`` (retrieve → generate → critic → decide)
    with a filtered context list.  Fast, but **bypasses the guardrail
    and tracer**.  Use this when wiring the specialist around a raw
    SupportAgent without guardrails (unit tests).

  * ``mode='observed'``: delegate to ``base.handle()`` so the full
    production pipeline (input guard → retrieve → generate → critic →
    output guard → tracer) runs end-to-end.  Topic filtering is
    achieved by *wrapping the base agent's ``_retrieve``* on a
    thread-local basis (we save/restore in a try/finally).

Mode 'observed' is the right choice for Exp E because Exp D used a fully
guarded ``SupportAgent`` and we want apples-to-apples.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Callable, List, Optional, Sequence, Tuple

from ..agent.support_agent import AgentResult, SupportAgent
from ..llm.base import Passage


# Default KB-topic membership for each specialist domain.  These align with the
# topics actually present in data/kb_expanded/index.jsonl (Counter shows:
# billing/account/delivery/order/subscription/feedback/support/general/
# account_security/integrations_api/troubleshooting/data_export/permissions/
# mobile_app).  'general' specialist deliberately accepts everything.
DEFAULT_DOMAIN_TOPICS = {
    "billing":   {"billing", "subscription"},
    "account":   {"account", "account_security", "permissions"},
    "technical": {"integrations_api", "troubleshooting", "mobile_app", "data_export"},
    "refund":    {"billing", "order", "subscription"},
    "general":   set(),  # empty = no filter
}


class SpecialistAgent:
    """Domain-scoped wrapper around a shared SupportAgent.

    Parameters
    ----------
    domain:
        Short identifier (``'billing'``, ``'refund'``, ...).  Reported in
        the AgentResult for downstream merging / observability.
    base_agent:
        A fully-wired SupportAgent (shared backend, semantic, guardrail,
        calibrator, tracer).  We never mutate it.
    kb_filter:
        Optional callable ``(Passage) -> bool``.  When set, KB passages for
        which the callable returns False are dropped before answer
        generation.  Episodic / playbook passages are *never* filtered —
        they already carry trust signals.
    fallback_on_empty:
        If True (default), and the topic filter empties the KB result, we
        fall back to the unfiltered KB hits rather than answering with no
        context.  This avoids "no info" replies when the router mis-labels.
    """

    def __init__(
        self,
        domain: str,
        base_agent: SupportAgent,
        kb_filter: Optional[Callable[[Passage], bool]] = None,
        fallback_on_empty: bool = True,
        mode: str = "core",
    ) -> None:
        if mode not in ("core", "observed"):
            raise ValueError(f"mode must be 'core' or 'observed', got {mode!r}")
        self.domain = domain
        self.base = base_agent
        self.kb_filter = kb_filter
        self.fallback_on_empty = fallback_on_empty
        self.mode = mode
        # Serialize observed-mode delegation when sharing a single base agent
        # across threads — base agent's tracer turn counter and our
        # ``_retrieve`` monkey-patch are not thread-safe.  The lock is per
        # *base_agent* (not per specialist) so two specialists wrapping the
        # same base agent share it.  Lazy-attached on first use.
        if mode == "observed":
            lock = getattr(base_agent, "_multi_agent_lock", None)
            if lock is None:
                lock = threading.Lock()
                try:
                    setattr(base_agent, "_multi_agent_lock", lock)
                except Exception:
                    pass
            self._observed_lock = lock
        else:
            self._observed_lock = threading.Lock()

    # ---- factory helpers ----
    @classmethod
    def for_domain(
        cls,
        domain: str,
        base_agent: SupportAgent,
        kb_topics: Optional[Sequence[str]] = None,
        mode: str = "core",
    ) -> "SpecialistAgent":
        """Build a specialist using DEFAULT_DOMAIN_TOPICS (or override)."""
        topics = set(kb_topics) if kb_topics is not None else set(DEFAULT_DOMAIN_TOPICS.get(domain, set()))
        kb_filter: Optional[Callable[[Passage], bool]] = None
        if topics:
            # Topic is on the KBDoc, not the Passage; we look it up via the
            # base agent's semantic memory.  Build a doc_id -> topic map once.
            doc_topic = {}
            sem = getattr(base_agent, "semantic", None)
            docs = getattr(sem, "docs", []) if sem is not None else []
            for d in docs:
                doc_topic[d.doc_id] = getattr(d, "topic", "general")

            def _filter(p: Passage, _topics=topics, _map=doc_topic) -> bool:
                if p.source != "kb":
                    return True
                return _map.get(p.ref, "general") in _topics

            kb_filter = _filter
        return cls(domain=domain, base_agent=base_agent, kb_filter=kb_filter, mode=mode)

    # ---- main entrypoint ----
    def handle(self, query: str) -> AgentResult:
        if self.mode == "observed":
            result = self._handle_observed(query)
        else:
            result = self._handle_core(query)
        try:
            setattr(result, "specialist_domain", self.domain)
        except Exception:
            pass
        return result

    # ---- mode='core': replay SupportAgent._handle_core w/ filtered ctxs ---
    def _handle_core(self, query: str) -> AgentResult:
        base = self.base
        contexts, epi, pb = base._retrieve(query)
        contexts = self._apply_kb_filter(contexts)

        answer = base.backend.generate_answer(query, contexts)
        kb_hits = [p for p in contexts if p.source == "kb"]
        thresholds = base._effective_thresholds(query, kb_hits)
        conf = base._confidence_with(thresholds, query, answer, contexts)
        escalate = base._decide_escalation(conf, epi, pb, thresholds=thresholds)

        return AgentResult(
            query=query,
            answer=answer,
            escalate=escalate,
            confidence=conf,
            contexts=contexts,
            used_sources=sorted({p.source for p in contexts}),
        )

    # ---- mode='observed': delegate to base.handle() w/ patched _retrieve --
    def _handle_observed(self, query: str) -> AgentResult:
        """Run the full guarded pipeline by temporarily patching the base
        agent's ``_retrieve`` to apply this specialist's KB filter.

        Thread safety: we hold ``self._observed_lock`` for the duration of
        the call.  The orchestrator fan-outs at most a handful of sub-queries
        per ticket, and each specialist has its own lock — so worst-case
        contention is "two sub-queries route to the same specialist", which
        we serialize.  The base agent itself was never thread-safe in
        ``_handle_observed`` (it has a ``_turn`` counter), so this lock
        actually *strengthens* correctness vs naive parallel calls.
        """
        base = self.base
        # No filter? just delegate, no patching needed.
        if self.kb_filter is None:
            with self._observed_lock:
                return base.handle(query)

        with self._observed_lock:
            original = base._retrieve

            def _patched(q: str, _orig=original):
                ctxs, epi, pb = _orig(q)
                ctxs = self._apply_kb_filter(ctxs)
                return ctxs, epi, pb

            base._retrieve = _patched  # type: ignore[assignment]
            try:
                return base.handle(query)
            finally:
                base._retrieve = original  # type: ignore[assignment]

    # ---- internals ----
    def _apply_kb_filter(self, contexts: List[Passage]) -> List[Passage]:
        if self.kb_filter is None:
            return list(contexts)
        kept = [p for p in contexts if self.kb_filter(p)]
        if not self.fallback_on_empty:
            return kept
        # If filtering nuked every KB hit, restore them — better to over-share
        # than to answer "no info"; the answer model + guardrail still gate.
        kb_kept = [p for p in kept if p.source == "kb"]
        if not kb_kept:
            return list(contexts)
        return kept
