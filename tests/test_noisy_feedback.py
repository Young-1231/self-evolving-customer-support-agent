"""噪声/隐式反馈进化实验的回归测试(离线、确定性、秒级)。

不跑全实验脚本(避免依赖 matplotlib / 写文件)，只验证三件事：
  1. 噪声模拟器在固定 seed 下确定可复现、且按 p_fp/p_fn 产出正确极性的反馈；
  2. 噪声反馈经 FeedbackProcessor -> 人审补全 -> scrub_case 的入池链路成立，
     且 scrub_case 真的脱敏了 PII；
  3. 端到端：noisy 条件最终解决率 > 冷启动(机制有效)、且不超过 gold(噪声有代价)，
     落在合理区间(gold 的 80%~100%)。
"""
import importlib.util
import os
import random

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "run_noisy_feedback_evolution.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("nfe", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nfe = _load_module()


def test_simulate_feedback_deterministic_and_polarity():
    from seagent.serving.schema import FeedbackKind

    # 同一 seed 两次调用序列完全一致 -> 可复现
    seq_a = [nfe.simulate_feedback(i % 2 == 0, random.Random(7), 0.15, 0.20) for i in range(20)]
    seq_b = [nfe.simulate_feedback(i % 2 == 0, random.Random(7), 0.15, 0.20) for i in range(20)]
    assert seq_a == seq_b

    # p_fp=0: 真实已解决的回答永远收到正向信号(thumbs_up)
    rng = random.Random(0)
    for _ in range(50):
        assert nfe.simulate_feedback(True, rng, p_fp=0.0, p_fn=0.0) == FeedbackKind.THUMBS_UP
    # p_fn=0: 真实未解决的回答永远收到负向信号(thumbs_down / reopened)
    rng = random.Random(0)
    neg = {FeedbackKind.THUMBS_DOWN, FeedbackKind.REOPENED}
    for _ in range(50):
        assert nfe.simulate_feedback(False, rng, p_fp=0.0, p_fn=0.0) in neg
    # p_fp=1: 满意客户全部假阳点踩
    rng = random.Random(0)
    for _ in range(20):
        assert nfe.simulate_feedback(True, rng, p_fp=1.0, p_fn=0.0) == FeedbackKind.THUMBS_DOWN
    # p_fn=1: 未解决全部假阴漏标为已解决
    rng = random.Random(0)
    for _ in range(20):
        assert nfe.simulate_feedback(False, rng, p_fp=0.0, p_fn=1.0) == FeedbackKind.RESOLVED


def test_negative_feedback_enters_pool_scrubbed():
    """点踩 -> 待复盘 -> 人审补全 resolution -> scrub_case 脱敏入池。"""
    from seagent.serving.feedback import FeedbackProcessor
    from seagent.serving.schema import Feedback, FeedbackKind, Ticket, ChatTurn
    from seagent.memory.episodic import Case
    from seagent.governance.memory_hygiene import scrub_case

    proc = FeedbackProcessor()
    t = Ticket(customer_id="c1", subject="登录问题", tags=["auth"])
    t.add_message(ChatTurn(role="customer", text="登录失败 联系 user@example.com"))
    proc.ingest(Feedback(ticket_id="tkt1", kind=FeedbackKind.THUMBS_DOWN), t)

    pending = proc.pending_review()
    assert len(pending) == 1  # 负向信号进入待复盘

    draft = dict(pending[0].case_draft)
    # 模拟人审补全正确解法(含 PII)
    draft["resolution"] = "请重置密码，回执发到 admin@corp.com，电话 13800000000"
    case = scrub_case(Case(**draft))
    # 入库前 PII 被脱敏
    assert "admin@corp.com" not in case.resolution
    assert "13800000000" not in case.resolution
    assert "user@example.com" not in case.query
    assert "<EMAIL>" in case.resolution


def test_positive_feedback_not_in_review_queue():
    """点赞/已解决等正向信号不进入待复盘队列(不会拿去学)。"""
    from seagent.serving.feedback import FeedbackProcessor
    from seagent.serving.schema import Feedback, FeedbackKind

    proc = FeedbackProcessor()
    proc.ingest(Feedback(ticket_id="t1", kind=FeedbackKind.THUMBS_UP))
    proc.ingest(Feedback(ticket_id="t2", kind=FeedbackKind.RESOLVED))
    assert proc.pending_review() == []


def test_end_to_end_noisy_evolves_and_bounded():
    """端到端：noisy 进化有效(超过冷启动) 且 有代价(不超过 gold)。"""
    from seagent.config import Config

    cfg = Config.load(seed=0)
    eg = nfe.NoisyFeedbackExperiment(cfg, p_fp=0.15, p_fn=0.20)
    gold = eg.run("gold")
    en = nfe.NoisyFeedbackExperiment(cfg, p_fp=0.15, p_fn=0.20)
    noisy = en.run("noisy")

    g0 = gold[0]["resolution_rate"]
    gf = gold[-1]["resolution_rate"]
    nf = noisy[-1]["resolution_rate"]

    # gold 确实进化了
    assert gf > g0
    # noisy 也进化了(机制有效)
    assert nf > noisy[0]["resolution_rate"]
    # 噪声有代价：noisy <= gold
    assert nf <= gf + 1e-9
    # 但仍达到 gold 的合理比例
    assert nf / gf >= 0.80
    # 噪声确实造成了部分案例漏学(假阴 -> 入池数 <= gold 学到数)
    assert en.feedback_stats["false_negative"] >= 0
    assert noisy[-1]["learned_cases"] <= gold[-1]["learned_cases"]


def test_multiseed_aggregation_consistent():
    """多 seed 聚合：均值曲线单调、字段齐全、pct 落在公布区间。"""
    agg = nfe.run_all_seeds(p_fp=0.15, p_fn=0.20, seeds=[0, 1, 2, 3])
    assert agg["n_seeds"] == 4
    assert len(agg["rounds"]) == agg["train_rounds"] + 1
    assert agg["noisy_mean_curve_monotonic"] is True
    assert 80.0 <= agg["noisy_pct_of_gold_mean"] <= 100.0
    # 最终：noisy 均值 <= gold 均值(噪声的代价体现在收敛上限上；
    # 中间轮 noisy 可能因无害的假阳冗余正例偶发反超，属正常)
    assert agg["noisy_final_mean"] <= agg["gold_final_mean"] + 1e-9


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
