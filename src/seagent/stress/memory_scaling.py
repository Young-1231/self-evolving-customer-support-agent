"""记忆膨胀压测：填充 EpisodicMemory 到不同规模, 测检索延迟 + 解决率拐点。

为什么需要：
    EpisodicMemory 默认用 BM25, 随 case 数增长 retrieve() 的扫描成本是 O(n);
    再叠加 _reindex() 在每次 add() 时全量重建。我们想知道 — n 长到多少
    才会让单次 retrieval 延迟显著抬升, 同时解决率是否早已收敛(收敛后再增量
    case 等于纯吃延迟无收益), 给"TTL / LRU / 案例去重"工程决策提供数据。

设计：
    - **合成填充**：不调 LLM，直接造 Case(query/resolution 都是模板化文本，
      但 BM25 token 多样以模拟真实分布)；
    - **同一 eval 子集**：所有规模点用同一组 eval ticket 跑，差异只来自记忆
      规模，便于做对照；
    - **真实计时**：只测 ``agent._retrieve`` 的耗时(避免把 LLM generation
      混进来), 同时跑完整 handle() 算解决率;
    - **零强制依赖**：默认接受 MockBackend，单元测试可跑。
"""
from __future__ import annotations

import os
import random
import string
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..llm.base import LLMBackend, Passage


@dataclass
class MemoryScalePoint:
    """单个规模点的测量结果。"""

    size: int
    avg_retrieval_ms: float = 0.0
    p95_retrieval_ms: float = 0.0
    avg_handle_ms: float = 0.0
    resolution_rate: float = 0.0
    escalation_rate: float = 0.0
    n_eval: int = 0
    error_rate: float = 0.0

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


# ---- 合成 Case 工厂 -------------------------------------------------------
_TOPICS = ["billing", "export", "webhook", "sso", "notify", "account",
           "team", "invoice", "trial", "api"]
_ACTIONS = ["升级", "降级", "退款", "导出", "合并", "重置", "邀请", "撤销",
            "重启", "排查"]


def _synth_case(i: int, rng: random.Random):
    """造一条 BM25 友好的 dummy Case(避免触发 import cycle，按需 import)。"""
    from ..memory.episodic import Case
    topic = rng.choice(_TOPICS)
    action = rng.choice(_ACTIONS)
    noise = "".join(rng.choices(string.ascii_lowercase, k=8))
    query = f"NimbusFlow {topic} 套餐 {action} 出问题了 {noise} 详情"
    resolution = (f"针对 {topic} 类问题, 建议先查日志 / 验证账单 / 确认权限, "
                  f"再走 {action} 流程。如失败请提供 {noise} 对应 trace_id。")
    return Case(
        case_id=f"synth_{i:07d}",
        query=query,
        resolution=resolution,
        should_escalate=(i % 7 == 0),
        topic=topic,
        source_query_id=f"synth_q_{i}",
        learned_round=0,
    )


# ---- 单规模点测量 ---------------------------------------------------------
def _measure_point(
    *,
    size: int,
    eval_tickets: Sequence[Any],
    agent_factory: Callable[[Any], Any],
    backend: LLMBackend,
    score_norm_k: float = 6.0,
    seed: int = 0,
) -> MemoryScalePoint:
    """填一个 size 规模的 EpisodicMemory, 跑 eval, 返回测量。

    eval_tickets: 每项需有 ``.text`` 属性(TicketSpec 就符合); 没有 ``.text``
    就当作 str 处理(便于通用)。
    """
    from ..memory.episodic import EpisodicMemory

    epi = EpisodicMemory(path=None, score_norm_k=score_norm_k)
    rng = random.Random(seed * 9973 + size)
    # 批量构造再 setattr，避免一次次 reindex 的 O(n^2)
    cases = [_synth_case(i, rng) for i in range(size)]
    epi.cases = list(cases)
    epi._reindex()  # 一次性 reindex
    if epi.path:
        epi._persist()

    agent = agent_factory(epi)

    retr_lat: List[float] = []
    handle_lat: List[float] = []
    n_resolved = 0
    n_escalate = 0
    n_err = 0

    for tk in eval_tickets:
        text = getattr(tk, "text", None) or (tk if isinstance(tk, str) else str(tk))
        # 1) 单独计 retrieval 时延(直接调 _retrieve, 不走 LLM)
        t0 = time.perf_counter()
        try:
            agent._retrieve(text)
            retr_lat.append((time.perf_counter() - t0) * 1000.0)
        except Exception:
            retr_lat.append((time.perf_counter() - t0) * 1000.0)
            n_err += 1
            continue
        # 2) 端到端 handle 时延 + 是否解决(escalate=False 视为解决)
        t1 = time.perf_counter()
        try:
            r = agent.handle(text)
            handle_lat.append((time.perf_counter() - t1) * 1000.0)
            if getattr(r, "escalate", False):
                n_escalate += 1
            else:
                n_resolved += 1
        except Exception:
            handle_lat.append((time.perf_counter() - t1) * 1000.0)
            n_err += 1

    def _avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else 0.0

    def _p95(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        import math
        k = max(1, min(len(s), int(math.ceil(0.95 * len(s)))))
        return round(s[k - 1], 3)

    n = len(eval_tickets) or 1
    return MemoryScalePoint(
        size=size,
        avg_retrieval_ms=_avg(retr_lat),
        p95_retrieval_ms=_p95(retr_lat),
        avg_handle_ms=_avg(handle_lat),
        resolution_rate=round(n_resolved / n, 4),
        escalation_rate=round(n_escalate / n, 4),
        n_eval=n,
        error_rate=round(n_err / n, 4),
    )


# ---- 顶层 API -------------------------------------------------------------
def scale_memory(
    *,
    sizes: Sequence[int] = (10, 100, 1000, 5000),
    eval_tickets: Sequence[Any],
    agent_factory: Callable[[Any], Any],
    backend: Optional[LLMBackend] = None,
    score_norm_k: float = 6.0,
    seed: int = 0,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[MemoryScalePoint]:
    """对每个规模点跑一遍测量, 返回有序结果。

    agent_factory(epi) -> agent: 调用方负责把传入的 EpisodicMemory 装进
    一个 SupportAgent(或兼容对象)。允许传入 MockBackend(测试/CI)或真实
    DeepSeekBackend(实跑)。
    """
    if backend is None:
        # 仅作类型 hint；真实调用方应自己传 backend, 这里不强求
        backend = LLMBackend()
    out: List[MemoryScalePoint] = []
    total = len(sizes)
    for i, sz in enumerate(sizes):
        pt = _measure_point(
            size=sz, eval_tickets=eval_tickets, agent_factory=agent_factory,
            backend=backend, score_norm_k=score_norm_k, seed=seed,
        )
        out.append(pt)
        if progress is not None:
            try:
                progress(i + 1, total)
            except Exception:
                pass
    return out


def find_knee(points: Sequence[MemoryScalePoint]) -> Optional[int]:
    """简单拐点检测：返回 avg_retrieval_ms 相对最小值首次 > 3x 的 size。

    不是科学的 kneedle 算法 — 只是给 README 报告里挑一个"该考虑 TTL 的"规模点。
    """
    if len(points) < 2:
        return None
    base = min(p.avg_retrieval_ms for p in points if p.avg_retrieval_ms > 0) or 1e-6
    for p in points:
        if p.avg_retrieval_ms > 3.0 * base:
            return p.size
    return None
