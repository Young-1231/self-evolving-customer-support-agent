"""Build an LLM backend from a Config."""
from __future__ import annotations

from ..config import Config
from .base import LLMBackend
from .mock import MockBackend


def build_backend(cfg: Config) -> LLMBackend:
    if cfg.backend == "mock":
        return MockBackend()
    if cfg.backend == "openai":
        from .openai_backend import OpenAIBackend

        return OpenAIBackend(
            model=cfg.model,
            api_base=cfg.api_base,
            api_key_env=cfg.api_key_env,
            temperature=cfg.temperature,
        )
    raise ValueError(f"unknown backend: {cfg.backend}")
