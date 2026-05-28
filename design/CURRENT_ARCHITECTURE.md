# 当前 Agent 完整架构（v2.9 状态快照）

_最后更新：2026-05-29 · commit `2a658a8` · 34 commits · 310 tests pass · GitHub: [self-evolving-customer-support-agent](https://github.com/Young-1231/self-evolving-customer-support-agent)_

## TL;DR

对齐 2026 SOTA 主流的 **多 agent 自进化客服系统**：渠道 → 入站 guardrail → IntentRouter → 5 个 SpecialistAgent fan-out → 单 agent 3 层记忆 + APR-CS 自适应路由 → per-sub aggregated guardrail → AgentResult。离线进化闭环：失败工单 → Reflector → 反事实归因 → Governance 流水线 → Skills 持久化。**Claude Code 五件套全实施**（Hooks/Skills/MCP/Subagents/简化版 Plan Mode）+ OpenViking L0/L1/L2 + Langfuse 可观测。整体对齐度 ~85%，在 3 个细节上领先公开做法。

## 一、在线推理路径

```
                            ┌──────────────────────────────────────┐
                            │     渠道层 (channels)                 │
                            │  FastAPI /chat /feedback /handoff    │
                            │  + OpenClaw IM gateway 兼容性         │
                            └─────────────────┬────────────────────┘
                                              │
              ┌───────────────────────────────▼───────────────────────────────┐
              │  GuardrailPipeline.check_input()  v2.9                         │
              │   注入/越狱 检测 (regex+keyword)                                │
              │   PII (Presidio optional; strict/balanced/relaxed)             │
              └───────────────────────────────┬───────────────────────────────┘
                                              │ blocked → 转人工
                                              ▼
       ┌──────────────────────────────────────────────────────────────────────┐
       │  MultiAgentOrchestrator  v2.3 + v2.7-2.9                              │
       │  ┌──────────────────────────┐                                         │
       │  │ IntentRouter (LLM)        │  ──► [sub_query_1, sub_query_2, ...]   │
       │  │ JSON输出+brace-balanced   │                                         │
       │  │ +LRU cache+fallback        │                                         │
       │  └─────────────┬─────────────┘                                         │
       │                ▼                                                       │
       │     [billing][account][technical][refund][general]  专家 fan-out       │
       │                │  各自 mode='core' (跳 guardrail，避免 v2.3 中毒)       │
       │                ▼                                                       │
       │     ┌────────────────────────────────────┐                             │
       │     │ SpecialistAgent.handle(sub_query)   │  每个域用 topic-filtered KB │
       │     │  = SupportAgent + kb_filter         │                             │
       │     └──────────────────┬─────────────────┘                             │
       │                        ▼                                               │
       │     ┌──────────────── SupportAgent 单 agent 内核 ────────────────┐    │
       │     │  ① 检索 (3层记忆，并发)                                       │    │
       │     │     ├─ Semantic: 176 篇 KB (NimbusFlow + Bitext)              │    │
       │     │     │   BM25 char-bigram                                       │    │
       │     │     ├─ Episodic: jsonl + BM25  OR  FsEpisodicStore (v2.5)     │    │
       │     │     │   OpenViking L0/L1/L2  (topic/date/case_id.md)           │    │
       │     │     └─ Procedural: jsonl playbooks OR SkillStore (v2.2)        │    │
       │     │         Claude Code Skills .md + frontmatter, ClawHub 兼容    │    │
       │     │         + APR-CS router: top_k_relevance / cf_weighted /      │    │
       │     │           conf_gated  (反事实 Δᵢ leave-one-out 评分)           │    │
       │     │  ② LLM 生成回答 (DeepSeek-V4-Flash / OpenAI-compatible / mock)  │    │
       │     │  ③ Critic 置信度                                              │    │
       │     │  ④ Calibrator (per-domain escalate_tau)                      │    │
       │     │  ⑤ Hook 触发 (v2.1, 8 lifecycle 点)                          │    │
       │     └──────────────────┬─────────────────────────────────────────┘    │
       │                        ▼                                               │
       │   收集所有 sub_results → 聚合 guardrail (v2.8 per_sub_aggregated)      │
       │   ┌──────────────────────────────────────────────────────────┐         │
       │   │ For each sub: guardrail.check_output(answer, contexts)    │         │
       │   │  ├─ groundedness check (deterministic OR LLM-judge v2.1)  │         │
       │   │  ├─ policy (v2.9 context-aware: order ID stripped)        │         │
       │   │  └─ PII redaction per-sub                                  │         │
       │   │ Aggregate:                                                 │         │
       │   │  • any-supported = bundle supported                        │         │
       │   │  • majority-vote escalate                                  │         │
       │   │  • ANY block → bundle block                                │         │
       │   │  • PII: per-sub redacted, concatenate                      │         │
       │   └──────────────────────┬───────────────────────────────────┘         │
       │                          ▼                                             │
       │   AgentResult (answer, escalate, confidence, sources, trace_id)        │
       └────────────────────────────────────┬───────────────────────────────────┘
                                            │
              ┌─────────────────────────────▼─────────────────────────────┐
              │ 可观测 (v2.6)：每 turn JSONL trace + 运营 dashboard          │
              │  → Langfuse exporter / OTel exporter (Phoenix/Datadog 兼容)  │
              └──────────────────────────────────────────────────────────┘
```

## 二、离线自进化路径（不改权重）

```
       线上反馈 (thumbs_down / reopened / 低 CSAT, FeedbackProcessor)
         ↓ 噪声反馈也可驱动(达 gold 的 92.6%)
       待复盘 Case 队列  → 人审补全 resolution
         ↓
       Episodic 经验池  (TTL / dedup / 冲突消解 / PII scrub)
         ↓
       Reflector "dreaming" (聚类失败案例)
         ↓
       APR-CS Counterfactual Evaluator
         leave-one-out 每条 tip 算 Δᵢ → 持久化到 Skill metadata
         ↓
       Governance Pipeline (v1.x)
         proposed → approved(人审) → canary → Regression Gate
                                        ↓ FAIL → ROLLBACK + audit
                                        ↓ PASS
                                     activate (写入 SkillStore)
         ↓ SLO 回归即触发自动 rollback
       下一次 retrieval 即生效（权重始终不变）
```

## 三、MCP 协议层（v2.4，工具标准化）

```
       MCP Client (in SupportAgent) ──┐ JSON-RPC 2.0 over stdio
                                       │
                  ┌────────────────────┼────────────────────┐
                  ▼                    ▼                    ▼
            order_server.py     user_server.py      refund_server.py     handoff_server.py
            (query_order,       (query_user,        (check_policy,      (transfer_to_human,
             update_order)       authenticate)       initiate_refund)    list_pending)

       → 兼容任意 MCP-compatible client (Zendesk MCP / Intercom MCP / 自建 CRM)
       → 4 servers / 8 tools / protocol version 2025-06-18
```

## 四、模块全清单

| 模块 | 路径 | 引入 commit | 功能 |
|---|---|---|---|
| **多 agent 编排** | `multi_agent/orchestrator.py` | v2.3 + v2.7-v2.9 | IntentRouter + Specialists fan-out + per-sub aggregated guardrail |
| Intent Router | `multi_agent/router.py` | v2.3 | LLM 意图分类 + 拆分 + LRU cache |
| Specialist Agent | `multi_agent/specialist.py` | v2.3 | 域级 KB filter + mode core/observed |
| Handoff 协议 | `multi_agent/handoff.py` | v2.3 | specialist 间转移协议 |
| **单 agent 内核** | `agent/support_agent.py` | c04 + v2.1 calibrator/hooks | self-RAG + critic + 8 lifecycle hooks |
| Critic | `agent/critic.py` | c04 | 置信度估计 |
| Escalation Voter | `agent/escalation_voting.py` | c21 P1 | 三信号 any/majority/weighted/unanimous |
| **3 层记忆** | `memory/{semantic,episodic,procedural}.py` | c04 | KB / 经验池 / playbook |
| BM25 | `memory/bm25.py` | c04 | 字符 bigram，CN/EN 友好 |
| Dense retriever | `memory/dense.py` | c14 | TF-IDF / Hybrid / embedding 可选 |
| **OpenViking FS Store** | `memory/fs_store.py` | v2.5 | L0/L1/L2 文件系统层级 episodic |
| **Skills 持久层** | `skills/{format,store,manifest}.py` | v2.2 | Claude Code Skills + ClawHub 兼容 |
| **进化** | `evolution/{reflector,router,counterfactual}.py` | c04 / c15 | dreaming + APR-CS 自适应路由 + 反事实 LOO |
| **Guardrails** | `guardrails/{pii,groundedness,injection,policy,pipeline}.py` | c09 + c21 LLM-judge + v2.9 policy fix | 入站/出站 4 类闸门 + context-aware policy |
| LLM-judge groundedness | `guardrails/groundedness_llm.py` | c21 P1 | LLM 二分类 + soft-fail |
| **Hooks** | `hooks/{types,registry,builtin}.py` | v2.1 | 8 lifecycle points |
| **Observability** | `obs/{trace,cost,metrics,dashboard}.py` | c10 | OTel-GenAI semconv 自实现 |
| Exporters | `obs/exporters/{langfuse,otel}.py` | v2.6 | Langfuse / OTel HTTP 推送 |
| **Governance** | `governance/{lifecycle,regression_gate,memory_hygiene}.py` | c11 | playbook 发布生命周期 + 回归门禁 + 审计 |
| **Calibration** | `calibration/{calibrator,domain_inference}.py` | c21 P1 | per-domain 阈值校准 |
| **Serving** | `serving/{schema,session,feedback,app}.py` | c12 | FastAPI + ticket schema + implicit feedback |
| **MCP 协议** | `mcp/{protocol,server,client,tools}.py` + `mcp_servers/*.py` | v2.4 | JSON-RPC stdio + 4 servers + 8 tools |
| **Stress test** | `stress/{generator,load_runner,memory_scaling}.py` | c16 | 500 LLM-生成工单压测 + 记忆膨胀 |
| **τ²-bench 集成** | `tau2_ext/{memory_agent,experience,reflect}.py` | c06 | tau2 官方 compute_metrics + APR-CS |
| **数据集 ingest** | `datasets/bitext.py` | c17 | Bitext 27k 转 KB |

## 五、与 2026 SOTA 公开做法对照表

| 层 | 2026 SOTA 公开做法 | 本项目状态 | 评分 |
|---|---|---|---|
| **渠道** | Sierra/Decagon multi-channel + IM gateway | FastAPI + OpenClaw 兼容（未真接） | 🟡 7/10 |
| **多 agent** | OpenAI Agents SDK / Claude Code Subagents | ✅ 自实现 + 4 轮迭代修通 multi_intent | 🟢 9/10 |
| **意图路由** | LangGraph / OpenAI CS demo handoff | ✅ IntentRouter LLM JSON 输出 + cache | 🟢 9/10 |
| **3 层记忆** | Mem0 / Letta / A-MEM | ✅ semantic + episodic + procedural | 🟢 9/10 |
| **Episodic 存储** | OpenViking 文件系统范式 | ✅ FsEpisodicStore L0/L1/L2 + 等价测过 | 🟢 9/10 |
| **Procedural 格式** | Claude Code Skills (markdown+frontmatter) | ✅ SkillStore + ClawHub 兼容 + 双向无损 | 🟢 9.5/10 |
| **反事实归因** | GEPA per-component (ICLR'26 Oral) | ✅ APR-CS tip-level（比 GEPA 细一级粒度） | 🟢 9.5/10 |
| **Self-RAG 自适应** | Asai et al. reflection tokens | ✅ APR-CS conf_gated + adaptive top-k | 🟢 9/10 |
| **工具协议** | Claude Code MCP (97M 月下载，标准) | ✅ 完整实现 4 server / 8 tools + 协议 round-trip 测过 | 🟢 9/10 |
| **Lifecycle Hooks** | Claude Code 25 hooks | ✅ 8 hooks 全实现 + 异常隔离 | 🟢 9/10 |
| **Plan/Task** | Claude Code Plan Mode | 🟡 IntentRouter 拆 + fan-out 算简化版 | 🟡 7/10 |
| **Computer/Browser use** | Anthropic Computer Use API | ❌ 未实现 | 🔴 0/10 |
| **Voice agent** | tau3-bench voice domain | ❌ 未实现 | 🔴 0/10 |
| **Guardrails 多层** | NeMo / guardrails-ai / Llama Guard | ✅ 4 类 + LLM-judge groundedness + v2.9 上下文感知 policy | 🟢 9/10 |
| **可观测** | Langfuse / Arize Phoenix / OpenLLMetry | ✅ exporter + docker-compose（未真起） | 🟡 8/10 |
| **Governance/治理** | misevolution defense (arXiv 2509.26354) | ✅ 4-state + regression gate + audit | 🟢 9/10 |
| **公开评测** | τ²-bench (Sierra 出品) | ✅ retail + airline 完整 pass^k 全谱 | 🟢 9.5/10 |
| **真实流量验证** | Anthropic Harvey 6x / Klarna 700 agents | ❌ 仅 500 LLM 生成工单（无生产数据） | 🔴 3/10 |
| **大规模记忆压测** | Mem0 production case | 🟡 拐点 1000 cases 测过，未到 10k+ | 🟡 6/10 |

**总体对齐度**：12/18 完全对齐 (66%) + 4/18 部分对齐 (22%) + 2/18 未实现 (11%)。**真实流量验证是唯一硬伤**。

## 六、领先 2026 公开做法的 3 个细节

1. **Tip-level counterfactual attribution**：GEPA 是 prompt-component-level（最细到 prompt 模块），APR-CS 进一步细到 **single-tip-level** 的 leave-one-out Δᵢ 并持久化到 playbook metadata 做治理。Anthropic Dreaming 公开描述只说 "prune stale memory"，未量化每条 playbook 的边际贡献。
2. **多 sub-result 聚合 guardrail 的 4 轮迭代过程**：v2.3 → v2.7 (merged) → v2.8 (per_sub_aggregated) → v2.9 (policy regex fix) 完整研究弧线公开记录，工业界没人公开过类似细节。
3. **三轮假设证伪 + 第四次命中**的研究方法学闭环（c21 Exp D + v2.x multi_intent 链）：从 KB→critic+PII→groundedness→policy regex 多次暴露认知偏差过程，比"我做了 A 涨了 X"更有说服力。

## 七、距离"真正最先进"还差什么

| 维度 | 怎么补 | 工作量 | 真实流量必需性 |
|---|---|---|---|
| **真实生产流量** | 接 Zendesk/Intercom 真账户 | 需公司支持 | ⭐⭐⭐⭐⭐ |
| **大规模记忆压测** | Exp G with 10k+ pre-populated cases | 1 天 + ~$1 | ⭐⭐⭐ |
| **Voice agent** | tau3-bench voice domain + STT/TTS | 1 周 | ⭐⭐ |
| **Computer/Browser use** | Anthropic Computer Use API | 2 周 | ⭐⭐ |
| **真实 A/B 灰度** | LaunchDarkly / 自建灰度 + on-call | 3-5 天 | ⭐⭐⭐⭐ |
| **Skills 真发布 ClawHub** | OpenClaw 社区流程 | 2 天 | ⭐ |
| **Langfuse 真起 docker + 推 trace** | docker-compose up + replay | 半天 | ⭐⭐ |
| **多语种/多市场** | i18n + 本地化 KB + 文化适配 | 2 周 | ⭐⭐⭐ |

## 八、关键实验数字汇总（可被招聘官当场验证）

| 实验 | 关键数字 | 文件 |
|---|---|---|
| 合成进化曲线 (full) | 解决率 34.2% → 71.1% | `experiments/metrics.json` |
| τ² retail (DeepSeek-V4-Flash, test=40×trials=4) | pass^1 0.925→0.931 全谱 ≥0 | `experiments/tau2/retail_results.json` |
| τ² airline (test=20×trials=4) | pass^2 +0.008 / pass^3 +0.012 一致性提升 | `experiments/tau2_airline/airline_results.json` |
| APR-CS 合成 cf_weighted | 解决率 100% / avg_tips 2.75（vs naive 75%/10） | `experiments/apr_cs/` |
| 500 工单压测（原始） | escalation 85.2% / p95 5.2s | `experiments/stress_test/load_summary.json` |
| Exp D (LLM-judge groundedness) | escalation 93→67%、normal_easy 6→40.5% | `experiments/stress_test_expanded/exp_d/` |
| **Exp E_v4 (v2.9 最终)** | **escalation 33.2% / multi_intent 55.3% / safety preserved** ⭐ | `experiments/stress_test_expanded/exp_e_v4/` |
| 记忆膨胀拐点 | knee = 1000 cases | `experiments/stress_test/memory_points.jsonl` |

## 九、何时使用本架构

**适合**：
- 单一产品线/单一域客服 agent
- 高频反馈密集 + 错误可量化（DB-state / 工单状态可追踪）
- 有人工兜底 + 合规/审计是 dealbreaker（金融/法律/医疗）
- 月工单量 ≥ 1000，足够形成经验池

**不适合**：
- 跨域通用 agent（这是 AGI 路线，不是这条线）
- 单次问答无反馈场景
- 监管禁微调的医疗诊断（自由度太低）
- 月工单量 <1000（经验池起不来）

## 十、参考路线图

- [`ROADMAP_V2.md`](ROADMAP_V2.md) — v2.x 6 项原始路线图
- [`NEXT_PHASE_P1.md`](NEXT_PHASE_P1.md) — P1 LLM-judge groundedness 设计
- [`apr_cs_innovation.md`](apr_cs_innovation.md) — APR-CS 算法对接 2026 工作脉络
- [`project_design.md`](project_design.md) — v1.x 设计文档
- [`production_architecture.md`](production_architecture.md) — 生产部署拓扑
- [`PROJECT_NARRATIVE.md`](PROJECT_NARRATIVE.md) — 综合资深视角全景叙事
- [`PROJECT_VS_INDUSTRY_2026.md`](PROJECT_VS_INDUSTRY_2026.md) — 行业对标
