"""Skill 文件格式 = YAML frontmatter + markdown body。

对标 Claude Code Skills（``--- ... --- + body``）。本模块负责：
  - ``parse_skill(text)`` 把一份 .md 文本解析成 ``Skill`` dataclass
  - ``dump_skill(skill)``  把 ``Skill`` 反向序列化成 .md 文本
  - ``skill_to_playbook`` / ``playbook_to_skill`` 与现有 ``Playbook`` 双向无损互转

设计原则
--------
1. **零强依赖**：优先用 PyYAML（若安装），缺则回退到一个小型手写 parser，
   只覆盖 dict / list / str / int / bool（够 skill frontmatter 用即可）。
2. **双向无损**：``round_trip = parse(dump(s)) == s``，包括元数据扩展字段。
3. **markdown body 任意**：不解析 body 结构，整段当 ``guidance`` 透传；
   这样人工编辑 body 不会影响检索/治理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from ..memory.procedural import Playbook

try:                                # 优先用 PyYAML
    import yaml as _yaml            # type: ignore
    _HAS_YAML = True
except Exception:                   # 缺依赖时回退
    _yaml = None                    # type: ignore
    _HAS_YAML = False


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------
@dataclass
class Skill:
    """与 Claude Code Skills 范式对齐的 SOP 条目。"""

    skill_id: str
    name: str = ""
    description: str = ""
    topic: str = "general"
    triggers: List[str] = field(default_factory=list)
    action: str = "answer"                  # "answer" | "escalate"
    version: int = 1
    enabled: bool = True
    created_round: int = 0
    source_case_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    body: str = ""                          # markdown body（含 guidance）

    # body 中第一个一级标题之后到下一节之间的纯文本作为 "guidance" 摘要；
    # 找不到结构化区块时，整段 body 即 guidance。
    def guidance(self) -> str:
        return _extract_guidance(self.body)


# ---------------------------------------------------------------------------
# frontmatter parse / dump
# ---------------------------------------------------------------------------
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_skill(text: str) -> Skill:
    """解析一份 markdown skill 文件。

    缺 frontmatter 时整篇当 body，``skill_id`` 用空字符串占位；
    上层（SkillStore）会用文件名兜底补 id。
    """
    m = _FM_RE.match(text.lstrip("﻿"))   # 忽略可能的 BOM
    if not m:
        return Skill(skill_id="", body=text.strip())
    front_text, body = m.group(1), m.group(2)
    front = _load_yaml(front_text)
    if not isinstance(front, dict):
        front = {}
    return _skill_from_frontmatter(front, body)


def dump_skill(skill: Skill) -> str:
    """把 Skill 写回 markdown + frontmatter 文本。

    总是写 frontmatter（哪怕字段都是默认），方便 diff / 人工编辑。
    body 若空，会补一份带 # Guidance / ## When to apply / ## What to do 骨架
    的可读模板（落到 disk 上对招聘官/审核者更友好）。
    """
    front: Dict[str, Any] = {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "topic": skill.topic,
        "triggers": list(skill.triggers),
        "action": skill.action,
        "version": int(skill.version),
        "enabled": bool(skill.enabled),
        "created_round": int(skill.created_round),
        "source_case_ids": list(skill.source_case_ids),
    }
    if skill.metadata:
        front["metadata"] = dict(skill.metadata)
    fm_text = _dump_yaml(front).rstrip()
    body = skill.body.strip() or _default_body(skill)
    return f"---\n{fm_text}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# Playbook <-> Skill 双向无损
# ---------------------------------------------------------------------------
def skill_to_playbook(skill: Skill) -> Playbook:
    """Skill → Playbook（业务运行时仍用 Playbook 数据结构）。"""
    return Playbook(
        playbook_id=skill.skill_id,
        topic=skill.topic,
        trigger_terms=list(skill.triggers),
        guidance=skill.guidance(),
        action=skill.action,
        enabled=skill.enabled,
        version=skill.version,
        source_case_ids=list(skill.source_case_ids),
        created_round=skill.created_round,
    )


def playbook_to_skill(
    pb: Playbook,
    name: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    body: str = "",
) -> Skill:
    """Playbook → Skill。

    若调用方没提供 body，会用 ``pb.guidance`` 构造一个最小可读 body，
    再次 ``skill_to_playbook`` 时 ``guidance`` 字段保持不变（无损 round-trip）。
    """
    sk = Skill(
        skill_id=pb.playbook_id,
        name=name or pb.playbook_id,
        description=description,
        topic=pb.topic,
        triggers=list(pb.trigger_terms),
        action=pb.action,
        version=pb.version,
        enabled=pb.enabled,
        created_round=pb.created_round,
        source_case_ids=list(pb.source_case_ids),
        metadata=dict(metadata or {}),
        body=body.strip(),
    )
    if not sk.body:
        sk.body = _body_from_guidance(pb.guidance, action=pb.action)
    return sk


# ---------------------------------------------------------------------------
# body 内 # Guidance 提取
# ---------------------------------------------------------------------------
_H1_GUIDANCE = re.compile(r"(?im)^#\s*guidance\s*\n+([\s\S]*?)(?:\n#{1,6}\s|\Z)")


def _extract_guidance(body: str) -> str:
    """从 markdown body 抽出 ``# Guidance`` 节正文；找不到就回退整段 body。"""
    body = (body or "").strip()
    if not body:
        return ""
    m = _H1_GUIDANCE.search(body)
    if m:
        return _strip_md(m.group(1)).strip()
    return _strip_md(body).strip()


