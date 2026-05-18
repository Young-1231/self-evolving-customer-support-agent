"""serving 服务层测试。

纯 stdlib 部分(schema / SessionManager / FeedbackProcessor)在 base python 下全绿。
FastAPI 端点用 pytest.importorskip 跳过(未装 fastapi 时自动 skip)。
    PYTHONPATH=src python -m pytest -q tests/test_serving.py
"""
from dataclasses import dataclass, field
from typing import List

import pytest

from seagent.serving.schema import (
    AgentReply,
    ChatTurn,
    Feedback,
    FeedbackKind,
    Ticket,
    TicketStatus,
)
from seagent.serving.session import SessionManager
from seagent.serving.feedback import FeedbackProcessor, to_training_signal


# --- schema ---

def test_schema_construction_and_ids():
    tkt = Ticket(customer_id="c1", subject="refund", channel="email")
    assert tkt.ticket_id.startswith("tkt-")
    assert tkt.status == TicketStatus.NEW
    assert tkt.messages == []

    turn = ChatTurn(role="customer", text="where is my refund")
    tkt.add_message(turn)
    assert tkt.last_customer_text() == "where is my refund"
    assert turn.turn_id.startswith("turn-")

    reply = AgentReply(answer="checking", confidence=0.8, sources=["kb"])
    assert reply.trace_id.startswith("trace-")
    assert reply.to_dict()["answer"] == "checking"

    fb = Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_DOWN, value=True)
    assert fb.feedback_id.startswith("fb-")
    assert fb.kind in FeedbackKind.ALL


# --- SessionManager 多轮 + handoff ---

def test_session_multi_turn():
    sm = SessionManager()
    tkt = sm.get_or_create(ticket_id=None, customer_id="c1")
    sm.append_customer(tkt.ticket_id, "hi")
    # NEW -> OPEN
    assert sm.get(tkt.ticket_id).status == TicketStatus.OPEN

    reply = AgentReply(answer="hello", confidence=0.9)
    sm.append_agent(tkt.ticket_id, reply)
    assert reply.turn_id  # 回填了 turn_id

    sm.append_customer(tkt.ticket_id, "still broken")
    hist = sm.history(tkt.ticket_id)
    assert [t.role for t in hist] == ["customer", "agent", "customer"]


def test_session_get_or_create_idempotent():
    sm = SessionManager()
    a = sm.get_or_create(ticket_id="tkt-fixed", customer_id="c1")
    b = sm.get_or_create(ticket_id="tkt-fixed", customer_id="c1")
    assert a is b
    assert a.ticket_id == "tkt-fixed"


def test_session_handoff_stops_autorespond():
    sm = SessionManager()
    tkt = sm.get_or_create(ticket_id=None, customer_id="c1")
    assert sm.should_autorespond(tkt.ticket_id) is True

    sm.handoff(tkt.ticket_id, reason="manual")
    assert sm.is_handed_off(tkt.ticket_id) is True
    assert sm.should_autorespond(tkt.ticket_id) is False
    t = sm.get(tkt.ticket_id)
    assert t.status == TicketStatus.ESCALATED
    assert "handoff" in t.tags


def test_session_counts():
    sm = SessionManager()
    t1 = sm.get_or_create(None, "c1")
    t2 = sm.get_or_create(None, "c2")
    sm.mark_resolved(t1.ticket_id)
    sm.handoff(t2.ticket_id)
    c = sm.counts()
    assert c["total"] == 2
    assert c["solved"] == 1
    assert c["handed_off"] == 1


# --- FeedbackProcessor: 噪声反馈 -> 学习信号 / 待复盘 / 代理指标 ---

def _ticket_with_query(q, tags=None):
    tkt = Ticket(customer_id="c1", tags=tags or [])
    tkt.add_message(ChatTurn(role="customer", text=q))
    return tkt


def test_to_training_signal_polarity():
    tkt = _ticket_with_query("how to reset password")
    up = to_training_signal(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_UP), tkt)
    down = to_training_signal(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_DOWN), tkt)
    reopen = to_training_signal(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.REOPENED), tkt)
    csat_low = to_training_signal(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.CSAT, value=1), tkt)
    csat_mid = to_training_signal(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.CSAT, value=3), tkt)

    assert up["polarity"] == 1 and up["is_failure"] is False
    assert down["polarity"] == -1 and down["is_failure"] is True
    assert reopen["polarity"] == -1 and reopen["is_failure"] is True
    assert csat_low["polarity"] == -1
    assert csat_mid["polarity"] == 0
    # 信号能还原触发它的客户原问题
    assert down["query"] == "how to reset password"
    # reopen 权重应高于单次点踩
    assert reopen["weight"] >= down["weight"]


def test_thumbs_down_and_reopen_become_review_drafts():
    fp = FeedbackProcessor()
    tkt = _ticket_with_query("charged twice for one order", tags=["billing"])

    fp.ingest(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_DOWN), tkt)
    fp.ingest(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.REOPENED), tkt)

    pending = fp.pending_review()
    assert len(pending) == 2
    for item in pending:
        # 待复盘案例必须人审、且不携带 gold resolution、未进入训练轮
        assert item.needs_human_review is True
        draft = item.case_draft
        assert draft["resolution"] == ""
        assert draft["learned_round"] == -1
        assert draft["query"] == "charged twice for one order"
        assert draft["topic"] == "billing"  # 从工单 tag 推断主题

    # reopen 的草稿应建议升级
    reopen_draft = pending[1].case_draft
    assert reopen_draft["should_escalate"] is True


