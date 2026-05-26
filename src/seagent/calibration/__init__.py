"""Per-domain threshold calibration for the support agent.

Why this exists
---------------
The agent's `escalate_tau` and `kb_conf_cap` were tuned for the Chinese
NimbusFlow corpus.  When the *same* thresholds are applied to English Bitext
e-commerce traffic (stress test Exp B), confidence systematically lands
*below* `escalate_tau=0.5` because the stiff "template" answers retrieved
from Bitext score lower under our BM25+critic chain.  Result: escalation
balloons to ~92% even though the KB is now objectively bigger.

This module provides:

  * :class:`DomainCalibrator` — persistable {domain -> thresholds} table the
    SupportAgent consults at runtime.  When unset, the agent's behaviour is
    unchanged (default-path preservation is a hard requirement).
  * :func:`infer_domain` — light-weight domain classifier that looks at the
    KB hits handed to the agent and decides which threshold profile to use.

Both pieces are zero-dependency / deterministic.
"""
from __future__ import annotations

from .calibrator import DomainCalibrator, DEFAULT_THRESHOLDS
from .domain_inference import infer_domain, KB_TOPIC_TO_DOMAIN

__all__ = [
    "DomainCalibrator",
    "DEFAULT_THRESHOLDS",
    "infer_domain",
    "KB_TOPIC_TO_DOMAIN",
]
