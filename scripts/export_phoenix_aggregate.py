#!/usr/bin/env python3
"""Pull Phoenix REST API and emit a one-file aggregate JSON snapshot
(per-project trace count + latency / cost / escalation rate / token usage).

Useful when 没法截图 / 想把 dashboard 数字 freeze 到文件里做 diff。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.request
from collections import Counter
from typing import Any, Dict, List


def fetch_all_spans(endpoint: str, project_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    url = f"{endpoint}/v1/projects/{project_id}/spans?limit=1000"
    while url:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read())
        out.extend(d.get("data", []))
        nc = d.get("next_cursor")
        url = f"{endpoint}/v1/projects/{project_id}/spans?limit=1000&cursor={nc}" if nc else None
    return out


def list_projects(endpoint: str) -> List[Dict[str, Any]]:
    with urllib.request.urlopen(f"{endpoint}/v1/projects", timeout=15) as r:
        return json.loads(r.read()).get("data", [])


def summarize(spans: List[Dict[str, Any]]) -> Dict[str, Any]:
    roots = [s for s in spans if not s.get("parent_id")]
    latencies = []
    costs = []
    in_toks = []
    out_toks = []
    escalates = 0
    blocked = 0
    verdicts: Counter = Counter()
    categories: Counter = Counter()
    for s in roots:
        a = s.get("attributes") or {}
        lat = a.get("seagent.latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))
        cost = a.get("gen_ai.usage.total_cost")
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
        in_t = a.get("gen_ai.usage.input_tokens")
        if isinstance(in_t, (int, float)):
            in_toks.append(int(in_t))
        out_t = a.get("gen_ai.usage.output_tokens")
        if isinstance(out_t, (int, float)):
            out_toks.append(int(out_t))
        if a.get("seagent.escalate"):
            escalates += 1
        if a.get("seagent.guardrail.blocked"):
            blocked += 1
        v = a.get("seagent.guardrail.verdict")
        if v:
            verdicts[str(v)] += 1
        c = a.get("seagent.category")
        if c:
            categories[str(c)] += 1

    def pct(arr, p):
        if not arr:
            return None
        return round(float(statistics.quantiles(arr, n=100)[p - 1]) if len(arr) > 1 else arr[0], 3)

    return {
        "total_spans": len(spans),
        "root_spans": len(roots),
        "phase_span_counts": dict(Counter(s.get("name") for s in spans if s.get("parent_id"))),
        "latency_ms": {
            "n": len(latencies),
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
            "p99": pct(latencies, 99),
            "max": max(latencies) if latencies else None,
        },
        "cost_usd": {
            "total": round(sum(costs), 6),
            "mean": round(sum(costs) / len(costs), 6) if costs else None,
        },
        "tokens": {
            "input_total": sum(in_toks),
            "output_total": sum(out_toks),
        },
        "escalation_rate": round(escalates / len(roots), 4) if roots else None,
        "blocked_rate": round(blocked / len(roots), 4) if roots else None,
        "guardrail_verdicts": dict(verdicts),
        "categories": dict(categories),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="http://127.0.0.1:6007")
    p.add_argument("--out", default="docs/screenshots/phoenix/aggregate.json")
    args = p.parse_args(argv)

    projects = list_projects(args.endpoint)
    snapshot: Dict[str, Any] = {"endpoint": args.endpoint, "projects": {}}
    for proj in projects:
        if proj["name"] == "default":
            continue
        spans = fetch_all_spans(args.endpoint, proj["id"])
        snapshot["projects"][proj["name"]] = {
            "id": proj["id"],
            **summarize(spans),
        }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    print(f"[export] wrote {args.out}")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
