"""Light-weight domain classifier for the calibrator.

Given a query and the KB hits the retriever returned, decide which
*calibration domain* to consult ("nimbusflow", "ecommerce", "default").

We deliberately keep this dumb-but-explainable: in our setup the KB doc_id
prefix is a clean signal (``kb_*`` => NimbusFlow, ``bx_*`` => Bitext
e-commerce).  When the prefix is ambiguous we fall back to the topic of the
top hit (using a static map that matches the topics actually present in
``data/kb_expanded/index.jsonl``).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# Topic -> domain map.  Populated from data/kb_expanded/index.jsonl:
#   nimbusflow: account_security, billing, data_export, general,
#               integrations_api, mobile_app, permissions, troubleshooting
#   ecommerce : account, billing, delivery, feedback, order, subscription,
#               support
# Note: "billing" overlaps both domains -- the doc_id prefix breaks the tie.
KB_TOPIC_TO_DOMAIN: Dict[str, str] = {
    # NimbusFlow-only topics
    "account_security": "nimbusflow",
    "data_export": "nimbusflow",
    "general": "nimbusflow",
    "integrations_api": "nimbusflow",
    "mobile_app": "nimbusflow",
    "permissions": "nimbusflow",
    "troubleshooting": "nimbusflow",
    # Bitext-only topics
    "account": "ecommerce",
    "delivery": "ecommerce",
    "feedback": "ecommerce",
    "order": "ecommerce",
    "subscription": "ecommerce",
    "support": "ecommerce",
    # ambiguous (both corpora) -> intentionally not in the map
    # "billing": <use ref prefix to disambiguate>
}

_NIMBUS_PREFIXES = ("kb_",)
_ECOMM_PREFIXES = ("bx_",)


def _domain_from_ref(ref: str) -> Optional[str]:
    if not ref:
        return None
    ref = ref.lower()
    if ref.startswith(_NIMBUS_PREFIXES):
        return "nimbusflow"
    if ref.startswith(_ECOMM_PREFIXES):
        return "ecommerce"
    return None


def infer_domain(
    query: str,
    kb_hits: Iterable[Any],
    *,
    topic_map: Optional[Dict[str, str]] = None,
) -> str:
    """Classify a query into a calibration domain.

    Parameters
    ----------
    query:
        The (possibly redacted) user query.  Currently unused by the rule
        engine but kept in the signature so future heuristics (language ID,
        keyword fallback) can use it without breaking callers.
    kb_hits:
        Iterable of objects with at least a ``ref`` attribute (i.e. our
        ``Passage`` objects).  ``score`` and ``text`` are tolerated but
        optional; ``source`` should be ``"kb"`` (non-KB hits are ignored).
    topic_map:
        Optional override for the topic->domain map.  Tests use this; in
        production callers should leave it ``None``.

    Returns
    -------
    str
        ``"nimbusflow"`` | ``"ecommerce"`` | ``"default"``.  Never raises.
    """
    tmap = topic_map if topic_map is not None else KB_TOPIC_TO_DOMAIN

    # Filter to KB hits and rank by score (best-first).  Use a small key
    # that tolerates missing attrs.
    kb_only: List[Any] = []
    for h in kb_hits or []:
        src = getattr(h, "source", "kb")
        if src in (None, "kb"):
            kb_only.append(h)

    if not kb_only:
        return "default"

    def _score(h: Any) -> float:
        try:
            return float(getattr(h, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    kb_only.sort(key=_score, reverse=True)

    # 1) ref-prefix signal (cheap & deterministic in this codebase)
    for h in kb_only:
        dom = _domain_from_ref(str(getattr(h, "ref", "") or ""))
        if dom:
            return dom

    # 2) topic-based fallback (used when Passage carries a topic attribute)
    for h in kb_only:
        topic = getattr(h, "topic", None) or getattr(h, "kb_topic", None)
        if topic and topic in tmap:
            return tmap[topic]

    return "default"
