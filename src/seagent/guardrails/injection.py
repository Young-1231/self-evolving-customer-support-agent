"""提示注入(prompt injection) / 越狱(jailbreak)检测。

业务用途：客服 Agent 会把用户输入拼进 prompt, 也会把对话沉淀进 episodic 记忆。
若放任注入文本, 攻击者可以让 Agent 忽略系统指令、泄露 system prompt、扮演无约束
角色(DAN), 甚至把恶意指令写进长期记忆形成"记忆投毒"。入站第一道闸就拦它。

借鉴：guardrails-ai(https://github.com/guardrails-ai/guardrails) 与
NVIDIA NeMo Guardrails(https://github.com/NVIDIA/NeMo-Guardrails) 的 input rail
思路——在内容进入 LLM 前用规则/检测器过一遍。这里用正则+关键词覆盖最常见的
英文与中文注入/越狱模式(确定性, 零依赖)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

# 每条规则: (模式名, 正则)。命中即记录, 多命中累加风险。
_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    # 覆盖/忽略既有指令
    ("override_instructions", re.compile(
        r"(?i)\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all)\b"
        r".{0,20}\b(instructions?|prompts?|rules?|context)\b")),
    ("override_instructions_cn", re.compile(
        r"(忽略|无视|忘记|不要理会|不用管).{0,8}(以上|之前|上面|前面|先前|所有).{0,8}"
        r"(指令|提示|规则|要求|设定)")),
    # 泄露/打印 system prompt
    ("reveal_system_prompt", re.compile(
        r"(?i)\b(reveal|show|print|repeat|output|leak|tell me)\b.{0,30}"
        r"\b(system|initial|original|developer)\b.{0,10}\b(prompt|instructions?|message)\b")),
    ("reveal_system_prompt_cn", re.compile(
        r"(显示|打印|输出|重复|告诉我|泄露|展示).{0,10}(系统|初始|原始|开发者).{0,6}(提示词|提示|指令|设定)")),
    # 角色扮演越狱
    ("jailbreak_dan", re.compile(
        r"(?i)\b(you are|act as|pretend to be|roleplay as)\b.{0,20}\b(dan|do anything now|"
        r"unrestricted|jailbroken|developer mode|no restrictions?)\b")),
    ("jailbreak_dan_cn", re.compile(
        r"(你现在是|扮演|假装你是|进入).{0,6}(dan|开发者模式|无限制|越狱|不受限制)", re.IGNORECASE)),
    # 解除安全限制
    ("disable_safety", re.compile(
        r"(?i)\b(without|no|ignore|bypass|disable|turn off)\b.{0,20}"
        r"\b(restrictions?|filters?|guidelines?|safety|policy|rules?|moral|ethical)\b")),
    ("disable_safety_cn", re.compile(
        r"(不受|绕过|关闭|解除|忽略).{0,6}(限制|过滤|安全|审查|道德|伦理|约束|规则)")),
    # 强行变更身份/系统角色注入
    ("role_injection", re.compile(
        r"(?i)\b(new instructions?|system\s*:|from now on you (are|will|must))\b")),
    ("role_injection_cn", re.compile(r"(从现在起你|新的指令|系统指令[:：])")),
]


@dataclass
class InjectionResult:
    flagged: bool                       # 是否判定为注入/越狱
    patterns: List[str] = field(default_factory=list)  # 命中的规则名
    score: float = 0.0                  # 命中数量归一化的风险分 [0,1]
    snippets: List[str] = field(default_factory=list)  # 命中的原文片段(便于排查/日志)


def detect_injection(user_text: str) -> InjectionResult:
    """检测用户输入中的提示注入 / 越狱意图。

    任一规则命中即 ``flagged=True``。``score`` 为命中规则数除以一个软上限(3),
    截断到 [0,1], 供 pipeline 区分"轻微可疑"与"强烈攻击"。
    """
    if not user_text:
        return InjectionResult(flagged=False)

    hits: List[str] = []
    snippets: List[str] = []
    for name, pat in _RULES:
        m = pat.search(user_text)
        if m:
            hits.append(name)
            snippets.append(m.group(0))

    score = min(1.0, len(hits) / 3.0)
    return InjectionResult(flagged=bool(hits), patterns=hits, score=score, snippets=snippets)
