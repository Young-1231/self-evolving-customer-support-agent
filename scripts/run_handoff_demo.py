"""v3.2 demo: OpenAI Agents SDK 2026 style ``handoff_to_X`` protocol.

Runs three scenarios through a MultiAgentOrchestrator with three domain
specialists (billing / account / technical) and prints the handoff trace
in the OpenAI Agents SDK 2026 function-tool-call format.

Run::

    python scripts/run_handoff_demo.py

Mock backend only — no network, no LLM key required.
"""
from __future__ import annotations

import json
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS, "..", "src"))

from seagent.agent.support_agent import SupportAgent
from seagent.config import Config
from seagent.data import KBDoc
from seagent.llm.base import LLMBackend
from seagent.llm.mock import MockBackend
from seagent.memory.semantic import SemanticMemory
from seagent.multi_agent import (
    IntentRouter,
    MultiAgentOrchestrator,
    SpecialistAgent,
    make_handoff_tool_schemas,
)


# ----------------------- KB -------------------------------------------------


def _kb():
    return [
        KBDoc("kb_b1", "退款政策", "billing",
              "您可以在订单完成后 14 天内申请退款。请进入「设置>账单」点击「申请退款」。"),
        KBDoc("kb_b2", "套餐降级", "subscription",
              "降级在当前计费周期结束后生效，差额以账户积分形式保留。"),
        KBDoc("kb_a1", "账户余额", "account",
              "账户余额会在每月 1 号结算，可在「设置>账户」查看明细。"),
        KBDoc("kb_a2", "重置密码", "account",
              "请进入「设置>安全」点击「忘记密码」，按邮箱链接重置即可。"),
        KBDoc("kb_a3", "账号安全", "account_security",
              "若账号疑似被盗，请立即重置密码并联系 support@nimbusflow.com 冻结账户。"),
        KBDoc("kb_t1", "API key 故障", "integrations_api",
              "请检查 API key 是否过期或权限不足；如仍不工作，重新生成 key。"),
        KBDoc("kb_t2", "Webhook 超时", "integrations_api",
              "请确保 Webhook 端点 5 秒内返回 2xx；可在「集成>日志」查看历史调用。"),
    ]


# ----------------------- stub router backend --------------------------------


class _RouterStub(LLMBackend):
    """Returns canned router payloads — one per query so the demo is
    deterministic without any real LLM."""

    name = "router_stub"

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def _chat(self, system, user):
        if not self.payloads:
            return '{"intents":[]}'
        i = min(self.calls, len(self.payloads) - 1)
        self.calls += 1
        return self.payloads[i]

    def generate_answer(self, query, contexts):
        return ""


# ----------------------- demo driver ---------------------------------------


def _print_trace(scenario, query, res):
    print("=" * 72)
    print(f"[{scenario}] query: {query}")
    print("-" * 72)
    print("answer:")
    print(res.answer)
    print()
    print(f"escalate={res.escalate}  confidence={res.confidence:.3f}")
    trace = getattr(res, "handoff_trace", []) or []
    if not trace:
        print("handoff trace: (none — specialist resolved without handoff)")
    else:
        print("handoff trace (OpenAI Agents SDK 2026 tool-call format):")
        for i, evt in enumerate(trace, 1):
            fn = evt["function"]["name"]
            args = evt["function"]["arguments"]
            tag = "[dispatched]" if evt.get("dispatched") else "[suppressed]"
            print(f"  {i}. {tag} {fn}")
            print(f"     context_summary: {args['context_summary']}")
            print(f"     reason         : {args['reason']}")
    print()


def main():
    kb = _kb()
    cfg = Config()
    backend = MockBackend()
    sem = SemanticMemory(kb, cfg.score_norm_k)
    base = SupportAgent(cfg, backend, sem)

    # NOTE: we deliberately build specialists WITHOUT a retrieval kb_filter
    # so each specialist still sees the top KB hit regardless of topic — that
    # is what lets ``_decide_handoff`` notice topic mismatches.  The
    # specialist's own ``domain_topics`` (set via the constructor) drives the
    # mismatch decision.  Production code can keep the filter on; this is a
    # demo trade-off so the handoff trace is observable.
    def _spec(domain, topics):
        return SpecialistAgent(
            domain=domain,
            base_agent=base,
            kb_filter=None,
            domain_topics=topics,
            handoff_confidence_threshold=0.3,
        )

    specialists = {
        "billing":   _spec("billing",   ["billing", "subscription"]),
        "account":   _spec("account",   ["account", "account_security", "permissions"]),
        "technical": _spec("technical", ["integrations_api", "troubleshooting", "mobile_app", "data_export"]),
        "general":   SpecialistAgent.for_domain("general", base),
    }

    # Canned router decisions — one per scenario.  In production these would
    # come from a real LLM router; in the demo we pin them so the trace is
    # deterministic.
    payloads = [
        # 1) "我账户余额怎么算" -> router mis-routes to billing; specialist
        #    detects the top KB hit is an *account* doc and hands off.
        '{"intents":[{"label":"billing","sub_query":"我账户余额怎么算","confidence":0.6}]}',
        # 2) "API key 不工作" -> straight to technical, no handoff
        '{"intents":[{"label":"technical","sub_query":"API key 不工作","confidence":0.85}]}',
        # 3) "退款迟迟不到 + 账号被盗" -> multi_intent fan-out + handoff mix:
        #    sub1 billing -> ok; sub2 starts at billing but should hand off
        #    to account (security topic).
        '{"intents":['
        '{"label":"billing","sub_query":"退款迟迟不到怎么办","confidence":0.85},'
        '{"label":"billing","sub_query":"账号被盗了怎么办","confidence":0.5}'
        ']}',
    ]

    router = IntentRouter(backend=_RouterStub(payloads))
    orch = MultiAgentOrchestrator(
        router, specialists,
        default_specialist="general",
        guardrail_mode="none",
        enable_mid_flight_handoff=True,
        max_handoff_hops=1,
    )

    print("OpenAI Agents SDK 2026 tool schemas available to each specialist:")
    schemas = make_handoff_tool_schemas(
        [d for d in specialists if d != "general"]
    )
    print(json.dumps([s["function"]["name"] for s in schemas], indent=2))
    print()

    _print_trace(
        "scenario 1 — topic mismatch (billing → account)",
        "我账户余额怎么算？",
        orch.handle("我账户余额怎么算？"),
    )
    _print_trace(
        "scenario 2 — in-domain (technical, no handoff)",
        "API key 不工作怎么办？",
        orch.handle("API key 不工作怎么办？"),
    )
    _print_trace(
        "scenario 3 — multi_intent + handoff (billing × 2; sub-2 → account)",
        "退款迟迟不到，并且我的账号好像被盗了。",
        orch.handle("退款迟迟不到，并且我的账号好像被盗了。"),
    )

    print("=" * 72)
    print("orchestrator stats:")
    for k, v in orch.stats().items():
        print(f"  {k:30s} {v}")


if __name__ == "__main__":
    main()
