"""Bitext customer-support adapter — HuggingFace
``bitext/Bitext-customer-support-llm-chatbot-training-dataset``.

The Bitext corpus is one of the most widely accepted open customer-support
benchmarks in 2024-2026 academic / industrial work (e.g., used as seed data
for instruction-tuned support chatbots and as a retrieval corpus for RAG
evaluations). It ships **27 intents × 11 categories ≈ 27k Q&A pairs** under
**CDLA-Sharing-1.0**, which is commercial-friendly and explicitly allows
redistribution of derived works.

We use it to fix the project's biggest honest weakness — the 30-doc synthetic
NimbusFlow KB — by converting Bitext rows into ~150 retrieval-ready KB docs.

Schema mapping
--------------
Bitext columns: ``flags, instruction, category, intent, response``
KB doc        : ``{doc_id, title, topic, text}``

For each intent we pick 5-6 representative responses (length filtered,
near-dedup, diversity by Jaccard) and emit one doc per pick. We additionally
emit a per-category "overview" doc that aggregates the canonical action for
each intent in that category — useful for category-level retrieval hits.

Design notes
------------
* **Deterministic** — given a fixed ``target_n`` and ``seed`` the output is
  bit-stable, so downstream KB hashes don't drift between runs.
* **Reentrant download** — ``download_bitext`` checks the cache first, only
  hits the network if missing. Network goes through huggingface_hub which
  honors the ambient HF mirror / proxy env.
* **No mutation of source data** — placeholders like ``{{Order Number}}``
  stay verbatim. They're harmless for BM25 retrieval (the tokenizer treats
  ``{{`` as noise bigrams) and the LLM in the agent path will simply respond
  in templated form.

Public API
~~~~~~~~~~
* :func:`download_bitext(cache_dir)`            -> CSV path
* :func:`load_bitext_rows(csv_path)`            -> List[dict]
* :func:`bitext_to_kb_docs(rows, target_n=150)` -> List[KBDoc]
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Re-export to caller for license traceability in reports.
BITEXT_REPO = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"
BITEXT_FILENAME = "Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv"
BITEXT_LICENSE = "CDLA-Sharing-1.0"

# Bitext category -> KB topic. Kept conservative; map to existing-style topics
# where possible (the original 30-doc KB uses billing/account/integration/etc).
_CATEGORY_TO_TOPIC: Dict[str, str] = {
    "ACCOUNT":      "account",
    "CANCEL":       "subscription",
    "CONTACT":      "support",
    "DELIVERY":     "delivery",
    "FEEDBACK":     "feedback",
    "INVOICE":      "billing",
    "ORDER":        "order",
    "PAYMENT":      "billing",
    "REFUND":       "billing",
    "SHIPPING":     "delivery",
    "SUBSCRIPTION": "subscription",
}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def download_bitext(
    cache_dir: str,
    *,
    repo_id: str = BITEXT_REPO,
    filename: str = BITEXT_FILENAME,
    downloader: Optional[Callable[..., str]] = None,
) -> str:
    """Download the Bitext CSV via ``huggingface_hub``. Reentrant.

    If the file is already in ``cache_dir`` under HF's standard layout we just
    return its path without touching the network. ``downloader`` is a test
    seam — pass a callable with the same signature as
    ``huggingface_hub.hf_hub_download`` to bypass the import (used by the
    unit tests, see ``tests/test_bitext_ingest.py``).
    """
    os.makedirs(cache_dir, exist_ok=True)
    if downloader is None:
        try:
            from huggingface_hub import hf_hub_download  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                f"huggingface_hub is required for download_bitext: {e}"
            ) from e
        downloader = hf_hub_download
    return downloader(
        repo_id=repo_id,
        filename=filename,
        repo_type="dataset",
        cache_dir=cache_dir,
    )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_bitext_rows(csv_path: str) -> List[Dict[str, str]]:
    """Load Bitext rows as a list of dicts with keys
    ``flags / instruction / category / intent / response``.
    """
    out: List[Dict[str, str]] = []
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # csv.DictReader gives us strings; strip surrounding whitespace.
            out.append({k: (v or "").strip() for k, v in row.items()})
    return out


# ---------------------------------------------------------------------------
# Conversion to KB docs
# ---------------------------------------------------------------------------
@dataclass
class KBDocDict:
    """Plain dict-backed KB doc (so we don't import seagent.data here and
    can be unit-tested standalone)."""
    doc_id: str
    title: str
    topic: str
    text: str

    def to_record(self) -> Dict[str, str]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "topic": self.topic,
            "text": self.text,
        }


_WORD = re.compile(r"[A-Za-z0-9]+")


def _norm_for_dedup(text: str) -> Tuple[str, ...]:
    """Lower-case word tuple for near-dedup / Jaccard diversity."""
    return tuple(w.lower() for w in _WORD.findall(text))


def _jaccard(a: Tuple[str, ...], b: Tuple[str, ...]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _title_from_intent(intent: str, idx: int) -> str:
    """``cancel_order`` -> ``Cancel order (variant 1)``."""
    pretty = intent.replace("_", " ").strip().capitalize()
    return f"{pretty} (variant {idx})"


def _select_per_intent(
    rows: Sequence[Dict[str, str]],
    *,
    per_intent: int,
    min_len: int,
    max_len: int,
    diversity_tau: float,
) -> Dict[str, List[Dict[str, str]]]:
    """Pick at most ``per_intent`` responses per intent.

    Steps:
      1. length filter (min_len <= len(response) <= max_len)
      2. canonical-text dedup (collapse whitespace + dedup on the
         word-bag tuple)
      3. greedy diversity: accept row only if Jaccard against all
         previously picked rows of the same intent < diversity_tau
      4. deterministic ordering via row index → identical output across runs

    Strict ``ValueError`` on empty input — we'd rather fail loud than emit
    a phantom KB.
    """
    if per_intent <= 0:
        raise ValueError("per_intent must be > 0")
    if not rows:
        raise ValueError("no rows to select from")

    by_intent: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
    for i, r in enumerate(rows):
        intent = r.get("intent", "")
        if not intent:
            continue
        by_intent.setdefault(intent, []).append((i, r))

    picked: Dict[str, List[Dict[str, str]]] = {}
    for intent, candidates in by_intent.items():
        seen_norms: set[Tuple[str, ...]] = set()
        picks: List[Dict[str, str]] = []
        pick_norms: List[Tuple[str, ...]] = []
        # candidates are already in source order — deterministic.
        for _idx, r in candidates:
            resp = r.get("response", "")
            n = len(resp)
            if n < min_len or n > max_len:
                continue
            norm = _norm_for_dedup(resp)
            if not norm or norm in seen_norms:
                continue
            seen_norms.add(norm)
            # diversity: reject if too close to any existing pick
            if any(_jaccard(norm, p) >= diversity_tau for p in pick_norms):
                continue
            picks.append(r)
            pick_norms.append(norm)
            if len(picks) >= per_intent:
                break
        if picks:
            picked[intent] = picks
    return picked


def _build_category_overview(
    category: str,
    intents_in_cat: List[str],
    rows_by_intent: Dict[str, List[Dict[str, str]]],
) -> Optional[KBDocDict]:
    """One concise per-category 'index' doc.

    Lists each intent under the category with the first (canonical) variant
    of its response as a one-line stub. Helps retrieval when a query is
    framed at the category level (e.g., "I have a billing question") rather
    than at one specific intent.
    """
    if not intents_in_cat:
        return None
    pretty_cat = category.lower()
    lines: List[str] = [
        f"{pretty_cat.capitalize()} support overview — common requests:",
    ]
    for intent in sorted(intents_in_cat):
        picks = rows_by_intent.get(intent, [])
        if not picks:
            continue
        first_resp = picks[0].get("response", "").strip()
        # one-line stub: take the first sentence (cut at first period)
        stub = re.split(r"(?<=[.!?])\s+", first_resp)[0]
        stub = stub[:240]
        lines.append(f"- {intent}: {stub}")
    text = "\n".join(lines)
    if len(lines) <= 1:
        return None
    return KBDocDict(
        doc_id=f"bx_{pretty_cat}_overview",
        title=f"{category.capitalize()} support overview",
        topic=_CATEGORY_TO_TOPIC.get(category, pretty_cat),
        text=text,
    )


def bitext_to_kb_docs(
    rows: Sequence[Dict[str, str]],
    *,
    target_n: int = 150,
    min_len: int = 120,
    max_len: int = 1600,
    diversity_tau: float = 0.85,
) -> List[KBDocDict]:
    """Convert Bitext rows into KB docs.

    Returns roughly ``target_n`` docs:
      * ~27 intents × ``per_intent`` variants (per_intent computed from
        target_n minus reserved overview slots)
      * plus 1 overview doc per category (≤11 with the bundled CSV)

    Determinism: identical (rows, target_n, **kwargs) → identical output.
    """
    if target_n < 30:
        raise ValueError("target_n too small; need at least 30 to be useful")
    if not rows:
        raise ValueError("rows is empty")

    intents = sorted({r["intent"] for r in rows if r.get("intent")})
    n_intents = len(intents)
    # reserve roughly 1 overview per category; cap reserve at target_n/4
    categories = sorted({r["category"] for r in rows if r.get("category")})
    n_overview = min(len(categories), max(1, target_n // 12))
    body_budget = target_n - n_overview
    per_intent = max(1, body_budget // max(1, n_intents))

    selected = _select_per_intent(
        rows,
        per_intent=per_intent,
        min_len=min_len,
        max_len=max_len,
        diversity_tau=diversity_tau,
    )

    docs: List[KBDocDict] = []

    # 1) per-intent variant docs
    for intent in intents:
        picks = selected.get(intent, [])
        if not picks:
            continue
        category = picks[0].get("category", "")
        topic = _CATEGORY_TO_TOPIC.get(category, category.lower() or "general")
        for j, r in enumerate(picks, start=1):
            resp = r.get("response", "").strip()
            instr = r.get("instruction", "").strip()
            # Compose body: short user-question lead-in + canonical answer.
            text_parts: List[str] = []
            if instr:
                text_parts.append(f"User question: {instr}")
            text_parts.append(resp)
            text = "\n\n".join(text_parts)
            doc = KBDocDict(
                doc_id=f"bx_{intent}_{j:02d}",
                title=_title_from_intent(intent, j),
                topic=topic,
                text=text,
            )
            docs.append(doc)

    # 2) per-category overview docs
    cat_to_intents: Dict[str, List[str]] = {}
    for intent, picks in selected.items():
        if not picks:
            continue
        cat = picks[0].get("category", "") or ""
        cat_to_intents.setdefault(cat, []).append(intent)
    # cap overview count to n_overview (deterministic — sort categories by name)
    for cat in sorted(cat_to_intents.keys())[:n_overview]:
        ov = _build_category_overview(cat, cat_to_intents[cat], selected)
        if ov is not None:
            docs.append(ov)

    # 3) sanity / determinism: dedupe by doc_id (must already be unique)
    seen_ids: set[str] = set()
    out: List[KBDocDict] = []
    for d in docs:
        if d.doc_id in seen_ids:
            continue
        seen_ids.add(d.doc_id)
        out.append(d)

    return out


def stable_hash_docs(docs: Sequence[KBDocDict]) -> str:
    """Hash a doc list deterministically — useful for cache invalidation."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda x: x.doc_id):
        h.update(d.doc_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(d.title.encode("utf-8"))
        h.update(b"\x00")
        h.update(d.topic.encode("utf-8"))
        h.update(b"\x00")
        h.update(d.text.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()[:16]
