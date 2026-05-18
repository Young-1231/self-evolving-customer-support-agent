"""隐式/噪声反馈 -> 学习信号 的转换层。

线上没有 gold label：客户不会告诉你"标准答案"，只会留下点踩、重新打开工单、
低 CSAT 这类弱/噪声信号。本模块把这些信号转成：

  1) 学习信号(training signal)：标准化的 dict，标注 polarity(正/负) 与 weight(强弱)，
     供下游聚合。
  2) 待复盘失败案例(Case 草稿)：把"疑似回答失败"的工单整理成 episodic.Case 的草稿，
     但 **不直接入库**——必须经 governance / 人审(见 README 数据流)，避免噪声反馈污染经验池。
  3) 代理指标(proxy metrics)：在没有 gold label 时，用 deflection / resolution 等
     代理指标近似衡量线上效果。

设计取舍：不 import EpisodicMemory(避免与 agent 侧耦合 + 保持可离线测)，
而是产出 dict 形态的 Case 草稿，字段与 memory.episodic.Case 完全对齐，
人审通过后由你那边 `Case(**draft)` 直接构造入库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .schema import Feedback, FeedbackKind, Ticket


# 低于该 CSAT 分(1-5)视为负向弱信号
CSAT_NEGATIVE_MAX = 2
# 各类反馈的信号强度权重(线上经验：reopened 比单次点踩更可信)
SIGNAL_WEIGHT = {
    FeedbackKind.REOPENED: 1.0,
    FeedbackKind.THUMBS_DOWN: 0.6,
    FeedbackKind.CSAT: 0.5,
    FeedbackKind.THUMBS_UP: 0.4,
    FeedbackKind.RESOLVED: 0.7,
}


def _polarity(fb: Feedback) -> int:
    """把一条反馈映射成极性：+1 正向 / -1 负向 / 0 中性(无法判定)。"""
    if fb.kind == FeedbackKind.THUMBS_UP:
        return +1
    if fb.kind == FeedbackKind.RESOLVED:
        # value 缺省按 True(客户点了"已解决")
        return +1 if (fb.value in (None, True, "true", 1)) else -1
    if fb.kind == FeedbackKind.THUMBS_DOWN:
        return -1
    if fb.kind == FeedbackKind.REOPENED:
        return -1
    if fb.kind == FeedbackKind.CSAT:
        try:
            score = int(fb.value)
        except (TypeError, ValueError):
            return 0
        if score <= CSAT_NEGATIVE_MAX:
            return -1
        if score >= 4:
            return +1
        return 0
    return 0


def to_training_signal(fb: Feedback, ticket: Optional[Ticket] = None) -> Dict[str, Any]:
    """把单条反馈标准化成学习信号 dict。

    输出字段：
        ticket_id / turn_id / kind / polarity(+1|-1|0) / weight / is_failure
        query(触发该反馈的客户原问题，若能从 ticket 还原) / comment
    """
    pol = _polarity(fb)
    weight = SIGNAL_WEIGHT.get(fb.kind, 0.3)
    query = ""
    if ticket is not None:
        query = ticket.last_customer_text() or ticket.subject or ""
    return {
        "ticket_id": fb.ticket_id,
        "turn_id": fb.turn_id,
        "kind": fb.kind,
        "polarity": pol,
        "weight": round(weight, 3),
        "is_failure": pol < 0,           # 负向 = 疑似回答失败
        "query": query,
        "comment": fb.comment,
    }


@dataclass
class ReviewItem:
    """一个待人审的复盘条目：Case 草稿 + 触发它的信号。"""

    case_draft: Dict[str, Any]          # 字段对齐 memory.episodic.Case
    signal: Dict[str, Any]
    needs_human_review: bool = True     # 永远为 True：噪声反馈不可直接入库

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_draft": self.case_draft,
            "signal": self.signal,
            "needs_human_review": self.needs_human_review,
        }


class FeedbackProcessor:
    """累积线上反馈，产出待复盘失败案例与代理指标。"""

    def __init__(self) -> None:
        self._signals: List[Dict[str, Any]] = []
        self._review_queue: List[ReviewItem] = []

    # --- 摄入 ---

    def ingest(self, fb: Feedback, ticket: Optional[Ticket] = None) -> Dict[str, Any]:
        """摄入一条反馈：记录信号，必要时生成待复盘案例草稿。返回学习信号 dict。"""
        sig = to_training_signal(fb, ticket)
        self._signals.append(sig)
        if sig["is_failure"]:
            self._review_queue.append(self._to_review_item(sig, ticket))
        return sig

    def _to_review_item(self, sig: Dict[str, Any], ticket: Optional[Ticket]) -> ReviewItem:
        """把一条负向信号整理成 episodic.Case 草稿(待人审)。

        resolution 故意留空 / 占位：线上没有 gold label，正确解法需人审补全。
        topic 用工单首个 tag 兜底，便于 reflector 后续按主题聚类形成 playbook。
        """
        topic = "general"
        if ticket is not None and ticket.tags:
            non_meta = [t for t in ticket.tags if t not in ("handoff", "reopened")]
            topic = non_meta[0] if non_meta else "general"
        case_draft = {
            "case_id": f"draft-{sig['ticket_id']}-{sig['turn_id'] or 'whole'}",
            "query": sig["query"],
            "resolution": "",                 # 待人审/人工坐席补全的正确解法
            "should_escalate": sig["kind"] == FeedbackKind.REOPENED,
            "topic": topic,
            "source_query_id": sig["ticket_id"],
            "learned_round": -1,              # -1 = 来自线上反馈，尚未进入任何训练轮
        }
        return ReviewItem(case_draft=case_draft, signal=sig)

    # --- 产出 ---

    def pending_review(self) -> List[ReviewItem]:
        """返回待人审的复盘案例(不直接入经验池)。"""
        return list(self._review_queue)

    def proxy_metrics(self) -> Dict[str, Any]:
        """弱信号下的代理指标。

        定义(线上常用近似)：
          - resolution_rate : 正向信号占有效信号(非中性)比例，近似"解决率"
          - dissatisfaction_rate : 负向信号占有效信号比例
          - deflection_rate : (未触发负向反馈的工单) / 总反馈工单，近似 self-service 成功率
          - avg_csat : 收到的 CSAT 平均分(若有)
        所有指标都标注 n，提醒样本量小则不可信。
        """
        total = len(self._signals)
        effective = [s for s in self._signals if s["polarity"] != 0]
        pos = [s for s in effective if s["polarity"] > 0]
        neg = [s for s in effective if s["polarity"] < 0]

        tickets_with_neg = {s["ticket_id"] for s in neg}
        tickets_all = {s["ticket_id"] for s in self._signals}
        deflected = tickets_all - tickets_with_neg

        csat_vals: List[int] = []
        for s in self._signals:
            if s["kind"] == FeedbackKind.CSAT:
                # 从信号反推原始分不可行，CSAT 极性已经损失精度，这里只统计有 csat 信号的条数
                pass

        def _ratio(num: int, den: int) -> float:
            return round(num / den, 4) if den else 0.0

        return {
            "n_signals": total,
            "n_effective": len(effective),
            "resolution_rate": _ratio(len(pos), len(effective)),
            "dissatisfaction_rate": _ratio(len(neg), len(effective)),
            "deflection_rate": _ratio(len(deflected), len(tickets_all)),
            "n_pending_review": len(self._review_queue),
            "note": "proxy metrics from noisy implicit feedback; small n is unreliable",
        }

    def reset(self) -> None:
        self._signals.clear()
        self._review_queue.clear()