def test_positive_feedback_no_review_draft():
    fp = FeedbackProcessor()
    tkt = _ticket_with_query("thanks it works")
    fp.ingest(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_UP), tkt)
    fp.ingest(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.RESOLVED, value=True), tkt)
    assert fp.pending_review() == []


def test_proxy_metrics():
    fp = FeedbackProcessor()
    t1 = _ticket_with_query("q1")
    t2 = _ticket_with_query("q2")
    t3 = _ticket_with_query("q3")
    fp.ingest(Feedback(ticket_id=t1.ticket_id, kind=FeedbackKind.THUMBS_UP), t1)
    fp.ingest(Feedback(ticket_id=t2.ticket_id, kind=FeedbackKind.THUMBS_DOWN), t2)
    fp.ingest(Feedback(ticket_id=t3.ticket_id, kind=FeedbackKind.CSAT, value=3), t3)  # 中性

    m = fp.proxy_metrics()
    assert m["n_signals"] == 3
    assert m["n_effective"] == 2          # 中性 CSAT 不计入
    assert m["resolution_rate"] == 0.5    # 1 正 / 2 有效
    assert m["dissatisfaction_rate"] == 0.5
    # 3 张工单，1 张有负向 -> deflection 2/3
    assert m["deflection_rate"] == pytest.approx(0.6667, abs=1e-3)
    assert m["n_pending_review"] == 1


# --- Case 草稿字段与 episodic.Case 对齐(人审通过后可直接构造) ---

def test_case_draft_matches_episodic_case_fields():
    Case = pytest.importorskip("seagent.memory.episodic").Case
    fp = FeedbackProcessor()
    tkt = _ticket_with_query("cannot login", tags=["auth"])
    fp.ingest(Feedback(ticket_id=tkt.ticket_id, kind=FeedbackKind.THUMBS_DOWN), tkt)
    draft = fp.pending_review()[0].case_draft
    # 人审补全 resolution 后，应能直接 Case(**draft) 构造
    draft["resolution"] = "guide user through password reset flow"
    case = Case(**draft)
    assert case.topic == "auth"
    assert case.query == "cannot login"


# --- FastAPI 端点(未装 fastapi 自动跳过) ---

@dataclass
class _StubResult:
    answer: str
    escalate: bool = False
    confidence: float = 0.7
    used_sources: List[str] = field(default_factory=lambda: ["kb"])


class _StubAgent:
    def __init__(self, escalate=False):
        self._escalate = escalate

    def handle(self, query: str):
        return _StubResult(answer=f"echo:{query}", escalate=self._escalate)


def _client(agent):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from seagent.serving.app import create_app

    return TestClient(create_app(agent))


def test_endpoint_healthz_and_chat():
    client = _client(_StubAgent())
    assert client.get("/healthz").json()["status"] == "ok"

    r = client.post("/chat", json={"customer_id": "c1", "text": "hello"})
    body = r.json()
    assert r.status_code == 200
    assert body["reply"]["answer"] == "echo:hello"
    assert body["handed_off"] is False
    tid = body["ticket_id"]

    # 多轮：复用同一 ticket
    r2 = client.post("/chat", json={"customer_id": "c1", "text": "again", "ticket_id": tid})
    assert r2.json()["ticket_id"] == tid


def test_endpoint_chat_validation():
    client = _client(_StubAgent())
    assert client.post("/chat", json={"customer_id": "c1", "text": "  "}).status_code == 400


def test_endpoint_escalating_agent_triggers_handoff():
    client = _client(_StubAgent(escalate=True))
    r = client.post("/chat", json={"customer_id": "c1", "text": "complex issue"})
    body = r.json()
    assert body["handed_off"] is True
    tid = body["ticket_id"]
    # 已转人工 -> 后续消息不再自动应答
    r2 = client.post("/chat", json={"customer_id": "c1", "text": "more", "ticket_id": tid})
    assert r2.json()["handed_off"] is True
    assert r2.json()["reply"] is None


def test_endpoint_feedback_and_metrics():
    client = _client(_StubAgent())
    tid = client.post("/chat", json={"customer_id": "c1", "text": "hi"}).json()["ticket_id"]

    fr = client.post("/feedback", json={"ticket_id": tid, "kind": "thumbs_down"})
    fbody = fr.json()
    assert fbody["queued_for_review"] is True
    assert fbody["signal"]["polarity"] == -1

    m = client.get("/metrics").json()
    assert m["feedback"]["n_pending_review"] == 1
    assert m["tickets"]["total"] == 1


def test_endpoint_feedback_bad_kind():
    client = _client(_StubAgent())
    tid = client.post("/chat", json={"customer_id": "c1", "text": "hi"}).json()["ticket_id"]
    assert client.post("/feedback", json={"ticket_id": tid, "kind": "bogus"}).status_code == 400


def test_endpoint_handoff():
    client = _client(_StubAgent())
    tid = client.post("/chat", json={"customer_id": "c1", "text": "hi"}).json()["ticket_id"]
    r = client.post("/handoff", json={"ticket_id": tid, "reason": "manual"})
    assert r.json()["handed_off"] is True
    assert client.post("/handoff", json={"ticket_id": "nope"}).status_code == 404
