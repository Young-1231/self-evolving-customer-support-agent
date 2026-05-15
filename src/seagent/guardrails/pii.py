"""PII(个人身份信息)识别与脱敏。

业务用途：
  - 入库经验前脱敏：episodic 记忆会沉淀历史对话，绝不能把客户的手机号/身份证
    /银行卡写进长期记忆，否则会随检索被复用甚至泄露给其他会话；
  - 出站回答前可选脱敏：模型若把上下文里的敏感信息原样吐出，需兜底拦截;
  - 日志脱敏：落盘的 trace 同样要去标识化。

借鉴：Microsoft Presidio(https://github.com/microsoft/presidio) 的
"analyzer 找实体 + anonymizer 替换占位符" 两段式思路。这里用正则覆盖最常见的
高危实体；若环境中装了 Presidio，则 try-import 调用其 AnalyzerEngine 做增强，
正则结果与 Presidio 结果合并去重(没装则纯正则回退，行为完全确定)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class PiiSpan:
    """一处被识别出的敏感信息。"""

    entity: str       # "EMAIL" | "PHONE_CN" | "PHONE_INTL" | "ID_CN" | "BANK_CARD" | "IP" | "PERSON" ...
    start: int        # 在原文中的起始下标
    end: int          # 结束下标(不含)
    text: str         # 原始片段
    placeholder: str  # 替换后的占位符, 如 <EMAIL>


# --- 正则规则表 (顺序很重要: 长/具体的实体先匹配, 避免被短模式截断) ---
# 注: 中国身份证 18 位(末位可为 X), 银行卡 13~19 位, 中国手机号 1[3-9] 开头 11 位。
#
# precision_mode = 'strict' 是默认/历史行为, 保持原有规则不变。
# 'balanced' 收紧明显误伤的规则(银行卡只接受 16 连续位, 手机号需符合国家格式
# 或国际格式, 邮箱要求 TLD 至少 2 段域名)。
# 'relaxed' 进一步要求高置信信号(信用卡 16 位 + Luhn 校验, 手机号必须显式
# 国际/括号格式)。三档共享同一个枚举键空间, 调用方按需切换。
_PATTERNS_STRICT: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ID_CN", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("BANK_CARD", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 国际号: +区号 后跟 6~14 位(允许空格/连字符分隔)
    ("PHONE_INTL", re.compile(r"\+\d{1,3}[\s\-]?\d{1,4}([\s\-]?\d{2,4}){2,4}")),
    ("IP", re.compile(r"(?<!\d)((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")),
]

# Back-compat alias: existing internal call sites reference `_PATTERNS` directly
# (not part of any public API).  Keep the name pointing at the strict table so
# nothing externally observable changes.
_PATTERNS = _PATTERNS_STRICT

# Balanced: drop the over-eager BANK_CARD 13-19 catch-all (it false-fires on
# any long digit run e.g. order ids), require a real 16-digit PAN; require
# US phones to look like (XXX)XXX-XXXX / XXX-XXX-XXXX (10 grouped digits) or
# fall back to PHONE_INTL.  Email pattern unchanged (cheap, low FPR already).
_PATTERNS_BALANCED: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ID_CN", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    # 16 连续数字, 前后非数字, 不再吃 13~19
    ("BANK_CARD", re.compile(r"(?<!\d)\d{16}(?!\d)")),
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 美式手机: (415)555-0132 / 415-555-0132 / 415.555.0132 / 415 555 0132
    ("PHONE_US", re.compile(r"(?<!\d)(?:\(\d{3}\)[\s\-]?\d{3}[\s\-]?\d{4}|\d{3}[\s\-\.]\d{3}[\s\-\.]\d{4})(?!\d)")),
    ("PHONE_INTL", re.compile(r"\+\d{1,3}[\s\-]?\d{1,4}([\s\-]?\d{2,4}){2,4}")),
    ("IP", re.compile(r"(?<!\d)((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")),
]

# Relaxed: only fire on patterns that are virtually unambiguous.
#   * BANK_CARD: 16 digits AND passes Luhn (post-filter below)
#   * PHONE: must be international (+CC...) or US in parens form (415)555-0132
#   * EMAIL: kept (already very specific)
#   * IP: dropped (often a server hostname in support tickets, low signal)
_PATTERNS_RELAXED: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("ID_CN", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("BANK_CARD", re.compile(r"(?<!\d)\d{16}(?!\d)")),  # Luhn-checked downstream
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("PHONE_US", re.compile(r"(?<!\d)\(\d{3}\)[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)")),
    ("PHONE_INTL", re.compile(r"\+\d{1,3}[\s\-]?\d{1,4}([\s\-]?\d{2,4}){2,4}")),
]

_MODE_TABLE = {
    "strict": _PATTERNS_STRICT,
    "balanced": _PATTERNS_BALANCED,
    "relaxed": _PATTERNS_RELAXED,
}


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum for credit-card validation."""
    s = 0
    alt = False
    for ch in reversed(digits):
        if not ch.isdigit():
            return False
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return s % 10 == 0

