"""The self-evolving customer-support agent (online inference path).

Per query it runs a self-RAG style loop:
  1. retrieve from semantic (KB) + episodic (experience) + procedural (playbooks)
  2. synthesize an answer grounded in the retrieved context (LLM backend)
  3. critic estimates confidence
  4. decide whether to escalate to a human, using (in priority order)
       a trusted past case > a fired playbook rule > uncertainty threshold

The agent's behavior changes over time *only* because its memory changes; the
model weights are never touched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..config import Config
from ..llm.base import LLMBackend, Passage
from ..memory.episodic import EpisodicMemory
from ..memory.procedural import ProceduralMemory
from ..memory.semantic import SemanticMemory
from .critic import Critic


@dataclass
class AgentResult:
    query: str
    answer: str
    escalate: bool
    confidence: float
    contexts: List[Passage] = field(default_factory=list)
    used_sources: List[str] = field(default_factory=list)
    guardrail: object = None      # optional GuardrailReport (output stage)
    trace_id: Optional[str] = None


class SupportAgent:
    def __init__(
        self,
        cfg: Config,
        backend: LLMBackend,
        semantic: SemanticMemory,
        episodic: Optional[EpisodicMemory] = None,
        procedural: Optional[ProceduralMemory] = None,
        guardrail: object = None,   # optional seagent.guardrails.GuardrailPipeline
        tracer: object = None,      # optional seagent.obs.Tracer
        calibrator: object = None,  # optional seagent.calibration.DomainCalibrator
    ):
        self.cfg = cfg
        self.backend = backend
        self.semantic = semantic
        self.episodic = episodic
        self.procedural = procedural
        self.guardrail = guardrail
        self.tracer = tracer
        # Per-domain threshold calibration.  When ``None`` (default) the agent
        # falls back to cfg.escalate_tau / cfg.kb_conf_cap, i.e. behaviour is
        # bit-for-bit identical to the pre-calibration code path.
        self.calibrator = calibrator
        self.critic = Critic(cfg, backend)
        self._turn = 0

    def handle(self, query: str) -> AgentResult:
        """Plain self-RAG path. Guardrails/tracing run only if wired in
        (production path), so the controlled-ablation harness stays untouched."""
        if self.guardrail is None and self.tracer is None:
            return self._handle_core(query)
        return self._handle_observed(query)

    # --- core reasoning (unchanged behaviour) ---
    def _retrieve(self, query: str):
        contexts: List[Passage] = []
        contexts.extend(self.semantic.retrieve(query, top_k=self.cfg.kb_top_k))
        epi: List[Passage] = []
        if self.episodic is not None and len(self.episodic):
            epi = self.episodic.retrieve(query, top_k=self.cfg.epi_top_k)
            contexts.extend(epi)
        pb: List[Passage] = []
        if self.procedural is not None and len(self.procedural):
            pb = self.procedural.retrieve(query)
            contexts.extend(pb)
        return contexts, epi, pb

    def _handle_core(self, query: str) -> AgentResult:
        contexts, epi, pb = self._retrieve(query)
        answer = self.backend.generate_answer(query, contexts)
        kb_hits = [p for p in contexts if p.source == "kb"]
        thresholds = self._effective_thresholds(query, kb_hits)
        conf = self._confidence_with(thresholds, query, answer, contexts)
        escalate = self._decide_escalation(conf, epi, pb, thresholds=thresholds)
        return AgentResult(
            query=query, answer=answer, escalate=escalate, confidence=conf,
            contexts=contexts, used_sources=sorted({p.source for p in contexts}),
        )

    # --- production path: input/output guardrails + per-stage tracing ---
    def _handle_observed(self, query: str) -> AgentResult:
        self._turn += 1
        tr = self.tracer
        trace = tr.start_turn(turn=self._turn, query=query, model=getattr(self.cfg, "model", "")) if tr is not None else None
        trace_id = getattr(trace, "trace_id", None)

        # input guardrail: block prompt-injection, redact PII before anything else
        gverdict, gblocked = "allow", False
        if self.guardrail is not None:
            inp = self.guardrail.check_input(query)
            if getattr(inp, "blocked", False):
                if tr is not None:
                    tr.end_turn(confidence=0.0, escalate=True,
                                guardrail_verdict=str(getattr(inp, "action", "block")).lower(),
                                guardrail_blocked=True)
                return AgentResult(
                    query=query, escalate=True, confidence=0.0,
                    answer="抱歉，您的请求无法处理，已为您转接人工客服。",
                    guardrail=inp, trace_id=trace_id,
                )
            query_safe = getattr(inp, "redacted_text", "") or query
        else:
            query_safe = query

        if tr is not None:
            with tr.span("retrieval"):
                contexts, epi, pb = self._retrieve(query_safe)
            tr.set_hits(contexts)
            with tr.span("generation"):
                answer = self.backend.generate_answer(query_safe, contexts)
            kb_hits = [p for p in contexts if p.source == "kb"]
            thresholds = self._effective_thresholds(query_safe, kb_hits)
            with tr.span("critic"):
                conf = self._confidence_with(thresholds, query_safe, answer, contexts)
        else:
            contexts, epi, pb = self._retrieve(query_safe)
            answer = self.backend.generate_answer(query_safe, contexts)
            kb_hits = [p for p in contexts if p.source == "kb"]
            thresholds = self._effective_thresholds(query_safe, kb_hits)
            conf = self._confidence_with(thresholds, query_safe, answer, contexts)

        escalate = self._decide_escalation(conf, epi, pb, thresholds=thresholds)
        out_report = None
        if self.guardrail is not None:
            if tr is not None:
                with tr.span("guardrail"):
                    out_report = self.guardrail.check_output(answer, contexts)
            else:
                out_report = self.guardrail.check_output(answer, contexts)
            if getattr(out_report, "redacted_answer", ""):
                answer = out_report.redacted_answer
            action = str(getattr(out_report, "action", "ALLOW")).upper()
            if action in ("ESCALATE", "BLOCK"):
                escalate = True
            gverdict = action.lower()
            gblocked = bool(getattr(out_report, "blocked", False))

        if tr is not None:
            in_tok, out_tok, cost = self._usage(query_safe, contexts, answer)
            tr.set_usage(in_tok, out_tok, cost)
            tr.end_turn(confidence=conf, escalate=escalate,
                        guardrail_verdict=gverdict, guardrail_blocked=gblocked)

        return AgentResult(
            query=query, answer=answer, escalate=escalate, confidence=conf,
            contexts=contexts, used_sources=sorted({p.source for p in contexts}),
            guardrail=out_report, trace_id=trace_id,
        )

    def _usage(self, query: str, contexts: List[Passage], answer: str):
        try:
            from ..obs import estimate_tokens, estimate_cost
            in_tok = estimate_tokens(query + "".join(p.text for p in contexts))
            out_tok = estimate_tokens(answer)
            return in_tok, out_tok, estimate_cost(getattr(self.cfg, "model", ""), in_tok, out_tok)
        except Exception:
            return 0, 0, 0.0

    def _decide_escalation(
        self,
        conf: float,
        epi: List[Passage],
        pb: List[Passage],
        thresholds: Optional[dict] = None,
    ) -> bool:
        # 1) a strongly-matched past case is the most trusted signal
        strong = [p for p in epi if p.score >= self.cfg.epi_trust_tau]
        if strong:
            best = max(strong, key=lambda p: p.score)
            return bool(best.escalate_hint)
        # 2) otherwise, a *well-matched* playbook rule (high trigger overlap only,
        #    so a topic-level rule never hijacks an unrelated query)
        fired = [p for p in pb if p.score >= self.cfg.playbook_fire_tau]
        if fired:
            best_pb = max(fired, key=lambda p: p.score)
            return bool(best_pb.escalate_hint)  # rule decides: escalate or handle
        # 3) cold start / unsure -> ask a human.  When a per-domain calibrator
        #    is wired in, it can lower (or raise) the bar for this domain.
        tau = (thresholds or {}).get("escalate_tau", self.cfg.escalate_tau)
        return conf < tau

    # ----- per-domain threshold plumbing (no-op when calibrator is None) -----
    def _effective_thresholds(self, query: str, kb_hits: List[Passage]) -> dict:
        """Return the {escalate_tau, kb_conf_cap} pair to use for this query.

        When no calibrator is wired in, returns the cfg defaults so the rest
        of the agent code is uniform regardless of calibration state.
        """
        if self.calibrator is None:
            return {
                "escalate_tau": self.cfg.escalate_tau,
                "kb_conf_cap": self.cfg.kb_conf_cap,
            }
        try:
            from ..calibration import infer_domain
            domain = infer_domain(query, kb_hits)
            return self.calibrator.get_thresholds(domain)
        except Exception:
            # Calibration is strictly a best-effort optimisation; if anything
            # blows up we fall back to cfg defaults (i.e. uncalibrated path).
            return {
                "escalate_tau": self.cfg.escalate_tau,
                "kb_conf_cap": self.cfg.kb_conf_cap,
            }

    def _confidence_with(
        self,
        thresholds: dict,
        query: str,
        answer: str,
        contexts: List[Passage],
    ) -> float:
        """Run the critic with a (possibly per-domain) kb_conf_cap.

        We can't easily inject a per-call cap into Critic, so when the
        thresholds differ from cfg we briefly swap critic.cfg with a
        shallow copy carrying the override.  Each handle() call runs on a
        single thread for one agent instance (the stress harness gives
        each worker its own agent via agent_factory), so the swap is
        thread-local in practice.
        """
        cap_override = thresholds.get("kb_conf_cap", self.cfg.kb_conf_cap)
        if cap_override == self.cfg.kb_conf_cap or self.calibrator is None:
            return self.critic.confidence(query, answer, contexts)
        from dataclasses import replace
        try:
            local_cfg = replace(self.cfg, kb_conf_cap=float(cap_override))
        except Exception:
            return self.critic.confidence(query, answer, contexts)
        prev = self.critic.cfg
        self.critic.cfg = local_cfg
        try:
            return self.critic.confidence(query, answer, contexts)
        finally:
            self.critic.cfg = prev
