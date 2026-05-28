"""Tests for the markdown-based Skills layer (v2.2 R6a).

Covers:
  - frontmatter parser via PyYAML AND via the fallback parser
  - Skill <-> Playbook round-trip is lossless
  - SkillStore load/save/upsert/delete/retrieve
  - ProceduralMemory(skill_store=...) is behaviourally equivalent to the
    classic jsonl-backed ProceduralMemory for the same logical playbooks
  - manifest generation
"""
from __future__ import annotations

import json
import os

import pytest

from seagent.memory.procedural import Playbook, ProceduralMemory
from seagent.skills import (
    Skill,
    SkillStore,
    dump_skill,
    generate_manifest,
    parse_skill,
    playbook_to_skill,
    skill_to_playbook,
)
from seagent.skills import format as fmt


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_skill() -> Skill:
    return Skill(
        skill_id="pb_billing_ans",
        name="年付月付退款",
        description="处理年付转月付时的差额退款咨询",
        topic="billing",
        triggers=["年付", "月付", "退款", "余额"],
        action="answer",
        version=2,
        enabled=True,
        created_round=3,
        source_case_ids=["q003", "q027"],
        metadata={"author": "reflector", "reviewed_by": "ops-team"},
        body="# Guidance\n\n年付转月付差额走 account credit，下次续费抵扣。\n",
    )


@pytest.fixture
def sample_playbook() -> Playbook:
    return Playbook(
        playbook_id="pb_billing_ans",
        topic="billing",
        trigger_terms=["年付", "月付", "退款", "余额"],
        guidance="年付转月付差额走 account credit，下次续费抵扣。",
        action="answer",
        enabled=True,
        version=2,
        source_case_ids=["q003", "q027"],
        created_round=3,
    )


# ---------------------------------------------------------------------------
# frontmatter parser (both PyYAML & fallback paths)
# ---------------------------------------------------------------------------
def test_dump_then_parse_round_trip(sample_skill):
    text = dump_skill(sample_skill)
    assert text.startswith("---\n")
    assert "skill_id" in text
    parsed = parse_skill(text)
    # 所有字段无损保留
    assert parsed.skill_id == sample_skill.skill_id
    assert parsed.name == sample_skill.name
    assert parsed.description == sample_skill.description
    assert parsed.topic == sample_skill.topic
    assert parsed.triggers == sample_skill.triggers
    assert parsed.action == sample_skill.action
    assert parsed.version == sample_skill.version
    assert parsed.enabled is True
    assert parsed.created_round == sample_skill.created_round
    assert parsed.source_case_ids == sample_skill.source_case_ids
    assert parsed.metadata == sample_skill.metadata
    assert "account credit" in parsed.guidance()


def test_fallback_yaml_parser_handles_subset(monkeypatch, sample_skill):
    """关掉 PyYAML，确认回退手写 parser 也能完成 round-trip。"""
    monkeypatch.setattr(fmt, "_HAS_YAML", False)
    monkeypatch.setattr(fmt, "_yaml", None)
    text = dump_skill(sample_skill)
    parsed = parse_skill(text)
    assert parsed.skill_id == sample_skill.skill_id
    assert parsed.triggers == sample_skill.triggers
    assert parsed.metadata == sample_skill.metadata
    assert parsed.action == sample_skill.action
    assert parsed.version == sample_skill.version


def test_parse_skill_handles_missing_frontmatter():
    text = "just some markdown without frontmatter"
    sk = parse_skill(text)
    assert sk.skill_id == ""
    assert "markdown without frontmatter" in sk.body


def test_parse_skill_handles_escalate_action():
    text = (
        "---\n"
        "skill_id: pb_x\n"
        "topic: general\n"
        "action: escalate\n"
        "triggers:\n"
        "  - 退款\n"
        "  - 纠纷\n"
        "---\n"
        "# Guidance\n\n遇到任何账单纠纷一律转人工。\n"
    )
    sk = parse_skill(text)
    assert sk.action == "escalate"
    assert sk.triggers == ["退款", "纠纷"]
    assert "转人工" in sk.guidance()