def _strip_md(text: str) -> str:
    """剥掉行首列表/引用记号，保留纯 guidance 文本。"""
    out_lines = []
    for line in text.splitlines():
        s = line.strip()
        s = re.sub(r"^[-*+]\s+", "", s)
        s = re.sub(r"^\d+\.\s+", "", s)
        s = re.sub(r"^>\s*", "", s)
        out_lines.append(s)
    return "\n".join(out_lines).strip()


def _body_from_guidance(guidance: str, action: str = "answer") -> str:
    """用 guidance 构造一段可读 markdown body（保留 # Guidance 段以保 round-trip 无损）。"""
    g = (guidance or "").strip()
    extra = ""
    if action == "escalate":
        extra = "\n\n## What to do\n\n1. 转人工 (`transfer_to_human_agents`)\n"
    return f"# Guidance\n\n{g}\n{extra}".rstrip() + "\n"


def _default_body(skill: Skill) -> str:
    return _body_from_guidance("（待补充 guidance）", action=skill.action)


# ---------------------------------------------------------------------------
# frontmatter -> Skill
# ---------------------------------------------------------------------------
def _skill_from_frontmatter(front: Dict[str, Any], body: str) -> Skill:
    def _as_list(v: Any) -> List[Any]:
        if v is None:
            return []
        if isinstance(v, list):
            return list(v)
        return [v]

    return Skill(
        skill_id=str(front.get("skill_id", "") or ""),
        name=str(front.get("name", "") or ""),
        description=str(front.get("description", "") or ""),
        topic=str(front.get("topic", "general") or "general"),
        triggers=[str(t) for t in _as_list(front.get("triggers"))],
        action=str(front.get("action", "answer") or "answer"),
        version=int(front.get("version", 1) or 1),
        enabled=_as_bool(front.get("enabled", True)),
        created_round=int(front.get("created_round", 0) or 0),
        source_case_ids=[str(t) for t in _as_list(front.get("source_case_ids"))],
        metadata=dict(front.get("metadata") or {}),
        body=(body or "").strip(),
    )


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "on", "1")
    return bool(v)


# ===========================================================================
# YAML 读写：优先 PyYAML，缺则回退手写
# ===========================================================================
def _load_yaml(text: str) -> Any:
    if _HAS_YAML:
        try:
            return _yaml.safe_load(text) or {}
        except Exception:
            pass    # 回退到手写 parser
    return _fallback_load(text)


def _dump_yaml(data: Any) -> str:
    if _HAS_YAML:
        try:
            return _yaml.safe_dump(
                data, allow_unicode=True, sort_keys=False, default_flow_style=False
            )
        except Exception:
            pass
    return _fallback_dump(data)


