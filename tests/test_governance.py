"""治理模块测试：生命周期 + 发布门禁 + 经验池治理。

仅用 base python，不依赖第三方库；guardrails 是否存在均能跑通(try-import)。
"""
from __future__ import annotations

import os

import pytest

from seagent.governance.lifecycle import (
    LifecycleState,
    PlaybookRegistry,
)
from seagent.governance.regression_gate import GateResult, evaluate_playbook
from seagent.governance.memory_hygiene import (
    dedup,
    detect_conflicts,
    scrub_case,
    ttl_filter,
)
from seagent.memory.procedural import Playbook, ProceduralMemory
from seagent.memory.episodic import Case
from seagent.eval.verifier import VerdictItem


def _pb(pid="pb-restart", topic="connectivity"):
    return Playbook(
        playbook_id=pid,
        topic=topic,
        trigger_terms=["wifi", "重启"],
        guidance="先重启路由器再观察 5 分钟。",
        action="answer",
        source_case_ids=["c1", "c2"],
        created_round=1,
    )


# ---------------------------------------------------------------------------
# (a) 生命周期：状态流转 + 审计日志 + rollback 生效
# ---------------------------------------------------------------------------
def test_lifecycle_flow_audit_and_rollback(tmp_path):
    reg_path = os.path.join(str(tmp_path), "governance", "playbook_registry.json")
    audit_path = os.path.join(str(tmp_path), "governance", "audit_log.jsonl")
    procedural = ProceduralMemory(path=None)
    reg = PlaybookRegistry(reg_path, audit_path, procedural=procedural)

    pb = _pb()
    reg.propose(pb, proposer="reflector")
    assert reg.get(pb.playbook_id).state == LifecycleState.PROPOSED.value

    reg.approve(pb.playbook_id, approver="alice")
    assert reg.get(pb.playbook_id).state == LifecycleState.APPROVED.value
    assert reg.get(pb.playbook_id).approver == "alice"

    reg.promote_to_canary(pb.playbook_id)
    assert reg.get(pb.playbook_id).state == LifecycleState.CANARY.value

    # activate 才把 playbook 写入 ProceduralMemory 并 enabled
    assert len(procedural) == 0
    reg.activate(pb.playbook_id)
    assert reg.get(pb.playbook_id).state == LifecycleState.ACTIVE.value
    assert len(procedural) == 1
    assert procedural.playbooks[0].enabled is True

    # rollback 生效：状态变终态 + 业务侧被禁用 + 记录原因
    reg.rollback(pb.playbook_id, actor="oncall", reason="SLO 回归: 解决率下跌")
    assert reg.get(pb.playbook_id).state == LifecycleState.ROLLED_BACK.value
    assert reg.get(pb.playbook_id).rollback_reason
    assert procedural.playbooks[0].enabled is False

    # 审计日志：每步都有记录，且含 who/when/what
    log = reg.read_audit_log()
    actions = [e["action"] for e in log]
    assert "propose" in actions
    assert "->approved" in actions
    assert "->canary" in actions
    assert "->active" in actions
    assert "->rolled_back" in actions
    for e in log:
        assert e["ts"] and e["actor"] and e["playbook_id"]


def test_lifecycle_illegal_transition_rejected(tmp_path):
    reg = PlaybookRegistry(
        os.path.join(str(tmp_path), "g", "reg.json"),
        os.path.join(str(tmp_path), "g", "audit.jsonl"),
    )
    pb = _pb()
    reg.propose(pb)
    # 不能从 proposed 直接 activate(必须先 approve→canary)
    with pytest.raises(ValueError):
        reg.activate(pb.playbook_id)
    # 终态后不可再转移
    reg.deprecate(pb.playbook_id, actor="alice", reason="弃用")
    with pytest.raises(ValueError):
        reg.approve(pb.playbook_id, approver="bob")


def test_lifecycle_registry_persisted_and_reloaded(tmp_path):
    reg_path = os.path.join(str(tmp_path), "g", "reg.json")
    audit_path = os.path.join(str(tmp_path), "g", "audit.jsonl")
    reg = PlaybookRegistry(reg_path, audit_path)
    reg.propose(_pb())
    reg.approve("pb-restart", approver="alice")

    # 重新加载注册表，状态应被持久化
    reg2 = PlaybookRegistry(reg_path, audit_path)
    assert reg2.get("pb-restart").state == LifecycleState.APPROVED.value
    assert reg2.get("pb-restart").approver == "alice"


# ---------------------------------------------------------------------------
# (b) regression gate：坏 playbook -> FAIL，好 playbook -> PASS
# ---------------------------------------------------------------------------
def _verdicts(resolved_flags, escalate_pred_gold):
    """构造一批 VerdictItem。escalate_pred_gold: List[(pred, gold)]。"""
    out = []
    for i, (res, (pe, ge)) in enumerate(zip(resolved_flags, escalate_pred_gold)):
        out.append(VerdictItem(
            query_id=f"q{i}", group=f"g{i % 3}", difficulty="easy",
            coverage=1.0 if res else 0.0,
            answer_correct=res, escalation_correct=(pe == ge),
            resolved=res, pred_escalate=pe, gold_escalate=ge,
        ))
    return out


