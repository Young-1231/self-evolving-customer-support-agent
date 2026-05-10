"""Evolution experiment harness.

Runs three conditions side by side and evaluates the held-out eval set after
each training round, producing the self-evolution curves:

  - static    : memory never updates (cold-start agent) -> flat baseline
  - episodic  : only the experience pool accumulates
  - full      : experience pool + Reflector-induced playbooks

Training "learns" only from tickets the verifier marks unresolved: the human
resolution of a failed ticket is written into episodic memory. Eval queries are
held-out paraphrases (same group, different wording), so any improvement is
generalization, not memorization of eval text.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set

from ..config import Config
from ..data import KBDoc, Query, load_kb, load_queries, split_queries
from ..llm.base import LLMBackend
from ..memory.episodic import Case, EpisodicMemory
from ..memory.procedural import ProceduralMemory
from ..memory.semantic import SemanticMemory
from ..evolution.reflector import Reflector
from ..agent.support_agent import SupportAgent
from .metrics import aggregate, failed_groups
from .verifier import verify


def _batches(items: List[Query], n: int) -> List[List[Query]]:
    n = max(1, n)
    size = (len(items) + n - 1) // n
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


class Experiment:
    def __init__(self, cfg: Config, backend: LLMBackend):
        self.cfg = cfg
        self.backend = backend
        self.kb: List[KBDoc] = load_kb(cfg.kb_index)
        self.kb_topic = {d.doc_id: d.topic for d in self.kb}
        self.semantic = SemanticMemory(self.kb, score_norm_k=cfg.score_norm_k)
        qs = load_queries(cfg.queries)
        sp = split_queries(qs)
        self.train = sorted(sp.get("train", []), key=lambda q: q.id)
        self.eval = sorted(sp.get("eval", []), key=lambda q: q.id)

    def _topic_of(self, q: Query) -> str:
        for d in q.gold_doc_ids:
            if d in self.kb_topic:
                return self.kb_topic[d]
        hits = self.semantic.retrieve(q.query, top_k=1)
        if hits:
            return self.kb_topic.get(hits[0].ref, "general")
        return "general"

    def _evaluate(self, agent: SupportAgent, baseline_failed: Optional[Set[str]]):
        verdicts = [verify(q, agent.handle(q.query), self.cfg.coverage_threshold) for q in self.eval]
        return verdicts

    def run_condition(self, condition: str) -> List[Dict]:
        episodic = None if condition == "static" else EpisodicMemory(path=None, score_norm_k=self.cfg.score_norm_k)
        procedural = ProceduralMemory(path=None) if condition == "full" else None
        reflector = Reflector(min_cluster=self.cfg.reflect_min_cluster)
        agent = SupportAgent(self.cfg, self.backend, self.semantic, episodic, procedural)

        records: List[Dict] = []
        # round 0: cold start
        v0 = self._evaluate(agent, None)
        baseline_failed = failed_groups(v0)
        records.append({"round": 0, "learned_cases": 0, "playbooks": 0,
                        **aggregate(v0, baseline_failed)})

        batches = _batches(self.train, self.cfg.train_rounds)
        for r, batch in enumerate(batches, start=1):
            if condition != "static":
                for tq in batch:
                    res = agent.handle(tq.query)
                    v = verify(tq, res, self.cfg.coverage_threshold)
                    if not v.resolved:  # capture the human resolution as a lesson
                        episodic.add(Case(
                            case_id=tq.id, query=tq.query, resolution=tq.resolution,
                            should_escalate=tq.should_escalate, topic=self._topic_of(tq),
                            source_query_id=tq.id, learned_round=r,
                        ))
                if condition == "full":
                    reflector.reflect(episodic.cases, procedural, r)
            v = self._evaluate(agent, baseline_failed)
            records.append({
                "round": r,
                "learned_cases": len(episodic) if episodic else 0,
                "playbooks": len(procedural) if procedural else 0,
                **aggregate(v, baseline_failed),
            })
        return records

    def run(self, conditions: Optional[List[str]] = None) -> Dict[str, List[Dict]]:
        conditions = conditions or ["static", "episodic", "full"]
        return {c: self.run_condition(c) for c in conditions}


def save_results(results: Dict[str, List[Dict]], workdir: str) -> str:
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, "metrics.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return path
