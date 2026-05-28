"""v3.1 — SubagentExecutor: a context-isolated subagent runner.

This is the Python implementation of the Anthropic Claude Code Subagent
pattern (2026): a subagent receives its *own* private slice of the KB and
its *own* episodic snapshot, runs a small backend call, then returns only
a :class:`SubagentSummary` to whoever invoked it.  The orchestrator never
appears in the subagent's prompt; the subagent never sees other
subagents' contexts.  This is what HCLTech reports as the +40% case
resolution lift on multi-intent customer-support workloads.

Key differences vs v2.3 :class:`SpecialistAgent`
------------------------------------------------
* ``SpecialistAgent`` wraps a *shared* SupportAgent (shared backend,
  shared semantic memory, shared guardrail, shared tracer).  Topic
  filtering happens by filtering passages returned from the shared KB.
* ``SubagentExecutor`` owns its **own** KB list (already filtered to its
  domain at construction time) and its own episodic snapshot.  Two
  executors of the same domain *do not* share these — pass independent
  copies.  This is the actual mechanism that produces context
  isolation.
* ``SpecialistAgent.handle`` returns an ``AgentResult`` with full
  contexts and the raw answer.  ``SubagentExecutor.handle`` returns a
  ``SubagentSummary`` whose answer is capped at
  :data:`SUMMARY_MAX_CHARS` and whose contexts are referenced by id
  only.

This module is fully external to :mod:`seagent.agent` and never imports
:class:`SupportAgent`.  It is therefore a *parallel* execution path, not
a replacement: the v2.3 SpecialistAgent / MultiAgentOrchestrator stack
continues to run unmodified.

Design constraints honoured
---------------------------
* Pure stdlib; no new third-party dependency.
* Works with both :class:`MockBackend` and any
  :class:`LLMBackend.generate_answer`-compatible backend.  We never
  assume a chat method; this keeps unit tests hermetic.
* Token budget is enforced by character truncation on the *input*
  context window before it reaches the backend.  Realistic token
  accounting is the job of the production backend; here we just need a
  monotonic cost signal that proves isolation is saving context.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..data import KBDoc
from ..llm.base import LLMBackend, Passage
from ..memory.bm25 import BM25
from ..memory.episodic import Case
from .specialist import DEFAULT_DOMAIN_TOPICS
from .summary import SUMMARY_MAX_CHARS, SubagentSummary


# Rough char-per-token heuristic for the mock pipeline.  Mirrors the
# convention used elsewhere in seagent obs code (we never call a real
# tokenizer in unit tests).
_CHARS_PER_TOKEN = 4

# Default confidence threshold below which a subagent self-reports
# ``needs_handoff``.  Aligned with the calibrator's mid-band default;
# subagent isolation should be conservative — handing off is cheap.
_DEFAULT_HANDOFF_TAU = 0.35


def _tokens_of(text: str) -> int:
    """Approximate token count for budget accounting."""
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


@dataclass
class _IsolatedKBView:
    """A private KB index owned by exactly one ``SubagentExecutor``.

    Not exported from the module — instantiated internally so the
    invariant "the executor controls its own retrieval surface" holds at
    type level.
    """

    docs: List[KBDoc]
    _bm25: Optional[BM25] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.docs:
            self._bm25 = BM25([f"{d.title} {d.text}" for d in self.docs])

    def retrieve(self, query: str, top_k: int = 3) -> List[Passage]:
        if not self._bm25 or not self.docs:
            return []
        out: List[Passage] = []
        for idx, score in self._bm25.search(query, top_k=top_k):
            d = self.docs[idx]
            # normalize to [0,1] same way SemanticMemory does
            norm = score / (score + 6.0) if score > 0 else 0.0
            out.append(Passage(source="kb", text=d.text, score=norm, ref=d.doc_id))
        return out


class SubagentExecutor:
    """Context-isolated subagent executor (Anthropic Claude Code pattern).

    Each instance owns:
      * an isolated KB view (private :class:`BM25` over its own docs),
      * an isolated episodic snapshot (a private list of :class:`Case`
        objects retrieved by simple substring scan — no shared index),
      * a private token budget, enforced before the backend call.

    Parameters
    ----------
    domain:
        Short tag (``'billing'``, ``'account'``, ``'technical'``, ...).
    base_model:
        Display name of the model in use (``'mock'``, ``'gpt-4o-mini'``,
        ...).  Stored for observability; not interpreted.
    kb:
        Domain-pre-filtered list of KB docs the subagent is allowed to
        see.  The executor takes a private copy so later mutation by the
        caller does not affect retrieval.
    episodic_snapshot:
        Domain-pre-filtered list of cases.  Same isolation contract as
        ``kb``: the caller may pass an empty list when the domain has no
        prior cases.
    token_budget:
        Maximum tokens (input + output) this subagent may consume.  The
        executor truncates context aggressively to stay within budget.
    max_context_chars:
        Hard cap on the concatenated context length fed to the backend.
        This is the lever that actually produces token savings vs a
        shared-context specialist.
    backend:
        Any :class:`LLMBackend`.  Defaults to a per-instance
        :class:`MockBackend` so tests stay hermetic.
    handoff_tau:
        Confidence threshold below which the subagent flags
        ``needs_handoff=True``.
    domain_kb_topics:
        Optional set of topics this domain "owns" — used to detect
        domain mismatch when none of the retrieved docs fall inside it
        (KB pre-filter assumed but defensive).
    """

    def __init__(
        self,
        domain: str,
        base_model: str,
        kb: Sequence[KBDoc],
        episodic_snapshot: Sequence[Case],
        token_budget: int = 2000,
        max_context_chars: int = 1500,
        backend: Optional[LLMBackend] = None,
        handoff_tau: float = _DEFAULT_HANDOFF_TAU,
        domain_kb_topics: Optional[Sequence[str]] = None,
    ) -> None:
        if not domain:
            raise ValueError("domain must be a non-empty string")
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if max_context_chars <= 0:
            raise ValueError("max_context_chars must be positive")
        self.domain = domain
        self.base_model = base_model
        # Defensive copies — the whole point of this class is isolation.
        self._kb_view = _IsolatedKBView(list(kb))
        self._episodic: List[Case] = list(episodic_snapshot)
        self.token_budget = int(token_budget)
        self.max_context_chars = int(max_context_chars)
        self.handoff_tau = float(handoff_tau)
        # Backend defaults: lazy import to avoid an import cycle if
        # someone wires a non-mock backend up later.
        if backend is None:
            from ..llm.mock import MockBackend
            backend = MockBackend(max_chars=max_context_chars)
        self.backend = backend
        # Topic membership map (None == no extra mismatch check).
        if domain_kb_topics is None:
            domain_kb_topics = DEFAULT_DOMAIN_TOPICS.get(domain, set())
        self._topics = set(domain_kb_topics) if domain_kb_topics else set()
        self._doc_topic = {d.doc_id: d.topic for d in self._kb_view.docs}
        # Observability counters
        self.n_calls = 0
        self.n_handoffs = 0
        self.n_budget_truncated = 0
        self.cumulative_tokens = 0
        # Per-instance UID for trace correlation
        self._uid = f"{domain}-{int(time.time() * 1000) % 1_000_000_000}"

    # ---- factory helper ----
    @classmethod
    def from_specialist_config(
        cls,
        domain: str,
        full_kb: Sequence[KBDoc],
        base_model: str = "mock",
        episodic_snapshot: Optional[Sequence[Case]] = None,
        token_budget: int = 2000,
        max_context_chars: int = 1500,
        backend: Optional[LLMBackend] = None,
    ) -> "SubagentExecutor":
        """Build a subagent from ``DEFAULT_DOMAIN_TOPICS`` membership.

        Slices ``full_kb`` down to the docs whose ``topic`` falls inside
        the domain's default topic set, then constructs the executor.
        ``episodic_snapshot`` defaults to an empty list — subagents in
        the Anthropic pattern start with a fresh context per ticket and
        rely on the orchestrator (not the subagent) to inject relevant
        past cases.
        """
        topics = set(DEFAULT_DOMAIN_TOPICS.get(domain, set()))
        if topics:
            docs = [d for d in full_kb if d.topic in topics]
        else:
            docs = list(full_kb)
        return cls(
            domain=domain,
            base_model=base_model,
            kb=docs,
            episodic_snapshot=list(episodic_snapshot or []),
            token_budget=token_budget,
            max_context_chars=max_context_chars,
            backend=backend,
            domain_kb_topics=topics or None,
        )

    # ---- main entrypoint ----
    def handle(self, sub_query: str) -> SubagentSummary:
        """Run the subagent end-to-end and return only a summary.

        Steps:
          1. Retrieve top-k passages from this subagent's *private* KB.
          2. Pull at most one related episodic case from the private
             snapshot (BM25 not needed at this scale — simple keyword
             overlap is enough and keeps the data structure auditable).
          3. Concatenate context up to ``max_context_chars``.
          4. Call ``backend.generate_answer``.
          5. Compute confidence (top-passage score, conservative).
          6. Decide ``needs_handoff`` from low confidence / empty KB /
             domain mismatch.
          7. Wrap in a :class:`SubagentSummary` (which truncates the
             answer to :data:`SUMMARY_MAX_CHARS`).
        """
        self.n_calls += 1
        try:
            return self._handle_inner(sub_query)
        except Exception as e:  # last-line defence
            return SubagentSummary(
                domain=self.domain,
                answer_summary="",
                confidence=0.0,
                needs_handoff=True,
                handoff_to=None,
                handoff_reason="executor_error",
                cited_doc_ids=[],
                token_budget_used=0,
                error=f"{type(e).__name__}: {e}",
            )

    def _handle_inner(self, sub_query: str) -> SubagentSummary:
        # 1) isolated KB retrieval
        passages = self._kb_view.retrieve(sub_query, top_k=3)

        # 2) at most one episodic hit from the private snapshot
        epi_passage = self._best_episodic(sub_query)
        all_passages: List[Passage] = list(passages)
        if epi_passage is not None:
            all_passages.append(epi_passage)

        # 3) enforce token budget via char truncation on the concatenated
        #    context.  This is what produces measurable context savings
        #    relative to a shared-context specialist.
        truncated_ctxs, truncated = self._truncate_to_budget(all_passages, sub_query)
        if truncated:
            self.n_budget_truncated += 1

        # 4) backend call (mock by default)
        answer = self.backend.generate_answer(sub_query, truncated_ctxs)

        # 5) confidence: top KB score, fall back to 0 if no docs
        kb_hits = [p for p in truncated_ctxs if p.source == "kb"]
        if kb_hits:
            confidence = max(p.score for p in kb_hits)
        else:
            confidence = 0.0
        # episodic evidence is a mild boost (matches v2.x policy spirit
        # without coupling to the calibrator)
        if epi_passage is not None and epi_passage in truncated_ctxs:
            confidence = min(1.0, confidence + 0.1)

        # 6) handoff decision
        needs_handoff = False
        handoff_to: Optional[str] = None
        handoff_reason: Optional[str] = None
        if not kb_hits:
            needs_handoff = True
            handoff_reason = "kb_empty"
        elif confidence < self.handoff_tau:
            needs_handoff = True
            handoff_reason = "low_confidence"
        else:
            # detect domain mismatch — top hit is in this domain's topic
            # set?  If we have no topic membership info, skip.
            if self._topics:
                top_doc_id = kb_hits[0].ref
                top_topic = self._doc_topic.get(top_doc_id)
                if top_topic and top_topic not in self._topics:
                    needs_handoff = True
                    handoff_reason = "domain_mismatch"
        if needs_handoff:
            self.n_handoffs += 1

        # 7) build the summary — note that we never put doc *text* into
        #    the summary.  Only ids leak.  That is the isolation contract.
        cited_ids = [p.ref for p in truncated_ctxs if p.source == "kb" and p.ref]
        tokens_in = _tokens_of(sub_query) + sum(
            _tokens_of(p.text) for p in truncated_ctxs
        )
        tokens_out = _tokens_of(answer)
        total_tokens = tokens_in + tokens_out
        self.cumulative_tokens += total_tokens

        return SubagentSummary(
            domain=self.domain,
            answer_summary=answer or "",
            confidence=confidence,
            needs_handoff=needs_handoff,
            handoff_to=handoff_to,
            handoff_reason=handoff_reason,
            cited_doc_ids=cited_ids,
            token_budget_used=total_tokens,
        )

    # ---- internals ----
    def _best_episodic(self, query: str) -> Optional[Passage]:
        if not self._episodic:
            return None
        q_tokens = {t.lower() for t in query.split() if t}
        best: Optional[Tuple[int, Case]] = None
        for c in self._episodic:
            tokens = {t.lower() for t in c.query.split() if t}
            overlap = len(q_tokens & tokens)
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, c)
        if best is None:
            return None
        _, case = best
        return Passage(
            source="episodic",
            text=case.resolution,
            score=0.5,  # constant — episodic evidence is supportive, not deciding
            ref=case.case_id,
            escalate_hint=case.should_escalate,
        )

    def _truncate_to_budget(
        self,
        passages: List[Passage],
        sub_query: str,
    ) -> Tuple[List[Passage], bool]:
        """Trim the passage list so the concatenated text fits within
        ``max_context_chars`` AND the rough token estimate fits within
        ``token_budget``.

        We keep passages in score order (already ranked by retrieve()),
        dropping from the tail once any cap would be exceeded.  Returns
        the kept list plus a flag indicating whether truncation
        happened.
        """
        kept: List[Passage] = []
        total_chars = 0
        # account for the query itself in the token budget
        running_tokens = _tokens_of(sub_query)
        # leave headroom for the output
        output_headroom = max(64, self.token_budget // 4)
        budget_in = max(1, self.token_budget - output_headroom)
        truncated = False
        for p in passages:
            ptext = p.text or ""
            p_chars = len(ptext)
            p_tokens = _tokens_of(ptext)
            if total_chars + p_chars > self.max_context_chars:
                # try to shrink this last passage to fit char budget
                remaining = self.max_context_chars - total_chars
                if remaining > 32:  # keep at least a meaningful chunk
                    shrunk = Passage(
                        source=p.source,
                        text=ptext[:remaining],
                        score=p.score,
                        ref=p.ref,
                        escalate_hint=p.escalate_hint,
                    )
                    kept.append(shrunk)
                    total_chars = self.max_context_chars
                    running_tokens += _tokens_of(shrunk.text)
                truncated = True
                break
            if running_tokens + p_tokens > budget_in:
                truncated = True
                break
            kept.append(p)
            total_chars += p_chars
            running_tokens += p_tokens
        return kept, truncated

    # ---- introspection ----
    def stats(self) -> Dict[str, int]:
        return {
            "domain": self.domain,
            "uid": self._uid,
            "n_calls": self.n_calls,
            "n_handoffs": self.n_handoffs,
            "n_budget_truncated": self.n_budget_truncated,
            "cumulative_tokens": self.cumulative_tokens,
            "kb_size": len(self._kb_view.docs),
            "episodic_size": len(self._episodic),
            "token_budget": self.token_budget,
        }


# ---- orchestrator-side helper ------------------------------------------------

def merge_subagent_summaries(
    summaries: Sequence[SubagentSummary],
) -> Dict[str, object]:
    """Deterministic merge of a list of :class:`SubagentSummary` objects.

    This is what an orchestrator does *after* fan-out: it sees the
    summaries (and only the summaries) and decides the final
    customer-facing artifact.  We do not (yet) re-run an LLM call here —
    the merge is structural so it is unit-testable in isolation.  A real
    orchestrator can wrap this and pass the merged artifact to a final
    LLM polish if desired.

    Returns
    -------
    A dict with:
        - ``answer``: joined customer-facing reply, prefixed per
          sub-question.
        - ``confidence``: min over sub-summaries (worst-case).
        - ``needs_handoff``: any subagent requested a handoff.
        - ``handoff_targets``: list of (domain, target) pairs.
        - ``cited_doc_ids``: union of all cited ids (deduped, order
          preserved).
        - ``total_tokens``: sum of ``token_budget_used``.
        - ``errors``: list of error strings (empty if none).
    """
    parts: List[str] = []
    confidences: List[float] = []
    needs_handoff = False
    handoff_targets: List[Tuple[str, Optional[str]]] = []
    cited: List[str] = []
    seen_cited = set()
    total_tokens = 0
    errors: List[str] = []
    for idx, s in enumerate(summaries, start=1):
        if s.error:
            errors.append(f"{s.domain}: {s.error}")
            parts.append(
                f"针对您的第 {idx} 个问题（{s.domain}）：处理时遇到错误，已转人工。"
            )
            confidences.append(0.0)
            needs_handoff = True
            continue
        parts.append(
            f"针对您的第 {idx} 个问题（{s.domain}）：\n{s.answer_summary}"
        )
        confidences.append(s.confidence)
        if s.needs_handoff:
            needs_handoff = True
            handoff_targets.append((s.domain, s.handoff_to))
        for did in s.cited_doc_ids:
            if did not in seen_cited:
                seen_cited.add(did)
                cited.append(did)
        total_tokens += s.token_budget_used
    return {
        "answer": "\n\n".join(parts) if parts else "",
        "confidence": min(confidences) if confidences else 0.0,
        "needs_handoff": needs_handoff,
        "handoff_targets": handoff_targets,
        "cited_doc_ids": cited,
        "total_tokens": total_tokens,
        "errors": errors,
    }


__all__ = ["SubagentExecutor", "merge_subagent_summaries"]
