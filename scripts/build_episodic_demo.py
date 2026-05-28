#!/usr/bin/env python
"""Seed ``data/episodic_demo/`` with ~30 demo cases from NimbusFlow train queries.

Run once (idempotent — re-running overwrites).  Used by ``docs/openviking_fs_context.md``
to show the L0/L1/L2 layout on a real (synthetic but realistic) corpus.

    python scripts/build_episodic_demo.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from seagent.config import Config
from seagent.data import load_kb, load_queries, split_queries
from seagent.memory.fs_store import FsEpisodicStore
from seagent.memory.episodic import Case
from seagent.memory.semantic import SemanticMemory


def _month_for_round(r: int) -> str:
    return f"2026-{(((r - 1) % 12) + 1):02d}"


def main():
    cfg = Config().resolve()
    kb = load_kb(cfg.kb_index)
    kbtopic = {d.doc_id: d.topic for d in kb}
    sem = SemanticMemory(kb, cfg.score_norm_k)

    qs = load_queries(cfg.queries)
    sp = split_queries(qs)
    train = sorted(sp.get("train", []), key=lambda q: q.id)

    root = os.path.join(HERE, "..", "data", "episodic_demo")
    # nuke for idempotence
    import shutil
    if os.path.isdir(root):
        shutil.rmtree(root)

    store = FsEpisodicStore(root_dir=root, scheme="topic_date", l0_top=3)
    for i, q in enumerate(train[:30], start=1):
        topic = kbtopic.get(q.gold_doc_ids[0], "general") if q.gold_doc_ids else "general"
        if topic == "general":
            hits = sem.retrieve(q.query, top_k=1)
            if hits:
                topic = kbtopic.get(hits[0].ref, "general")
        case = Case(
            case_id=q.id, query=q.query, resolution=q.resolution,
            should_escalate=q.should_escalate, topic=topic,
            source_query_id=q.id, learned_round=(i // 5) + 1,
        )
        store.add(case, metadata={"created_at": _month_for_round((i // 5) + 1)})

    print(f"[demo] wrote {len(store)} cases under {root}")
    s = store.stats()
    print(f"[demo] {s['n_l0']} L0 buckets, {s['n_l1']} L1 buckets")
    for l0, n in sorted(s["l0_sizes"].items()):
        print(f"        L0_{l0:20s} {n} case(s)")


if __name__ == "__main__":
    main()
