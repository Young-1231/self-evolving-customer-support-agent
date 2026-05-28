#!/usr/bin/env python3
"""Push 历史 seagent trace JSONL 到本地 Arize Phoenix（Item 1, 2026-05-29）。

Phoenix 是 docker-free 本地 LLM observability 平台 —— `pip install
arize-phoenix` 即可起 web UI，不需要 self-host Langfuse 的完整 stack。

为什么不直接复用 ``scripts/replay_to_langfuse.py --backend otel``：

* 复用脚本默认走自实现 OTLP/HTTP JSON encoder（v2.6 ``OtelExporter``），但
  Phoenix 11.x 的 ``/v1/traces`` 只收 OTLP/protobuf，所以 JSON path 会得到
  HTTP 415。
* SDK path 虽然能用 OTLP protobuf，但 SDK 跑得久会触发 BatchSpanProcessor
  的内部线程导出，replay 整批 trace 时无法精确控制 project_name / project
  分组 / progress。
*
此脚本：
  * 用 ``opentelemetry-sdk`` + ``OTLPSpanExporter (HTTP/protobuf)`` 直推；
  * 每个 trace 文件挂到独立 Phoenix project（``service.name`` 或 OpenInference
    ``openinference.project.name`` resource attr）；
  * 复用 v2.6 ``trace_to_otel_payload`` 的字段映射（gen_ai.* / seagent.*）
    保证语义一致，但绕过 HTTP JSON 编码走真正的 OTel SDK；
  * 完全 read-only：不修改任何 src/seagent 文件。

Usage::

    python scripts/push_to_phoenix.py \\
        --endpoint http://127.0.0.1:6007 \\
        --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \\
                 --project exp_d \\
        --traces experiments/stress_test_expanded/exp_e_v4/load_records.jsonl \\
                 --project exp_e_v4 --as-load-records
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# 允许从源码树直接跑
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from seagent.obs.trace import read_traces  # noqa: E402


# ---------------------------------------------------------------------------
# load_records -> seagent Trace dict 适配
# ---------------------------------------------------------------------------

def _load_records_to_trace(rec: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """把 load_records.jsonl 一行（stress 输出格式）映射成 Trace dict。

    load_records 没有 phase_ms 拆分，所以这里用 latency_ms 全挂在 ``generation``
    阶段，并补一个 ``ts`` 字段（基于当前时间 + idx 偏移防止重叠）。
    """
    trace_id = rec.get("trace_id") or uuid.uuid4().hex
    if not isinstance(trace_id, str) or len(trace_id) != 32:
        trace_id = uuid.uuid4().hex
    lat = float(rec.get("latency_ms") or 0.0)
    # 假 ts：每条 trace 间隔 10ms，避免完全重叠（仅用于 UI 时序）
    ts = time.time() - (60 * 60) + idx * 0.01
    return {
        "trace_id": trace_id,
        "turn": idx + 1,
        "ts": ts,
        "latency_ms": lat,
        "phase_ms": {"generation": lat},  # 单阶段近似
        "hits": [],
        "n_hits": 0,
        "confidence": float(rec.get("confidence", 0.0) or 0.0),
        "escalate": bool(rec.get("escalate", False)),
        "guardrail_verdict": rec.get("guardrail_action", "allow") or "allow",
        "guardrail_blocked": bool(rec.get("guardrail_blocked", False)),
        "model": "deepseek-chat",
        "in_tokens": int(rec.get("in_tokens", 0) or 0),
        "out_tokens": int(rec.get("out_tokens", 0) or 0),
        "cost_usd": float(rec.get("cost_usd", 0.0) or 0.0),
        "query": (rec.get("text") or "")[:500],
        "answer": (rec.get("answer") or "")[:1000],
        "error": rec.get("error"),
        "category": rec.get("category"),
        "ticket_id": rec.get("ticket_id"),
    }


# ---------------------------------------------------------------------------
# OTel SDK -> Phoenix push
# ---------------------------------------------------------------------------

def _make_provider(endpoint: str, project_name: str):
    """每个 project 起独立 TracerProvider，service.name 作为 Phoenix project key。"""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create(
        {
            "service.name": project_name,
            "service.version": "v2.6",
            # OpenInference 约定，Phoenix 会用这个分 project
            "openinference.project.name": project_name,
            "telemetry.sdk.name": "seagent-push-to-phoenix",
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=endpoint.rstrip("/") + "/v1/traces",
        timeout=30,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter, max_export_batch_size=64))
    return provider, exporter


def _vendor_from_model(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    return "unknown"


def _emit_trace(tracer, rec: Dict[str, Any]) -> None:
    """根 span = agent_turn；phase_ms 每项 = 子 span。

    属性贴近 OTel GenAI 语义约定 + seagent 自有命名空间。
    """
    from opentelemetry.trace import Status, StatusCode

    model = rec.get("model", "deepseek-chat")
    with tracer.start_as_current_span("agent_turn") as root:
        root.set_attribute("gen_ai.system", _vendor_from_model(model))
        root.set_attribute("gen_ai.request.model", model)
        root.set_attribute("gen_ai.usage.input_tokens", int(rec.get("in_tokens", 0) or 0))
        root.set_attribute("gen_ai.usage.output_tokens", int(rec.get("out_tokens", 0) or 0))
        root.set_attribute("gen_ai.usage.total_cost", float(rec.get("cost_usd", 0.0) or 0.0))
        root.set_attribute("seagent.turn", int(rec.get("turn", 0) or 0))
        root.set_attribute("seagent.confidence", float(rec.get("confidence", -1.0)))
        root.set_attribute("seagent.escalate", bool(rec.get("escalate", False)))
        root.set_attribute("seagent.guardrail.verdict", rec.get("guardrail_verdict", "allow") or "allow")
        root.set_attribute("seagent.guardrail.blocked", bool(rec.get("guardrail_blocked", False)))
        root.set_attribute("seagent.n_hits", int(rec.get("n_hits", 0) or 0))
        root.set_attribute("seagent.latency_ms", float(rec.get("latency_ms", 0.0) or 0.0))
        if rec.get("category"):
            root.set_attribute("seagent.category", str(rec["category"]))
        if rec.get("ticket_id"):
            root.set_attribute("seagent.ticket_id", str(rec["ticket_id"]))
        q = rec.get("query") or ""
        if q:
            root.set_attribute("seagent.query", q[:500])
            # OpenInference 友好字段，Phoenix UI 直接显示 input
            root.set_attribute("input.value", q[:1000])
            root.set_attribute("input.mime_type", "text/plain")
        a = rec.get("answer") or ""
        if a:
            root.set_attribute("output.value", a[:1000])
            root.set_attribute("output.mime_type", "text/plain")
        # span.kind：让 Phoenix 把它当 LLM span 渲染
        root.set_attribute("openinference.span.kind", "LLM")

        if rec.get("error"):
            root.set_attribute("seagent.error", str(rec["error"]))
            root.set_status(Status(StatusCode.ERROR, str(rec["error"])))
        else:
            root.set_status(Status(StatusCode.OK))

        phase_ms: Dict[str, float] = rec.get("phase_ms") or {}
        for phase, dur_ms in phase_ms.items():
            with tracer.start_as_current_span(phase) as child:
                child.set_attribute("seagent.phase", phase)
                child.set_attribute("seagent.phase.latency_ms", float(dur_ms or 0.0))
                if phase == "retrieval":
                    hits = rec.get("hits") or []
                    child.set_attribute("seagent.retrieval.n_hits", len(hits))
                    if hits:
                        child.set_attribute(
                            "seagent.retrieval.hits", json.dumps(hits)[:2000]
                        )
                    child.set_attribute("openinference.span.kind", "RETRIEVER")
                elif phase == "generation":
                    child.set_attribute("openinference.span.kind", "LLM")
                elif phase == "guardrail":
                    child.set_attribute("openinference.span.kind", "GUARDRAIL")
                else:
                    child.set_attribute("openinference.span.kind", "CHAIN")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _read_any(path: str, as_load_records: bool) -> List[Dict[str, Any]]:
    """读 trace 文件，自动适配 seagent Trace JSONL vs load_records JSONL。"""
    if as_load_records:
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                records.append(_load_records_to_trace(rec, idx))
        return records
    return read_traces(path)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Push seagent trace JSONL to local Phoenix.")
    p.add_argument(
        "--endpoint",
        default=os.environ.get("PHOENIX_OTLP_ENDPOINT", "http://127.0.0.1:6007"),
        help="Phoenix HTTP base URL（不含 /v1/traces）",
    )
    p.add_argument(
        "--spec", action="append", required=True,
        help="格式 'PROJECT:PATH[:load_records]'，可重复。"
             "load_records 为可选 flag，启用 load_records 适配。",
    )
    p.add_argument("--limit", type=int, default=0, help="每个文件只推前 N 条（0=全部）")
    p.add_argument("--dry-run", action="store_true", help="不推 Phoenix，只打印映射统计")
    return p


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)

    total_ok, total_skip = 0, 0
    per_project: List[Tuple[str, int]] = []

    for spec in args.spec:
        parts = spec.split(":", 2)
        if len(parts) < 2:
            print(f"[push] bad spec (need PROJECT:PATH): {spec}", file=sys.stderr)
            continue
        project_name = parts[0]
        path = parts[1]
        as_load = (len(parts) >= 3 and parts[2] == "load_records")

        if not os.path.exists(path):
            print(f"[push] SKIP missing: {path}", file=sys.stderr)
            continue

        records = _read_any(path, as_load)
        if args.limit > 0:
            records = records[: args.limit]
        if not records:
            print(f"[push] {project_name}: 0 records in {path}, skipping")
            per_project.append((project_name, 0))
            continue

        print(f"[push] project={project_name} path={path} records={len(records)}"
              f" as_load_records={as_load} endpoint={args.endpoint}")

        if args.dry_run:
            per_project.append((project_name, len(records)))
            total_ok += len(records)
            continue

        provider, exporter = _make_provider(args.endpoint, project_name)
        tracer = provider.get_tracer("seagent.replay", "v2.6")
        ok = 0
        for r in records:
            try:
                _emit_trace(tracer, r)
                ok += 1
            except Exception as e:
                total_skip += 1
                print(f"[push] WARN emit failed: {e}", file=sys.stderr)
        # flush + shutdown 每个 project 的 provider，确保导出完成
        provider.shutdown()
        per_project.append((project_name, ok))
        total_ok += ok

    print("[push] --- summary ---")
    for name, n in per_project:
        print(f"  {name}: {n} traces pushed")
    print(f"[push] DONE total_ok={total_ok} total_skip={total_skip}")
    return 0 if total_skip == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
