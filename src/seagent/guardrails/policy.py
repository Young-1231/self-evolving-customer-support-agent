"""出站合规策略检查(output policy / content moderation rail)。

业务用途：客服回答可能踩业务红线——超额承诺退款、给出法律/医疗"保证"、泄露
内部系统信息(数据库表名、内部接口、员工工号)等。这些不是"幻觉", 而是"说了不该
说的", 需要独立于 groundedness 的合规闸单独把关。规则可配置, 便于按业务调整。

借鉴：NeMo Guardrails 的 output rail 与 guardrails-ai 的 validator 体系——对生成
内容应用可声明的规则集合。这里给出确定性正则规则, 并允许调用方传入自定义规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# 退款金额承诺的软上限: 超过该数额的"退/赔"承诺需人工复核(可按业务覆盖)。
DEFAULT_REFUND_CAP = 1000.0

# 规则: (规则名, 严重度, 判定函数)。判定函数返回命中的原文片段或 None。
Rule = Tuple[str, str, Callable[[str], Optional[str]]]

_LEGAL_GUARANTEE = re.compile(
    r"(法律上?保证|绝对合法|我们保证.{0,6}(胜诉|不违法)|legally guarantee|guaranteed (legal|to win))",
    re.IGNORECASE,
)
_INTERNAL_LEAK = re.compile(
    r"(?i)(内部(系统|文档|接口|数据库|工单)|员工工号|数据库表|内部 ?api|select \* from|"
    r"system prompt|backend (url|endpoint)|internal[_\- ](api|tool|db|endpoint))"
)
# 金额: ¥/$/RMB/元 + 数字, 用于退款承诺上限判定
_MONEY = re.compile(
    r"(?:¥|\$|RMB\s*|￥)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:元|块|RMB|yuan)?",
    re.IGNORECASE,
)
_REFUND_CTX = re.compile(r"(退款|退还|赔偿|补偿|refund|reimburse|compensat)", re.IGNORECASE)


@dataclass
class Violation:
    rule: str          # 规则名
    severity: str      # "block" | "rewrite" | "warn"
    snippet: str       # 命中的原文片段


def _rule_legal(answer: str) -> Optional[str]:
    m = _LEGAL_GUARANTEE.search(answer)
    return m.group(0) if m else None


def _rule_internal(answer: str) -> Optional[str]:
    m = _INTERNAL_LEAK.search(answer)
    return m.group(0) if m else None


def _make_refund_rule(cap: float) -> Callable[[str], Optional[str]]:
    def _rule(answer: str) -> Optional[str]:
        # 仅当句子同时出现退款语境与金额, 且金额超上限才算违规
        if not _REFUND_CTX.search(answer):
            return None
        for m in _MONEY.finditer(answer):
            raw = m.group(1)
            if not raw:
                continue
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                continue
            if amount > cap:
                return m.group(0).strip()
        return None
    return _rule


def check_output_policy(
    answer: str,
    refund_cap: float = DEFAULT_REFUND_CAP,
    extra_rules: Optional[List[Rule]] = None,
) -> List[Violation]:
    """对回答做合规检查, 返回违规列表(空列表=合规)。

    Args:
        answer: 待检查的出站回答。
        refund_cap: 退款/赔偿承诺金额上限, 超过即违规。
        extra_rules: 业务自定义规则, 形如 ``(name, severity, fn)``。
    """
    if not answer:
        return []

    rules: List[Rule] = [
        ("refund_over_cap", "block", _make_refund_rule(refund_cap)),
        ("legal_guarantee", "rewrite", _rule_legal),
        ("internal_info_leak", "block", _rule_internal),
    ]
    if extra_rules:
        rules.extend(extra_rules)

    violations: List[Violation] = []
    for name, severity, fn in rules:
        snippet = fn(answer)
        if snippet:
            violations.append(Violation(rule=name, severity=severity, snippet=snippet))
    return violations
