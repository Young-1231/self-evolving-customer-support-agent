"""Project configuration.

Defaults are defined in code so the project runs with zero config files and
zero third-party deps. ``Config.load`` optionally overlays a YAML file when
PyYAML is installed.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class Config:
    # --- data ---
    kb_index: str = "data/kb/index.jsonl"
    queries: str = "data/eval/queries.jsonl"

    # --- llm backend: mock | openai ---
    backend: str = "mock"
    model: str = "gpt-4o-mini"
    api_base: Optional[str] = None  # e.g. http://localhost:8000/v1 for vllm
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.0

    # --- retrieval ---
    kb_top_k: int = 4
    epi_top_k: int = 3
    # score normalization constant: norm(s) = s / (s + score_norm_k)
    score_norm_k: float = 6.0
    # episodic match must clear this normalized score to be trusted
    epi_trust_tau: float = 0.45
    # confidence below this -> escalate to a human (uncertainty handling)
    escalate_tau: float = 0.5
    # KB-only confidence is capped: "found a doc" != "sure it resolves the issue"
    kb_conf_cap: float = 0.85
    # a playbook rule only acts on the escalation decision when its trigger
    # overlap is high enough (precision guard against topic-level over-firing)
    playbook_fire_tau: float = 0.7

    # --- verifier ---
    coverage_threshold: float = 1.0  # fraction of keypoints that must appear

    # --- evolution / reflector ---
    reflect_min_cluster: int = 2  # min failed cases sharing a topic to form a playbook
    train_rounds: int = 6

    # --- runtime ---
    seed: int = 0
    workdir: str = "experiments"

    def resolve(self) -> "Config":
        """Make relative data paths absolute against the project root."""
        for k in ("kb_index", "queries", "workdir"):
            v = getattr(self, k)
            if not os.path.isabs(v):
                setattr(self, k, os.path.join(PROJECT_ROOT, v))
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def load(cls, path: Optional[str] = None, **overrides: Any) -> "Config":
        data: Dict[str, Any] = {}
        if path and os.path.exists(path):
            try:
                import yaml  # type: ignore

                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                data = {}
        valid = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in valid}
        data.update({k: v for k, v in overrides.items() if k in valid and v is not None})
        return cls(**data).resolve()
