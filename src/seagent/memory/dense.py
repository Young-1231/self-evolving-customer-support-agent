"""Alternative retrievers for the retrieval-method ablation.

The point of this module is to show that the self-evolution result ("experience
accumulation -> higher resolution rate") does *not* depend on BM25 in
particular. We provide drop-in replacements for ``SemanticMemory`` (same
interface: ``retrieve(query, top_k) -> List[Passage]``) backed by different
retrieval mechanisms:

  - ``TfidfCosineRetriever`` : pure-Python character n-gram TF-IDF + cosine
    similarity. Zero third-party deps, deterministic. Stands in as a
    "dense-style" (vector) baseline that is fully offline.
  - ``EmbeddingRetriever``   : real sentence-transformer embeddings + cosine,
    used only if ``sentence-transformers`` is already installed (try-import).
    No model is downloaded eagerly; if the package or model is unavailable the
    caller is expected to skip this condition.
  - ``HybridRetriever``      : late-fusion of BM25 and TF-IDF-cosine scores.

All of them return ``List[Passage]`` with the configured ``source`` tag so the
same ``SupportAgent`` works unchanged.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from ..data import KBDoc
from ..llm.base import Passage
from .bm25 import BM25, tokenize


def _norm(score: float, k: float) -> float:
    return score / (score + k) if score > 0 else 0.0


# --------------------------------------------------------------------------- #
# character n-gram TF-IDF vectors                                             #
# --------------------------------------------------------------------------- #
def char_ngrams(text: str, n_min: int = 2, n_max: int = 4) -> List[str]:
    """Character n-grams over a normalized form, plus word tokens.

    Mixing word tokens (from the shared CN/EN tokenizer) with character n-grams
    makes the similarity robust to paraphrase: the eval queries are paraphrases
    of the training tickets, so surface overlap is partial.
    """
    base = (text or "").lower()
    grams: List[str] = list(tokenize(base))  # reuse CN/EN word + CJK handling
    compact = "".join(base.split())
    for n in range(n_min, n_max + 1):
        if len(compact) < n:
            continue
        grams.extend(compact[i : i + n] for i in range(len(compact) - n + 1))
    return grams


class _TfidfIndex:
    """A small in-memory TF-IDF index with cosine search over fixed docs."""

    def __init__(self, raw_docs: Sequence[str]):
        self.docs_grams: List[Counter] = [Counter(char_ngrams(d)) for d in raw_docs]
        self.N = len(self.docs_grams)
        df: Counter = Counter()
        for c in self.docs_grams:
            df.update(c.keys())
        # smoothed idf, always positive
        self.idf: Dict[str, float] = {
            t: math.log((1 + self.N) / (1 + n)) + 1.0 for t, n in df.items()
        }
        self.doc_vecs: List[Dict[str, float]] = [self._vec(c) for c in self.docs_grams]
        self.doc_norms: List[float] = [
            math.sqrt(sum(w * w for w in v.values())) for v in self.doc_vecs
        ]

    def _vec(self, grams: Counter) -> Dict[str, float]:
        # sublinear tf weighting
        return {t: (1.0 + math.log(f)) * self.idf.get(t, 0.0) for t, f in grams.items()}

    def search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        if not self.N:
            return []
        qv = self._vec(Counter(char_ngrams(query)))
        qn = math.sqrt(sum(w * w for w in qv.values()))
        if qn == 0.0:
            return []
        scored: List[Tuple[int, float]] = []
        for i, dv in enumerate(self.doc_vecs):
            dn = self.doc_norms[i]
            if dn == 0.0:
                continue
            # iterate over the smaller vector
            small, big = (qv, dv) if len(qv) <= len(dv) else (dv, qv)
            dot = sum(w * big.get(t, 0.0) for t, w in small.items())
            if dot <= 0.0:
                continue
            scored.append((i, dot / (qn * dn)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]


class TfidfCosineRetriever:
    """SemanticMemory-compatible retriever over a KB, using TF-IDF + cosine."""

    name = "tfidf_cosine"

    def __init__(self, docs: List[KBDoc], source: str = "kb"):
        self.docs = docs
        self.source = source
        self._index = _TfidfIndex([f"{d.title} {d.text}" for d in docs] or [""])

    def retrieve(self, query: str, top_k: int = 4) -> List[Passage]:
        out: List[Passage] = []
        for idx, sim in self._index.search(query, top_k=top_k):
            if idx >= len(self.docs):
                continue
            d = self.docs[idx]
            out.append(Passage(source=self.source, text=d.text, score=float(sim), ref=d.doc_id))
        return out


class HybridRetriever:
    """Late-fusion of BM25 and TF-IDF-cosine over the same KB.

    Final score = w_bm25 * norm(bm25) + w_tfidf * cosine, both already in [0,1].
    """

    name = "hybrid"

    def __init__(
        self,
        docs: List[KBDoc],
        source: str = "kb",
        score_norm_k: float = 6.0,
        w_bm25: float = 0.5,
        w_tfidf: float = 0.5,
    ):
        self.docs = docs
        self.source = source
        self._k = score_norm_k
        self.w_bm25 = w_bm25
        self.w_tfidf = w_tfidf
        texts = [f"{d.title} {d.text}" for d in docs] or [""]
        self._bm25 = BM25(texts)
        self._tfidf = _TfidfIndex(texts)

    def retrieve(self, query: str, top_k: int = 4) -> List[Passage]:
        fused: Dict[int, float] = {}
        # pull a wider candidate pool from each arm, then re-rank by fused score
        pool = max(top_k * 3, top_k)
        for idx, s in self._bm25.search(query, top_k=pool):
            fused[idx] = fused.get(idx, 0.0) + self.w_bm25 * _norm(s, self._k)
        for idx, sim in self._tfidf.search(query, top_k=pool):
            fused[idx] = fused.get(idx, 0.0) + self.w_tfidf * float(sim)
        ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:top_k]
        out: List[Passage] = []
        for idx, score in ranked:
            if idx >= len(self.docs) or score <= 0.0:
                continue
            d = self.docs[idx]
            out.append(Passage(source=self.source, text=d.text, score=float(score), ref=d.doc_id))
        return out


# --------------------------------------------------------------------------- #
# optional real embeddings (only if sentence-transformers is installed)       #
# --------------------------------------------------------------------------- #
def sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


class EmbeddingRetriever:
    """SemanticMemory-compatible retriever using real sentence embeddings.

    Constructing this requires ``sentence-transformers`` to be importable *and*
    the model to load. Both can fail in an offline environment; callers should
    guard with ``sentence_transformers_available()`` and catch exceptions to
    skip the condition (we never download a model implicitly here -- whatever
    the local cache resolves ``model_name`` to is what gets used).
    """

    name = "embedding"

    def __init__(
        self,
        docs: List[KBDoc],
        model_name: str = "all-MiniLM-L6-v2",
        source: str = "kb",
    ):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.docs = docs
        self.source = source
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        texts = [f"{d.title} {d.text}" for d in docs] or [""]
        self._emb = self._encode(texts)

    def _encode(self, texts: Sequence[str]):
        return self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )

    def retrieve(self, query: str, top_k: int = 4) -> List[Passage]:
        import numpy as np  # type: ignore

        q = self._encode([query])[0]
        sims = self._emb @ q  # normalized -> cosine
        order = np.argsort(-sims)[:top_k]
        out: List[Passage] = []
        for idx in order:
            i = int(idx)
            if i >= len(self.docs):
                continue
            d = self.docs[i]
            out.append(Passage(source=self.source, text=d.text, score=float(sims[i]), ref=d.doc_id))
        return out
