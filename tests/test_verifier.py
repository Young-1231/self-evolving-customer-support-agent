from seagent.data import Query
from seagent.eval.verifier import keypoint_coverage, verify


class _R:
    def __init__(self, answer, escalate):
        self.answer = answer
        self.escalate = escalate


def _q(**kw):
    base = dict(id="q", split="eval", group="g", query="x", required_keypoints=["15分钟内有效", "至少 8 位"],
                gold_doc_ids=[], should_escalate=False, difficulty="easy", resolution="")
    base.update(kw)
    return Query(**base)


def test_keypoint_coverage_whitespace_insensitive():
    assert keypoint_coverage("链接15分钟内有效，密码至少8位", ["15分钟内有效", "至少 8 位"]) == 1.0
    assert keypoint_coverage("链接15分钟内有效", ["15分钟内有效", "至少 8 位"]) == 0.5


def test_verify_resolved_requires_coverage_and_escalation():
    q = _q()
    assert verify(q, _R("链接15分钟内有效，密码至少 8 位", False)).resolved is True
    # missing a keypoint -> not resolved
    assert verify(q, _R("链接15分钟内有效", False)).resolved is False
    # wrong escalation decision -> not resolved even with full coverage
    assert verify(q, _R("链接15分钟内有效，密码至少 8 位", True)).resolved is False


def test_verify_escalation_case():
    q = _q(required_keypoints=["转接人工"], should_escalate=True, difficulty="hard")
    assert verify(q, _R("我已为您转接人工", True)).resolved is True
    assert verify(q, _R("我已为您转接人工", False)).resolved is False
