# Changelog

按时间倒序。所有 v2.x 在 2026-05-28 同日完成，对应 [`design/ROADMAP_V2.md`](design/ROADMAP_V2.md) 全部 6 项。

---

## v2.6 — Langfuse + OTLP trace exporters [`84a5184`] (2026-05-28)

**R5**：把现有 obs JSONL trace 导出到 Langfuse self-host / 任意 OTel-compatible 后端（Phoenix/Datadog/Jaeger）。

- `src/seagent/obs/exporters/{langfuse,otel}.py`：SDK preferred，HTTP fallback，零硬依赖
- `scripts/replay_to_langfuse.py`：把过往 Exp D/E 的 trace 直接 replay 到 Langfuse（不耗 LLM 配额）
- `deploy/langfuse/docker-compose.yml`：full v3 self-host stack（postgres + clickhouse + redis + minio + web + worker）
- 新增 25 测试，**280 passed**

## v2.5 — OpenViking L0/L1/L2 filesystem episodic store [`a063827`] (2026-05-28)

**R3**：借鉴 volcengine/OpenViking (24.8k★, 2026)，把 episodic 改成文件系统层级目录 + 叶子层 BM25。

- `src/seagent/memory/fs_store.py`：L0(topic) / L1(YYYY-MM 或 subtopic) / L2(markdown+frontmatter case)
- 三种 scheme：`topic_date` / `topic_subtopic` / `flat`
- 与 `EpisodicMemory.add/retrieve/__len__` 接口兼容、1k cases retrieve <50ms
- 合成基准 ablation：fs_flat 与 jsonl_episodic **bit-exact 相等**（regression guard）；fs_topic_date 解决率持平 escalation F1 +0.04
- Exp F scaffold 已留下，待 DeepSeek 充值后真跑 τ²-bench airline 验证 +6-12pp（OpenViking 公开自报数）
- 新增 12 测试，**255 passed**

## v2.4 — MCP server 化工具 [`b92da8b`] (2026-05-28)

**R6b**：对标 Claude Code MCP 范式（2026 已 97M 月下载、9k-17k servers，事实标准）。

- `src/seagent/mcp/{protocol,server,client,tools}.py`：JSON-RPC 2.0 over stdio，NDJSON framing，protocol version `2025-06-18`
- 4 个 mocked CS MCP servers：`order` / `user` / `refund` / `handoff`（共 8 tools）
- `with_mcp_tools(agent, toolset)` 装饰函数返回代理 agent，**SupportAgent / SpecialistAgent 源码零修改**
- 零第三方依赖；安装 `mcp` SDK 后自动用 SDK
- 新增 43 测试，**243 passed**

## v2.3 — Subagent + Handoff multi-specialist [`afda93d`] (2026-05-28)

**R2**：借鉴 openai-cs-agents-demo + Claude Code Subagents 范式。**修最大业务硬伤 multi_intent 0%**。

- `src/seagent/multi_agent/{router,specialist,handoff,orchestrator}.py`：IntentRouter (LLM JSON 输出 + brace-balanced extraction + cache + 保守 fallback) / SpecialistAgent (topic-filtered KB) / MultiAgentOrchestrator (fan-out + merge)
- **Exp E 真实数字（DeepSeek-chat，500 mixed tickets，mode='core'）**：

| metric | Exp D | **Exp E** | Δ |
|---|---|---|---|
| **multi_intent res** | **0.0%** | **46.8%** | **+46.8pp** ↑↑ |
| escalation_rate | 67.2% | 36.6% | -30.6pp |
| p50 latency | 5099ms | 4820ms | -5% |

⚠️ caveat: 主 run 用 mode='core' 绕过 guardrail；mode='observed' apples-to-apples run 在 88/500 时 DeepSeek 余额耗尽（HTTP 402）。partial artifacts 保留为 `*.guarded_partial.*`，待充值后重跑。

- 新增 24 测试，**200 passed**

## v2.2 — Skills 化 playbook [`6746782`] (2026-05-28)

**R6a**：对标 Claude Code Skills + OpenClaw ClawHub 范式（13k+ 社区 Skills 生态）。

- `src/seagent/skills/{format,store,manifest}.py`：Skill dataclass + markdown/frontmatter parser + 双向 `Playbook↔Skill` 转换（lossless round-trip 已测）
- `ProceduralMemory` 加可选 `skill_store=None` 参数，**default 严格等价旧 jsonl 行为**
- `data/skills/*.md`：9 个示例 skill（从 Reflector 产出转换）+ `manifest.json`
- PyYAML 可选 + fallback 手写 YAML 子集 parser
- 新增 14 测试，**176 passed**

## v2.1 — Lifecycle hooks for guardrail/audit [`868ab41`] (2026-05-28)

**R1**：对标 Claude Code 25-lifecycle hooks。

- `src/seagent/hooks/{types,registry,builtin}.py`：8 个 lifecycle 点（PRE_INPUT / POST_INPUT / PRE_GENERATION / POST_GENERATION / PRE_OUTPUT_GUARD / POST_OUTPUT_GUARD / ON_ESCALATE / ON_BLOCK）+ HookRegistry (priority + exception isolation)
- 3 个 builtin hooks 包装 c21 已有逻辑：LLM-judge groundedness / EscalationVoter (majority/weighted) / audit log
- `SupportAgent` 加可选 `hook_registry=None`，**default 严格等价旧行为，c21 Exp D 数字不动**
- 新增 20 测试，**162 passed**

---

## v1.x 主线（c21 收尾）

- **c21 (2026-05-28)**: Exp D LLM-judge groundedness 验证 §4h 真瓶颈
  - escalation 93% → 67% / normal_easy res 6.5% → 40.5% / multilingual 0% → 53%
  - 实测验证"3 轮证伪→第 4 次成功"研究闭环
- c20 (2026-05-28): P1 LLM-judge scaffold + portfolio (PITCH/RESUME)
- c19 (2026-05-27): PROJECT_NARRATIVE + PROJECT_VS_INDUSTRY_2026
- c18 (2026-05-26): calibration + PII precision (Exp C 证伪)
- c17 (2026-05-24): Bitext KB 扩展 (Exp A/B 证伪)
- c16 (2026-05-23): 500 LLM 生成工单压测
- c15 (2026-05-21): APR-CS adaptive routing + counterfactual scoring
- c14 (2026-05-20): 4 项消融 (噪声反馈 / ROI / 检索方法 / 生产架构)
- c01-c13 (2026-05-07 至 19): 基础架构 → tau2 集成 → 4 层生产加固

## 测试演化

| 阶段 | 测试数 |
|---|---|
| c04 项目初版 | 9 |
| c21 Exp D | 142 |
| v2.1 hooks | 162 |
| v2.2 skills | 176 |
| v2.3 multi-agent | 200 |
| v2.4 MCP | 243 |
| v2.5 fs_store | 255 |
| **v2.6 langfuse** | **280** |
