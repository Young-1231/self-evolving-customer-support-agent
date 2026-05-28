"""Filesystem-backed episodic case store (v2.5 R3).

Borrows the OpenViking (volcengine, 24.8k stars, 2026-05) paradigm of using a
directory hierarchy as the agent's context database.  The flat jsonl pool used
by :class:`seagent.memory.episodic.EpisodicMemory` works fine up to ~1k cases
but degrades past that — BM25 over a single flat corpus becomes both slow and
noisy.  OpenViking reports tau2-bench retail +6.87pp / airline +11.87pp from
re-organizing the experience pool into a layered filesystem:

    L0  (coarse)   topic-level directories         (billing / account / ...)
    L1  (medium)   time buckets or sub-topics       (2026-05 / refund / ...)
    L2  (fine)     individual case markdown files   (L2_<case_id>.md)

Retrieval narrows L0 → L1 → L2, with BM25 used only at the leaf set.  This
file is a pure-stdlib re-implementation of that idea tailored to the
:class:`Case` dataclass already used by SEA's episodic memory; no third-party
dependency, no LLM call, no network.

The public API mirrors :class:`EpisodicMemory` (``add``, ``retrieve``,
``__len__``) so it can be dropped into ``SupportAgent`` / ``Experiment`` as a
1:1 substitute.  We do NOT modify any existing src/seagent file.

The on-disk layout is intentionally git-friendly: each case is a small
markdown file with YAML-ish frontmatter, so diffs, code review and human
audit work out of the box.

Reference (OpenViking self-reported numbers, research/05_2026_github_radar.md):
    tau2-bench retail: +6.87pp ; airline: +11.87pp
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..llm.base import Passage
from .bm25 import BM25, tokenize
from .episodic import Case

__all__ = ["FsEpisodicStore", "parse_markdown_case", "render_markdown_case"]


# ---------------------------------------------------------------------------
# small markdown frontmatter parser (independent of v2.2 skills.format to keep
# this module standalone — no cross-module coupling)
# ---------------------------------------------------------------------------

_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def _coerce(raw: str):
    """Best-effort scalar coercion (int / bool / quoted-string / bare)."""
    s = raw.strip()
    if not s:
        return ""
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    try:
        if s.lstrip("-").isdigit():
            return int(s)
    except Exception:
        pass
    try:
        return float(s) if any(ch in s for ch in ".eE") and s.replace(".", "", 1).lstrip("-").isdigit() else s
    except Exception:
        return s


def parse_markdown_case(text: str) -> Tuple[Dict[str, object], str]:
    """Split ``text`` into (frontmatter_dict, body)."""
    m = _FRONT.match(text)
    if not m:
        return {}, text
    fm: Dict[str, object] = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        km = _KV.match(line)
        if not km:
            continue
        fm[km.group(1)] = _coerce(km.group(2))
    return fm, text[m.end():]


def render_markdown_case(case: Case) -> str:
    """Render a Case as markdown-with-frontmatter."""
    fm_lines = [
        "---",
        f"case_id: {case.case_id}",
        f"topic: {case.topic}",
        f"should_escalate: {'true' if case.should_escalate else 'false'}",
        f"source_query_id: {case.source_query_id}",
        f"learned_round: {case.learned_round}",
        "---",
    ]
    body = [
        "# Case " + case.case_id,
        "",
        "## Query",
        "",
        case.query,
        "",
        "## Resolution",
        "",
        case.resolution,
        "",
    ]
    return "\n".join(fm_lines + [""] + body)


# ---------------------------------------------------------------------------
# Filesystem store
# ---------------------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9_\-一-鿿]+")


def _safe(name: str, fallback: str = "unknown") -> str:
    name = (name or "").strip()
    if not name:
        return fallback
    s = _SAFE.sub("_", name).strip("_")
    return s or fallback


def _norm(score: float, k: float) -> float:
    return score / (score + k) if score > 0 else 0.0


VALID_SCHEMES = ("topic_date", "topic_subtopic", "flat")


class FsEpisodicStore:
    """Drop-in replacement for :class:`EpisodicMemory` backed by a directory.

    Parameters
    ----------
    root_dir:
        Filesystem root.  Created lazily on first ``add``.  ``None`` keeps the
        store fully in memory (handy for tests / ablation runs).
    scheme:
        - ``topic_date``    L0 = case.topic, L1 = ``YYYY-MM`` from
          ``metadata.created_at`` (fallback: ``round_{learned_round:03d}``).
        - ``topic_subtopic`` L0 = case.topic, L1 = ``metadata.subtopic`` or
          first KB doc id under that topic.
        - ``flat``          L0 = ``L0_all``, L1 = ``L1_all``.  This is the
          sanity-check baseline that should match the legacy jsonl store.
    score_norm_k:
        Same normalization constant as :class:`EpisodicMemory`.
    l0_top:
        Number of L0 directories to keep after the coarse filter.  ``None``
        means "all" (degenerates to legacy behaviour but keeps the bookkeeping).
    """

    def __init__(
        self,
        root_dir: Optional[str] = None,
        scheme: str = "topic_date",
        score_norm_k: float = 6.0,
        l0_top: Optional[int] = 2,
    ):
        if scheme not in VALID_SCHEMES:
            raise ValueError(f"unknown scheme {scheme!r}; expected one of {VALID_SCHEMES}")
        self.root_dir = root_dir
        self.scheme = scheme
        self._k = score_norm_k
        self._l0_top = l0_top
        self.cases: List[Case] = []
        # Side metadata kept alongside each case (created_at, subtopic, ...)
        self._meta: List[Dict[str, object]] = []
        # path of the rendered markdown file (None if root_dir is None)
        self._paths: List[Optional[str]] = []
        # bucket assignment per case as (l0, l1)
        self._buckets: List[Tuple[str, str]] = []
        # L0 -> [case index, ...]; L0+L1 -> [case index, ...]
        self._l0_index: Dict[str, List[int]] = {}
        self._l1_index: Dict[Tuple[str, str], List[int]] = {}
        # per-L1 BM25 caches; invalidated on add
        self._bm25_cache: Dict[Tuple[str, str], BM25] = {}
        # L0-level BM25 over a single "L0 super-doc" (concat of all case
        # queries) — gives a content-aware coarse filter that does not rely
        # on the L0 directory name matching the query language.
        self._l0_bm25: Optional[BM25] = None
        self._l0_order: List[str] = []
        if root_dir and os.path.isdir(root_dir):
            self._load()

    # ------------------------------------------------------------------ #
    # bucket inference
    # ------------------------------------------------------------------ #
    def _infer_buckets(self, case: Case, meta: Dict[str, object]) -> Tuple[str, str]:
        if self.scheme == "flat":
            return ("all", "all")
        l0 = _safe(case.topic or "general", fallback="general")
        if self.scheme == "topic_date":
            ca = str(meta.get("created_at") or "").strip()
            if len(ca) >= 7 and ca[4] == "-":
                l1 = ca[:7]
            else:
                l1 = f"round_{int(case.learned_round):03d}"
            return (l0, _safe(l1))
        # topic_subtopic
        sub = str(meta.get("subtopic") or "").strip() or _safe(case.source_query_id or "misc")
        return (l0, _safe(sub))

    # ------------------------------------------------------------------ #
    # persistence
    # ------------------------------------------------------------------ #
    def _case_path(self, l0: str, l1: str, case_id: str) -> str:
        assert self.root_dir is not None
        d = os.path.join(self.root_dir, f"L0_{l0}", f"L1_{l1}")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"L2_{_safe(case_id, fallback='case')}.md")

    def _persist(self, idx: int) -> None:
        if not self.root_dir:
            self._paths[idx] = None
            return
        l0, l1 = self._buckets[idx]
        p = self._case_path(l0, l1, self.cases[idx].case_id)
        with open(p, "w", encoding="utf-8") as f:
            f.write(render_markdown_case(self.cases[idx]))
        self._paths[idx] = p

    def _load(self) -> None:
        self.cases = []
        self._meta = []
        self._paths = []
        self._buckets = []
        self._l0_index = {}
        self._l1_index = {}
        self._bm25_cache = {}
        assert self.root_dir is not None
        # iterate L0_*/L1_*/L2_*.md
        for l0_dir in sorted(os.listdir(self.root_dir)):
            l0_path = os.path.join(self.root_dir, l0_dir)
            if not (os.path.isdir(l0_path) and l0_dir.startswith("L0_")):
                continue
            for l1_dir in sorted(os.listdir(l0_path)):
                l1_path = os.path.join(l0_path, l1_dir)
                if not (os.path.isdir(l1_path) and l1_dir.startswith("L1_")):
                    continue
                for fn in sorted(os.listdir(l1_path)):
                    if not (fn.startswith("L2_") and fn.endswith(".md")):
                        continue
                    fp = os.path.join(l1_path, fn)
                    with open(fp, "r", encoding="utf-8") as f:
                        text = f.read()
                    fm, body = parse_markdown_case(text)
                    case = Case(
                        case_id=str(fm.get("case_id") or fn[3:-3]),
                        query=_extract_section(body, "Query"),
                        resolution=_extract_section(body, "Resolution"),
                        should_escalate=bool(fm.get("should_escalate", False)),
                        topic=str(fm.get("topic") or "general"),
                        source_query_id=str(fm.get("source_query_id") or ""),
                        learned_round=int(fm.get("learned_round") or 0),
                    )
                    self.cases.append(case)
                    self._meta.append({})
                    self._paths.append(fp)
                    l0 = l0_dir[len("L0_"):]
                    l1 = l1_dir[len("L1_"):]
                    self._buckets.append((l0, l1))
                    self._l0_index.setdefault(l0, []).append(len(self.cases) - 1)
                    self._l1_index.setdefault((l0, l1), []).append(len(self.cases) - 1)

    # ------------------------------------------------------------------ #
    # public api
    # ------------------------------------------------------------------ #
    def add(self, case: Case, metadata: Optional[Dict[str, object]] = None) -> None:
        meta = dict(metadata or {})
        self.cases.append(case)
        self._meta.append(meta)
        self._paths.append(None)
        l0, l1 = self._infer_buckets(case, meta)
        self._buckets.append((l0, l1))
        idx = len(self.cases) - 1
        self._l0_index.setdefault(l0, []).append(idx)
        self._l1_index.setdefault((l0, l1), []).append(idx)
        # invalidate the BM25 cache for that leaf and the L0 super-index
        self._bm25_cache.pop((l0, l1), None)
        self._l0_bm25 = None
        self._persist(idx)

    def _ensure_l0_bm25(self) -> None:
        """Build a corpus where each "document" is the concatenation of all
        case queries inside one L0 directory.  This lets the coarse filter
        score L0 buckets by content, not just by dir-name overlap.  Matches
        EpisodicMemory's raw-string-into-BM25 convention so behaviour stays
        consistent.
        """
        if self._l0_bm25 is not None:
            return
        order = sorted(self._l0_index.keys())
        docs = []
        for l0 in order:
            # Concatenated raw query string (BM25 will char-tokenize it,
            # same as the leaf-level BM25 — keeps scoring scale uniform).
            docs.append(" ".join(self.cases[i].query for i in self._l0_index[l0]))
        self._l0_bm25 = BM25(docs)
        self._l0_order = order

    def __len__(self) -> int:
        return len(self.cases)

    # ---- L0 / L1 selection ---------------------------------------------- #
    def _score_l0(self, query: str, query_tokens: Sequence[str]) -> List[Tuple[str, float]]:
        """Rank L0 buckets by (a) content BM25 against the per-L0 super-doc
        and (b) dir-name token overlap, then a size tie-breaker."""
        if not self._l0_index:
            return []
        self._ensure_l0_bm25()
        assert self._l0_bm25 is not None
        # content score from L0 super-corpus
        content_scores = {self._l0_order[i]: s
                          for i, s in self._l0_bm25.search(query, top_k=len(self._l0_order))}
        qset = set(query_tokens)
        scored: List[Tuple[str, float]] = []
        for l0, idxs in self._l0_index.items():
            l0_tokens = set(tokenize(l0))
            name_overlap = len(qset & l0_tokens)
            content = content_scores.get(l0, 0.0)
            scored.append((l0, content + name_overlap + 1e-3 * len(idxs)))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored

    def _select_l0(self, query: str, query_tokens: Sequence[str]) -> List[str]:
        ranked = self._score_l0(query, query_tokens)
        if self._l0_top is None or self._l0_top <= 0:
            return [l0 for l0, _ in ranked]
        # Always include L0s with non-zero content score; pad with dir-name
        # overlap; finally pad with the largest buckets.
        keep: List[str] = []
        for l0, score in ranked:
            # 1e-3 * size is the size-tiebreak floor; anything above it means
            # there was real signal (content BM25 or name overlap).
            if score > 1.0 + 1e-3 * len(self._l0_index[l0]):
                keep.append(l0)
            if len(keep) >= self._l0_top:
                break
        # pad if we haven't filled the budget
        if len(keep) < self._l0_top:
            for l0, _ in ranked:
                if l0 in keep:
                    continue
                keep.append(l0)
                if len(keep) >= self._l0_top:
                    break
        if not keep:
            keep = [l0 for l0, _ in ranked[: self._l0_top]]
        return keep

    def _select_l1(self, l0s: Iterable[str]) -> List[Tuple[str, str]]:
        """Return list of (l0, l1) buckets within selected L0s, time-sorted
        descending so more recent buckets win ties at L2."""
        keys: List[Tuple[str, str]] = []
        for l0 in l0s:
            for (a, b) in self._l1_index.keys():
                if a == l0:
                    keys.append((a, b))
        # recency proxy: lexical sort descending on b (YYYY-MM sorts correctly,
        # round_NNN sorts correctly, subtopic strings just sort stably)
        keys.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return keys

    # ---- BM25 over a leaf ---------------------------------------------- #
    def _bm25_for(self, key: Tuple[str, str]) -> Tuple[BM25, List[int]]:
        idxs = self._l1_index.get(key, [])
        if not idxs:
            return BM25([]), []
        cached = self._bm25_cache.get(key)
        if cached is not None:
            return cached, idxs
        # IMPORTANT: match the EpisodicMemory behaviour exactly so the two
        # stores are byte-equivalent when scheme='flat'.  EpisodicMemory
        # passes raw query strings to BM25 (each character becomes a token);
        # we preserve that to guarantee retrieval parity.
        bm = BM25([self.cases[i].query for i in idxs])
        self._bm25_cache[key] = bm
        return bm, idxs

    def retrieve(self, query: str, top_k: int = 3) -> List[Passage]:
        if not self.cases:
            return []
        qtokens = tokenize(query)
        l0s = self._select_l0(query, qtokens)
        keys = self._select_l1(l0s)
        # Collect every candidate case in the selected L0/L1 subtree and
        # re-score them in a SINGLE BM25 corpus.  This is the key fix vs a
        # naive per-leaf top_k merge: per-leaf scoring is biased by leaf size
        # (a 1-case leaf always returns that case), whereas a unified rerank
        # keeps the IDF / avgdl statistics consistent with the legacy flat
        # store.  L0/L1 still narrow the candidate set — the same scale-out
        # win OpenViking reports — but the final ranking stays sound.
        case_idxs: List[int] = []
        seen_set: set = set()
        for key in keys:
            for i in self._l1_index.get(key, []):
                if i not in seen_set:
                    seen_set.add(i)
                    case_idxs.append(i)
        if not case_idxs:
            return []
        rerank = BM25([self.cases[i].query for i in case_idxs])
        candidates: List[Tuple[int, float]] = []
        for local_i, score in rerank.search(query, top_k=top_k):
            if score > 0.0:
                candidates.append((case_idxs[local_i], score))
        candidates.sort(key=lambda x: (-x[1], x[0]))
        out: List[Passage] = []
        seen: set = set()
        for ci, score in candidates:
            if ci in seen:
                continue
            seen.add(ci)
            c = self.cases[ci]
            out.append(
                Passage(
                    source="episodic",
                    text=c.resolution,
                    score=_norm(score, self._k),
                    ref=c.case_id,
                    escalate_hint=c.should_escalate,
                )
            )
            if len(out) >= top_k:
                break
        return out

    # ---- introspection (used by tests / ablation reports) -------------- #
    def stats(self) -> Dict[str, object]:
        return {
            "n_cases": len(self.cases),
            "scheme": self.scheme,
            "n_l0": len(self._l0_index),
            "n_l1": len(self._l1_index),
            "l0_sizes": {k: len(v) for k, v in self._l0_index.items()},
        }

    def bucket_of(self, case_id: str) -> Optional[Tuple[str, str]]:
        for i, c in enumerate(self.cases):
            if c.case_id == case_id:
                return self._buckets[i]
        return None


def _extract_section(body: str, name: str) -> str:
    """Pull the text under a ``## <name>`` header in the markdown body."""
    pat = re.compile(rf"^##\s+{re.escape(name)}\s*$", re.MULTILINE)
    m = pat.search(body)
    if not m:
        return ""
    start = m.end()
    nxt = re.search(r"^##\s+", body[start:], re.MULTILINE)
    chunk = body[start: start + nxt.start()] if nxt else body[start:]
    return chunk.strip()
