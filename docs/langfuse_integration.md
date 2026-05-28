# Langfuse Integration (v2.6 R5)

把 seagent 的本地 trace JSONL 推到 Langfuse self-host，免去 grep JSON 的痛苦，
直接在 UI 上看每条 turn 的 retrieval/generation/critic/guardrail 时延分解、
检索命中、token/成本、置信度与决策。

## 为什么是 Langfuse

v2.6 之前 obs 模块只产 JSONL + markdown dashboard，没有 UI；给评审 / 招聘官看
Exp D / Exp E 数据时只能粘截图或贴 JSON。Langfuse 是 2026 最被接受的
self-host LLM observability 平台，符合 OpenTelemetry GenAI 语义约定，可一键
docker compose 起整套 stack；同时 seagent 提供等价的 `OtelExporter`，可推任何
OTLP-compatible 后端（Phoenix / Datadog / Jaeger / Tempo / SigNoz / Honeycomb），
不绑定单一厂商。

## 启动 self-host

```bash
cd deploy/langfuse
cp .env.example .env
# 改掉所有 CHANGE_ME；ENCRYPTION_KEY 用 openssl rand -hex 32 生成
docker compose up -d
# ~30s 后访问
open http://localhost:3000
# 默认登录 .env 里的 LANGFUSE_INIT_USER_EMAIL / PASSWORD
```

UI 里创建一个 project，复制 public_key / secret_key 写到 shell：

```bash
export LANGFUSE_HOST=http://localhost:3000
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

## Replay 历史实验

无需重跑 LLM。直接把 v2.5 留下的 trace 推上去：

```bash
# 干跑确认 payload 映射
python scripts/replay_to_langfuse.py \
    --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
    --dry-run --limit 1 --print-sample

# 真推 exp_d + exp_e
python scripts/replay_to_langfuse.py \
    --traces experiments/stress_test_expanded/exp_d/traces/stress_trace.jsonl \
    --traces experiments/stress_test_expanded/exp_e/traces/stress_trace.jsonl \
    --tag exp_d --tag v2.6
```

输出：`DONE: 200/200 traces uploaded successfully`。

## 字段映射

seagent `Trace` -> Langfuse trace + observation。一条 turn 对应 1 个 trace 和
N 个 observation（每个 phase 一条）。

| seagent.obs.Trace          | Langfuse                                | 说明                                          |
| -------------------------- | --------------------------------------- | --------------------------------------------- |
| `trace_id` (uuid hex)      | `trace.id`                              | 直接复用，便于反查本地 JSONL                  |
| `turn`                     | `trace.metadata.turn` + `tags[turn:N]`  | 多 turn 会话过滤                              |
| `ts` (epoch)               | `trace.timestamp` (RFC3339)             | UTC 毫秒                                      |
| `query`                    | `trace.input`                           | 已截断到 500 字符                             |
| `latency_ms`               | `trace.metadata.latency_ms`             | 端到端                                        |
| `confidence`               | `trace.metadata.confidence`             | -1 表示未评估                                 |
| `escalate`                 | `trace.metadata.escalate`               | 是否转人工                                    |
| `guardrail_verdict`        | `trace.metadata.guardrail_verdict`      | allow/redact/block/flag                       |
| `guardrail_blocked`        | `trace.metadata.guardrail_blocked`      | bool                                          |
| `error`                    | `trace.metadata.error` + `tags[error]`  | 非空时打 error 标签                           |
| `phase_ms[retrieval]`      | span(name="retrieval")                  | metadata.latency_ms                           |
| `hits` (前几条)            | span(retrieval).output                  | 整个 list                                     |
| `n_hits`                   | span(retrieval).metadata.n_hits         |                                               |
| `phase_ms[generation]`     | generation(name="generation")           | model/usage 都挂这里                          |
| `model`                    | generation.model                        | OTel `gen_ai.request.model`                   |
| `in_tokens` / `out_tokens` | generation.usage.{input,output,total}   | Langfuse 标准                                 |
| `cost_usd`                 | generation.usage.totalCost              | Langfuse 接受 totalCost / 各自 price          |
| `phase_ms[critic]`         | span(name="critic")                     | 反思阶段耗时                                  |
| `phase_ms[guardrail]`      | span(name="guardrail")                  | metadata 含 verdict/blocked                   |

OTel 路径（`scripts/replay_to_langfuse.py --backend otel`）字段对齐
OpenTelemetry GenAI 语义约定：`gen_ai.system`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
`gen_ai.usage.total_cost`，并把 phase 挂在子 span name 上。

## SDK 或 HTTP

`LangfuseExporter` 在初始化时优先 `import langfuse`；缺失会静默回落到自实现的
HTTP POST `/api/public/ingestion`（Basic auth = `public_key:secret_key`）。所以
项目仍保持 `dependencies = []`，零硬依赖。

`OtelExporter` 同理：优先 `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`；
缺失走最小 OTLP/HTTP JSON encoder POST `/v1/traces`。

## Hooks 集成（可选）

v2.1 已经有 `audit_log_hook` 落 JSONL。若想把 trace 实时推 Langfuse 而不是
跑完 replay，可加 hook（不在本 release 内置；自定义示例）：

```python
from seagent.obs.exporters import LangfuseExporter
exporter = LangfuseExporter(...)
def langfuse_export_hook(event):
    if event.kind == "turn_end":
        exporter.export_trace(event.payload)
```

注意阻塞 hook 会增加 turn 延迟；生产建议另起异步 worker 消费 JSONL。

## 替代方案

| 后端         | 配置                                  | 备注                                  |
| ------------ | ------------------------------------- | ------------------------------------- |
| Phoenix      | `OtelExporter(endpoint=phoenix_url)`  | 本地开发轻量，OSS                     |
| Datadog      | OTel + DD agent OTLP intake           | 加 `dd-api-key` header                |
| Jaeger/Tempo | OTLP `/v1/traces`                     | 仅 span，没 LLM 专属可视化            |
| Honeycomb    | `headers={"X-Honeycomb-Team": "..."}` |                                       |
| Langfuse Cloud | `host=https://cloud.langfuse.com`   | 不想 self-host 时                     |

## 截图占位

self-host UI 启动后，按以下页面截图替换：

- `assets/langfuse_trace_list.png` — Traces 列表，按 `tag:exp_d` 过滤。
- `assets/langfuse_trace_detail.png` — 单条 trace 的瀑布图（retrieval ~25ms,
  generation ~38s, critic ~5s, guardrail ~1s），右侧 metadata 看 confidence /
  escalate / guardrail_verdict。
- `assets/langfuse_cost_dashboard.png` — Sessions/Cost 看板，按 model 聚合
  日累计 cost_usd（即 v2.5 markdown dashboard 的 UI 化）。

（本环境不能起 docker，截图留给可启动 docker 的环境补。）

## 已知不映射的字段

- 多模态 input/output：当前 seagent 只有文本，无需 attachments。
- Score：seagent 的 `confidence` 已挂 metadata，未上 Langfuse `score` 接口
  （需独立 ingest call）。后续若接入 verifier 自动评分可补。
