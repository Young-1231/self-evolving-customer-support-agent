"""Semantic memory = the static knowledge base, retrieved with BM25."""
from __future__ import annotations

from typing import List

from ..data import KBDoc
from ..llm.base import Passage
from .bm25 import BM25


def _norm(score: float, k: float) -> float:
    return score / (score + k) if score > 0 else 0.0


class SemanticMemory:
    def __init__(self, docs: List[KBDoc], score_norm_k: float = 6.0):
        self.docs = docs
        self._k = score_norm_k
        self._bm25 = BM25([f"{d.title} {d.text}" for d in docs] or [""])

    def retrieve(self, query: str, top_k: int = 4) -> List[Passage]:
        out: List[Passage] = []
        for idx, score in self._bm25.search(query, top_k=top_k):
            d = self.docs[idx]
            out.append(Passage(source="kb", text=d.text, score=_norm(score, self._k), ref=d.doc_id))
        return out
