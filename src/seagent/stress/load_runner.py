"""并发压测 harness：把一批 TicketSpec 以 max_concurrency 并发跑过 agent。

特性：
    - **每条 ticket 独立 try/except**：一条 500 错误不会拖垮整个 run，
      失败计入 ``error_rate`` 而不是异常退出。
    - **真实计时**：从 agent_factory() 拿到 agent，记 wallclock 端到端
      latency；agent 内部 phase_ms / cost 通过 tracer 落 trace。
    - **线程安全**：agent_factory 每个 worker 调一次，避免多线程共享
      mutable 状态(BM25 index / EpisodicMemory 增减、tracer 当前 turn 等)。
    - **聚合函数 summarize_load**：返回 QPS / p50 / p95 / p99 / 错误率 /
      按 category 拆解的 escalation/error 分布。
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .generator import TicketSpec


@dataclass
class LoadRecord:
    """一条 ticket 跑完的结果(便于落 jsonl / 聚合)。"""

    ticket_id: str
    category: str
    text: str
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0
    escalate: bool = False
    guardrail_action: str = "allow"
    guardrail_blocked: bool = False
    confidence: float = -1.0
    answer: str = ""
    trace_id: Optional[str] = None
    error: Optional[str] = None

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


# ---- 单条执行 -------------------------------------------------------------
def _run_one(ticket: TicketSpec, agent: Any, tracer: Any = None) -> LoadRecord:
    rec = LoadRecord(ticket_id=ticket.ticket_id, category=ticket.category, text=ticket.text)
    t0 = time.perf_counter()
    try:
        result = agent.handle(ticket.text)
        rec.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        rec.answer = (getattr(result, "answer", "") or "")[:500]
        rec.escalate = bool(getattr(result, "escalate", False))
        rec.confidence = float(getattr(result, "confidence", -1.0))
        rec.trace_id = getattr(result, "trace_id", None)
        g = getattr(result, "guardrail", None)
        if g is not None:
            rec.guardrail_action = str(getattr(g, "action", "allow")).lower()
            rec.guardrail_blocked = bool(getattr(g, "blocked", False))
        # 如果 tracer 有最后一条 turn 的成本，把它回填进 record(best effort)
        if tracer is not None:
            try:
                # tracer 默认不暴露最近一条 turn，跳过 - usage 已在 trace.jsonl
                pass
            except Exception:
                pass
    except Exception as e:
        rec.latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        rec.error = f"{type(e).__name__}: {e}"[:300]
    return rec


# ---- 顶层并发 runner ------------------------------------------------------
def run_load(
    tickets: Sequence[TicketSpec],
    agent_factory: Callable[[], Any],
    *,
    max_concurrency: int = 20,
    tracer: Any = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[LoadRecord]:
    """把 tickets 并发跑过 agent。

    agent_factory: 无参可调用, 每个 worker 调一次拿到独立 agent 实例(避免
    跨线程共享 mutable 状态)。即便底层 backend 是 thread-safe 的, 也最好
    每线程一份 agent 实例(尤其 tracer 自带 _cur turn 状态)。

    Returns: 与 tickets 同序的 LoadRecord 列表。
    """
    n = len(tickets)
    results: List[Optional[LoadRecord]] = [None] * n
    tls = threading.local()
    done_n = 0
    done_lock = threading.Lock()

    def _agent_for_worker() -> Any:
        ag = getattr(tls, "agent", None)
        if ag is None:
            ag = agent_factory()
            tls.agent = ag
        return ag

    def _job(i: int, ticket: TicketSpec) -> None:
        nonlocal done_n
        try:
            ag = _agent_for_worker()
        except Exception as e:
            results[i] = LoadRecord(
                ticket_id=ticket.ticket_id, category=ticket.category, text=ticket.text,
                error=f"agent_factory_failed: {type(e).__name__}: {e}",
            )
        else:
            results[i] = _run_one(ticket, ag, tracer=tracer)
        with done_lock:
            done_n += 1
            if progress is not None:
                try:
                    progress(done_n, n)
                except Exception:
                    pass

    if n == 0:
        return []

    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
        futs = [pool.submit(_job, i, t) for i, t in enumerate(tickets)]
        for _ in as_completed(futs):
            pass

    return [r for r in results if r is not None]


# ---- 聚合 -----------------------------------------------------------------
def _percentile(xs: Sequence[float], p: float) -> float:
    """nearest-rank percentile, 与 seagent.obs.metrics 风格一致。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    if p <= 0:
        return float(s[0])
    if p >= 100:
        return float(s[-1])
    # ceil(p/100 * n)，再 clamp 到 [1,n]
    import math
    k = max(1, min(len(s), int(math.ceil((p / 100.0) * len(s)))))
    return float(s[k - 1])


