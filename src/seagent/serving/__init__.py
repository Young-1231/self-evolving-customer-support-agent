"""Serving 服务层：把自进化客服 agent 包成可部署的线上服务。

分层：
  - schema.py   : 贴近真实 ticketing(Zendesk/Intercom) 的数据模型(纯 stdlib dataclass)
  - session.py  : 多轮会话状态管理 + 转人工(human handoff) 标记(纯 stdlib)
  - feedback.py : 隐式/噪声反馈 -> 学习信号 的转换(纯 stdlib)
  - app.py      : FastAPI 应用(可选依赖；缺失时本模块其余部分仍可正常导入与测试)

核心业务逻辑全部纯 stdlib、可离线单测；FastAPI 仅作 HTTP 适配层。
"""
from __future__ import annotations

from .schema import (
    AgentReply,
    ChatTurn,
    Feedback,
    FeedbackKind,
    Ticket,
    TicketStatus,
)
from .session import SessionManager
from .feedback import FeedbackProcessor

__all__ = [
    "AgentReply",
    "ChatTurn",
    "Feedback",
    "FeedbackKind",
    "Ticket",
    "TicketStatus",
    "SessionManager",
    "FeedbackProcessor",
]
