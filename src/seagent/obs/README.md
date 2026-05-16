# `seagent.obs` — 生产级可观测性模块

把自进化客服 Agent 当成真实线上系统来观测。一次"对话回合 (turn)" = 一条 **trace**，
trace 内含若干阶段 **span**(retrieval / generation / critic / guardrail)。trace 以
**JSONL** 落到 `experiments/traces/*.jsonl`，再聚合成运营指标、渲染成运营报告。

零第三方依赖即可跑通(纯 stdlib：`time / json / uuid / dataclasses / contextlib`)。
matplotlib 仅为可选增强(画趋势图)，缺失不影响任何功能。

## 借鉴的开源项目 / 规范

- **OpenTelemetry GenAI 语义约定** (Semantic Conventions for Generative AI)：字段命名
  与 trace/span 结构对齐其 `gen_ai.*`(model、token usage、阶段 span 等)。
- **Langfuse**：trace → observation(span) 的层级，以及 dashboards 的运营指标聚合范式
  (deflection / latency 分布 / cost)。
- **Arize Phoenix**：RAG retrieval spans(记录命中来源 source/ref/score)、evals 聚合与
  project overview 看板的呈现方式。
- **OpenLLMetry** (Traceloop)：用 OTel 给 LLM 调用做埋点、把 token/cost 记进 span 的做法。

本模块是上述思路的"零依赖离线版":同样的 trace JSONL 之后可被任意一家平台导入。

## 一条 trace 记录的字段(及其业务含义)

| 字段 | 含义 | 真实业务对应 |
| --- | --- | --- |
| `trace_id` | 回合唯一 ID (uuid) | 工单/会话追踪键 |
| `turn` | 第几个回合 | 会话内序号 |
| `ts` | 回合开始 epoch 秒 | 发生时间 |
| `latency_ms` | 端到端耗时 | SLA、用户等待体验 |
| `phase_ms` | 各阶段耗时 `{retrieval, generation, critic, guardrail}` | 性能瓶颈定位 |
| `hits` | 检索命中 `[{source, ref, score}]` | RAG 召回可解释性/引用来源 |
| `n_hits` | 命中条数 | 召回体量 |
| `confidence` | critic 置信度 [0,1] | 回答可信度 |
| `escalate` | 是否转人工 | 是否需要人工坐席介入 |
| `guardrail_verdict` | `allow / redact / block / flag ...` | 出站安全/合规结论 |
| `guardrail_blocked` | 是否被拦截 | 合规拦截统计 |
| `model` | 模型名 | 成本归因 |
| `in_tokens / out_tokens` | 估算/回填 token | LLM 账单口径 |
| `cost_usd` | 估算成本 | 直接对应账单 |
| `query` | 截断/脱敏后的查询 | 排障(可选) |
| `error` | 异常信息 | 故障 turn 标记 |

## 聚合出的运营指标(`metrics.aggregate`)

`deflection_rate`(自助解决率 = 未转人工占比)、`escalation_rate`(转人工率)、
`avg/p50/p95 latency`(时延分布)、`total/avg cost`、`avg_hits`(平均检索命中)、
`guardrail_block_rate`(拦截率)、`error_rate`(异常率)、`total_tokens`。
`summary_table()` 输出 markdown 表;`dashboard.render_ops_report()` 输出完整运营报告。

## 接入 `support_agent` 时如何包裹各阶段计时

不改现有文件，在调用方包一层即可:

```python
from seagent.obs import Tracer, estimate_tokens, estimate_cost

tracer = Tracer(workdir=cfg.workdir)
tracer.start_turn(turn=i, query=query, model=cfg.model)

with tracer.span("retrieval"):
    kb  = semantic.retrieve(query, top_k=cfg.kb_top_k)
    epi = episodic.retrieve(query, top_k=cfg.epi_top_k) if episodic else []
    pb  = procedural.retrieve(query) if procedural else []
contexts = kb + epi + pb
tracer.set_hits(contexts)            # 直接吃 Passage 列表(读 source/ref/score)

with tracer.span("generation"):
    answer = backend.generate_answer(query, contexts)

with tracer.span("critic"):
    conf = critic.confidence(query, answer, contexts)

with tracer.span("guardrail"):
    report = guardrail_pipeline.run(answer, contexts)  # 可选

# token/cost 估算(若 provider 返回 usage 则直接回填覆盖)
in_tok  = estimate_tokens(query + " ".join(p.text for p in contexts))
out_tok = estimate_tokens(answer)
tracer.set_usage(in_tok, out_tok, estimate_cost(cfg.model, in_tok, out_tok))

tracer.end_turn(
    confidence=conf,
    escalate=result.escalate,
    guardrail_verdict=getattr(report, "verdict", "allow"),
    guardrail_blocked=getattr(report, "blocked", False),
)
```

`SupportAgent.handle()` 的内部顺序(retrieve → generate → critic → escalate)与上面
span 一一对应，所以包裹是无侵入的——把 `handle()` 的几行拆出来分别套 `with span(...)`
即可,也可直接用 `AgentResult` 的字段填 `end_turn`。
```
