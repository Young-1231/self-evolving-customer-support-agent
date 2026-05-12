"""Domain-level experience playbook for the tau2-bench memory agent.

A playbook is a small list of distilled, auditable tips (operating guidance the
agent learned from failed training tasks). It is injected into the agent system
prompt at inference time. Same governance idea as the synthetic-benchmark
ProceduralMemory: human-readable, versioned via the JSON file, toggleable.

APR-CS extension (2026-05)
--------------------------
The on-disk schema now optionally carries a ``scores`` map of ``tip -> Delta_i``
(produced offline by
:class:`seagent.evolution.counterfactual.CounterfactualEvaluator`). ``tips`` may
still be a bare list of strings, so old playbook files keep loading unchanged
(``scores={}`` in that case). Callers that don't care about routing can keep
using ``load_playbook(path) -> List[str]`` exactly as before.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Tuple

PLAYBOOK_ENV = "SEAGENT_TAU2_PLAYBOOK"


def _parse(data) -> Tuple[List[str], Dict[str, float]]:
    if isinstance(data, list):
        tips = data
        scores: Dict[str, float] = {}
    elif isinstance(data, dict):
        tips = data.get("tips", [])
        raw_scores = data.get("scores") or (data.get("meta") or {}).get("scores") or {}
        scores = {str(k): float(v) for k, v in raw_scores.items()}
    else:
        tips, scores = [], {}
    clean_tips = [str(t).strip() for t in tips if str(t).strip()]
    # Only keep score entries that actually correspond to a kept tip.
    clean_scores = {t: scores[t] for t in clean_tips if t in scores}
    return clean_tips, clean_scores


def load_playbook(path: str | None) -> List[str]:
    """Backwards-compatible loader: returns just the tip list.

    Kept as-is for existing call sites (and existing tests).
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tips, _ = _parse(data)
    return tips


def load_playbook_with_scores(path: str | None) -> Tuple[List[str], Dict[str, float]]:
    """APR-CS-aware loader: also returns the per-tip Delta_i scores (or {})."""
    if not path or not os.path.exists(path):
        return [], {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _parse(data)


def save_playbook(path: str, tips: List[str], meta: dict | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = meta or {}
    # ``meta["scores"]`` is the canonical place per spec; we also surface it at
    # the top level for ergonomics, but readers should prefer ``meta.scores``.
    payload = {
        "version": meta.get("version", 1),
        "meta": meta,
        "tips": tips,
    }
    if "scores" in meta:
        payload["scores"] = meta["scores"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
