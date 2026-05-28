"""SkillManifest：一个 skills 目录的元数据汇总（ClawHub 风格 manifest.json）。

为什么需要 manifest？真实场景下 skills 数量可能很多（ClawHub 社区已 13k+），
agent 每轮对话不可能把每个 skill body 都读到 context；常规做法是：

  1. 启动时只加载 ``manifest.json``（只含 id / name / triggers / description）
  2. 检索阶段拿 triggers 做粗筛
  3. 命中的 skill 再懒加载完整 markdown body

本模块只负责生成 manifest；懒加载策略留给上层 Agent 决定。

注：与 Claude Code / ClawHub 的具体 manifest schema 仅在概念层对齐
（id / name / triggers / description），ClawHub 的真实发布接口待真实接入后验证。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from .format import Skill, parse_skill


@dataclass
class SkillManifestEntry:
    skill_id: str
    name: str
    description: str
    topic: str
    triggers: List[str] = field(default_factory=list)
    action: str = "answer"
    version: int = 1
    enabled: bool = True
    path: str = ""              # 相对 manifest 所在目录的文件路径


@dataclass
class SkillManifest:
    version: str = "1"
    skills: List[SkillManifestEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "skills": [asdict(e) for e in self.skills],
        }


def generate_manifest(skills_dir: str, write: bool = True) -> SkillManifest:
    """扫描目录，生成 manifest（默认会写 ``manifest.json``）。"""
    if not os.path.isdir(skills_dir):
        raise FileNotFoundError(f"skills_dir 不存在: {skills_dir}")
    entries: List[SkillManifestEntry] = []
    for name in sorted(os.listdir(skills_dir)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        path = os.path.join(skills_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            sk = parse_skill(f.read())
        if not sk.skill_id:
            sk.skill_id = os.path.splitext(name)[0]
        entries.append(SkillManifestEntry(
            skill_id=sk.skill_id,
            name=sk.name or sk.skill_id,
            description=sk.description,
            topic=sk.topic,
            triggers=list(sk.triggers),
            action=sk.action,
            version=sk.version,
            enabled=sk.enabled,
            path=name,
        ))
    manifest = SkillManifest(skills=entries)
    if write:
        out_path = os.path.join(skills_dir, "manifest.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)
    return manifest


def _main(argv: List[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m seagent.skills.manifest <skills_dir>", file=sys.stderr)
        return 2
    skills_dir = argv[0]
    m = generate_manifest(skills_dir, write=True)
    print(f"[manifest] 已生成 {os.path.join(skills_dir, 'manifest.json')}，含 {len(m.skills)} 条 skill")
    for e in m.skills:
        print(f"  - {e.skill_id} [{e.topic}/{e.action}] {e.name}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
