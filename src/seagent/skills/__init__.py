"""Skills 化的 procedural memory：markdown + frontmatter 单文件 SOP。

对标 Claude Code Skills 范式（一个 skill = 一份可独立编辑、git-friendly 的
markdown 文件，frontmatter 描述元数据，body 描述 SOP），并保留与项目原有
``Playbook`` 数据结构的**双向无损转换**——governance / regression_gate /
Reflector 等上游模块的接口完全不变。

本模块只新增能力，不修改任何已有的 ``procedural.py`` / ``governance`` /
``reflector`` 行为；启用方式是把 ``ProceduralMemory(skill_store=...)``
的可选参数指向一个 ``SkillStore``，即可让 procedural memory 的底层从
jsonl 切换到一个 ``data/skills/*.md`` 的目录。
"""
from .format import (
    Skill,
    parse_skill,
    dump_skill,
    skill_to_playbook,
    playbook_to_skill,
)
from .store import SkillStore
from .manifest import SkillManifest, generate_manifest

__all__ = [
    "Skill",
    "parse_skill",
    "dump_skill",
    "skill_to_playbook",
    "playbook_to_skill",
    "SkillStore",
    "SkillManifest",
    "generate_manifest",
]
