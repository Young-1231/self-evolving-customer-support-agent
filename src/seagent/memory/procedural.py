"""Procedural memory = playbooks induced offline by the Reflector.

A playbook is an *auditable, versioned, toggleable* generalization over several
failed cases that share a topic. It carries:
  - trigger_terms     : tokens used to match incoming queries
  - guidance          : distilled resolution text (provides keypoints at answer time)
  - action            : "answer" | "escalate"  (explicit escalation governance)
  - enabled / version : human-in-the-loop control + rollback

This is the project's defense against *misevolution* (arXiv 2509.26354): every
self-generated behavior change is a reviewable artifact that can be disabled or
rolled back, never an opaque weight update.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ..llm.base import Passage
from .bm25 import tokenize


@dataclass
class Playbook:
    playbook_id: str
    topic: str
    trigger_terms: List[str]
    guidance: str
    action: str = "answer"          # "answer" | "escalate"
    enabled: bool = True
    version: int = 1
    source_case_ids: List[str] = field(default_factory=list)
    created_round: int = 0


class ProceduralMemory:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.playbooks: List[Playbook] = []
        if path and os.path.exists(path):
            self._load()

    # --- persistence ---
    def _load(self) -> None:
        self.playbooks = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.playbooks.append(Playbook(**json.loads(line)))

    def _persist(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for p in self.playbooks:
                f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")

    # --- governance api ---
    def upsert(self, pb: Playbook) -> None:
        for i, ex in enumerate(self.playbooks):
            if ex.playbook_id == pb.playbook_id:
                pb.version = ex.version + 1
                self.playbooks[i] = pb
                self._persist()
                return
        self.playbooks.append(pb)
        self._persist()

    def set_enabled(self, playbook_id: str, enabled: bool) -> bool:
        for p in self.playbooks:
            if p.playbook_id == playbook_id:
                p.enabled = enabled
                self._persist()
                return True
        return False

    def __len__(self) -> int:
        return len(self.playbooks)

    # --- retrieval ---
    def retrieve(self, query: str, top_k: int = 2) -> List[Passage]:
        q = set(tokenize(query))
        scored = []
        for p in self.playbooks:
            if not p.enabled:
                continue
            trig = set()
            for t in p.trigger_terms:
                trig.update(tokenize(t))
            if not trig:
                continue
            overlap = len(q & trig) / len(trig)
            if overlap > 0:
                scored.append((overlap, p))
        scored.sort(key=lambda x: -x[0])
        out: List[Passage] = []
        for overlap, p in scored[:top_k]:
            out.append(
                Passage(
                    source="playbook",
                    text=p.guidance,
                    score=min(1.0, 0.5 + 0.5 * overlap),  # a fired rule is high-confidence
                    ref=p.playbook_id,
                    escalate_hint=(p.action == "escalate"),
                )
            )
        return out
