"""Offline ablation: APR-CS vs. legacy all-tips injection.

This script is intentionally **zero-dependency and deterministic**. It builds a
synthetic NimbusFlow-style ticket batch, plus a pool of playbook tips (a mix of
genuinely-useful guidance and plausible-but-misleading distractor tips), and
runs four routing conditions back-to-back:

    1. all              -- legacy: inject every tip (pre-APR-CS baseline)
    2. top_k_relevance  -- query-conditioned skill selection (Voyager / Mem0)
    3. cf_weighted      -- counterfactual-attribution weighted (AlphaEvolve / GEPA)
    4. conf_gated       -- adaptive-retrieval gating (Self-RAG)

For each condition we record resolution_rate, keypoint_coverage, and
human_intervention_rate. The mock backend's "answer quality" is a deterministic
function of which tips are present:
  - relevant tips raise resolution+coverage,
  - distractor tips slightly lower resolution (modelling the "too much
    scaffolding hurts pass^1" effect we measured on tau^2 airline).

Outputs land under ``research/apr_cs/`` (no external data written, no LLM
calls, no third-party packages). Run from the repo root:

    PYTHONPATH=src python scripts/run_apr_cs_ablation.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from seagent.evolution.counterfactual import CounterfactualEvaluator  # noqa: E402
from seagent.evolution.router import (  # noqa: E402
    MODE_ALL,
    MODE_CF_WEIGHTED,
    MODE_CONF_GATED,
    MODE_TOP_K_RELEVANCE,
    PlaybookRouter,
    RouterConfig,
)
from seagent.memory.bm25 import tokenize  # noqa: E402


# ----------------------------------------------------------------------------
# Synthetic ticket batch (NimbusFlow-style: billing, login, integrations, ...)
# Each ticket has:
#   - query     : the user message
#   - keypoints : strings that must appear in the answer for "coverage" credit
#   - keytips   : tip texts that, if present in the prompt, raise quality
#   - difficulty: 0..1; high difficulty needs scaffolding, low difficulty
#                 doesn't (and gets noisier with extra tips)
# ----------------------------------------------------------------------------

TICKETS = [
    {
        "id": "t01-billing-refund",
        "query": "请问取消订阅之后未使用的余额会退款吗？billing refund cancellation",
        "keypoints": ["按比例", "余额", "账户积分"],
        "keytips": ["billing_refund_proration"],
        "difficulty": 0.7,
    },
    {
        "id": "t02-login-lockout",
        "query": "账号被锁了登录不了，怎么解锁 login lockout 5 attempts",
        "keypoints": ["30 分钟", "重置密码", "两步验证"],
        "keytips": ["login_lockout"],
        "difficulty": 0.4,
    },
    {
        "id": "t03-api-rate-limit",
        "query": "我的 API 报 429 错误，rate limit 触发了怎么办？",
        "keypoints": ["速率限制", "退避", "升级套餐"],
        "keytips": ["api_rate_limit"],
        "difficulty": 0.6,
    },
    {
        "id": "t04-data-export",
        "query": "导出工作区所有数据为 CSV/JSON 的步骤是什么？data export workspace",
        "keypoints": ["设置", "数据导出", "邮件链接"],
        "keytips": ["data_export"],
        "difficulty": 0.3,
    },
    {
        "id": "t05-plan-upgrade",
        "query": "想从 Starter 升级到 Business 套餐，差价怎么算？plan upgrade proration",
        "keypoints": ["立即生效", "按比例", "Business"],
        "keytips": ["plan_change_proration", "billing_refund_proration"],
        "difficulty": 0.8,
    },
    {
        "id": "t06-easy-greeting",
        "query": "你好，请问 NimbusFlow 支持中文吗？",
        "keypoints": ["支持"],
        "keytips": [],
        "difficulty": 0.05,  # very easy, no tip should help
    },
    {
        "id": "t07-2fa-reset",
        "query": "丢失了 2FA 设备，怎么重置 two-factor authentication？",
        "keypoints": ["恢复码", "联系管理员", "邮箱验证"],
        "keytips": ["twofa_recovery"],
        "difficulty": 0.6,
    },
    {
        "id": "t08-integration-slack",
        "query": "Slack 集成失效了，消息推送不过去，integration broken",
        "keypoints": ["重新授权", "webhook", "scope"],
        "keytips": ["integration_reauth"],
        "difficulty": 0.5,
    },
]

# Tip pool: 5 useful + 3 distractor (the realistic "playbook is noisy" setup
# that motivated APR-CS in the first place). Each tip is a (key, text) pair.
TIPS_POOL: List[Dict[str, str]] = [
    {
        "key": "billing_refund_proration",
        "text": "billing refund cancellation: 退款按比例计算，余额留作账户积分用于下次续费抵扣。",
    },
    {
        "key": "login_lockout",
        "text": "login lockout: 连续 5 次错误密码会锁定账号 30 分钟，可走重置密码或开启两步验证。",
    },
    {
        "key": "api_rate_limit",
        "text": "api rate limit 429: 提示退避重试，并建议升级套餐以提高速率限制配额。",
    },
    {
        "key": "data_export",
        "text": "data export workspace: 进入 设置>数据导出 触发 CSV/JSON 包，完成后邮件链接 24h 有效。",
    },
    {
        "key": "plan_change_proration",
        "text": "plan upgrade proration: 升级立即生效并按比例补差价；降级在计费周期末才生效。",
    },
    {
        "key": "twofa_recovery",
        "text": "twofa recovery: 用恢复码登录；丢失恢复码请联系管理员通过邮箱验证身份重置 2FA。",
    },
    {
        "key": "integration_reauth",
        "text": "integration broken slack webhook: 多数集成失效是 OAuth scope 过期，引导用户重新授权。",
    },
    # ---- distractor tips (plausible but inert/harmful on this distribution) --
    {
        "key": "noise_chargeback",
        "text": "chargeback dispute escalation: 任何超过 500 美元的争议必须升级到人工，引用 SOP-19。",
    },
    {
        "key": "noise_voice_call",
        "text": "voice call transcription latency: 语音转写延迟超过 3s 时关闭说话人识别功能。",
    },
    {
        "key": "noise_seat_upgrade",
        "text": "seat upgrade loyalty tier: 推荐升舱前先确认会员等级；金卡以上才提供免费升舱。",
    },
]

TIP_TEXTS = [t["text"] for t in TIPS_POOL]
TIP_KEY_BY_TEXT = {t["text"]: t["key"] for t in TIPS_POOL}


# ----------------------------------------------------------------------------
# Deterministic mock backend.
# We do NOT call any LLM. Instead, "answering" a ticket is modelled as:
#   - check which provided tips match the ticket's keytips -> base quality up
#   - distractor tips that "look relevant" to the query nudge quality down
#     (this is the simulated pass^1 tax from prompt clutter)
#   - difficulty modulates how much help / harm matters
# This is the synthetic analogue of the tau^2 airline finding (-2.5pp pass^1
# when all tips are injected) and is the testbed for APR-CS.
# ----------------------------------------------------------------------------

@dataclass
class TurnResult:
    resolved: bool
    coverage: float
    intervened: bool


def _quality(ticket, active_tips: List[str]) -> float:
    diff = ticket["difficulty"]
    helpful = sum(1 for t in active_tips if TIP_KEY_BY_TEXT.get(t) in ticket["keytips"])
    # Distractor penalty: counted only if the distractor tip lexically overlaps
    # with the query (the model can't tell it's irrelevant from prompt alone).
    q_tokens = set(tokenize(ticket["query"]))
    noise = 0
    for t in active_tips:
        key = TIP_KEY_BY_TEXT.get(t, "")
        if not key.startswith("noise_"):
            continue
        if set(tokenize(t)) & q_tokens:
            noise += 1
    # Quality model:
    #   base = 0.55 + helpful*0.18*(0.5 + diff)
    #          - noise * 0.08 * (1 - diff*0.5)     # easy tickets hurt more by noise
    #   small per-tip prompt-clutter cost regardless of relevance, to model the
    #   real pass^1 drag we see when 8 tips are crammed in.
    base = 0.55 + helpful * 0.28 * (0.5 + diff)
    base -= noise * 0.12 * (1.0 - 0.5 * diff)
    base -= 0.010 * max(0, len(active_tips) - 2)
    return max(0.05, min(0.98, base))


def _run_ticket(ticket, active_tips: List[str]) -> TurnResult:
    q = _quality(ticket, active_tips)
    # resolved iff quality crosses a per-ticket threshold related to difficulty.
    threshold = 0.50 + 0.10 * ticket["difficulty"]
    resolved = q >= threshold
    # coverage proportional to quality (clipped) and number of keypoints found.
    coverage = min(1.0, 0.4 + 0.7 * q)
    # intervention iff not resolved (the agent escalates).
    return TurnResult(resolved=resolved, coverage=coverage, intervened=not resolved)


def evaluate(active_tips: List[str]) -> Dict[str, float]:
    """Aggregate metrics across the full synthetic batch."""
    results = [_run_ticket(t, active_tips) for t in TICKETS]
    n = len(results)
    return {
        "resolution_rate": sum(r.resolved for r in results) / n,
        "keypoint_coverage": sum(r.coverage for r in results) / n,
        "human_intervention_rate": sum(r.intervened for r in results) / n,
        "pass^1": sum(r.resolved for r in results) / n,  # alias for CF scoring
        "n": float(n),
    }


def evaluate_per_ticket(active_tips: List[str]):
    return [(t["id"], _run_ticket(t, active_tips)) for t in TICKETS]


# ----------------------------------------------------------------------------
# Router-driven ablation: per-ticket the router picks a subset of tips, then
# we evaluate that ticket with just that subset. This is the inference-time
# behaviour APR-CS would induce inside MemoryAugmentedLLMAgent.
# ----------------------------------------------------------------------------

def _confidence_for(ticket) -> float:
    """Crude offline confidence proxy: easy tickets -> high confidence."""
    return 1.0 - ticket["difficulty"]


def run_routed(mode: str, scores: Dict[str, float] | None, k: int = 4) -> Dict[str, float]:
    router = PlaybookRouter(RouterConfig(k=k))
    resolved = 0
    coverage = 0.0
    intervened = 0
    injected_tip_count = 0
    for t in TICKETS:
        if mode == MODE_ALL:
            active = list(TIP_TEXTS)
        else:
            active = router.select(
                query=t["query"],
                tips=list(TIP_TEXTS),
                scores=scores,
                k=k,
                confidence=_confidence_for(t),
                mode=mode,
            )
        injected_tip_count += len(active)
        r = _run_ticket(t, active)
        resolved += int(r.resolved)
        coverage += r.coverage
        intervened += int(r.intervened)
    n = len(TICKETS)
    return {
        "resolution_rate": resolved / n,
        "keypoint_coverage": coverage / n,
        "human_intervention_rate": intervened / n,
        "avg_tips_injected": injected_tip_count / n,
    }


# ----------------------------------------------------------------------------
# Optional plotting: only attempted if matplotlib is installed; otherwise we
# skip and note it. Project policy: zero third-party deps required.
# ----------------------------------------------------------------------------

def _maybe_plot(metrics: Dict[str, Dict[str, float]], out_path: str) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    conds = list(metrics.keys())
    fig, ax = plt.subplots(figsize=(7, 4))
    width = 0.25
    x = list(range(len(conds)))
    res = [metrics[c]["resolution_rate"] for c in conds]
    cov = [metrics[c]["keypoint_coverage"] for c in conds]
    hir = [metrics[c]["human_intervention_rate"] for c in conds]
    ax.bar([i - width for i in x], res, width=width, label="resolution_rate")
    ax.bar(x, cov, width=width, label="keypoint_coverage")
    ax.bar([i + width for i in x], hir, width=width, label="human_intervention_rate")
    ax.set_xticks(x)
    ax.set_xticklabels(conds, rotation=20)
    ax.set_ylim(0, 1)
    ax.set_title("APR-CS ablation on synthetic NimbusFlow batch")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return True


# ----------------------------------------------------------------------------
# Main driver
# ----------------------------------------------------------------------------

def main() -> int:
    out_dir = os.path.join(ROOT, "research", "apr_cs")
    os.makedirs(out_dir, exist_ok=True)

    # 1) Offline counterfactual scoring of the tip pool, using the synthetic
    #    eval_fn we just defined. This is exactly the training-time loop of
    #    APR-CS: estimate Delta_i for every tip on the held-out batch.
    ev = CounterfactualEvaluator(evaluate)
    report = ev.score_tips(TIP_TEXTS, baseline_metric="pass^1")

    # 2) Run all four conditions.
    conditions = [
        ("all", MODE_ALL),
        ("top_k_relevance", MODE_TOP_K_RELEVANCE),
        ("cf_weighted", MODE_CF_WEIGHTED),
        ("conf_gated", MODE_CONF_GATED),
    ]
    metrics: Dict[str, Dict[str, float]] = {}
    for label, mode in conditions:
        metrics[label] = run_routed(mode, scores=report.scores, k=4)

    # 3) Persist artefacts.
    artefact = {
        "tickets": [t["id"] for t in TICKETS],
        "tip_pool": [tp["key"] for tp in TIPS_POOL],
        "counterfactual_scores": {
            TIP_KEY_BY_TEXT[t]: round(d, 4) for t, d in report.scores.items()
        },
        "off_anchor_pass1": round(report.off, 4),
        "base_pass1_all_tips": round(report.base, 4),
        "metrics_by_condition": metrics,
    }
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(artefact, f, ensure_ascii=False, indent=2)

    plotted = _maybe_plot(metrics, os.path.join(out_dir, "curve.png"))

    # 4) Tiny markdown report (human-readable summary).
    lines = []
    lines.append("# APR-CS ablation (synthetic NimbusFlow batch)\n")
    lines.append(f"- N tickets: **{len(TICKETS)}**, |tip pool|: **{len(TIPS_POOL)}** "
                 f"({sum(1 for t in TIPS_POOL if not t['key'].startswith('noise_'))} useful "
                 f"+ {sum(1 for t in TIPS_POOL if t['key'].startswith('noise_'))} distractor)\n")
    lines.append(f"- off-anchor pass^1 (no tips): **{report.off:.3f}**\n")
    lines.append(f"- base pass^1 (all tips):     **{report.base:.3f}**\n\n")
    lines.append("## Per-condition metrics\n")
    lines.append("| condition | resolution | keypoint_cov | intervention | avg_tips |\n")
    lines.append("|---|---:|---:|---:|---:|\n")
    for label, _ in conditions:
        m = metrics[label]
        lines.append(
            f"| {label} | {m['resolution_rate']:.3f} | {m['keypoint_coverage']:.3f} "
            f"| {m['human_intervention_rate']:.3f} | {m['avg_tips_injected']:.2f} |\n"
        )
    lines.append("\n## Counterfactual Delta_i (sorted)\n")
    for tip, d in report.ranked():
        lines.append(f"- `{TIP_KEY_BY_TEXT[tip]}`: Delta = {d:+.4f}\n")
    if not plotted:
        lines.append("\n_(matplotlib not installed: curve.png skipped)_\n")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.writelines(lines)

    # 5) Echo the table to stdout for CI grepping.
    print("APR-CS ablation results:")
    print(f"{'condition':<18} {'resolution':>10} {'keypoint':>10} {'intervene':>10} {'avg_tips':>9}")
    for label, _ in conditions:
        m = metrics[label]
        print(f"{label:<18} {m['resolution_rate']:>10.3f} "
              f"{m['keypoint_coverage']:>10.3f} {m['human_intervention_rate']:>10.3f} "
              f"{m['avg_tips_injected']:>9.2f}")
    print(f"\nArtefacts -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
