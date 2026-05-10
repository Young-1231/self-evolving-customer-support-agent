from seagent.memory.episodic import Case, EpisodicMemory
from seagent.memory.procedural import Playbook, ProceduralMemory


def test_episodic_retrieves_resolution_by_paraphrase():
    m = EpisodicMemory(path=None)
    m.add(Case(case_id="c1", query="我忘记密码了怎么重置",
               resolution="点击忘记密码，链接15分钟内有效", should_escalate=False))
    ps = m.retrieve("登录密码记不住了怎么重新设置", top_k=1)
    assert ps and ps[0].source == "episodic"
    assert "15分钟内有效" in ps[0].text
    assert ps[0].escalate_hint is False


def test_procedural_version_and_toggle():
    m = ProceduralMemory(path=None)
    pb = Playbook(playbook_id="pb_x", topic="billing", trigger_terms=["退款", "纠纷"],
                  guidance="转接账单团队", action="escalate")
    m.upsert(pb)
    assert len(m) == 1 and m.playbooks[0].version == 1
    m.upsert(Playbook(playbook_id="pb_x", topic="billing", trigger_terms=["退款"],
                      guidance="更新", action="escalate"))
    assert m.playbooks[0].version == 2  # upsert bumps version (auditable)

    ps = m.retrieve("我要申请退款纠纷处理")
    assert ps and ps[0].escalate_hint is True
    m.set_enabled("pb_x", False)        # rollback / disable
    assert m.retrieve("我要申请退款纠纷处理") == []
