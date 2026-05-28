"""出站合规策略检查(output policy / content moderation rail)。

业务用途：客服回答可能踩业务红线——超额承诺退款、给出法律/医疗"保证"、泄露
内部系统信息(数据库表名、内部接口、员工工号)等。这些不是"幻觉", 而是"说了不该
说的", 需要独立于 groundedness 的合规闸单独把关。规则可配置, 便于按业务调整。

借鉴：NeMo Guardrails 的 output rail 与 guardrails-ai 的 validator 体系——对生成
内容应用可声明的规则集合。这里给出确定性正则规则, 并允许调用方传入自定义规则。

v2.9 修订(2026-05-28)
---------------------
``_MONEY`` 原先是 "数字 + 可选货币符号" 弱匹配, multi_intent 场景下子答案普遍出现
"订单号 #38294" / "your order #27501" / "ticket no 12345" 等订单号, 会被识别成
金额 38294/27501/12345 > $1000 refund_cap, 触发 refund_over_cap → BLOCK,
经由 per-sub aggregation 的 ANY-BLOCK 规则将整 bundle 判 BLOCK,
导致 multi_intent resolution_rate 永远 0%。

修复思路: 让金额识别**上下文感知**:

1. ``_ORDER_NUMBER_PATTERNS`` 先识别"订单号/工单号/单号/order/ticket/user id" 等
   编号字段, 把这些字段的字面跨度从待检文本中剔除 (replaced with spaces, 保持
   offset 对齐, 便于其他规则继续工作)。
2. ``_MONEY_STRICT`` 要求数字必须**紧邻强货币标识**(¥/$/￥ 紧贴, 或前后 30 字符
   内带"退款/refund/赔付/compensate/reimburse" + 后缀"元/块/RMB/yuan/USD/CNY"
   之一)才识别成金额。
3. 退款上限仅在数字真的被识别为金额、且额度 > cap 时触发。

这是 v2.x 唯一真改 guardrail 源码的修订, 闭环 multi_intent 0% 问题。
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

# ---- v2.9: 上下文感知金额识别 ----
#
# 1) 订单/单号/用户 ID 等编号字段——先识别并屏蔽, 避免误判成金额。
#    覆盖中英文常见写法: 订单号 #38294, 订单 38294, order #38294, order id 38294,
#    工单号 #99999, ticket no 12345, ticket id: 12345, user id 123456789。
_ORDER_NUMBER_PATTERNS = re.compile(
    r"(?ix)"
    r"(?:"
    r"   (?:订单|工单|运单|单)\s*(?:号|编号|id|no\.?)?\s*[\#:：]?\s*[0-9][0-9,\-]*"
    r" | (?:order|ticket|tracking|case|invoice|reference|ref|user|customer|account)"
    r"     \s*(?:id|no\.?|number|\#)?\s*[\#:：]?\s*[0-9][0-9,\-]*"
    r" | \#\s*[0-9][0-9,\-]*"
    r")"
)

# 2) 强金额: 数字必须紧贴强货币标识。
#    形式 A: 货币符号在前  -- $5000 / ¥5000 / RMB 5000 / USD 5000
#    形式 B: 货币词在后    -- 5000 元 / 5000 块 / 5000 RMB / 5000 yuan / 5000 USD
_MONEY_STRICT = re.compile(
    r"(?ix)"
    r"(?:"
    r"   (?:¥|\$|￥|RMB|USD|CNY)\s*([0-9][0-9,]*(?:\.[0-9]+)?)"
    r" | ([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:元|块|RMB|yuan|USD|CNY|人民币)"
    r")"
)

# 3) 退款语境窗口 (用于额外裸数字判定时的上下文窗扫描, 这里只做语境标记)。
_REFUND_CTX = re.compile(r"(退款|退还|赔偿|补偿|赔付|refund|reimburse|compensat)", re.IGNORECASE)


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


def _mask_order_numbers(text: str) -> str:
    """把订单号/工单号/user id 等编号跨度替换成同长度的空白, 保留 offset 对齐。

    保留 offset 对齐有两个好处:
    1) 其他规则在 mask 之后的文本上做的 match 仍能映射回原文位置 (虽然本模块
       目前不依赖这一点, 但为未来扩展留有余地);
    2) 不引入意外的词边界 (例如把 "订单 #38294 退款 5000 元" 折叠成 "退款 5000 元"
       前是否粘连其他词)。
    """
    def _blank(m: re.Match) -> str:
        return " " * (m.end() - m.start())
    return _ORDER_NUMBER_PATTERNS.sub(_blank, text)


def _make_refund_rule(cap: float) -> Callable[[str], Optional[str]]:
    def _rule(answer: str) -> Optional[str]:
        # 仅当出现退款语境才进一步看金额。
        if not _REFUND_CTX.search(answer):
            return None

        # 先把订单号/工单号等编号字段屏蔽, 避免编号数字被误判成金额。
        scrubbed = _mask_order_numbers(answer)

        # 在 scrubbed 文本上做强金额匹配。
        for m in _MONEY_STRICT.finditer(scrubbed):
            raw = m.group(1) or m.group(2) or ""
            if not raw:
                continue
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                continue
            if amount > cap:
                # 截取原文中对应跨度作为 snippet (offset 对齐之后是安全的)。
                return answer[m.start():m.end()].strip()
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
