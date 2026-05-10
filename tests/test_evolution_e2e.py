"""End-to-end: the self-evolution loop must actually improve the agent."""
from seagent.config import Config
from seagent.eval.harness import Experiment
from seagent.llm.mock import MockBackend


def _run():
    cfg = Config().resolve()
    cfg.train_rounds = 6
    return Experiment(cfg, MockBackend()).run()


def test_static_is_flat_and_evolution_improves():
    res = _run()
    static, epi, full = res["static"], res["episodic"], res["full"]

    # cold-start baseline is identical across conditions
    assert static[0]["resolution_rate"] == epi[0]["resolution_rate"] == full[0]["resolution_rate"]

    # no-evolution baseline stays flat
    assert static[-1]["resolution_rate"] == static[0]["resolution_rate"]

    # experience accumulation lifts resolution substantially
    assert epi[-1]["resolution_rate"] >= epi[0]["resolution_rate"] + 0.25
    # repeated errors drop
    assert epi[-1]["repeated_error_rate"] < 0.6
    # learning the escalation policy improves F1 from the cold start
    assert epi[-1]["escalation_f1"] > epi[0]["escalation_f1"]

    # playbooks never hurt and help keypoint coverage
    assert full[-1]["resolution_rate"] >= epi[-1]["resolution_rate"] - 1e-9
    assert full[-1]["keypoint_coverage"] >= epi[-1]["keypoint_coverage"] - 1e-9
    assert full[-1]["playbooks"] > 0


def test_monotonic_trend_in_cases():
    epi = _run()["episodic"]
    cases = [r["learned_cases"] for r in epi]
    assert cases == sorted(cases)  # memory only grows