def test_regression_gate_pass_and_fail():
    # baseline: 3/4 resolved；最后一条因 keypoint 覆盖不足未解决(转人工决策本就正确)
    from seagent.eval.metrics import aggregate
    base_v = _verdicts([True, True, True, False],
                       [(True, True), (False, False), (False, False), (False, False)])
    baseline = aggregate(base_v)

    # 好 playbook：把最后一条的覆盖补齐 -> 解决率上升、转人工率不变 -> PASS
    good_v = _verdicts([True, True, True, True],
                       [(True, True), (False, False), (False, False), (False, False)])
    good = evaluate_playbook("good-pb", baseline, lambda c: good_v)
    assert isinstance(good, GateResult)
    assert good.passed is True
    assert "PASS" in good.reason

    # 坏 playbook：解决率下跌(2/4) -> FAIL
    bad_v = _verdicts([True, False, True, False],
                      [(True, True), (False, False), (False, False), (False, False)])
    bad = evaluate_playbook("bad-pb", baseline, lambda c: bad_v)
    assert bad.passed is False
    assert "FAIL" in bad.reason
    assert bad.deltas["resolution_rate"] < 0


def test_regression_gate_intervention_rate_regression():
    """转人工率反向退化(变高超阈值)也应 FAIL。"""
    from seagent.eval.metrics import aggregate
    # baseline: 无人转人工
    base_v = _verdicts([True, True, True, True],
                       [(False, False)] * 4)
    baseline = aggregate(base_v)
    # 候选：3/4 误转人工，human_intervention_rate 从 0 -> 0.75，远超 0.05 容忍
    cand_v = _verdicts([True, True, True, True],
                       [(True, False), (True, False), (True, False), (False, False)])
    res = evaluate_playbook("over-escalate", baseline, lambda c: cand_v)
    assert res.passed is False
    assert "human_intervention_rate" in res.reason


# ---------------------------------------------------------------------------
# (c) memory hygiene: dedup / ttl / conflict / scrub
# ---------------------------------------------------------------------------
def test_dedup_merges_near_duplicates():
    cases = [
        Case("c1", "wifi 连不上怎么办", "请重启路由器", False, topic="net", learned_round=1),
        Case("c2", "wifi 连不上 怎么办", "请重启路由器", False, topic="net", learned_round=2),
        Case("c3", "如何申请退款", "在订单页点击退款", False, topic="billing", learned_round=3),
    ]
    out = dedup(cases, threshold=0.8)
    ids = {c.case_id for c in out}
    assert "c1" in ids and "c3" in ids
    assert "c2" not in ids          # 近重复被合并
    assert len(cases) == 3          # 入参未被修改


def test_ttl_filter_expires_old_cases():
    cases = [
        Case("old", "q", "r", False, learned_round=1),
        Case("mid", "q", "r", False, learned_round=5),
        Case("new", "q", "r", False, learned_round=8),
    ]
    kept = ttl_filter(cases, current_round=10, ttl_rounds=6)  # 保留 round>4
    ids = {c.case_id for c in kept}
    assert ids == {"mid", "new"}
    # None = 永不过期
    assert len(ttl_filter(cases, current_round=10, ttl_rounds=None)) == 3


def test_detect_conflicts_escalation_and_phrase():
    cases = [
        # 同题但转人工决策相反
        Case("a1", "网络故障如何处理", "自助重启即可", False, topic="net", learned_round=1),
        Case("a2", "网络故障如何处理", "需转人工排查", True, topic="net", learned_round=2),
        # 互斥短语
        Case("b1", "路由问题", "无需重启设备", False, topic="net", learned_round=3),
        Case("b2", "路由配置", "需要重启设备生效", False, topic="net", learned_round=4),
        # 不同 topic，不应判冲突
        Case("c1", "退款问题", "可以退款", False, topic="billing", learned_round=5),
    ]
    conflicts = detect_conflicts(cases)
    pairs = {tuple(sorted((c.case_a, c.case_b))) for c in conflicts}
    assert ("a1", "a2") in pairs
    assert ("b1", "b2") in pairs
    # billing 那条孤立，不应进任何冲突对
    assert all("c1" not in p for p in pairs)


def test_scrub_case_redacts_pii():
    case = Case(
        "s1",
        "我的邮箱是 zhangsan@example.com，手机号 13800138000",
        "请联系 13900139000，或发到 admin@corp.cn",
        False,
        topic="account",
        learned_round=1,
    )
    scrubbed = scrub_case(case)
    # 原始 PII 不应残留
    for leak in ("zhangsan@example.com", "13800138000", "13900139000", "admin@corp.cn"):
        assert leak not in scrubbed.query
        assert leak not in scrubbed.resolution
    # 占位符存在(无论走 guardrails 还是正则兜底)
    assert "<EMAIL>" in scrubbed.query or "<EMAIL>" in scrubbed.resolution
    # 非文本字段不变 + 入参未被修改
    assert scrubbed.case_id == "s1" and scrubbed.topic == "account"
    assert "13800138000" in case.query
