"""Langfuse trace exporter (v2.6 R5)。

把 seagent 的 trace JSONL 推到 Langfuse self-host 或 cloud。

字段映射（seagent.obs.Trace -> Langfuse trace + observation）::

    seagent.Trace          Langfuse                  备注
    ─────────────────────  ───────────────────────   ─────────────────────────────
    trace_id               trace.id (hex)            直接复用 UUID hex
    turn                   trace.metadata.turn       便于按 turn 过滤
    ts                     trace.timestamp           epoch sec -> ISO8601
    latency_ms             trace.metadata.latency_ms 端到端耗时
    query                  trace.input               已截断/脱敏的查询
    model                  generation.model          gen_ai.* 语义
    in_tokens/out_tokens   generation.usage.{input,  Langfuse 标准 usage 字段
                            output}
    cost_usd               generation.usage.total    Langfuse 也接受 totalCost
    phase_ms[retrieval]    span(name=retrieval)      每个 phase 一条 observation
    phase_ms[generation]   generation(name=generation)
    phase_ms[critic]       span(name=critic)
    phase_ms[guardrail]    span(name=guardrail)
    hits                   span(retrieval).output    [{source,ref,score}] 列表
    n_hits                 span(retrieval).metadata.n_hits
    confidence             trace.metadata.confidence
    escalate               trace.metadata.escalate   决策可观测
    guardrail_verdict      trace.metadata.guardrail_verdict
    guardrail_blocked      trace.metadata.guardrail_blocked
    error                  trace.metadata.error      非空时 trace 标记为失败

实现细节：
  - 优先 ``langfuse`` SDK（>=2.30）；缺失则 HTTP POST 到
    ``{host}/api/public/ingestion``（Langfuse 文档化的 batch 接口，basic-auth =
    public_key:secret_key）。
  - 时间戳统一 UTC ISO8601（Langfuse 要求 RFC3339）。
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..trace import read_traces


# ---------------------------------------------------------------------------
# 字段映射（纯函数，便于测试）
# ---------------------------------------------------------------------------

def _to_iso8601(ts: float) -> str:
    """epoch 秒 -> RFC3339 / ISO8601（UTC, 毫秒精度）。"""
    try:
        ts = float(ts)
    except Exception:
        ts = time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Langfuse 接受带毫秒的 ISO8601；保留 Z 后缀更稳
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def trace_to_langfuse_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """seagent Trace dict -> Langfuse ingestion batch payload。

    返回的 dict 形如::

        {
            "batch": [
                {"type": "trace-create", "id": "...", "timestamp": "...", "body": {...}},
                {"type": "span-create",  "id": "...", "timestamp": "...", "body": {...}},
                {"type": "generation-create", ...},
                ...
            ],
            "metadata": {"sdk": "seagent-exporter", "version": "v2.6"}
        }

    一条 turn 对应 1 个 trace + N 个 observation（每个 phase 一条）。
    """
    trace_id = record.get("trace_id") or uuid.uuid4().hex
    ts_iso = _to_iso8601(record.get("ts") or time.time())

    # ---- trace 主体 ----
    trace_body: Dict[str, Any] = {
        "id": trace_id,
        "timestamp": ts_iso,
        "name": "agent_turn",
        "input": record.get("query", ""),
        "metadata": {
            "turn": int(record.get("turn", 0) or 0),
            "latency_ms": float(record.get("latency_ms", 0.0) or 0.0),
            "confidence": float(record.get("confidence", -1.0)),
            "escalate": bool(record.get("escalate", False)),
            "guardrail_verdict": record.get("guardrail_verdict", "allow"),
            "guardrail_blocked": bool(record.get("guardrail_blocked", False)),
            "n_hits": int(record.get("n_hits", 0) or 0),
            "error": record.get("error"),
        },
        "tags": ["seagent", f"turn:{record.get('turn', 0)}"],
    }
    if record.get("error"):
        trace_body["tags"].append("error")

    batch: List[Dict[str, Any]] = [
        {
            "id": uuid.uuid4().hex,
            "type": "trace-create",
            "timestamp": ts_iso,
            "body": trace_body,
        }
    ]

    # ---- 每个 phase 一条 observation ----
    phase_ms: Dict[str, float] = record.get("phase_ms") or {}
    base_ts = record.get("ts") or time.time()
    cursor = float(base_ts)
    for phase, dur_ms in phase_ms.items():
        dur_s = float(dur_ms or 0.0) / 1000.0
        start_iso = _to_iso8601(cursor)
        end_iso = _to_iso8601(cursor + dur_s)
        obs_id = uuid.uuid4().hex
        is_generation = phase == "generation"

        body: Dict[str, Any] = {
            "id": obs_id,
            "traceId": trace_id,
            "name": phase,
            "startTime": start_iso,
            "endTime": end_iso,
            "metadata": {"latency_ms": float(dur_ms or 0.0)},
        }
        if phase == "retrieval":
            body["output"] = record.get("hits", [])
            body["metadata"]["n_hits"] = int(record.get("n_hits", 0) or 0)
        if is_generation:
            body["model"] = record.get("model", "")
            body["input"] = record.get("query", "")
            body["usage"] = {
                "input": int(record.get("in_tokens", 0) or 0),
                "output": int(record.get("out_tokens", 0) or 0),
                "total": int(record.get("in_tokens", 0) or 0)
                + int(record.get("out_tokens", 0) or 0),
                "unit": "TOKENS",
                "totalCost": float(record.get("cost_usd", 0.0) or 0.0),
            }
        if phase == "guardrail":
            body["metadata"]["verdict"] = record.get("guardrail_verdict", "allow")
            body["metadata"]["blocked"] = bool(record.get("guardrail_blocked", False))

        batch.append(
            {
                "id": uuid.uuid4().hex,
                "type": "generation-create" if is_generation else "span-create",
                "timestamp": start_iso,
                "body": body,
            }
        )
        cursor += dur_s

    return {
        "batch": batch,
        "metadata": {"sdk": "seagent-exporter", "version": "v2.6"},
    }


# ---------------------------------------------------------------------------
# Exporter 主体
# ---------------------------------------------------------------------------

class LangfuseExporter:
    """把 seagent trace 推到 Langfuse。

    用法::

        exp = LangfuseExporter(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host="http://localhost:3000",   # self-host
        )
        ok, fail = exp.export_traces_from_jsonl("experiments/.../traces.jsonl")
        print(f"{ok}/{ok+fail} uploaded")

    参数：
        public_key / secret_key: Langfuse project key（self-host UI 创建）
        host:    Langfuse base URL；默认 cloud
        use_sdk: True=优先 langfuse SDK；False=强制 HTTP fallback（测试用）
        http_client: 可注入的 requests-like 对象（带 .post(url, **kw)），便于
                     mock 测试；缺省时用 stdlib urllib
        dry_run: 不真打 HTTP，只渲染 payload；export_trace 返回 payload dict
    """

    INGESTION_PATH = "/api/public/ingestion"

    def __init__(
        self,
        public_key: str = "",
        secret_key: str = "",
        host: str = "https://cloud.langfuse.com",
        use_sdk: bool = True,
        http_client: Any = None,
        dry_run: bool = False,
    ):
        self.public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        self.secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY", "")
        self.host = (host or os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")).rstrip("/")
        self.dry_run = bool(dry_run)
        self._http_client = http_client
        self._sdk = None
        if use_sdk and not dry_run:
            try:
                import langfuse  # type: ignore

                self._sdk = langfuse.Langfuse(
                    public_key=self.public_key,
                    secret_key=self.secret_key,
                    host=self.host,
                )
            except Exception:
                self._sdk = None  # 静默 fallback 到 HTTP

    # ------------------------------------------------------------------
    @property
    def backend(self) -> str:
        """当前实际使用的推送通道：'sdk' / 'http' / 'dry_run'。"""
        if self.dry_run:
            return "dry_run"
        return "sdk" if self._sdk is not None else "http"

    # ------------------------------------------------------------------
    def export_trace(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """导出单条 trace；返回 payload（dry_run 时即返回值，HTTP 时返回响应 dict）。

        失败时抛 RuntimeError；调用方可在批量场景下捕获。
        """
        payload = trace_to_langfuse_payload(record)
        if self.dry_run:
            return payload
        if self._sdk is not None:
            # SDK 路径：每个 batch item 单独 emit
            for item in payload["batch"]:
                t = item["type"]
                body = item["body"]
                try:
                    if t == "trace-create":
                        self._sdk.trace(**_filter_kwargs(body))
                    elif t == "generation-create":
                        self._sdk.generation(**_filter_kwargs(body))
                    elif t == "span-create":
                        self._sdk.span(**_filter_kwargs(body))
                except Exception as e:
                    raise RuntimeError(f"langfuse sdk emit failed: {e}") from e
            return {"status": "ok", "via": "sdk", "items": len(payload["batch"])}
        return self._http_post_batch(payload)

    # ------------------------------------------------------------------
    def export_traces_from_jsonl(
        self, path: str, on_error: str = "skip"
    ) -> Tuple[int, int]:
        """批量导出一个 JSONL；返回 (success, fail)。

        on_error: 'skip' 静默跳过坏行；'raise' 抛出第一条错误。
        """
        records = read_traces(path)
        return self.export_traces(records, on_error=on_error)

    def export_traces(
        self, records: Iterable[Dict[str, Any]], on_error: str = "skip"
    ) -> Tuple[int, int]:
        ok, fail = 0, 0
        for r in records:
            try:
                self.export_trace(r)
                ok += 1
            except Exception:
                fail += 1
                if on_error == "raise":
                    raise
        return ok, fail

    # ------------------------------------------------------------------
    def _http_post_batch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.host + self.INGESTION_PATH
        auth = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode("utf-8")
        ).decode("ascii")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "User-Agent": "seagent-langfuse-exporter/v2.6",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        # 优先用注入的 client（mock / requests 兼容）
        if self._http_client is not None:
            resp = self._http_client.post(url, data=body, headers=headers)
            status = getattr(resp, "status_code", 200)
            if status >= 400:
                raise RuntimeError(f"langfuse http {status}: {getattr(resp, 'text', '')!r}")
            return {"status": "ok", "via": "http", "code": status}

        # stdlib fallback
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                code = resp.getcode()
                if code >= 400:
                    raise RuntimeError(f"langfuse http {code}")
                return {"status": "ok", "via": "http", "code": code}
        except urllib.error.URLError as e:
            raise RuntimeError(f"langfuse http error: {e}") from e


def _filter_kwargs(body: Dict[str, Any]) -> Dict[str, Any]:
    """剔除 SDK 不接受的字段（traceId / startTime 等已由 SDK 自管）。"""
    drop = {"traceId", "startTime", "endTime"}
    out = {k: v for k, v in body.items() if k not in drop and v is not None}
    # Langfuse SDK 的 generation/span 都接受 start_time/end_time 蛇形别名
    if "startTime" in body:
        out["start_time"] = body["startTime"]
    if "endTime" in body:
        out["end_time"] = body["endTime"]
    if "traceId" in body:
        out["trace_id"] = body["traceId"]
    return out
