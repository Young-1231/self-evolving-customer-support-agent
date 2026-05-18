"""线上服务的数据模型，对齐真实 ticketing 系统(Zendesk / Intercom)的字段语义。

设计原则：
  - 纯 stdlib dataclass，不依赖 pydantic / FastAPI，核心逻辑可离线单测。
  - 字段命名与主流工单系统对齐，方便后续接入时做一一映射(见 README 的 Zendesk/Intercom 对照)。
  - 所有时间用 ISO-8601 UTC 字符串，避免序列化时的时区歧义。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# --- 枚举(用字符串常量，保持 JSON 友好、无第三方 enum 依赖) ---

class TicketStatus:
    """工单生命周期状态，对齐 Zendesk 的 status 字段。"""

    NEW = "new"            # 刚创建，尚未有 agent 回复
    OPEN = "open"          # agent 处理中
    PENDING = "pending"    # 等待客户回复
    SOLVED = "solved"      # 已解决
    REOPENED = "reopened"  # 客户对解决不满意，重新打开(强失败信号)
    ESCALATED = "escalated"  # 已转人工

    ALL = (NEW, OPEN, PENDING, SOLVED, REOPENED, ESCALATED)


class FeedbackKind:
    """反馈类型。线上多为隐式/噪声信号，没有 gold label。"""

    THUMBS_UP = "thumbs_up"     # 显式正反馈(点赞)
    THUMBS_DOWN = "thumbs_down"  # 显式负反馈(点踩)
    RESOLVED = "resolved"       # 客户确认已解决(隐式正信号)
    REOPENED = "reopened"       # 客户重新打开工单(强隐式负信号)
    CSAT = "csat"               # 满意度评分(1-5，弱噪声信号)

    ALL = (THUMBS_UP, THUMBS_DOWN, RESOLVED, REOPENED, CSAT)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class ChatTurn:
    """一轮对话消息。role 对齐 OpenAI/Intercom 的 author 概念。"""

    role: str               # "customer" | "agent" | "human_agent" | "system"
    text: str
    turn_id: str = field(default_factory=lambda: _new_id("turn"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Ticket:
    """工单，对齐 Zendesk ticket / Intercom conversation。"""

    customer_id: str
    subject: str = ""
    channel: str = "chat"          # chat | email | web | api(对齐 Zendesk via.channel)
    ticket_id: str = field(default_factory=lambda: _new_id("tkt"))
    messages: List[ChatTurn] = field(default_factory=list)
    status: str = TicketStatus.NEW
    priority: str = "normal"       # low | normal | high | urgent
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def add_message(self, turn: ChatTurn) -> ChatTurn:
        self.messages.append(turn)
        self.updated_at = _now_iso()
        return turn

    def last_customer_text(self) -> Optional[str]:
        for t in reversed(self.messages):
            if t.role == "customer":
                return t.text
        return None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class AgentReply:
    """agent 的一次回复。是 AgentResult 的服务层投影 + 治理元数据。

    与 agent.support_agent.AgentResult 的映射(由 app.py 适配层完成)：
        AgentResult.answer      -> answer
        AgentResult.escalate    -> escalate
        AgentResult.confidence  -> confidence
        AgentResult.used_sources-> sources
    """

    answer: str
    escalate: bool = False
    confidence: float = 0.0
    sources: List[str] = field(default_factory=list)
    guardrail: Optional[str] = None   # 命中护栏时的标记，如 "pii_redacted"
    trace_id: str = field(default_factory=lambda: _new_id("trace"))
    turn_id: str = ""                 # 对应写入 ticket 的 agent ChatTurn.turn_id

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Feedback:
    """一条反馈。线上的真实反馈大多是隐式/噪声信号。"""

    ticket_id: str
    kind: str                       # FeedbackKind.*
    value: Any = None               # thumbs: bool; csat: int(1-5); resolved/reopened: bool
    turn_id: str = ""               # 针对哪一轮 agent 回复(可空，表示针对整单)
    comment: str = ""
    feedback_id: str = field(default_factory=lambda: _new_id("fb"))
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
