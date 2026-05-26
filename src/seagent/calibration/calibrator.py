"""Persistable per-domain threshold table.

A *domain* is just a string label ("nimbusflow", "ecommerce", "default", ...).
For each domain we keep the two thresholds the SupportAgent currently consults:

  * ``escalate_tau`` — confidence below which the agent hands off to a human;
  * ``kb_conf_cap`` — ceiling applied to KB-only confidence (Critic).

The JSON schema is deliberately flat so a human can hand-edit it in a pinch::

    {
      "nimbusflow": {"escalate_tau": 0.50, "kb_conf_cap": 0.85},
      "ecommerce":  {"escalate_tau": 0.35, "kb_conf_cap": 0.70},
      "default":    {"escalate_tau": 0.50, "kb_conf_cap": 0.85}
    }

The "default" entry is the safety net.  If a domain is asked for that isn't in
the table, we silently fall back to "default".  If "default" itself is
missing, we fall back to the hard-coded ``DEFAULT_THRESHOLDS`` below (which
mirror the cfg defaults; the agent's behaviour is then provably unchanged).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

# Mirror seagent.config.Config defaults so an empty calibrator is a no-op.
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "escalate_tau": 0.50,
    "kb_conf_cap": 0.85,
}

_KNOWN_KEYS = set(DEFAULT_THRESHOLDS.keys())


@dataclass
class DomainCalibrator:
    """A small registry of per-domain thresholds.

    Parameters
    ----------
    domain_thresholds:
        Mapping ``{domain: {threshold_name: value}}``.  Unknown threshold
        names are silently dropped (forward-compat); unknown domains fall
        back to ``"default"``.
    """

    domain_thresholds: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # ----------------------- construction -------------------------------
    def __post_init__(self) -> None:
        # normalise: filter to known keys, coerce to float
        clean: Dict[str, Dict[str, float]] = {}
        for dom, thr in (self.domain_thresholds or {}).items():
            if not isinstance(thr, Mapping):
                continue
            row: Dict[str, float] = {}
            for k, v in thr.items():
                if k in _KNOWN_KEYS:
                    try:
                        row[k] = float(v)
                    except (TypeError, ValueError):
                        continue
            if row:
                clean[str(dom)] = row
        self.domain_thresholds = clean

    # ----------------------- lookup -------------------------------------
    def get_thresholds(self, domain: Optional[str]) -> Dict[str, float]:
        """Return the *complete* threshold dict for ``domain``.

        Lookup order: ``domain`` -> ``"default"`` -> ``DEFAULT_THRESHOLDS``.
        The returned dict is always a full set of known keys (callers can
        index directly without `.get` dances).
        """
        out: Dict[str, float] = dict(DEFAULT_THRESHOLDS)
        # overlay "default" first, then the specific domain, so the specific
        # domain wins for any key it sets.
        if "default" in self.domain_thresholds:
            out.update(self.domain_thresholds["default"])
        if domain and domain in self.domain_thresholds and domain != "default":
            out.update(self.domain_thresholds[domain])
        return out

    def has_domain(self, domain: str) -> bool:
        return domain in self.domain_thresholds

    # ----------------------- persistence --------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {"domains": self.domain_thresholds},
            ensure_ascii=False, indent=2, sort_keys=True,
        )

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    @classmethod
    def from_json(cls, raw: str) -> "DomainCalibrator":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls()
        # tolerate both {"domains": {...}} and a flat {domain: {...}} layout
        if isinstance(data, dict) and "domains" in data and isinstance(data["domains"], dict):
            return cls(domain_thresholds=data["domains"])
        if isinstance(data, dict):
            return cls(domain_thresholds=data)  # type: ignore[arg-type]
        return cls()

    @classmethod
    def load(cls, path: str) -> "DomainCalibrator":
        if not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())
