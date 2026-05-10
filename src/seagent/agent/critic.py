"""Confidence estimation (self-critique).

By default confidence is derived from retrieval evidence so the system is fully
deterministic offline:
  - a strong episodic (case) match  => high confidence (we've solved this before)
  - a fired playbook                => high confidence (we have an explicit rule)
  - KB-only evidence                => capped confidence (we found a doc, but are
                                       not sure it actually resolves the issue)

If the backend implements ``judge_confidence`` (e.g. an LLM-as-critic), that
value is used instead. This mirrors the self-RAG critic loop.
"""
from __future__ import annotations

from typing import List

from ..config import Config
from ..llm.base import LLMBackend, Passage


class Critic:
    def __init__(self, cfg: Config, backend: LLMBackend):
        self.cfg = cfg
        self.backend = backend

    def confidence(self, query: str, answer: str, contexts: List[Passage]) -> float:
        llm_c = self.backend.judge_confidence(query, answer, contexts)
        if llm_c >= 0.0:
            return llm_c
        epi = [p.score for p in contexts if p.source == "episodic"]
        pb = [p.score for p in contexts if p.source == "playbook"]
        kb = [p.score for p in contexts if p.source == "kb"]
        epi_conf = max(epi) if epi else 0.0
        pb_conf = max(pb) if pb else 0.0
        kb_conf = min(self.cfg.kb_conf_cap, max(kb)) if kb else 0.0
        return max(epi_conf, pb_conf, kb_conf)
