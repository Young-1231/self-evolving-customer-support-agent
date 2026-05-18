"""会话状态管理：按 ticket 维护多轮历史 + 转人工(human handoff) 标记。

纯 stdlib，内存态(单进程)。生产里这一层会换成 Redis / 数据库，但接口语义不变，
方便后续替换实现而不动 app.py。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .schema import AgentReply, ChatTurn, Ticket, TicketStatus


class SessionManager:
    """以 ticket_id 为键管理会话状态。

    线程安全(加锁)，因为 FastAPI 默认在线程池里跑同步端点。
    """

    def __init__(self) -> None:
        self._tickets: Dict[str, Ticket] = {}
        # 被转人工接管的工单集合：接管后 agent 不再自动应答
        self._handed_off: Dict[str, str] = {}  # ticket_id -> reason
        self._lock = threading.RLock()

    # --- 工单生命周期 ---

    def get_or_create(
        self,
        ticket_id: Optional[str],
        customer_id: str,
        subject: str = "",
        channel: str = "chat",
    ) -> Ticket:
        """已有则取，没有则按给定 ticket_id(或自动生成)创建。"""
        with self._lock:
            if ticket_id and ticket_id in self._tickets:
                return self._tickets[ticket_id]
            tkt = Ticket(customer_id=customer_id, subject=subject, channel=channel)
            if ticket_id:
                tkt.ticket_id = ticket_id
            self._tickets[tkt.ticket_id] = tkt
            return tkt

    def get(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock:
            return self._tickets.get(ticket_id)

    # --- 多轮消息 ---

    def append_customer(self, ticket_id: str, text: str) -> ChatTurn:
        with self._lock:
            tkt = self._tickets[ticket_id]
            turn = ChatTurn(role="customer", text=text)
            tkt.add_message(turn)
            if tkt.status == TicketStatus.NEW:
                tkt.status = TicketStatus.OPEN
            return turn

    def append_agent(self, ticket_id: str, reply: AgentReply) -> ChatTurn:
        """把 agent 回复落到 ticket 历史，并回填 turn_id 到 reply。"""
        with self._lock:
            tkt = self._tickets[ticket_id]
            turn = ChatTurn(role="agent", text=reply.answer)
            tkt.add_message(turn)
            reply.turn_id = turn.turn_id
            # agent 主动判断需要转人工时，状态置 escalated(但接管动作由 handoff 完成)
            if reply.escalate:
                tkt.status = TicketStatus.ESCALATED
            return turn

    def append_human_agent(self, ticket_id: str, text: str) -> ChatTurn:
        with self._lock:
            tkt = self._tickets[ticket_id]
            turn = ChatTurn(role="human_agent", text=text)
            tkt.add_message(turn)
            return turn

    def history(self, ticket_id: str) -> List[ChatTurn]:
        with self._lock:
            tkt = self._tickets.get(ticket_id)
            return list(tkt.messages) if tkt else []

    # --- 转人工(human handoff) ---

    def handoff(self, ticket_id: str, reason: str = "agent_escalation") -> Ticket:
        """把工单移交人工坐席。之后 should_autorespond 返回 False。"""
        with self._lock:
            tkt = self._tickets[ticket_id]
            tkt.status = TicketStatus.ESCALATED
            if "handoff" not in tkt.tags:
                tkt.tags.append("handoff")
            self._handed_off[ticket_id] = reason
            return tkt

    def is_handed_off(self, ticket_id: str) -> bool:
        with self._lock:
            return ticket_id in self._handed_off

    def should_autorespond(self, ticket_id: str) -> bool:
        """是否仍由 agent 自动应答(已转人工的工单交给人，不再自动答)。"""
        return not self.is_handed_off(ticket_id)

    # --- 反馈驱动的状态流转 ---

    def mark_resolved(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock:
            tkt = self._tickets.get(ticket_id)
            if tkt:
                tkt.status = TicketStatus.SOLVED
            return tkt

    def mark_reopened(self, ticket_id: str) -> Optional[Ticket]:
        with self._lock:
            tkt = self._tickets.get(ticket_id)
            if tkt:
                tkt.status = TicketStatus.REOPENED
                if "reopened" not in tkt.tags:
                    tkt.tags.append("reopened")
            return tkt

    # --- 运营统计(给 /metrics 用) ---

    def counts(self) -> Dict[str, int]:
        with self._lock:
            out = {s: 0 for s in TicketStatus.ALL}
            for tkt in self._tickets.values():
                out[tkt.status] = out.get(tkt.status, 0) + 1
            out["total"] = len(self._tickets)
            out["handed_off"] = len(self._handed_off)
            return out
