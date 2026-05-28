# Observability v2 (v2.6 R5)

记录从 v2.0 到 v2.6 obs 模块的演化路径。

## v2.0 - v2.5：本地 JSONL + markdown

`src/seagent/obs/` 自实现轻量 tracing：

- `trace.py` — `Tracer` 把每个 turn 序列化成一行 JSON，落 `experiments/.../traces/*.jsonl`。
  - 字段贴近 OTel GenAI 语义约定（model / usage / cost / phase_ms / hits）。
  - 上下文管理器 `with tracer.span("retrieval"): ...` 累加阶段耗时。
- `metrics.py` — JSONL -> deflection / escalation / p50/p95 latency / cost 聚合。
- `dashboard.py` — markdown 表格输出，给 README / 实验报告贴。
- `cost.py` — 简单 cost 模型，估算 DeepSeek token 消耗。

优点：零依赖，崩溃安全（追加写）。缺点：没有 UI；要看 Exp D / Exp E 数据
要 grep JSON 或 pandas 加载。

## v2.6：增量加 exporters，三轨并存

新增 `src/seagent/obs/exporters/`，**不动任何旧文件**：

```
obs/
├── cost.py          (unchanged)
├── dashboard.py     (unchanged)
├── metrics.py       (unchanged)
├── trace.py         (unchanged, 仍是 truth of record)
└── exporters/       <-- 新增 v2.6
    ├── __init__.py
    ├── langfuse.py  LangfuseExporter + trace_to_langfuse_payload
    └── otel.py      OtelExporter + trace_to_otel_payload
```

三轨同时存在：

| 通道                | 角色                                | 何时用                                |
| ------------------- | ----------------------------------- | ------------------------------------- |
| JSONL（trace.py）   | 本地 source of truth                | 始终开                                |
| markdown dashboard  | offline 报告                        | 实验复盘、README                      |
| Langfuse / OTLP     | UI 可视化、按 tag 过滤、瀑布图      | demo / 招聘官 / 生产监控              |

JSONL 不会被取代；exporter 只是 fan-out。这保留了崩溃安全性 + 离线可复算。

## 与 v2.1 Hooks 的关系

v2.1 引入 `seagent.hooks`，已有 `audit_log_hook` 把审计事件落 JSONL。v2.6 没
强制把 exporter 也做成 hook，原因：

1. Langfuse / OTLP 调用是网络 IO，阻塞 hook 会增加 turn p95 latency；
2. replay 模式（`scripts/replay_to_langfuse.py`）已足够覆盖 demo / 评审需求；
3. 用户想要实时推可自定义 hook（见 `docs/langfuse_integration.md` 示例），
   不需要框架强制。

## 与 OpenTelemetry 的关系

`trace.py` 注释里就声明字段贴近 OTel GenAI 语义约定。`OtelExporter` 把这层
语义对齐显式落到 OTLP：

- `gen_ai.system` 从 `model` 推（deepseek/openai/anthropic/google/unknown）。
- `gen_ai.request.model` = `Trace.model`。
- `gen_ai.usage.input_tokens` / `output_tokens` = `in_tokens` / `out_tokens`。
- `gen_ai.usage.total_cost` = `cost_usd`（OpenLLMetry 扩展约定，非 OTel GA）。
- Phase span 名 = `retrieval` / `generation` / `critic` / `guardrail`。

所以 seagent 既能推 Langfuse 的专用模型（trace+observation），也能推任何符合
OTLP 的后端，零绑定。

## 数字保留约束

v2.6 是观测层的增量增强，**不改任何实验结果**：

- c21 baseline 数字（v2.0 之前）：不动。
- v2.1 ~ v2.5 各 release 报告：不动。
- 255 tests pass 基线：v2.6 新增 25 个 exporter tests，旧 255 仍全 pass。

## 命令一览

```bash
# 干跑（不打网络，看 payload 映射）
python scripts/replay_to_langfuse.py \
    --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
    --dry-run --print-sample --limit 1

# 推 Langfuse self-host
export LANGFUSE_HOST=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-...
export LANGFUSE_SECRET_KEY=sk-...
python scripts/replay_to_langfuse.py \
    --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl

# 推任意 OTLP 后端（Phoenix / DD / Jaeger / Honeycomb）
python scripts/replay_to_langfuse.py \
    --backend otel \
    --otel-endpoint http://localhost:4318 \
    --traces experiments/stress_test_expanded/exp_e/traces/stress_trace.jsonl
```
