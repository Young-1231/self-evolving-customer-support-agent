"""Pure-Python BM25 retriever with a Chinese-friendly tokenizer.

No third-party dependencies: works for mixed Chinese/English text by emitting
ascii word tokens plus CJK unigrams and bigrams. This keeps the whole project
runnable in a clean environment (and deterministic, which the offline mock
backend and CI rely on).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Sequence, Tuple

_ASCII = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> List[str]:
    """Tokenize mixed CN/EN text into retrieval tokens.

    - ascii runs (lowercased) -> word tokens
    - each run of CJK characters -> unigrams + character bigrams
    """
    text = (text or "").lower()
    tokens: List[str] = []
    tokens.extend(_ASCII.findall(text))
    for run in _CJK_RUN.findall(text):
        tokens.extend(run)  # unigrams
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))  # bigrams
    return tokens


class BM25:
    """Classic Okapi BM25 over a fixed corpus of pre-tokenized documents."""

    def __init__(self, corpus_tokens: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: List[List[str]] = [list(d) for d in corpus_tokens]
        self.N = len(self.docs)
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        self.tf: List[Counter] = [Counter(d) for d in self.docs]
        df: Counter = Counter()
        for tfi in self.tf:
            df.update(tfi.keys())
        # BM25+ style idf, floored at a small positive value to avoid negatives.
        self.idf = {
            t: max(1e-6, math.log(1 + (self.N - n + 0.5) / (n + 0.5)))
            for t, n in df.items()
        }

    def score(self, query_tokens: Sequence[str], idx: int) -> float:
        if not self.doc_len[idx]:
            return 0.0
        tfi = self.tf[idx]
        dl = self.doc_len[idx]
        s = 0.0
        for t in set(query_tokens):
            f = tfi.get(t, 0)
            if not f:
                continue
            idf = self.idf.get(t, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
            s += idf * (f * (self.k1 + 1)) / denom
        return s

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        q = tokenize(query)
        scored = [(i, self.score(q, i)) for i in range(self.N)]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return [(i, s) for i, s in scored[:top_k] if s > 0.0]
