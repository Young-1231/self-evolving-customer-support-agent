"""轻量级 tracing(对齐 OpenTelemetry GenAI 语义约定，零第三方依赖)。

一次"对话回合(turn)"= 一条 trace；trace 内含若干 span(retrieval/generation/
critic/guardrail 等阶段)。借鉴 Langfuse / Arize Phoenix / OpenLLMetry 的做法，把
trace 落成 JSONL(每行一条 turn 记录)，方便离线聚合与导入任意看板。

字段命名尽量贴近 OTel GenAI 语义约定(spec: Semantic Conventions for GenAI)：
  - gen_ai.* 在这里映射为 model / usage / cost；
  - span 的 name + duration 对应阶段耗时；
  - 检索命中记 source/ref/score(对应 RAG 的 retrieval spans)。

设计要点：
  - 纯 stdlib(time / json / uuid / dataclasses / contextlib)；
  - Tracer 既能 `with tracer.span("generation"): ...` 计时，也能直接
    `tracer.log_turn(record)` 把一条完整 turn 写盘；
  - 写盘用追加(append)+ 每行一个 JSON 对象，崩溃也不会损坏历史记录。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional

# 已知阶段名(仅作约定，log_turn 不强制校验，便于扩展)
PHASES = ("retrieval", "generation", "critic", "guardrail")


@dataclass
class RetrievalHit:
    """单条检索命中来源(对应 Passage 的 source/ref/score)。"""

    source: str            # "kb" | "episodic" | "playbook"
    ref: str = ""          # doc_id / case_id / playbook_id
    score: float = 0.0     # 归一化检索置信度 [0,1]


@dataclass
class Span:
    """一个阶段 span：名字 + 起止时间 + 耗时(ms) + 附加属性。"""

    name: str
    start_ts: float = 0.0
    end_ts: float = 0.0
    latency_ms: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    """一次对话回合的完整 trace(写盘时序列化为一行 JSON)。

    参考 OTel GenAI 语义约定整理的运营关键字段：
      - 标识：trace_id / turn / ts
      - 时延：latency_ms(整体) + phase_ms(各阶段)
      - 检索：hits(source+ref+score 列表) + n_hits
      - 决策：confidence / escalate
      - 安全：guardrail_verdict(allow|redact|block|...)
      - 成本：model / in_tokens / out_tokens / cost_usd
    """

    trace_id: str = ""
    turn: int = 0
    ts: float = 0.0                       # turn 开始的 epoch 秒
    latency_ms: float = 0.0               # 端到端耗时
    phase_ms: Dict[str, float] = field(default_factory=dict)  # 各阶段耗时
    hits: List[Dict[str, Any]] = field(default_factory=list)  # 检索命中来源
    n_hits: int = 0
    confidence: float = -1.0
    escalate: bool = False
    guardrail_verdict: str = "allow"      # allow | redact | block | flag ...
    guardrail_blocked: bool = False
    model: str = ""
    in_tokens: int = 0
    out_tokens: int = 0
    cost_usd: float = 0.0
    query: str = ""                       # 截断/脱敏后的查询(便于排障，可选)
    error: Optional[str] = None           # 异常 turn 标记

    def to_record(self) -> Dict[str, Any]:
        return asdict(self)


class Tracer:
    """把每个 turn 的 trace 追加写入 experiments/traces/*.jsonl。

    用法::

        tracer = Tracer(workdir="experiments")
        tracer.start_turn(turn=0, query="...", model="deepseek-chat")
        with tracer.span("retrieval") as sp:
            hits = retrieve(...)
            sp.attributes["k"] = len(hits)
        tracer.set_hits(hits_as_passages)
        with tracer.span("generation"):
            answer = backend.generate_answer(...)
        ...
        tracer.end_turn(confidence=conf, escalate=esc, guardrail_verdict="allow")

    每个 turn 结束(end_turn)即落盘一行。也可绕过上述流程直接 ``log_turn(record)``。
    """

    def __init__(
        self,
        workdir: str = "experiments",
        filename: Optional[str] = None,
        clock: Any = time.perf_counter,
    ):
        self.dir = os.path.join(workdir, "traces")
        os.makedirs(self.dir, exist_ok=True)
        if filename is None:
            filename = "trace-%s.jsonl" % time.strftime("%Y%m%d")
        self.path = os.path.join(self.dir, filename)
        self._clock = clock                # 可注入假时钟，便于测试计时
        self._cur: Optional[Trace] = None  # 当前 turn 的 trace
        self._turn_t0: float = 0.0

    # --- turn 生命周期 -----------------------------------------------------
    def start_turn(self, turn: int, query: str = "", model: str = "") -> Trace:
        self._cur = Trace(
            trace_id=uuid.uuid4().hex,
            turn=turn,
            ts=time.time(),
            model=model,
            query=query[:500],
        )
        self._turn_t0 = self._clock()
        return self._cur

    @contextmanager
    def span(self, name: str) -> Iterator[Span]:
        """计时一个阶段。退出时把耗时累加进当前 turn 的 phase_ms。

        即便没有 start_turn(独立计时场景)也可使用，仅做计时不落盘。
        """
        sp = Span(name=name, start_ts=self._clock())
        try:
            yield sp
        finally:
            sp.end_ts = self._clock()
            sp.latency_ms = round((sp.end_ts - sp.start_ts) * 1000.0, 3)
            if self._cur is not None:
                self._cur.phase_ms[name] = round(
                    self._cur.phase_ms.get(name, 0.0) + sp.latency_ms, 3
                )

    # --- 往当前 turn 写入业务字段 -----------------------------------------
    def set_hits(self, hits: Any) -> None:
        """记录检索命中来源。

        hits 可以是 Passage 列表(取 source/ref/score)或 RetrievalHit/dict 列表。
        """
        if self._cur is None:
            return
        recs: List[Dict[str, Any]] = []
        for h in hits or []:
            source = getattr(h, "source", None)
            if source is None and isinstance(h, dict):
                recs.append(
                    {
                        "source": h.get("source", ""),
                        "ref": h.get("ref", ""),
                        "score": float(h.get("score", 0.0)),
                    }
                )
                continue
            recs.append(
                {
                    "source": source or "",
                    "ref": getattr(h, "ref", "") or "",
                    "score": float(getattr(h, "score", 0.0) or 0.0),
                }
            )
        self._cur.hits = recs
        self._cur.n_hits = len(recs)

    def set_usage(self, in_tokens: int = 0, out_tokens: int = 0, cost_usd: float = 0.0) -> None:
        if self._cur is None:
            return
        self._cur.in_tokens = int(in_tokens)
        self._cur.out_tokens = int(out_tokens)
        self._cur.cost_usd = float(cost_usd)

    def end_turn(
        self,
        confidence: float = -1.0,
        escalate: bool = False,
        guardrail_verdict: str = "allow",
        guardrail_blocked: bool = False,
        error: Optional[str] = None,
    ) -> Trace:
        """收尾当前 turn：算端到端耗时、填决策/安全字段并落盘。"""
        assert self._cur is not None, "end_turn() 必须在 start_turn() 之后调用"
        cur = self._cur
        cur.latency_ms = round((self._clock() - self._turn_t0) * 1000.0, 3)
        cur.confidence = float(confidence)
        cur.escalate = bool(escalate)
        cur.guardrail_verdict = guardrail_verdict
        cur.guardrail_blocked = bool(guardrail_blocked)
        cur.error = error
        self.log_turn(cur.to_record())
        self._cur = None
        return cur

    # --- 直接落盘一条 turn 记录 -------------------------------------------
    def log_turn(self, record: Dict[str, Any]) -> None:
        """把一条 turn 记录(dict 或 Trace)以 JSONL 追加写盘。"""
        if isinstance(record, Trace):
            record = record.to_record()
        line = json.dumps(record, ensure_ascii=False, default=str)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_traces(path: str) -> List[Dict[str, Any]]:
    """读取一个 JSONL trace 文件为记录列表(跳过空行/坏行)。"""
    records: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
