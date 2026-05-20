# 生产部署架构（Self-Evolving Customer-Support Agent）

把项目里已实现的四层（serving / guardrails / observability / governance）放进一张真实业务的部署拓扑。
绿字模块在本仓库已有可运行实现，灰字是上线时要接的外部系统（本项目用桩/接口预留）。

## 1. 在线服务路径（请求 → 回答）

```
                 渠道(Web/App/Zendesk/Intercom/微信)
                              │ webhook / API
                              ▼
                      ┌───────────────┐
                      │  API Gateway  │  鉴权 / 限流 / 多租户路由
                      └───────┬───────┘
                              ▼
        ┌──────────────────────────────────────────────┐
        │      Serving (FastAPI)   src/seagent/serving  │
        │  /chat  /feedback  /handoff  /healthz /metrics │
        │  SessionManager: 按 ticket 维护多轮会话         │
        └───────┬───────────────────────────────┬───────┘
                ▼                                 │
   ┌────────────────────────┐                    │
   │ Guardrails (INPUT)      │ guardrails/        │
   │ · 注入/越狱检测 → 拦截    │                    │
   │ · PII 脱敏(Presidio可选) │                    │
   └────────┬───────────────┘                    │
            │ (blocked → 直接转人工)                │
            ▼                                      │
   ┌─────────────────────────────────────────┐    │
   │  SupportAgent.handle()  (self-RAG)        │    │
   │  ┌──────────────┐                         │    │
   │  │ Retrieval     │ Semantic(KB) +          │    │
   │  │ (BM25/Hybrid) │ Episodic(经验池) +       │    │
   │  │               │ Procedural(playbook)    │    │
   │  └──────┬───────┘                          │    │
   │         ▼  检索命中(source/ref/score)       │    │
   │  ┌──────────────┐   ┌──────────────┐       │    │
   │  │ LLM Backend   │   │   Critic     │       │    │
   │  │ vLLM/API/mock │   │ 置信度估计    │       │    │
   │  └──────┬───────┘   └──────┬───────┘       │    │
   │         ▼ answer            ▼ confidence    │    │
   │  转人工决策: 可信案例 > 命中规则 > 不确定阈值   │    │
   └────────┬──────────────────────────────────┘    │
            ▼                                          │
   ┌────────────────────────┐                         │
   │ Guardrails (OUTPUT)     │                         │
   │ · groundedness 防幻觉    │                         │
   │ · 合规策略 · PII 脱敏     │                         │
   │ → allow / rewrite / escalate / block             │
   └────────┬───────────────┘                         │
            ▼                                          ▼
      AgentReply ──────────────────────────►  Human Handoff Queue
   (answer, escalate, confidence,             (低置信/规则/guardrail 命中
    sources, guardrail, trace_id)              → 人工坐席接管)
            │
            ▼  每一步埋点
   ┌─────────────────────────────────────────────┐
   │ Observability  src/seagent/obs  (OTel GenAI)  │
   │ trace: latency p50/p95 · token · cost ·        │
   │ 检索命中 · 置信度 · guardrail 裁决 · 是否转人工   │
   │ → Langfuse / Arize Phoenix (灰)  → 告警(SLO)    │
   └─────────────────────────────────────────────┘
```

## 2. 离线自进化路径（反馈 → 受治理发布，不改权重）

```
线上隐式/显式反馈(点踩 / reopen / 低CSAT / 人工解决记录)
        │  src/seagent/serving/feedback.py
        ▼
  待复盘失败队列 (needs_human_review)         ← 噪声反馈也可驱动(达 gold 的 ~93%)
        │
        ▼  人审补全正确解法 + memory_hygiene.scrub_case(PII脱敏)
  Episodic 经验池 (TTL / 去重 / 冲突消解)
        │
        ▼  Reflector("dreaming") 聚类失败 → 归纳候选 playbook
        │
        ▼  Governance 发布流水线  src/seagent/governance
  ┌──────────────────────────────────────────────────────┐
  │ propose → approve(人审) → canary(灰度) →               │
  │   ┌── Regression Gate ──┐                              │
  │   │ 启用前/后跑同一 eval  │  指标回退超阈 → 拒绝         │
  │   │ 集对比关键指标        │  ──────────► ROLLBACK       │
  │   └──────────┬──────────┘                              │
  │              ▼ 通过                                     │
  │           activate (写入 ProceduralMemory, enabled)     │
  │     全程审计日志(who/when/what/why) · 一键回滚           │
  └──────────────────────────────────────────────────────┘
        │
        ▼  下一次在线检索即生效（模型权重始终不变）
   线上指标回升  ── SLO 回归即触发自动 rollback ──┐
        └────────────────────────────────────────┘
```

## 3. SLO / 监控（上线必备）
| 维度 | 指标 | 触发动作 |
|---|---|---|
| 质量 | deflection rate↓ / escalation rate↑ / groundedness↓ | 告警 + 灰度回滚候选 playbook |
| 成本 | avg token / cost-per-ticket 越拐点(pool≈17) | 触发 TTL/top-k 记忆淘汰 |
| 时延 | p95 latency 越阈 | 降检索 top-k / 缓存 |
| 安全 | guardrail 拦截率异常 / PII 漏报 | 阻断 + 人工复核 |
| 进化 | 新 playbook 上线后 7 日指标 | 未达预期自动 deprecate |

## 4. 本仓库已实现 vs 上线需补
- **已实现可跑**：serving(FastAPI 接口)、guardrails(注入/PII/groundedness/policy)、obs(trace+看板)、governance(生命周期+回归门禁+审计)、memory(三层)、自进化闭环、确定性评测、τ²-bench 验证。
- **上线需接**（已留接口）：真实向量库/检索服务、Langfuse/Phoenix 后端、真实 ticketing webhook、鉴权/多租户、坐席系统、告警平台、PII 用 Presidio 生产配置。
