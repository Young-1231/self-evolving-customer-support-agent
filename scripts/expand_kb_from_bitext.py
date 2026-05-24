#!/usr/bin/env python
"""Build the expanded KB: original 30-doc NimbusFlow + ~150 Bitext docs.

Outputs land under ``data/kb_expanded/``:
    index.jsonl          — merged KB index (one doc per line, same schema as
                           data/kb/index.jsonl)
    kb_*.md              — copy of original NimbusFlow markdown
    bx_*.md              — one markdown per Bitext-derived doc

The original ``data/kb/index.jsonl`` is **never** touched. The new path is
consumed by ``scripts/run_stress_test_expanded.py`` via ``KB_INDEX_PATH``
env var override (the upstream stress harness reads ``cfg.kb_index``).

Usage:
    python scripts/expand_kb_from_bitext.py \
        [--target-bitext-n 150] [--seed 0]

This script is a one-shot builder; the resulting KB is fully deterministic
given (CSV file hash, target_bitext_n, seed).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from typing import Dict, List

# Allow running both `python scripts/...` and PYTHONPATH=src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.data import load_kb  # noqa: E402
from seagent.datasets.bitext import (  # noqa: E402
    BITEXT_LICENSE,
    BITEXT_REPO,
    bitext_to_kb_docs,
    download_bitext,
    load_bitext_rows,
    stable_hash_docs,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ORIG_KB_DIR = os.path.join(ROOT, "data", "kb")
ORIG_INDEX = os.path.join(ORIG_KB_DIR, "index.jsonl")
EXPANDED_DIR = os.path.join(ROOT, "data", "kb_expanded")
EXPANDED_INDEX = os.path.join(EXPANDED_DIR, "index.jsonl")
HF_CACHE = os.environ.get("HF_HUB_CACHE", os.path.join(ROOT, ".hf_cache"))


def _write_md(path: str, doc_id: str, title: str, topic: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"doc_id: {doc_id}\n")
        f.write(f"title: {title}\n")
        f.write(f"topic: {topic}\n")
        f.write("---\n\n")
        f.write(text.rstrip() + "\n")


def _print_stats(records: List[Dict[str, str]]) -> None:
    n = len(records)
    topics = Counter(r["topic"] for r in records)
    avg_len = sum(len(r["text"]) for r in records) / max(1, n)
    print(f"\n[stats] total docs: {n}")
    print(f"[stats] avg text length: {avg_len:.1f} chars")
    print(f"[stats] topic distribution:")
    for t, k in sorted(topics.items(), key=lambda x: -x[1]):
        print(f"  {t:>14s} : {k:3d}")


def build_expanded_kb(target_bitext_n: int = 150, seed: int = 0) -> str:
    os.makedirs(EXPANDED_DIR, exist_ok=True)

    # ---- 1) load originals --------------------------------------------------
    original_docs = load_kb(ORIG_INDEX)
    print(f"[build] original NimbusFlow KB: {len(original_docs)} docs")

    # copy the 30 original markdown files verbatim into the new tree so the
    # expanded folder is self-contained (and the original tree stays untouched).
    for fname in os.listdir(ORIG_KB_DIR):
        if fname.endswith(".md"):
            src = os.path.join(ORIG_KB_DIR, fname)
            dst = os.path.join(EXPANDED_DIR, fname)
            shutil.copyfile(src, dst)

    # ---- 2) download Bitext + convert --------------------------------------
    print(f"[build] downloading Bitext CSV from HF: {BITEXT_REPO}")
    print(f"[build]   (license: {BITEXT_LICENSE}, cache: {HF_CACHE})")
    csv_path = download_bitext(HF_CACHE)
    print(f"[build]   csv path: {csv_path}")
    rows = load_bitext_rows(csv_path)
    print(f"[build]   loaded {len(rows)} Bitext rows")

    bx_docs = bitext_to_kb_docs(rows, target_n=target_bitext_n)
    print(f"[build] bitext_to_kb_docs -> {len(bx_docs)} docs "
          f"(hash={stable_hash_docs(bx_docs)})")

    # write each Bitext-derived doc out as markdown
    for d in bx_docs:
        p = os.path.join(EXPANDED_DIR, f"{d.doc_id}.md")
        _write_md(p, d.doc_id, d.title, d.topic, d.text)

    # ---- 3) merged index.jsonl ---------------------------------------------
    records: List[Dict[str, str]] = []
    for d in original_docs:
        records.append({
            "doc_id": d.doc_id,
            "title":  d.title,
            "topic":  d.topic,
            "text":   d.text,
        })
    for d in bx_docs:
        records.append(d.to_record())

    # uniqueness check across both sources (defensive — NimbusFlow uses kb_xxx,
    # Bitext uses bx_xxx, so collisions should never happen)
    ids = [r["doc_id"] for r in records]
    dups = [k for k, v in Counter(ids).items() if v > 1]
    if dups:
        raise RuntimeError(f"merged KB has duplicate doc_ids: {dups[:5]}")

    with open(EXPANDED_INDEX, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[build] wrote merged index: {EXPANDED_INDEX}")

    _print_stats(records)
    return EXPANDED_INDEX


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-bitext-n", type=int, default=150,
                    help="how many Bitext-derived KB docs to emit (default 150)")
    ap.add_argument("--seed", type=int, default=0,
                    help="(currently unused — selection is fully deterministic)")
    args = ap.parse_args()
    build_expanded_kb(target_bitext_n=args.target_bitext_n, seed=args.seed)


if __name__ == "__main__":
    main()