# ---------------------------------------------------------------------------
# Fallback 手写 YAML（仅 dict / list / str / int / float / bool / None）
# ---------------------------------------------------------------------------
def _fallback_load(text: str) -> Dict[str, Any]:
    """非常受限的 YAML 子集 parser：

    - 顶层是 mapping
    - value 是 scalar / inline list ``[a, b]`` / block list（每行 ``- xxx``）
      / 嵌套 mapping（缩进 2 空格）
    - 不支持 anchor / tag / 多文档
    """
    lines = text.splitlines()
    pos = [0]

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _scalar(s: str) -> Any:
        s = s.strip()
        if s == "" or s.lower() == "null" or s == "~":
            return None
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False
        # 引号
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
            return s[1:-1]
        # 数字
        try:
            if re.fullmatch(r"-?\d+", s):
                return int(s)
            if re.fullmatch(r"-?\d+\.\d+", s):
                return float(s)
        except Exception:
            pass
        # inline list  [a, b, c]
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            if not inner:
                return []
            parts = _split_inline_list(inner)
            return [_scalar(p) for p in parts]
        return s

    def _split_inline_list(inner: str) -> List[str]:
        out, buf, depth, quote = [], "", 0, ""
        for ch in inner:
            if quote:
                buf += ch
                if ch == quote:
                    quote = ""
            elif ch in ("'", '"'):
                buf += ch
                quote = ch
            elif ch in "[{":
                depth += 1
                buf += ch
            elif ch in "]}":
                depth -= 1
                buf += ch
            elif ch == "," and depth == 0:
                out.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(buf.strip())
        return out

    def _peek() -> Optional[str]:
        while pos[0] < len(lines):
            line = lines[pos[0]]
            if line.strip() == "" or line.lstrip().startswith("#"):
                pos[0] += 1
                continue
            return line
        return None

    def _parse_block(indent: int) -> Any:
        line = _peek()
        if line is None:
            return None
        # 列表：当前缩进的 "- "
        if line.lstrip().startswith("- ") or line.lstrip() == "-":
            out: List[Any] = []
            while True:
                line = _peek()
                if line is None or _indent(line) < indent:
                    break
                stripped = line.lstrip()
                if not (stripped.startswith("- ") or stripped == "-"):
                    break
                if _indent(line) != indent:
                    break
                pos[0] += 1
                val = stripped[1:].lstrip()
                if val == "":
                    # 列表项 = 下一缩进块（dict 或更深 list）
                    out.append(_parse_block(indent + 2))
                else:
                    out.append(_scalar(val))
            return out
        # mapping
        out_d: Dict[str, Any] = {}
        while True:
            line = _peek()
            if line is None or _indent(line) < indent:
                break
            if _indent(line) != indent:
                break
            stripped = line.lstrip()
            if stripped.startswith("- "):
                break       # 走到了父级未处理的列表
            if ":" not in stripped:
                pos[0] += 1
                continue
            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            pos[0] += 1
            if rest != "":
                out_d[key] = _scalar(rest)
            else:
                # 下一缩进：可能是 block list / nested mapping / None
                nxt = _peek()
                if nxt is None or _indent(nxt) <= indent:
                    out_d[key] = None
                else:
                    out_d[key] = _parse_block(_indent(nxt))
        return out_d

    result = _parse_block(0)
    if not isinstance(result, dict):
        return {}
    return result


def _fallback_dump(data: Any, indent: int = 0) -> str:
    """非常受限的 YAML dumper（够 skill frontmatter 用）。"""
    out: List[str] = []
    pad = " " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            key = str(k)
            if isinstance(v, dict):
                if not v:
                    out.append(f"{pad}{key}: {{}}")
                else:
                    out.append(f"{pad}{key}:")
                    out.append(_fallback_dump(v, indent + 2))
            elif isinstance(v, list):
                if not v:
                    out.append(f"{pad}{key}: []")
                else:
                    out.append(f"{pad}{key}:")
                    child_pad = " " * (indent + 2)
                    for item in v:
                        if isinstance(item, (dict, list)):
                            out.append(f"{child_pad}-")
                            out.append(_fallback_dump(item, indent + 4))
                        else:
                            out.append(f"{child_pad}- {_scalar_repr(item)}")
            else:
                out.append(f"{pad}{key}: {_scalar_repr(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                out.append(f"{pad}-")
                out.append(_fallback_dump(item, indent + 2))
            else:
                out.append(f"{pad}- {_scalar_repr(item)}")
    else:
        out.append(f"{pad}{_scalar_repr(data)}")
    return "\n".join(out)


def _scalar_repr(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # 需要加引号的情况：包含特殊 YAML 字符 / 像 bool/数字 / 以特殊字符开头
    needs_quote = (
        s == ""
        or s.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
        or re.match(r"^[\-?:&*!|>%@`#\[\]\{\}]", s)
        or ":" in s
        or "#" in s
        or "\n" in s
        or s.strip() != s
    )
    try:
        float(s)
        needs_quote = True
    except Exception:
        pass
    if needs_quote:
        # 用双引号，转义反斜杠与双引号
        esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    return s
