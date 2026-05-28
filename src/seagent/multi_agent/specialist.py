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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..agent.support_agent import AgentResult, SupportAgent
from ..llm.base import Passage
from .handoff import HandoffRequest


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
        handoff_confidence_threshold: float = 0.3,
        domain_topics: Optional[Sequence[str]] = None,
    ) -> None:
        if mode not in ("core", "observed"):
            raise ValueError(f"mode must be 'core' or 'observed', got {mode!r}")
        self.domain = domain
        self.base = base_agent
        self.kb_filter = kb_filter
        self.fallback_on_empty = fallback_on_empty
        self.mode = mode
        # v3.2 mid-flight handoff heuristic config.  ``domain_topics`` is the
        # set of KB topics this specialist owns — used by ``_decide_handoff``
        # to detect a topic mismatch.  When unset we fall back to
        # DEFAULT_DOMAIN_TOPICS[domain].
        self.handoff_confidence_threshold = float(handoff_confidence_threshold)
        if domain_topics is None:
            self.domain_topics = set(DEFAULT_DOMAIN_TOPICS.get(domain, set()))
        else:
            self.domain_topics = set(domain_topics)
        # Lazy doc_id -> topic map for KB hits (populated on demand).  Sharing
        # the lookup across calls avoids walking semantic.docs every handle().
        self._doc_topic_map: Optional[Dict[str, str]] = None
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
        handoff_confidence_threshold: float = 0.3,
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
        return cls(
            domain=domain,
            base_agent=base_agent,
            kb_filter=kb_filter,
            mode=mode,
            handoff_confidence_threshold=handoff_confidence_threshold,
            domain_topics=topics,
        )

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
        # v3.2: emit a mid-flight HandoffRequest when our confidence is low
        # or the user's question doesn't actually match our KB topic.  The
        # orchestrator picks it up via getattr(result, 'handoff_request', None)
        # — old call sites that don't look for it see byte-identical output.
        handoff_req = self._decide_handoff(query, result)
        if handoff_req is not None:
            try:
                setattr(result, "handoff_request", handoff_req)
            except Exception:
                pass
        return result

    # ---- v3.2 mid-flight handoff decision -------------------------------
    def _decide_handoff(
        self, query: str, result: AgentResult
    ) -> Optional[HandoffRequest]:
        """Heuristic: should this specialist hand off mid-flight?

        Triggers (in order of priority):

        1. **Topic mismatch** — top retrieved KB hit belongs to a topic that
           is NOT in ``self.domain_topics`` *and* maps cleanly to a sibling
           specialist domain.  This is the "router mis-classified, I see
           you actually want X" case.
        2. **Low confidence** — ``result.confidence`` is strictly below
           ``self.handoff_confidence_threshold``.  Fallback target: ``human``.

        The ``general`` specialist never emits handoffs (it is the catch-all
        terminal node — handing off from general would loop).
        """
        if self.domain == "general":
            return None

        # ---------- 1) KB topic mismatch ----------
        target = self._infer_target_domain_from_contexts(result)
        if target is not None and target != self.domain:
            return HandoffRequest(
                from_domain=self.domain,
                target_domain=target,
                context_summary=self._summarise_for_handoff(query, result),
                reason="topic_mismatch",
                confidence=float(result.confidence),
                urgency="normal",
            )

        # ---------- 2) low confidence ----------
        if float(result.confidence) < self.handoff_confidence_threshold:
            return HandoffRequest(
                from_domain=self.domain,
                target_domain="human",
                context_summary=self._summarise_for_handoff(query, result),
                reason="low_confidence",
                confidence=float(result.confidence),
                urgency="normal",
            )
        return None

    def _infer_target_domain_from_contexts(
        self, result: AgentResult
    ) -> Optional[str]:
        """Look at the top KB hit's topic and find which sibling specialist
        domain (per DEFAULT_DOMAIN_TOPICS) would own it.  Returns ``None``
        when we can't tell or when the hit is already in-domain."""
        if not self.domain_topics:
            # general / no-filter specialist — never claim mismatch
            return None
        doc_topic_map = self._get_doc_topic_map()
        if not doc_topic_map:
            return None
        for p in result.contexts or []:
            if p.source != "kb":
                continue
            topic = doc_topic_map.get(p.ref)
            if not topic:
                continue
            if topic in self.domain_topics:
                # the very first in-domain KB hit means we're good — no
                # handoff. Bail out early.
                return None
            # find a sibling specialist that owns this topic.  We prefer the
            # canonical mapping in DEFAULT_DOMAIN_TOPICS (skipping ourself
            # and 'general' which is the no-filter catch-all).
            for cand_domain, cand_topics in DEFAULT_DOMAIN_TOPICS.items():
                if cand_domain in (self.domain, "general"):
                    continue
                if topic in cand_topics:
                    return cand_domain
            return None
        return None

    def _get_doc_topic_map(self) -> Dict[str, str]:
        if self._doc_topic_map is not None:
            return self._doc_topic_map
        sem = getattr(self.base, "semantic", None)
        docs = getattr(sem, "docs", []) if sem is not None else []
        self._doc_topic_map = {
            d.doc_id: getattr(d, "topic", "general") for d in docs
        }
        return self._doc_topic_map

    def _summarise_for_handoff(
        self, query: str, result: AgentResult, max_len: int = 200
    ) -> str:
        """Cheap deterministic summary for the next specialist — no LLM
        call.  Future work: have the LLM author this via the tool-call."""
        q = (query or "").strip().replace("\n", " ")
        if len(q) > max_len:
            q = q[:max_len] + "…"
        return (
            f"[from {self.domain}] user_query: {q} "
            f"(my_confidence={float(result.confidence):.2f})"
        )

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