def test_parse_skill_inline_list():
    text = (
        "---\n"
        "skill_id: pb_inline\n"
        'triggers: ["年付", "月付"]\n'
        "---\n"
        "# Guidance\nfoo\n"
    )
    sk = parse_skill(text)
    assert sk.triggers == ["年付", "月付"]


# ---------------------------------------------------------------------------
# Skill <-> Playbook round-trip
# ---------------------------------------------------------------------------
def test_playbook_skill_round_trip_lossless(sample_playbook):
    sk = playbook_to_skill(sample_playbook)
    pb2 = skill_to_playbook(sk)
    assert pb2.playbook_id == sample_playbook.playbook_id
    assert pb2.topic == sample_playbook.topic
    assert pb2.trigger_terms == sample_playbook.trigger_terms
    assert pb2.guidance == sample_playbook.guidance
    assert pb2.action == sample_playbook.action
    assert pb2.enabled == sample_playbook.enabled
    assert pb2.version == sample_playbook.version
    assert pb2.source_case_ids == sample_playbook.source_case_ids
    assert pb2.created_round == sample_playbook.created_round


def test_skill_playbook_round_trip_through_markdown(sample_playbook, tmp_path):
    sk = playbook_to_skill(sample_playbook, name="bill", description="x")
    text = dump_skill(sk)
    sk2 = parse_skill(text)
    pb2 = skill_to_playbook(sk2)
    # 反向：业务字段全保留
    assert pb2 == sample_playbook


# ---------------------------------------------------------------------------
# SkillStore basic ops
# ---------------------------------------------------------------------------
def test_store_save_load_roundtrip(tmp_path, sample_skill):
    store = SkillStore(str(tmp_path))
    store.upsert_skill(sample_skill)
    # reload from disk in a fresh store
    store2 = SkillStore(str(tmp_path))
    got = store2.get(sample_skill.skill_id)
    assert got is not None
    assert got.triggers == sample_skill.triggers
    assert got.metadata == sample_skill.metadata


def test_store_upsert_bumps_version(tmp_path, sample_playbook):
    store = SkillStore(str(tmp_path))
    store.upsert(sample_playbook)
    pb2 = Playbook(
        playbook_id=sample_playbook.playbook_id,
        topic="billing",
        trigger_terms=["退款"],
        guidance="更新版",
        action="answer",
    )
    store.upsert(pb2)
    sk = store.get(sample_playbook.playbook_id)
    assert sk is not None
    # 第一次 version 来自 sample_playbook (=2)，第二次 upsert 应 +1
    assert sk.version == 3
    assert sk.triggers == ["退款"]


def test_store_set_enabled_and_delete(tmp_path, sample_playbook):
    store = SkillStore(str(tmp_path))
    store.upsert(sample_playbook)
    assert store.set_enabled(sample_playbook.playbook_id, False) is True
    assert store.retrieve("年付 月付 退款") == []   # disabled → no hit
    store.set_enabled(sample_playbook.playbook_id, True)
    assert store.retrieve("年付 月付 退款"), "重新启用后应能检索到"
    assert store.delete(sample_playbook.playbook_id) is True
    assert store.get(sample_playbook.playbook_id) is None
    assert not os.path.exists(os.path.join(str(tmp_path), f"{sample_playbook.playbook_id}.md"))


def test_store_retrieve_uses_trigger_overlap(tmp_path):
    store = SkillStore(str(tmp_path))
    store.upsert(Playbook(
        playbook_id="pb_a", topic="billing",
        trigger_terms=["年付", "月付"],
        guidance="A guidance", action="answer",
    ))
    store.upsert(Playbook(
        playbook_id="pb_b", topic="login",
        trigger_terms=["密码", "登录"],
        guidance="B guidance", action="escalate",
    ))
    out = store.retrieve("我想从年付转月付", top_k=2)
    assert out and out[0].ref == "pb_a"
    assert out[0].source == "playbook"
    out2 = store.retrieve("登录密码忘了", top_k=2)
    assert out2 and out2[0].ref == "pb_b"
    assert out2[0].escalate_hint is True


