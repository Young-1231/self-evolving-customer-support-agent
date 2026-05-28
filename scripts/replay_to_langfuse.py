#!/usr/bin/env python3
"""Replay 历史 seagent trace 到 Langfuse（v2.6 R5）。

不消耗 LLM 配额 —— 纯导入历史 JSONL。

典型用法
--------

干跑（不真打 HTTP，只渲染 payload 看一眼）::

    python scripts/replay_to_langfuse.py \
        --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
        --dry-run --limit 1

推到 self-host Langfuse::

    export LANGFUSE_PUBLIC_KEY=pk-xxx
    export LANGFUSE_SECRET_KEY=sk-xxx
    export LANGFUSE_HOST=http://localhost:3000
    python scripts/replay_to_langfuse.py \
        --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
        --tag exp_d --tag v2.6

批量推 exp_d + exp_e::

    python scripts/replay_to_langfuse.py \
        --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
        --traces experiments/stress_test_expanded/exp_e/traces/stress_trace.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 允许从源码树直接跑
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from seagent.obs.exporters import LangfuseExporter, OtelExporter  # noqa: E402
from seagent.obs.trace import read_traces  # noqa: E402


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Replay seagent trace JSONL to Langfuse / OTLP backend."
    )
    p.add_argument(
        "--traces", action="append", required=True,
        help="trace JSONL 文件路径；可重复以批量推多个文件",
    )
    p.add_argument(
        "--backend", choices=["langfuse", "otel"], default="langfuse",
        help="目标后端（默认 langfuse）",
    )
    p.add_argument("--host", default=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    p.add_argument("--public-key", default=os.environ.get("LANGFUSE_PUBLIC_KEY", ""))
    p.add_argument("--secret-key", default=os.environ.get("LANGFUSE_SECRET_KEY", ""))
    p.add_argument(
        "--otel-endpoint",
        default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
    )
    p.add_argument(
        "--tag", action="append", default=[],
        help="给每条 trace 附加 tag（仅 langfuse 后端）",
    )
    p.add_argument("--limit", type=int, default=0, help="只推前 N 条（0=全部）")
    p.add_argument("--dry-run", action="store_true", help="不打网络，仅渲染 payload")
    p.add_argument(
        "--print-sample", action="store_true",
        help="同时打印第一条 payload（用于检查映射）",
    )
    p.add_argument("--on-error", choices=["skip", "raise"], default="skip")
    return p


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)

    # ------ 构造 exporter ------
    if args.backend == "langfuse":
        exporter = LangfuseExporter(
            public_key=args.public_key,
            secret_key=args.secret_key,
            host=args.host,
            dry_run=args.dry_run,
        )
    else:
        exporter = OtelExporter(
            endpoint=args.otel_endpoint,
            dry_run=args.dry_run,
        )

    print(f"[replay] backend={args.backend} channel={exporter.backend} host={args.host if args.backend=='langfuse' else args.otel_endpoint}")
    if not args.dry_run and args.backend == "langfuse" and not (args.public_key and args.secret_key):
        print("[replay] WARN: Langfuse public_key/secret_key 未设置，HTTP 调用将 401 失败", file=sys.stderr)

    # ------ 逐文件 replay ------
    total_ok, total_fail = 0, 0
    sample_printed = False
    for path in args.traces:
        if not os.path.exists(path):
            print(f"[replay] SKIP missing: {path}", file=sys.stderr)
            continue
        records = read_traces(path)
        if args.limit > 0:
            records = records[: args.limit]
        # tag 附加
        if args.tag and args.backend == "langfuse":
            for r in records:
                r.setdefault("_tags", []).extend(args.tag)

        # 打印示例 payload
        if args.print_sample and records and not sample_printed:
            from seagent.obs.exporters import (
                trace_to_langfuse_payload,
                trace_to_otel_payload,
            )
            sample_payload = (
                trace_to_langfuse_payload(records[0])
                if args.backend == "langfuse"
                else trace_to_otel_payload(records[0])
            )
            print("[replay] --- sample payload (first record) ---")
            print(json.dumps(sample_payload, indent=2, ensure_ascii=False)[:4000])
            print("[replay] --- end sample ---")
            sample_printed = True

        ok, fail = exporter.export_traces(records, on_error=args.on_error)
        print(f"[replay] {path}: {ok}/{ok+fail} uploaded")
        total_ok += ok
        total_fail += fail

    print(f"[replay] DONE: {total_ok}/{total_ok+total_fail} traces uploaded successfully")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
