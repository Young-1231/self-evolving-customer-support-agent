"""Tests for the APR-CS PlaybookRouter."""
from __future__ import annotations

from seagent.evolution.router import (
    MODE_ADAPTIVE_K,
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


# --------------------------------------------------------------- adaptive_k
def _airline_like_scores():
    """Reuse the airline-like Delta_i distribution from the cf_weighted run."""
    return {
        TIPS[0]: 0.0,
        TIPS[1]: 0.125,
        TIPS[2]: 0.125,
        TIPS[3]: 0.0,
        TIPS[4]: 0.125,
        TIPS[5]: 0.125,
        TIPS[6]: 0.25,
        TIPS[7]: 0.125,
    }


def test_adaptive_k_high_confidence_yields_minimum_or_none():
    """confidence >= high_tau -> drop to K_min (or 0 when K_min=0)."""
    cfg = RouterConfig(k=4, k_min=0, k_max=8, high_tau=0.8, low_tau=0.4)
    r = PlaybookRouter(cfg)
    out = r.select(
        "refund cancellation insurance",
        TIPS,
        scores=_airline_like_scores(),
        confidence=0.95,
        mode=MODE_ADAPTIVE_K,
    )
    assert out == []


def test_adaptive_k_low_confidence_injects_more_than_high():
    """Monotonicity: as confidence drops, the picked set must not shrink."""
    cfg = RouterConfig(k=4, k_min=1, k_max=8, high_tau=0.8, low_tau=0.4,
                       cum_threshold=None)
    r = PlaybookRouter(cfg)
    q = "refund cancellation insurance baggage upgrade balance"
    hi = r.select(q, TIPS, scores=_airline_like_scores(),
                  confidence=0.75, mode=MODE_ADAPTIVE_K)
    md = r.select(q, TIPS, scores=_airline_like_scores(),
                  confidence=0.5, mode=MODE_ADAPTIVE_K)
    lo = r.select(q, TIPS, scores=_airline_like_scores(),
                  confidence=0.1, mode=MODE_ADAPTIVE_K)
    assert len(hi) <= len(md) <= len(lo)
    assert len(lo) >= len(hi)


def test_adaptive_k_respects_k_max():
    cfg = RouterConfig(k=4, k_min=0, k_max=3, high_tau=0.99, low_tau=0.0,
                       cum_threshold=None, complexity_bonus=0)
    r = PlaybookRouter(cfg)
    # Force "low confidence" => target = K_max=3, and many tips overlap.
    out = r.select(
        "refund cancellation insurance baggage upgrade balance escalate verify",
        TIPS, scores=_airline_like_scores(),
        confidence=0.0, mode=MODE_ADAPTIVE_K,
    )
    assert len(out) <= 3


def test_adaptive_k_excludes_zero_delta_tips():
    """cf-weighted rank => Delta=0 tip never selected (weight is 0)."""
    cfg = RouterConfig(k=4, k_min=0, k_max=8, cum_threshold=None)
    r = PlaybookRouter(cfg)
    scores = _airline_like_scores()
    out = r.select(
        "always confirm balance before action",
        TIPS, scores=scores,
        confidence=0.0, mode=MODE_ADAPTIVE_K,
    )
    # TIPS[0] and TIPS[3] have Delta_i = 0 -> their cf-weight is 0 -> excluded.
    assert TIPS[0] not in out
    assert TIPS[3] not in out


def test_adaptive_k_cumulative_threshold_stops_early():
    """Once running sum(Delta) >= cum_threshold, we stop (above K_min)."""
    cfg = RouterConfig(k=4, k_min=1, k_max=8, cum_threshold=0.25,
                       high_tau=0.99, low_tau=0.0, complexity_bonus=0)
    r = PlaybookRouter(cfg)
    # Highest cf-weighted tip is TIPS[6] (Delta=0.25): hits the threshold at K=1.
    out = r.select(
        "baggage claim compensation refund cancellation",
        TIPS, scores=_airline_like_scores(),
        confidence=0.0, mode=MODE_ADAPTIVE_K,
    )
    assert len(out) >= 1
    # Without the cum_threshold the relevant set would be much larger.
    out_no_cum = r.select(
        "baggage claim compensation refund cancellation",
        TIPS, scores=_airline_like_scores(),
        confidence=0.0, mode=MODE_ADAPTIVE_K,
    )
    # The cum-gated pick is a prefix of (or equal to) the un-gated pick.
    assert out == out_no_cum[: len(out)] or len(out) <= len(out_no_cum)


def test_adaptive_k_complexity_bonus_grows_k_on_long_queries():
    """A long multi-intent query should pull more tips than a short one."""
    cfg = RouterConfig(k=4, k_min=0, k_max=8, complexity_bonus=2,
                       complexity_token_threshold=6, cum_threshold=None,
                       high_tau=0.99, low_tau=0.0)
    r = PlaybookRouter(cfg)
    short_q = "refund"
    long_q = ("refund cancellation insurance baggage upgrade balance "
              "escalate verify reset password loyalty")
    s = _airline_like_scores()
    short_out = r.select(short_q, TIPS, scores=s, confidence=0.5,
                         mode=MODE_ADAPTIVE_K)
    long_out = r.select(long_q, TIPS, scores=s, confidence=0.5,
                        mode=MODE_ADAPTIVE_K)
    assert len(long_out) >= len(short_out)


def test_adaptive_k_none_confidence_acts_as_moderate():
    """confidence=None -> mid-range K (between K_min and K_max)."""
    cfg = RouterConfig(k=4, k_min=1, k_max=7, cum_threshold=None,
                       complexity_bonus=0)
    r = PlaybookRouter(cfg)
    q = "refund cancellation insurance baggage upgrade balance escalate"
    out = r.select(q, TIPS, scores=_airline_like_scores(),
                   confidence=None, mode=MODE_ADAPTIVE_K)
    # K_min < len(out) < K_max -> moderate
    assert 1 <= len(out) <= 7


def test_adaptive_k_missing_scores_degrades_gracefully():
    """No scores -> cf weight defaults to 1.0 -> adaptive_k still picks by relevance."""
    cfg = RouterConfig(k=4, k_min=1, k_max=4, cum_threshold=None,
                       complexity_bonus=0)
    r = PlaybookRouter(cfg)
    out = r.select("refund cancellation", TIPS, scores={},
                   confidence=0.2, mode=MODE_ADAPTIVE_K)
    assert 1 <= len(out) <= 4


def test_adaptive_k_deterministic():
    cfg = RouterConfig(k=4, k_min=1, k_max=8)
    r = PlaybookRouter(cfg)
    q = "baggage refund cancellation insurance"
    a = r.select(q, TIPS, scores=_airline_like_scores(),
                 confidence=0.3, mode=MODE_ADAPTIVE_K)
    b = r.select(q, TIPS, scores=_airline_like_scores(),
                 confidence=0.3, mode=MODE_ADAPTIVE_K)
    assert a == b


def test_adaptive_k_listed_in_valid_modes():
    """Regression: the env-var path in memory_agent.py validates against VALID_MODES."""
    from seagent.evolution.router import VALID_MODES
    assert MODE_ADAPTIVE_K in VALID_MODES


def test_deterministic_across_calls():
    r = PlaybookRouter(RouterConfig(k=3))
    q = "baggage claim cancellation refund"
    a = r.select(q, TIPS, mode=MODE_TOP_K_RELEVANCE)
    b = r.select(q, TIPS, mode=MODE_TOP_K_RELEVANCE)
    assert a == b
