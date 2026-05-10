"""Dataset loading for the NimbusFlow customer-support benchmark."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class KBDoc:
    doc_id: str
    title: str
    topic: str
    text: str


@dataclass
class Query:
    id: str
    split: str
    group: str
    query: str
    required_keypoints: List[str]
    gold_doc_ids: List[str]
    should_escalate: bool
    difficulty: str
    resolution: str


def load_kb(path: str) -> List[KBDoc]:
    docs: List[KBDoc] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            docs.append(KBDoc(d["doc_id"], d["title"], d.get("topic", "general"), d["text"]))
    return docs


def load_queries(path: str) -> List[Query]:
    qs: List[Query] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            qs.append(
                Query(
                    id=d["id"],
                    split=d["split"],
                    group=d["group"],
                    query=d["query"],
                    required_keypoints=list(d["required_keypoints"]),
                    gold_doc_ids=list(d.get("gold_doc_ids", [])),
                    should_escalate=bool(d["should_escalate"]),
                    difficulty=d["difficulty"],
                    resolution=d["resolution"],
                )
            )
    return qs


def split_queries(qs: List[Query]) -> Dict[str, List[Query]]:
    out: Dict[str, List[Query]] = {"train": [], "eval": []}
    for q in qs:
        out.setdefault(q.split, []).append(q)
    return out
