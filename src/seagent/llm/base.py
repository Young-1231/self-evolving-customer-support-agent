"""LLM backend abstraction.

The agent never calls a provider SDK directly; it talks to an ``LLMBackend``.
This lets the exact same agent/evolution code run against:
  - MockBackend   : deterministic, zero-dependency (CI, demos, ablations)
  - OpenAIBackend : any OpenAI-compatible endpoint (OpenAI API or local vLLM)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Passage:
    """A retrieved context passage handed to the backend for answer synthesis."""

    source: str          # "kb" | "episodic" | "playbook"
    text: str
    score: float         # normalized retrieval confidence in [0, 1]
    ref: str = ""        # doc_id / case_id / playbook_id
    escalate_hint: Optional[bool] = None  # episodic/playbook may carry a decision


class LLMBackend:
    """Interface every backend implements."""

    name: str = "base"

    def generate_answer(self, query: str, contexts: List[Passage]) -> str:
        """Produce a customer-support reply grounded in ``contexts``."""
        raise NotImplementedError

    def judge_confidence(self, query: str, answer: str, contexts: List[Passage]) -> float:
        """Optional self-critique. Returns confidence in [0, 1].

        Default returns -1.0, signalling the agent to fall back to its
        retrieval-based confidence estimate.
        """
        return -1.0
