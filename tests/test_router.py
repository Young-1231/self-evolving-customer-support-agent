"""Tests for the APR-CS PlaybookRouter."""
from __future__ import annotations

from seagent.evolution.router import (
    MODE_ALL,
    MODE_CF_WEIGHTED,
    MODE_CONF_GATED,
    MODE_TOP_K_RELEVANCE,
    PlaybookRouter,
    RouterConfig,
)


TIPS = [
    "Always verify the user's identity before any account change.",
    "When the customer reports a refund issue, check the most recent transaction first.",
    "If a flight cancellation is involved, confirm whether insurance was purchased.",
    "Escalate to a human agent if the dispute concerns chargebacks above $500.",
    "Reset password flows must use the verified email on file.",
    "Recommend seat-upgrade only after confirming loyalty tier.",
    "Baggage claims require the baggage tag identifier before any compensation.",
    "Decline any policy change request that lacks two-factor confirmation.",
]


def test_all_mode_returns_input_order_unchanged():
    r = PlaybookRouter()
    out = r.select("any query", TIPS, mode=MODE_ALL)
    assert out == TIPS  # bit-exact legacy behaviour


def test_all_mode_strips_blanks_but_preserves_order():
    r = PlaybookRouter()
    out = r.select("q", ["a", "", "  ", "b", "a"], mode=MODE_ALL)
    assert out == ["a", "b"]  # dedup + strip, original order


def test_top_k_relevance_picks_most_overlapping_tips():
    r = PlaybookRouter(RouterConfig(k=2))
    out = r.select(
        "flight cancellation refund -- did the customer buy insurance?",
        TIPS,
        mode=MODE_TOP_K_RELEVANCE,
    )
    assert len(out) <= 2
    # Both the "refund" tip and the "cancellation/insurance" tip should be
    # highly ranked; at least one of them must appear.
    joined = " | ".join(out).lower()
    assert "cancellation" in joined or "refund" in joined


def test_top_k_relevance_returns_empty_when_no_overlap():
    r = PlaybookRouter(RouterConfig(k=4))
    out = r.select("xyzzy plugh foo bar", TIPS, mode=MODE_TOP_K_RELEVANCE)
    assert out == []


def test_cf_weighted_excludes_negative_contribution():
    """A tip with Delta_i <= 0 must never be selected by cf_weighted."""
    r = PlaybookRouter(RouterConfig(k=4))
    scores = {
        TIPS[0]: 0.10,    # verify identity -- positive
        TIPS[1]: -0.05,   # refund tip is HARMFUL, must be excluded
        TIPS[2]: 0.08,    # cancellation+insurance -- positive
        TIPS[3]: 0.02,    # escalate -- positive
        TIPS[4]: 0.0,     # zero marginal -- excluded
        TIPS[5]: 0.05,
        TIPS[6]: 0.04,
        TIPS[7]: 0.03,
    }
    out = r.select(
        "Customer wants a refund after a cancelled flight.",
        TIPS,
        scores=scores,
        mode=MODE_CF_WEIGHTED,
    )
    assert TIPS[1] not in out, "negative-Delta tip leaked through cf_weighted"
    assert TIPS[4] not in out, "zero-Delta tip leaked through cf_weighted"
    # The cancellation tip is both relevant and positive: must appear.
    assert TIPS[2] in out


def test_cf_weighted_missing_scores_default_to_one():
    """No scores at all => cf_weighted degenerates to relevance ranking."""
    r = PlaybookRouter(RouterConfig(k=3))
    rel_out = r.select(
        "verify identity before account change",
        TIPS,
        mode=MODE_TOP_K_RELEVANCE,
    )
    cf_out = r.select(
        "verify identity before account change",
        TIPS,
        scores={},
        mode=MODE_CF_WEIGHTED,
    )
    assert rel_out == cf_out


def test_conf_gated_returns_empty_when_highly_confident():
    r = PlaybookRouter(RouterConfig(k=4, low_tau=0.4, high_tau=0.8))
    out = r.select(
        "verify identity",
        TIPS,
        confidence=0.95,
        mode=MODE_CONF_GATED,
    )
    assert out == []


def test_conf_gated_halves_k_at_medium_confidence():
    r = PlaybookRouter(RouterConfig(k=4, low_tau=0.4, high_tau=0.8))
    out_med = r.select(
        "refund cancellation insurance baggage",
        TIPS,
        confidence=0.5,
        mode=MODE_CONF_GATED,
    )
    out_low = r.select(
        "refund cancellation insurance baggage",
        TIPS,
        confidence=0.1,
        mode=MODE_CONF_GATED,
    )
    assert len(out_med) <= 2  # halved budget
    assert len(out_low) >= len(out_med)


def test_conf_gated_none_confidence_acts_like_topk():
    r = PlaybookRouter(RouterConfig(k=3))
    q = "refund and cancellation"
    gated = r.select(q, TIPS, confidence=None, mode=MODE_CONF_GATED)
    rel = r.select(q, TIPS, mode=MODE_TOP_K_RELEVANCE)
    assert gated == rel[: len(gated)]


def test_unknown_mode_raises():
    r = PlaybookRouter()
    try:
        r.select("q", TIPS, mode="not_a_mode")
    except ValueError:
        return
    raise AssertionError("expected ValueError on unknown mode")


def test_empty_tips_returns_empty():
    r = PlaybookRouter()
    assert r.select("anything", [], mode=MODE_TOP_K_RELEVANCE) == []
    assert r.select("anything", [], mode=MODE_ALL) == []


def test_deterministic_across_calls():
    r = PlaybookRouter(RouterConfig(k=3))
    q = "baggage claim cancellation refund"
    a = r.select(q, TIPS, mode=MODE_TOP_K_RELEVANCE)
    b = r.select(q, TIPS, mode=MODE_TOP_K_RELEVANCE)
    assert a == b
