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

## v2.6 R6（2026-05-29）：Phoenix 替代 Langfuse

**背景**：本机没装 docker，self-host Langfuse 完整 stack（postgres + clickhouse +
redis + worker + web）成本太高。改用 [Arize Phoenix](https://github.com/Arize-ai/phoenix)
——pip-installable LLM observability UI，单进程跑、SQLite 后端、零容器依赖、
原生 OTLP/HTTP 接收端。这是 2026 年 OSS LLM 观测领域事实标准之一。

### 选型对照

| 维度 | Langfuse self-host | Phoenix（local） |
|---|---|---|
| 部署 | docker compose 5 容器 | `pip install arize-phoenix` 单进程 |
| 体积 | 镜像 ~3 GB | venv +459 MB（含 scipy / scikit-learn / pyarrow） |
| 协议 | 私有 SDK + OTLP 兼容 | OTLP/protobuf 原生 |
| UI | 多用户 / 项目权限 | 单租户，project = `service.name` |
| 数据 | postgres + clickhouse | SQLite（`.phoenix_data/phoenix.db`） |
| 适合 | prod 多团队 | 本地复盘 + demo |

### 启动

```bash
source .venv-tau2/bin/activate
export PHOENIX_WORKING_DIR=$(pwd)/.phoenix_data
export PHOENIX_HOST=127.0.0.1
export PHOENIX_PORT=6007   # 6006 被本机另一个 http.server 占了
export PHOENIX_GRPC_PORT=4319
nohup python -m phoenix.server.main serve > .phoenix_data/server.log 2>&1 &
# UI: http://127.0.0.1:6007
# OTLP/HTTP ingest: http://127.0.0.1:6007/v1/traces  (protobuf only)
# OTLP/gRPC ingest: 127.0.0.1:4319
```

> 兼容性注意：本机 Python 3.13 + Phoenix 11.38.0 需要 `starlette==0.45.3`、
> `arize-phoenix-evals<1.0`（实测 0.29.0 可用）、`pytz`。Phoenix 8.x 在
> py3.13 会因 ABC/Generic MRO 报错；Phoenix 16.x 依赖 pydantic-ai-slim 太重。

### 历史 trace 导入

`scripts/push_to_phoenix.py` 用 OTel SDK + OTLP/HTTP-protobuf 把历史 JSONL
push 进 Phoenix，按文件分 project（`service.name`）。**不动任何 v2.6
exporter 源码**，仅在 script 层做了 load_records → Trace dict 适配，因为
Exp E_v4 的 `traces/stress_trace.jsonl` 是空的（v3 stress runner 路径），
真数据落在 `load_records.jsonl`。

```bash
python scripts/push_to_phoenix.py \
    --endpoint http://127.0.0.1:6007 \
    --spec "exp_d:experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl" \
    --spec "exp_e_v4:experiments/stress_test_expanded/exp_e_v4/load_records.jsonl:load_records"
# -> exp_d: 485 traces, exp_e_v4: 500 traces, total 985.
```

字段映射（v2.6 `trace_to_otel_payload` 的精神 + OpenInference UI 友好字段）：

- `gen_ai.system / gen_ai.request.model / gen_ai.usage.{input,output}_tokens / gen_ai.usage.total_cost`
- `seagent.turn / .confidence / .escalate / .guardrail.verdict / .guardrail.blocked / .latency_ms / .category / .ticket_id`
- `input.value / output.value`（让 Phoenix UI 把 query/answer 直接渲染成 LLM input/output）
- `openinference.span.kind = LLM | RETRIEVER | GUARDRAIL | CHAIN`
- 子 span：`retrieval / generation / critic / guardrail`，每个挂 `seagent.phase.latency_ms`

### 验证 / 聚合导出

REST API 拉聚合，写 `docs/screenshots/phoenix/aggregate.json`：

```bash
python scripts/export_phoenix_aggregate.py
```

实际本地结果（截至 2026-05-29 推 985 traces 之后）：

| Project | root spans | phase spans | latency p50 / p95 / p99 (ms) | cost (USD) | escalate% |
|---|---|---|---|---|---|
| exp_d | 485 | 1449 | 240 / 996 / 1494 | 0.1737 | 67.2% |
| exp_e_v4 | 500 | 500 | — (load_records 无 phase) | 0.0 | 33.2% |

注：exp_e_v4 的 cost / tokens 在 `load_records.jsonl` 里为 0（v3 stress runner
没记），所以 Phoenix 上看不到 token spend；latency 仍可见。exp_d 的 phase
spans 不到 4×485 = 1940，是因为部分 turn 因 confidence 早返回未跑完整 pipeline。

### 截图存档

`docs/screenshots/phoenix/`（用 Chromium headless 抓，非伪造）：

| 文件 | 内容 |
|---|---|
| `01_projects_home.png` | Projects 主界面，exp_d (485) / exp_e_v4 (500) / default 三卡 |
| `02_exp_d_trace_list.png` | exp_d 的 trace 列表（Spans tab，agent_turn / guardrail / generation 等） |
| `03_exp_d_trace_detail.png` | 单条 trace 展开：root agent_turn + phase 子 span 树（guardrail / generation / retrieval），右栏 attributes / input |
| `04_exp_e_v4_trace_list.png` | exp_e_v4 trace 列表 |
| `05_exp_d_metrics.png` | Aggregate Metrics 页：Traces over time、Trace Latency P50/P95/P99、Cost、Token usage、Top models |
| `aggregate.json` | REST API 拉的聚合 JSON，CI 可 diff |

### ROI 一句话

> Phoenix 给的 LLM observability 90% 价值 = Langfuse self-host 的 90%，
> 但部署成本从 docker-compose 5 容器降到 `pip install + python -m phoenix.server.main`，
> 适合作为 demo / 本地复盘 / 招聘官演示的默认选择；Langfuse 留给真上生产多团队场景。

### 不破坏现有约束

- 没动任何 `src/seagent/*` 源代码（include obs/exporters/*）。
- 新增脚本：`scripts/push_to_phoenix.py`、`scripts/export_phoenix_aggregate.py`。
- 310 tests baseline：`tests/test_exporters.py` 25 个仍全 pass（Phoenix 走的是
  独立 push 路径，不经 `OtelExporter` 的 HTTP/JSON 编码）。
- 磁盘：venv +459 MB（超出原设计目标 200 MB；不可避免，scipy + scikit-learn +
  pyarrow + grpcio 是 Phoenix 11.x 硬依赖；139 GB 可用，影响可忽略）。