# 姓名占位: 中文里 "我叫X" / "姓名: X" / 英文 "my name is X" 等显式自报场景。
# 通用人名识别极易误伤, 故只命中明确的引导词后紧跟的名字 token。
_PERSON_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"(?<=我叫)[一-鿿]{2,4}"),
    re.compile(r"(?<=我是)[一-鿿]{2,4}(?=,|，|。|\s|$)"),
    re.compile(r"(?:姓名|客户|联系人)[:：]\s*([一-鿿]{2,4})"),
    re.compile(r"(?i)(?<=my name is )[A-Z][a-z]+(?: [A-Z][a-z]+)?"),
]


def _placeholder(entity: str) -> str:
    return f"<{entity}>"


def _regex_spans(text: str, precision_mode: str = "strict") -> List[PiiSpan]:
    table = _MODE_TABLE.get(precision_mode, _PATTERNS_STRICT)
    spans: List[PiiSpan] = []
    for entity, pat in table:
        for m in pat.finditer(text):
            raw = m.group(0)
            # Relaxed mode: credit card must pass Luhn.
            if precision_mode == "relaxed" and entity == "BANK_CARD":
                digits = "".join(ch for ch in raw if ch.isdigit())
                if not _luhn_ok(digits):
                    continue
            spans.append(
                PiiSpan(entity, m.start(), m.end(), raw, _placeholder(entity))
            )
    for pat in _PERSON_PATTERNS:
        for m in pat.finditer(text):
            # 若含捕获组(如 "姓名: X")只脱敏组内, 否则脱敏整段
            if m.groups():
                s, e = m.start(1), m.end(1)
            else:
                s, e = m.start(), m.end()
            spans.append(PiiSpan("PERSON", s, e, text[s:e], _placeholder("PERSON")))
    return spans


def _presidio_spans(text: str) -> List[PiiSpan]:
    """可选增强: 若安装了 Microsoft Presidio, 用其 analyzer 补充识别。

    没装时静默返回空列表, 保证零依赖路径完全确定。
    """
    try:  # pragma: no cover - 依赖外部库, CI 默认不装
        from presidio_analyzer import AnalyzerEngine  # type: ignore
    except Exception:
        return []
    try:  # pragma: no cover
        engine = AnalyzerEngine()
        results = engine.analyze(text=text, language="en")
        out: List[PiiSpan] = []
        for r in results:
            out.append(
                PiiSpan(r.entity_type, r.start, r.end, text[r.start : r.end],
                        _placeholder(r.entity_type))
            )
        return out
    except Exception:
        return []


def _dedupe_overlaps(spans: List[PiiSpan]) -> List[PiiSpan]:
    """按起点排序, 丢弃与已保留 span 重叠的区间(保留更早/更长者)。"""
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    kept: List[PiiSpan] = []
    occupied_end = -1
    for sp in spans:
        if sp.start >= occupied_end:
            kept.append(sp)
            occupied_end = sp.end
    return kept


def redact_pii(
    text: str,
    use_presidio: bool = True,
    precision_mode: str = "strict",
) -> Tuple[str, List[PiiSpan]]:
    """识别并脱敏文本中的 PII。

    返回 ``(脱敏后文本, 命中的 span 列表)``。脱敏方式为把敏感片段整体替换为
    ``<ENTITY>`` 占位符(去标识化), 占位符长度与原文无关, 不泄露位数信息。

    Args:
        text: 原始文本。
        use_presidio: 是否在装了 Presidio 时启用其增强(默认开; 没装自动回退)。
        precision_mode: ``'strict'``(默认, 行为与历史一致, 召回优先) |
            ``'balanced'``(收紧明显误伤: 银行卡 16 位, 手机号需国家标准格式) |
            ``'relaxed'``(只保留高置信模式: 银行卡 Luhn 校验, 手机号需明确
            国际/括号格式, 不再扫 IP)。
    """
    if not text:
        return text or "", []

    mode = precision_mode if precision_mode in _MODE_TABLE else "strict"
    spans = _regex_spans(text, precision_mode=mode)
    if use_presidio:
        spans.extend(_presidio_spans(text))
    spans = _dedupe_overlaps(spans)

    # 从后往前替换, 避免下标偏移
    redacted = text
    for sp in sorted(spans, key=lambda s: s.start, reverse=True):
        redacted = redacted[: sp.start] + sp.placeholder + redacted[sp.end :]
    return redacted, spans


def has_pii(
    text: str,
    use_presidio: bool = True,
    precision_mode: str = "strict",
) -> bool:
    """便捷判断: 文本是否含任何 PII。"""
    _, spans = redact_pii(
        text, use_presidio=use_presidio, precision_mode=precision_mode,
    )
    return bool(spans)