# ---------------------------------------------------------------------------
# ProceduralMemory(skill_store=...) ↔ classic ProceduralMemory equivalence
# ---------------------------------------------------------------------------
def _seed_playbooks():
    return [
        Playbook(
            playbook_id="pb_billing_ans", topic="billing",
            trigger_terms=["年付", "月付", "退款"],
            guidance="差额走 credit", action="answer",
        ),
        Playbook(
            playbook_id="pb_login_esc", topic="login",
            trigger_terms=["密码", "登录"],
            guidance="转人工", action="escalate",
        ),
    ]


def test_procedural_memory_skill_store_behaves_like_classic(tmp_path):
    classic = ProceduralMemory(path=None)
    store = SkillStore(str(tmp_path))
    skilled = ProceduralMemory(skill_store=store)
    for pb in _seed_playbooks():
        # ProceduralMemory.upsert 会修改入参对象 (version 自增)，
        # 给两份独立拷贝避免相互影响
        from copy import deepcopy
        classic.upsert(deepcopy(pb))
        skilled.upsert(deepcopy(pb))

    assert len(classic) == len(skilled) == 2

    queries = [
        "我想从年付转月付",
        "我密码记不起来怎么办",
        "完全不相关的问候",
    ]
    for q in queries:
        a = classic.retrieve(q, top_k=2)
        b = skilled.retrieve(q, top_k=2)
        assert [p.ref for p in a] == [p.ref for p in b], f"query={q!r}"
        assert [p.escalate_hint for p in a] == [p.escalate_hint for p in b]
        assert [round(p.score, 4) for p in a] == [round(p.score, 4) for p in b]
        assert [p.text for p in a] == [p.text for p in b]

    # set_enabled 也等价
    assert classic.set_enabled("pb_login_esc", False) is True
    assert skilled.set_enabled("pb_login_esc", False) is True
    assert classic.retrieve("密码登录") == skilled.retrieve("密码登录") == []


def test_procedural_memory_jsonl_path_still_default(tmp_path):
    """skill_store=None 时严格走旧 jsonl 路径，对应已有 162 tests 的契约。"""
    jsonl = tmp_path / "proc.jsonl"
    pm = ProceduralMemory(path=str(jsonl))
    pm.upsert(Playbook(playbook_id="pb_x", topic="t",
                        trigger_terms=["x"], guidance="g", action="answer"))
    # 落盘成 jsonl，不是 markdown
    assert jsonl.exists()
    with open(jsonl, "r", encoding="utf-8") as f:
        first = json.loads(f.readline())
    assert first["playbook_id"] == "pb_x"


# ---------------------------------------------------------------------------
# manifest generation
# ---------------------------------------------------------------------------
def test_generate_manifest(tmp_path, sample_skill):
    store = SkillStore(str(tmp_path))
    store.upsert_skill(sample_skill)
    # 再加一条以验证 manifest 含多条
    store.upsert_skill(Skill(
        skill_id="pb_login_esc", name="登录转人工",
        topic="login", triggers=["密码", "登录"],
        action="escalate", body="# Guidance\n转人工\n",
    ))
    m = generate_manifest(str(tmp_path), write=True)
    assert len(m.skills) == 2
    ids = {e.skill_id for e in m.skills}
    assert ids == {"pb_billing_ans", "pb_login_esc"}
    # 每条 entry 不含 body，只含元数据
    for e in m.skills:
        assert e.path.endswith(".md")
        assert e.triggers
    # manifest.json 落盘
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    assert os.path.exists(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["version"] == "1"
    assert len(data["skills"]) == 2
