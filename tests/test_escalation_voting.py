"""Tests for the three-signal escalation voter (P1)."""
from seagent.agent.escalation_voting import EscalationVoter, DEFAULT_WEIGHTS


def test_any_mode_or_semantics():
    v = EscalationVoter("any")
    assert v.vote(False, False, False).escalate is False
    assert v.vote(True, False, False).escalate is True
    assert v.vote(False, True, False).escalate is True
    assert v.vote(True, True, True).escalate is True


def test_majority_needs_at_least_two():
    v = EscalationVoter("majority")
    assert v.vote(True, False, False).escalate is False
    assert v.vote(False, True, False).escalate is False
    assert v.vote(True, True, False).escalate is True
    assert v.vote(True, False, True).escalate is True
    assert v.vote(True, True, True).escalate is True


def test_unanimous_needs_all_three():
    v = EscalationVoter("unanimous")
    assert v.vote(True, True, False).escalate is False
    assert v.vote(True, True, True).escalate is True
    assert v.vote(False, False, False).escalate is False


def test_weighted_default_thresholds():
    v = EscalationVoter("weighted")  # threshold=0.5, weights = default 0.4/0.4/0.2
    # critic alone (0.4) — below 0.5
    assert v.vote(True, False, False).escalate is False
    # critic + policy = 0.6 — over 0.5
    r = v.vote(True, False, True)
    assert r.escalate is True
    assert r.weighted_sum == 0.6
    # groundedness alone = 0.4 below threshold
    assert v.vote(False, True, False).escalate is False


def test_weighted_custom_threshold():
    v = EscalationVoter("weighted", threshold=0.7)
    # 0.4 + 0.4 = 0.8 over 0.7
    assert v.vote(True, True, False).escalate is True
    # 0.4 + 0.2 = 0.6 under 0.7
    assert v.vote(True, False, True).escalate is False


def test_unknown_mode_degrades_to_any():
    v = EscalationVoter("nonsense_mode_typo")
    assert v.mode == "any"
    assert v.vote(True, False, False).escalate is True


def test_result_carries_diagnostic_fields():
    v = EscalationVoter("majority")
    r = v.vote(True, True, False)
    assert r.signals == {"critic": True, "groundedness": True, "policy": False}
    assert "2/3" in r.reason
    assert r.mode == "majority"
