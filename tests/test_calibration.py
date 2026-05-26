"""Offline tests for the per-domain threshold calibrator."""
from seagent.calibration import DomainCalibrator
from seagent.calibration.calibrator import DEFAULT_THRESHOLDS
from seagent.calibration.domain_inference import infer_domain


class _Hit:
    def __init__(self, ref, score=0.5, source="kb", topic=None):
        self.ref = ref
        self.score = score
        self.source = source
        if topic is not None:
            self.topic = topic


def test_calibrator_roundtrip_and_lookup(tmp_path):
    c = DomainCalibrator({"nimbusflow": {"escalate_tau": 0.55, "kb_conf_cap": 0.80},
                          "ecommerce": {"escalate_tau": 0.35}})
    # specific domain wins
    assert c.get_thresholds("nimbusflow")["escalate_tau"] == 0.55
    # missing keys fall back to defaults
    assert c.get_thresholds("ecommerce")["kb_conf_cap"] == DEFAULT_THRESHOLDS["kb_conf_cap"]
    # unknown domain -> defaults
    assert c.get_thresholds("nope") == DEFAULT_THRESHOLDS
    # persistence
    p = tmp_path / "th.json"
    c.save(str(p))
    loaded = DomainCalibrator.load(str(p))
    assert loaded.get_thresholds("nimbusflow")["escalate_tau"] == 0.55


def test_calibrator_drops_unknown_keys_and_bad_values():
    c = DomainCalibrator({"x": {"escalate_tau": "0.4", "nonsense_key": 1, "kb_conf_cap": "bad"}})
    th = c.get_thresholds("x")
    assert th["escalate_tau"] == 0.4
    assert th["kb_conf_cap"] == DEFAULT_THRESHOLDS["kb_conf_cap"]


def test_domain_inference_by_ref_prefix():
    assert infer_domain("q", [_Hit("kb_001", 0.7), _Hit("bx_billing_1", 0.3)]) == "nimbusflow"
    assert infer_domain("q", [_Hit("bx_order_2", 0.8), _Hit("kb_005", 0.4)]) == "ecommerce"
    assert infer_domain("q", []) == "default"


def test_domain_inference_topic_fallback():
    # ref with no recognised prefix -> use topic map
    h = _Hit("unknown_xyz", 0.7, topic="delivery")
    assert infer_domain("q", [h]) == "ecommerce"


def test_domain_inference_ignores_non_kb_hits():
    epi = _Hit("case_1", 0.9, source="episodic")
    kb = _Hit("kb_010", 0.4)
    assert infer_domain("q", [epi, kb]) == "nimbusflow"
