"""SkillStore：一个目录 = 一组 markdown skill 文件。

对 ProceduralMemory 暴露的接口与原 jsonl 版完全一致——``upsert`` /
``set_enabled`` / ``retrieve`` / ``__len__`` ——因此可以直接做底层替换：

    proc = ProceduralMemory(skill_store=SkillStore("data/skills/"))

检索策略沿用 ProceduralMemory 原 BM25 trigger-overlap 评分公式，保证替换前后
检索结果对同一组 playbook 等价（tests/test_skills.py 严格验证）。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from ..llm.base import Passage
from ..memory.bm25 import tokenize
from ..memory.procedural import Playbook
from .format import (
    Skill,
    parse_skill,
    dump_skill,
    playbook_to_skill,
    skill_to_playbook,
)


class SkillStore:
    """以目录形式持久化 skills；与 Playbook 双向同步。"""

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: Dict[str, Skill] = {}
        if skills_dir and os.path.isdir(skills_dir):
            self.load_all()

    # ---------------- persistence ----------------
    def load_all(self) -> List[Skill]:
        """扫描目录下所有 ``*.md``，加载成 Skill。"""
        self.skills = {}
        if not (self.skills_dir and os.path.isdir(self.skills_dir)):
            return []
        for name in sorted(os.listdir(self.skills_dir)):
            if not name.endswith(".md") or name.startswith("_"):
                continue
            path = os.path.join(self.skills_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            sk = parse_skill(text)
            if not sk.skill_id:
                # 用文件名兜底
                sk.skill_id = os.path.splitext(name)[0]
            self.skills[sk.skill_id] = sk
        return list(self.skills.values())

    def save(self, skill: Skill) -> str:
        """落盘单个 skill；返回路径。"""
        if not self.skills_dir:
            raise ValueError("SkillStore 没有 skills_dir，无法落盘")
        os.makedirs(self.skills_dir, exist_ok=True)
        path = os.path.join(self.skills_dir, f"{skill.skill_id}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(dump_skill(skill))
        return path

    # ---------------- governance-compatible API ----------------
    def get(self, skill_id: str) -> Optional[Skill]:
        return self.skills.get(skill_id)

    def list_skills(self) -> List[Skill]:
        return list(self.skills.values())

    def upsert_skill(self, skill: Skill) -> Skill:
        """与 ProceduralMemory.upsert 同义：同 id 则 version+=1，再覆盖。"""
        prev = self.skills.get(skill.skill_id)
        if prev is not None:
            skill.version = prev.version + 1
        self.skills[skill.skill_id] = skill
        if self.skills_dir:
            self.save(skill)
        return skill

    def upsert(self, pb: Playbook) -> Playbook:
        """ProceduralMemory.upsert 透传：Playbook → Skill 再落盘。"""
        prev_sk = self.skills.get(pb.playbook_id)
        sk = playbook_to_skill(
            pb,
            name=prev_sk.name if prev_sk else pb.playbook_id,
            description=prev_sk.description if prev_sk else "",
            metadata=dict(prev_sk.metadata) if prev_sk else None,
            body=prev_sk.body if prev_sk else "",
        )
        if prev_sk is not None:
            # 与 ProceduralMemory 行为一致：upsert 一次 → version += 1
            sk.version = prev_sk.version + 1
            # 但若 caller 已经传了更高 version（治理侧 promote），就尊重 caller
            if pb.version > sk.version:
                sk.version = pb.version
        self.skills[pb.playbook_id] = sk
        if self.skills_dir:
            self.save(sk)
        return skill_to_playbook(sk)

    def set_enabled(self, skill_id: str, enabled: bool) -> bool:
        sk = self.skills.get(skill_id)
        if sk is None:
            return False
        sk.enabled = enabled
        if self.skills_dir:
            self.save(sk)
        return True

    def delete(self, skill_id: str) -> bool:
        sk = self.skills.pop(skill_id, None)
        if sk is None:
            return False
        if self.skills_dir:
            path = os.path.join(self.skills_dir, f"{skill_id}.md")
            if os.path.exists(path):
                os.remove(path)
        return True

    def __len__(self) -> int:
        return len(self.skills)

    # ---------------- retrieval ----------------
    def retrieve(self, query: str, top_k: int = 2) -> List[Passage]:
        """BM25-style trigger overlap，公式与 ProceduralMemory.retrieve 完全一致。"""
        q = set(tokenize(query))
        scored = []
        for sk in self.skills.values():
            if not sk.enabled:
                continue
            trig = set()
            for t in sk.triggers:
                trig.update(tokenize(t))
            if not trig:
                continue
            overlap = len(q & trig) / len(trig)
            if overlap > 0:
                scored.append((overlap, sk))
        scored.sort(key=lambda x: -x[0])
        out: List[Passage] = []
        for overlap, sk in scored[:top_k]:
            out.append(
                Passage(
                    source="playbook",  # 检索来源对外仍是 "playbook"，下游兼容
                    text=sk.guidance(),
                    score=min(1.0, 0.5 + 0.5 * overlap),
                    ref=sk.skill_id,
                    escalate_hint=(sk.action == "escalate"),
                )
            )
        return out

    # ProceduralMemory 还会读 ``.playbooks`` 列表（治理 demo 里有遍历），
    # 这里提供一个等价 view，按需懒构造。
    @property
    def playbooks(self) -> List[Playbook]:
        return [skill_to_playbook(s) for s in self.skills.values()]