def summarize_load(records: Sequence[LoadRecord]) -> Dict[str, Any]:
    """聚合：QPS / latency 分布 / cost / 错误率 / 按 category 拆解。

    QPS 用 (n_success / total_wallclock_seconds) 近似 — wallclock 用所有
    record 的 latency 之"理论上限"(max(end) - min(start))在并发场景下无法直接
    重建, 因此退化为 ``n_total / (sum(latency_ms)/1000 / concurrency_est)``，
    并把 sum_latency_s 直接暴露给调用方让其自由计算。
    """
    n = len(records)
    if n == 0:
        return {"n": 0}
    successes = [r for r in records if r.error is None]
    n_ok = len(successes)
    n_err = n - n_ok

    lat = [r.latency_ms for r in successes]
    cost = [r.cost_usd for r in successes]
    sum_lat_s = sum(lat) / 1000.0

    # 按 category 拆 escalation / error / avg_latency
    by_cat: Dict[str, Dict[str, Any]] = {}
    for r in records:
        b = by_cat.setdefault(r.category, {"n": 0, "n_err": 0, "n_escalate": 0,
                                           "n_block": 0, "lat_sum": 0.0})
        b["n"] += 1
        if r.error is not None:
            b["n_err"] += 1
        else:
            b["lat_sum"] += r.latency_ms
            if r.escalate:
                b["n_escalate"] += 1
            if r.guardrail_blocked:
                b["n_block"] += 1
    for c, b in by_cat.items():
        n_cat = b["n"]
        ok_cat = n_cat - b["n_err"]
        b["error_rate"] = round(b["n_err"] / n_cat, 4)
        b["escalation_rate"] = round((b["n_escalate"] / ok_cat) if ok_cat else 0.0, 4)
        b["block_rate"] = round((b["n_block"] / ok_cat) if ok_cat else 0.0, 4)
        b["avg_latency_ms"] = round((b["lat_sum"] / ok_cat) if ok_cat else 0.0, 2)
        # resolution = 成功跑完 & 没有 escalate & 没有 block
        b["resolution_rate"] = round(
            (max(0, ok_cat - b["n_escalate"] - b["n_block"]) / n_cat), 4
        )

    return {
        "n": n,
        "n_success": n_ok,
        "n_error": n_err,
        "error_rate": round(n_err / n, 4),
        "avg_latency_ms": round(sum(lat) / max(1, len(lat)), 2),
        "p50_latency_ms": round(_percentile(lat, 50), 2),
        "p95_latency_ms": round(_percentile(lat, 95), 2),
        "p99_latency_ms": round(_percentile(lat, 99), 2),
        "min_latency_ms": round(min(lat), 2) if lat else 0.0,
        "max_latency_ms": round(max(lat), 2) if lat else 0.0,
        "sum_latency_s": round(sum_lat_s, 3),
        "avg_cost_usd": round(sum(cost) / max(1, len(cost)), 6) if cost else 0.0,
        "total_cost_usd": round(sum(cost), 6) if cost else 0.0,
        "escalation_rate": round(sum(1 for r in successes if r.escalate) / max(1, n_ok), 4),
        "block_rate": round(sum(1 for r in successes if r.guardrail_blocked) / max(1, n_ok), 4),
        "by_category": by_cat,
    }


def estimate_qps(records: Sequence[LoadRecord], wallclock_s: float) -> float:
    """给 (records, 实际 wallclock 秒数) 算 QPS — wallclock 必须由调用方计时。"""
    if wallclock_s <= 0 or not records:
        return 0.0
    n_ok = sum(1 for r in records if r.error is None)
    return round(n_ok / wallclock_s, 3)
