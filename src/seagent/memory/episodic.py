"""Episodic memory = the experience pool of past resolved/escalated cases.

Cases are appended as the agent learns (a failed training ticket plus its human
resolution). Retrieval matches the *incoming query* against stored case queries
(paraphrase matching via BM25) and surfaces the stored resolution as context,
together with the recorded escalation outcome. This is case-based reasoning in
the spirit of Memento (arXiv 2508.16153) and A-MEM (arXiv 2502.12110).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import List, Optional

from ..llm.base import Passage
from .bm25 import BM25


@dataclass
class Case:
    case_id: str
    query: str            # the (training) user query that produced this lesson
    resolution: str       # the human/ground-truth resolution text
    should_escalate: bool
    topic: str = "general"
    source_query_id: str = ""
    learned_round: int = 0


def _norm(score: float, k: float) -> float:
    return score / (score + k) if score > 0 else 0.0


class EpisodicMemory:
    def __init__(self, path: Optional[str] = None, score_norm_k: float = 6.0):
        self.path = path
        self._k = score_norm_k
        self.cases: List[Case] = []
        self._bm25: Optional[BM25] = None
        if path and os.path.exists(path):
            self._load()

    # --- persistence ---
    def _load(self) -> None:
        self.cases = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.cases.append(Case(**json.loads(line)))
        self._reindex()

    def _persist(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for c in self.cases:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    def _reindex(self) -> None:
        self._bm25 = BM25([c.query for c in self.cases]) if self.cases else None

    # --- api ---
    def add(self, case: Case) -> None:
        self.cases.append(case)
        self._reindex()
        self._persist()

    def __len__(self) -> int:
        return len(self.cases)

    def retrieve(self, query: str, top_k: int = 3) -> List[Passage]:
        if not self._bm25:
            return []
        out: List[Passage] = []
        for idx, score in self._bm25.search(query, top_k=top_k):
            c = self.cases[idx]
            out.append(
                Passage(
                    source="episodic",
                    text=c.resolution,
                    score=_norm(score, self._k),
                    ref=c.case_id,
                    escalate_hint=c.should_escalate,
                )
            )
        return out
