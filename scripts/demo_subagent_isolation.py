#!/usr/bin/env python
"""v3.1 demo — context-isolated subagent fan-out vs shared-context baseline.

Runs three :class:`SubagentExecutor` instances (billing / account /
technical) over three customer-support sub-queries.  Each subagent only
sees the KB slice for its domain; the orchestrator only sees the
returned :class:`SubagentSummary` objects.  We then estimate the
context-token cost of the *shared-context baseline* (what v2.3 actually
does — every specialist's underlying SupportAgent indexes the full KB)
and print the savings.

Usage:
    python scripts/demo_subagent_isolation.py

Zero LLM calls — :class:`seagent.llm.mock.MockBackend` is used
throughout.
"""
from __future__ import annotations

import os
import sys
from typing import List

THIS = os.path.dirname(__file__)
SRC = os.path.abspath(os.path.join(THIS, "..", "src"))
sys.path.insert(0, SRC)

from seagent.data import KBDoc
from seagent.multi_agent.subagent_executor import (
    SubagentExecutor,
    merge_subagent_summaries,
)


# A small KB spanning every domain we will exercise.  Realistic enough
# that BM25 has a meaningful score signal.
KB: List[KBDoc] = [
    # billing
    KBDoc("kb_b1", "退款政策", "billing",
          "您可以在订单完成后 14 天内申请退款。请进入「设置>账单」点击「申请退款」。"
          "退款一般 7-14 个工作日到账，期间可查看「账单流水」跟踪状态。"),
    KBDoc("kb_b2", "套餐降级", "subscription",
          "降级在当前计费周期结束后生效，差额以账户积分形式保留并可在下次升级时抵扣。"),
    KBDoc("kb_b3", "发票开具", "billing",
          "可在「账单>发票」中开具增值税普通发票或专用发票，需提交税号与抬头。"),
    # account
    KBDoc("kb_a1", "重置密码", "account",
          "请进入「设置>安全」点击「忘记密码」，系统会向注册邮箱发送重置链接。"),
    KBDoc("kb_a2", "双因素登录", "account_security",
          "在「安全」设置中开启 2FA，可绑定 Google Authenticator 等身份验证器应用。"),
    KBDoc("kb_a3", "团队权限", "permissions",
          "管理员可在「成员管理」中调整成员角色（Owner / Admin / Editor / Viewer）。"),
    # technical
    KBDoc("kb_t1", "Webhook 超时排查", "integrations_api",
          "请检查回调端点是否返回 2xx，并确保 5 秒内响应；可在「集成>日志」查看历史调用。"),
    KBDoc("kb_t2", "导出数据失败", "data_export",
          "若导出任务卡住，可在「数据>导出」点击「重启任务」；超过 30 分钟无响应将自动回滚。"),
    KBDoc("kb_t3", "移动端崩溃", "mobile_app",
          "请确认 App 已升级至 v3.2.1+ 并在「设置>关于」中提交崩溃日志。"),
    # general (not part of any specialist's slice — only the general fallback sees it)
    KBDoc("kb_g1", "联系客服", "general",
          "您可以通过 support@nimbusflow.com 联系我们的支持团队。"),
]


# Three customer queries spanning three domains
DEMO_QUERIES = [
    ("billing",   "我想申请退款，订单已经下了 10 天，退款多久到账？"),
    ("account",   "怎么开启双因素登录？"),
    ("technical", "我的 webhook 一直超时，该怎么排查？"),
]


def _hr(s: str = "") -> None:
    print("─" * 72)
    if s:
        print(s)
        print("─" * 72)


def main() -> None:
    _hr("v3.1  Anthropic Subagent context-isolation demo")
    print(f"KB size (full): {len(KB)} docs, total chars="
          f"{sum(len(d.text) for d in KB)}")

    # Build one subagent per domain — each constructor slices the KB to
    # that domain's topic set, so each subagent sees ONLY its own docs.
    subagents = {
        d: SubagentExecutor.from_specialist_config(
            domain=d,
            full_kb=KB,
            base_model="mock",
            token_budget=2000,
            max_context_chars=1500,
        )
        for d in ("billing", "account", "technical")
    }
    _hr("Isolated KB views (proof: no overlap)")
    for d, ex in subagents.items():
        ids = [doc.doc_id for doc in ex._kb_view.docs]
        print(f"  {d:9s} kb_size={ex.stats()['kb_size']}  doc_ids={ids}")

    # Run fan-out: each subagent processes its own sub-query and returns
    # only a SubagentSummary.
    _hr("Fan-out: each subagent returns ONLY a SubagentSummary")
    summaries = []
    for domain, query in DEMO_QUERIES:
        s = subagents[domain].handle(query)
        summaries.append(s)
        print(f"\n[{domain}] query = {query}")
        print(f"  answer_summary    = {s.answer_summary[:80]}...")
        print(f"  confidence        = {s.confidence:.3f}")
        print(f"  needs_handoff     = {s.needs_handoff} "
              f"(reason={s.handoff_reason})")
        print(f"  cited_doc_ids     = {s.cited_doc_ids}")
        print(f"  token_budget_used = {s.token_budget_used}")

    # Orchestrator merge — orchestrator NEVER sees the full passage text,
    # only the structured SubagentSummary list.
    _hr("Orchestrator merge (sees only summaries, not passage text)")
    merged = merge_subagent_summaries(summaries)
    print(merged["answer"])
    print()
    print(f"  merged.confidence    = {merged['confidence']:.3f}")
    print(f"  merged.needs_handoff = {merged['needs_handoff']}")
    print(f"  merged.cited_doc_ids = {merged['cited_doc_ids']}")
    print(f"  merged.total_tokens  = {merged['total_tokens']}")

    # ─── Token comparison ───────────────────────────────────────────
    # Shared-context baseline: in v2.3 each SpecialistAgent wraps a
    # SupportAgent whose SemanticMemory indexes the full KB.  Even with
    # post-hoc passage filtering, the BM25 index and the retrieval
    # call evaluate over every doc.  We approximate "what each
    # specialist would tokenize if it consumed the full KB" as
    # len(full_kb_text) / 4 (chars-per-token heuristic, matches the
    # subagent module's own accounting).
    isolated_tokens = sum(s.token_budget_used for s in summaries)
    full_kb_chars = sum(len(d.text) for d in KB)
    shared_per_agent_tokens = full_kb_chars // 4
    shared_total_tokens = len(DEMO_QUERIES) * shared_per_agent_tokens
    saved = shared_total_tokens - isolated_tokens
    saved_pct = (saved / shared_total_tokens * 100.0) if shared_total_tokens else 0.0

    _hr("Context-token cost comparison")
    print(f"  shared-context baseline (N={len(DEMO_QUERIES)} agents × full KB):")
    print(f"      {shared_total_tokens:>6d} tokens "
          f"({shared_per_agent_tokens} × {len(DEMO_QUERIES)})")
    print(f"  isolated subagents (per-domain slice + budget cap):")
    isolated_breakdown = ", ".join(
        f"{s.domain}={s.token_budget_used}" for s in summaries
    )
    print(f"      {isolated_tokens:>6d} tokens ({isolated_breakdown})")
    print(f"  savings: {saved:>6d} tokens  ({saved_pct:.1f}% reduction)")

    # Final per-subagent stats
    _hr("Per-subagent stats (each instance owns its own counters)")
    for d, ex in subagents.items():
        st = ex.stats()
        print(f"  {d:9s} {st}")

    _hr("Done.")


if __name__ == "__main__":
    main()
