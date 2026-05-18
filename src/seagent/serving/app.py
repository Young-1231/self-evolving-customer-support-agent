"""FastAPI 应用：把自进化客服 agent 暴露成 HTTP 服务。

FastAPI 是 **可选依赖**：
  - 装了 fastapi+uvicorn：`create_app(agent)` 返回可运行的 ASGI app。
  - 没装：本模块仍能被 import(不抛错)，`create_app` 抛出友好的 RuntimeError，
    且 schema/session/feedback 等核心逻辑完全不受影响、可独立测试。

agent 通过依赖注入传入(任意实现了 `.handle(query) -> AgentResult` 的对象都行)，
因此线上接真实 SupportAgent、测试里接 mock 都用同一套代码。

端点：
  POST /chat      进一条客户消息 -> AgentReply
  POST /feedback  上报一条(隐式/显式)反馈
  POST /handoff   转人工
  GET  /healthz   健康检查
  GET  /metrics   运营 + 代理指标
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .schema import AgentReply, Feedback, FeedbackKind, Ticket, TicketStatus
from .session import SessionManager
from .feedback import FeedbackProcessor

# --- 可选依赖：try-import fastapi ---
try:  # pragma: no cover - import 分支本身无需覆盖
    from fastapi import Body, Depends, FastAPI, HTTPException
    _HAS_FASTAPI = True
except Exception:  # noqa: BLE001
    FastAPI = None  # type: ignore
    Body = Depends = HTTPException = None  # type: ignore
    _HAS_FASTAPI = False


_FASTAPI_HINT = (
    "FastAPI 未安装，无法启动 HTTP 服务。\n"
    "  pip install 'fastapi>=0.110' 'uvicorn[standard]'\n"
    "核心业务逻辑(schema/session/feedback)不依赖 FastAPI，可直接 import 使用。"
)


# --- 适配层：把 agent 的 AgentResult 投影成服务层 AgentReply ---

def result_to_reply(result: Any, guardrail: Optional[str] = None) -> AgentReply:
    """把 agent.support_agent.AgentResult(或任意鸭子类型)转成 AgentReply。

    只读取约定字段，故 mock 对象只需带 answer/escalate/confidence/used_sources。
    """
    return AgentReply(
        answer=getattr(result, "answer", ""),
        escalate=bool(getattr(result, "escalate", False)),
        confidence=float(getattr(result, "confidence", 0.0)),
        sources=list(getattr(result, "used_sources", []) or []),
        guardrail=guardrail,
    )


def create_app(
    agent: Any,
    sessions: Optional[SessionManager] = None,
    feedback: Optional[FeedbackProcessor] = None,
) -> "FastAPI":
    """构造 FastAPI app。

    Args:
        agent: 实现了 `handle(query: str) -> AgentResult` 的对象(真实 SupportAgent 或 mock)。
        sessions / feedback: 可注入，便于测试复用同一状态。
    """
    if not _HAS_FASTAPI:
        raise RuntimeError(_FASTAPI_HINT)

    sm = sessions or SessionManager()
    fp = feedback or FeedbackProcessor()

    app = FastAPI(title="self-evolving support agent", version="0.1.0")

    # 依赖注入：端点通过 Depends 拿到这些单例，测试可 override
    def get_agent() -> Any:
        return agent

    def get_sessions() -> SessionManager:
        return sm

    def get_feedback() -> FeedbackProcessor:
        return fp

    app.state.agent = agent
    app.state.sessions = sm
    app.state.feedback = fp

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "agent": type(agent).__name__}

    @app.post("/chat")
    def chat(
        payload: Dict[str, Any] = Body(...),
        agent: Any = Depends(get_agent),
        sessions: SessionManager = Depends(get_sessions),
    ) -> Dict[str, Any]:
        """进一条客户消息，返回 AgentReply。

        请求体: {customer_id, text, ticket_id?, subject?, channel?}
        """
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        customer_id = payload.get("customer_id") or "anon"

        tkt = sessions.get_or_create(
            ticket_id=payload.get("ticket_id"),
            customer_id=customer_id,
            subject=payload.get("subject", ""),
            channel=payload.get("channel", "chat"),
        )
        sessions.append_customer(tkt.ticket_id, text)

        # 已转人工的工单不再自动应答，交给人工坐席
        if not sessions.should_autorespond(tkt.ticket_id):
            return {
                "ticket_id": tkt.ticket_id,
                "handed_off": True,
                "reply": None,
                "detail": "ticket is handled by a human agent",
            }

        result = agent.handle(text)
        reply = result_to_reply(result)
        sessions.append_agent(tkt.ticket_id, reply)
        # agent 自判需要升级时，直接标记转人工(可被运营策略覆盖)
        if reply.escalate:
            sessions.handoff(tkt.ticket_id, reason="agent_escalation")
        return {"ticket_id": tkt.ticket_id, "handed_off": reply.escalate, "reply": reply.to_dict()}

    @app.post("/feedback")
    def submit_feedback(
        payload: Dict[str, Any] = Body(...),
        sessions: SessionManager = Depends(get_sessions),
        feedback: FeedbackProcessor = Depends(get_feedback),
    ) -> Dict[str, Any]:
        """上报反馈(thumbs/resolved/reopened/csat)，转成学习信号。

        请求体: {ticket_id, kind, value?, turn_id?, comment?}
        """
        ticket_id = payload.get("ticket_id")
        kind = payload.get("kind")
        if not ticket_id or kind not in FeedbackKind.ALL:
            raise HTTPException(status_code=400, detail="ticket_id and a valid kind are required")

        fb = Feedback(
            ticket_id=ticket_id,
            kind=kind,
            value=payload.get("value"),
            turn_id=payload.get("turn_id", ""),
            comment=payload.get("comment", ""),
        )
        tkt = sessions.get(ticket_id)
        # 反馈驱动状态流转
        if kind == FeedbackKind.RESOLVED:
            sessions.mark_resolved(ticket_id)
        elif kind == FeedbackKind.REOPENED:
            sessions.mark_reopened(ticket_id)
        tkt = sessions.get(ticket_id)  # 取流转后的最新态

        signal = feedback.ingest(fb, tkt)
        return {"feedback_id": fb.feedback_id, "signal": signal, "queued_for_review": signal["is_failure"]}

    @app.post("/handoff")
    def handoff(
        payload: Dict[str, Any] = Body(...),
        sessions: SessionManager = Depends(get_sessions),
    ) -> Dict[str, Any]:
        """把工单转人工。请求体: {ticket_id, reason?}"""
        ticket_id = payload.get("ticket_id")
        if not ticket_id or sessions.get(ticket_id) is None:
            raise HTTPException(status_code=404, detail="unknown ticket_id")
        tkt = sessions.handoff(ticket_id, reason=payload.get("reason", "manual"))
        return {"ticket_id": ticket_id, "status": tkt.status, "handed_off": True}

    @app.get("/metrics")
    def metrics(
        sessions: SessionManager = Depends(get_sessions),
        feedback: FeedbackProcessor = Depends(get_feedback),
    ) -> Dict[str, Any]:
        """运营指标(工单状态分布) + 反馈代理指标。"""
        return {
            "tickets": sessions.counts(),
            "feedback": feedback.proxy_metrics(),
        }

    return app
