"""Deterministic mock backend.

It simulates an LLM that *grounds its answer in the retrieved context* and does
not hallucinate facts that are absent from context. Concretely it returns the
text of the highest-scoring retrieved passage, optionally merging a second
distinct source. This is the honest mechanism behind the self-evolution curve:

  - Before any experience is accumulated, only KB passages are available. For
    "hard" queries the KB lacks some required keypoints, so the answer misses
    them and the verifier marks a failure.
  - After the agent has seen (and failed) a paraphrase during training, the
    human resolution is stored in episodic memory. Retrieval then surfaces it,
    so the answer now contains the previously-missing keypoints -> pass.

The mock never reads the gold keypoints, so improvement reflects genuine
context reuse, not label leakage.
"""
from __future__ import annotations

from typing import List

from .base import LLMBackend, Passage


class MockBackend(LLMBackend):
    name = "mock"

    def __init__(self, max_chars: int = 600):
        self.max_chars = max_chars

    def generate_answer(self, query: str, contexts: List[Passage]) -> str:
        if not contexts:
            return "抱歉，我暂时没有找到相关信息，建议您稍后再试或联系支持团队。"
        ordered = sorted(contexts, key=lambda p: -p.score)
        parts: List[str] = []
        seen_sources = set()
        for p in ordered:
            # merge at most one passage per source to mimic a synthesized reply
            if p.source in seen_sources and p.source != "playbook":
                continue
            seen_sources.add(p.source)
            parts.append(p.text.strip())
            if sum(len(x) for x in parts) >= self.max_chars:
                break
        answer = " ".join(parts)
        return answer[: self.max_chars]
