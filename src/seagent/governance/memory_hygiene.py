"""经验池治理(memory hygiene)：去重 / TTL 遗忘 / 冲突消解 / 入库脱敏。

长期记忆若任其无限堆积，会出现三类病：近重复 case 灌水、陈旧 case 误导、同一
topic 下相互矛盾的解法并存(检索时谁先命中谁说了算 = 不可控)。这对应 Mem0 /
Letta / Zep / MemArchitect(arXiv 2603.18330) 强调的记忆 TTL、遗忘、冲突消解与
PII 治理，也是防 misevolution(arXiv 2509.26354) 的"输入侧"卫生。

四个能力均为**纯函数式**，复用 ``memory.episodic.Case``，不修改 EpisodicMemory：
  - ``dedup``            : 近重复 case 合并(token Jaccard 相似度阈值)；
  - ``ttl_filter``       : 按 learned_round 或时间戳过期；
  - ``detect_conflicts`` : 同 topic 下矛盾解法启发式标记(转人工决策相反 / 冲突短语)；
  - ``scrub_case``       : 入库前脱敏，优先调用 guardrails.pii，缺失则正则兜底。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Callable, List, Optional, Tuple

from ..memory.bm25 import tokenize
from ..memory.episodic import Case


# ----------------------------------------------------------------------------
# 1) 去重 dedup
# ----------------------------------------------------------------------------
def _jaccard(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedup(cases: List[Case], threshold: float = 0.85) -> List[Case]:
    """合并近重复 case。

    以 (query + resolution) 的 token Jaccard 相似度判重：相似度 >= threshold 视为
    重复，保留先入者(更早学到的)，丢弃后续近重复。返回去重后的新列表(不改入参)。
    """
    kept: List[Case] = []
    for c in cases:
        text = f"{c.query} {c.resolution}"
        dup = False
        for k in kept:
            if _jaccard(text, f"{k.query} {k.resolution}") >= threshold:
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept


# ----------------------------------------------------------------------------
# 2) TTL 遗忘 ttl_filter
# ----------------------------------------------------------------------------
def ttl_filter(
    cases: List[Case],
    current_round: int,
    ttl_rounds: Optional[int] = None,
) -> List[Case]:
    """按 learned_round 做 TTL 遗忘。

    保留 ``current_round - learned_round < ttl_rounds`` 的 case，过期的丢弃。
    ``ttl_rounds=None`` 表示永不过期(直接原样返回拷贝)。这是记忆"主动遗忘"的最简
    实现：陈旧经验在分布漂移后会变成噪声甚至误导，定期清退。
    """
    if ttl_rounds is None:
        return list(cases)
    return [c for c in cases if (current_round - c.learned_round) < ttl_rounds]


# ----------------------------------------------------------------------------
# 3) 冲突消解 detect_conflicts
# ----------------------------------------------------------------------------
@dataclass
class ConflictPair:
    """一对被判定为矛盾的 case。"""

    case_a: str        # case_id
    case_b: str
    topic: str
    reason: str        # 冲突类型说明


# 互斥短语对：同一 topic 下若两条 case 的解法分别命中相反指令，则判为矛盾。
_CONFLICT_PHRASES: List[Tuple[str, str]] = [
    ("无需重启", "需要重启"),
    ("不要重启", "请重启"),
    ("可以退款", "不予退款"),
    ("支持退款", "不支持退款"),
    ("无需人工", "需转人工"),
    ("enable", "disable"),
    ("turn on", "turn off"),
    ("allowed", "not allowed"),
]


def _phrase_conflict(ra: str, rb: str) -> Optional[str]:
    la, lb = ra.lower(), rb.lower()
    for p, q in _CONFLICT_PHRASES:
        if (p in la and q in lb) or (q in la and p in lb):
            return f"互斥短语: '{p}' vs '{q}'"
    return None


def detect_conflicts(cases: List[Case]) -> List[ConflictPair]:
    """检测同一 topic 下相互矛盾的解法(启发式)。

    两类信号：
      (a) 转人工决策相反(should_escalate 一真一假)且 query 高度相似 —— 同样的问题
          却给出"自答 vs 转人工"两种相反处置；
      (b) resolution 命中互斥短语对(如"需要重启" vs "无需重启")。
    命中即标记成 ConflictPair，交由人工/上层做冲突消解(保留哪条、合并还是淘汰)。
    """
    out: List[ConflictPair] = []
    n = len(cases)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = cases[i], cases[j]
            if a.topic != b.topic:
                continue
            # (a) 相同问题但转人工决策相反
            if a.should_escalate != b.should_escalate and _jaccard(a.query, b.query) >= 0.6:
                out.append(ConflictPair(a.case_id, b.case_id, a.topic,
                                        "同题转人工决策相反(should_escalate 矛盾)"))
                continue
            # (b) 解法互斥短语
            why = _phrase_conflict(a.resolution, b.resolution)
            if why:
                out.append(ConflictPair(a.case_id, b.case_id, a.topic, why))
    return out


# ----------------------------------------------------------------------------
# 4) 入库脱敏 scrub_case
# ----------------------------------------------------------------------------
# 正则兜底(guardrails.pii 不可用时)。与 guardrails/pii.py 的高危实体保持一致。
_FALLBACK_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ID_CN", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("BANK_CARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("IP", re.compile(r"(?<!\d)((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")),
]


def _fallback_redact(text: str) -> str:
    """纯正则脱敏兜底(零依赖、行为确定)。"""
    if not text:
        return text or ""
    out = text
    for entity, pat in _FALLBACK_PATTERNS:
        out = pat.sub(f"<{entity}>", out)
    return out


def _redact(text: str) -> str:
    """优先用 guardrails.pii.redact_pii；不存在则正则兜底(try-import)。"""
    try:
        from ..guardrails.pii import redact_pii  # type: ignore
    except Exception:
        return _fallback_redact(text)
    try:
        redacted, _ = redact_pii(text)
        return redacted
    except Exception:
        return _fallback_redact(text)


def scrub_case(case: Case, fields: Optional[List[str]] = None) -> Case:
    """入库前对 case 的文本字段脱敏，返回脱敏后的新 Case(不改入参)。

    经验池会被长期检索复用，绝不能把客户的手机号/邮箱/身份证/银行卡写进长期记忆。
    默认脱敏 ``query`` 与 ``resolution`` 两个自由文本字段。
    """
    fields = fields or ["query", "resolution"]
    patch = {f: _redact(getattr(case, f)) for f in fields}
    return replace(case, **patch)
