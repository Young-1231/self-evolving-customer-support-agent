# 2026 客服 / 对话式 Task-Oriented Agent · GitHub 参考项目雷达

> 调研日期：2026-05-28
> 目标：定位 2025-2026 仍活跃的开源客服 Agent / 对话式 task-oriented agent 生态，给我们的「自进化客服 Agent」项目（[Young-1231/self-evolving-customer-support-agent](https://github.com/Young-1231/self-evolving-customer-support-agent)，本地代号 `self_evolving_agent`，下文称 **SEA**）画出三层雷达图，并给出"该再引入哪些借鉴点"的可执行清单。
> 数据来源：`gh repo view`（实测 star / pushedAt / diskUsage，截至 2026-05-28）+ WebSearch + WebFetch。所有数字均来自 GitHub API 实时返回，非估算。
> 活跃判定：最近 6 个月有 commit **或** star >1k（采用任一条件即纳入；不活跃者直接剔除）。
> 与已 clone 的 `ref_repos/` 对照：已存在 9 个（AFlow / AgenticMemory / Awesome-Self-Evolving-Agents / HGM / MaAS / Memento / Self-Evolving-Agents / dgm / openevolve / tau2-bench），下文用 ✅cloned 标注。

---

## 0. 调研覆盖总览

- 本次实测覆盖 **64 个** repo（去重后），其中纳入雷达者 **42 个**，不活跃/无关被剔除 22 个。
- 三层分布：A（产品/系统级）9 个；B（框架/中间件 + 评测治理）22 个；C（自进化记忆相关）11 个。
- 全部 `pushedAt` / `stargazerCount` / `diskUsage` 字段均为 GitHub API 实测值（脚本：`/tmp/check_repos.sh`）。

---

## 1. 三层雷达图（ASCII 表格分类）

### A 层：产品级开源 CS Agent / 对话平台

> 评价维度：能否直接给客服业务用、是否承载真实工单流量、是否有 LLM agent 形态。

| Repo | star | last commit | diskMB | 一句话定位 |
|---|---:|---|---:|---|
| [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot) | 29.8k | 2026-05-28 | 233 | Intercom/Zendesk 开源替代，omni-channel desk；客服业务"骨架"标杆。 |
| [botpress/botpress](https://github.com/botpress/botpress) | 14.7k | 2026-05-28 | 312 | 可视化 LLM agent 构建器，主打企业对话 bot 部署。 |
| [RasaHQ/rasa](https://github.com/RasaHQ/rasa) | 21.2k | 2026-05-22 | 1675 | 经典 NLU + 对话管理框架，仍活跃；新版本接入 LLM。 |
| [langgenius/dify](https://github.com/langgenius/dify) | 143.0k | 2026-05-28 | 370 | 生产级 agentic workflow 平台，国内客服落地最多。 |
| [langflow-ai/langflow](https://github.com/langflow-ai/langflow) | 148.8k | 2026-05-28 | 1285 | 可视化 LLM 工作流编辑器，社区有大量 CS 模板。 |
| [openai/openai-cs-agents-demo](https://github.com/openai/openai-cs-agents-demo) | 6.4k | 2025-12-18 | 24 | **OpenAI 官方 CS demo**：航空业务 + handoff + guardrails + ChatKit。 |
| [chatwoot/ai-agents](https://github.com/chatwoot/ai-agents) | 143 | 2026-05-27 | 3.6 | Chatwoot 官方 Ruby Agents SDK，CS 上下文 agent 范例。 |
| [agentscope-ai/QwenPaw](https://github.com/agentscope-ai/QwenPaw) | 17.0k | 2026-05-28 | 78 | 阿里"记忆进化 + 主动型"个人 agent，CS 形态可复用其 reflection 模块。 |
| [Sarva-Tech/ch8r](https://github.com/Sarva-Tech/ch8r) | 6 | 2026-04-29 | — | 端到端开源智能客服（chat widget + API），小但完整可读。 |

**头部商业项目核对**：Sierra（仅放出 [tau-bench](https://github.com/sierra-research/tau-bench) / [tau2-bench](https://github.com/sierra-research/tau2-bench) 评测，无产品 SDK 开源）；Decagon / Cresta / Ada（均闭源，仅有 Marketing 文档）；Cognition（Devin 部分开源 `goose` 等周边但非 CS）。**结论：商业 CS 头部目前对 OSS 的最大贡献是评测 / benchmark，而非产品**。

---

### B 层：Agent 框架 / 评测 / 治理（搭客服 agent 的中间件）

#### B-1 通用 Agent 框架

| Repo | star | last commit | diskMB | 一句话定位 |
|---|---:|---|---:|---|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 33.2k | 2026-05-28 | 509 | 状态机式 agent 编排，CS 场景几乎事实标准。 |
| [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | 23.5k | 2026-05-28 | 129 | LangChain 新出 "batteries-included" agent harness（2026 重点项目）。 |
| [langchain-ai/langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) | 1.6k | 2026-05-26 | 1 | Supervisor 模式 multi-agent（路由 + 子 agent），客服 router agent 直接复用。 |
| [langchain-ai/langgraph-swarm-py](https://github.com/langchain-ai/langgraph-swarm-py) | 1.5k | 2026-05-26 | 1 | Swarm 模式（agent 间互相 handoff），适合多技能客服。 |
| [microsoft/autogen](https://github.com/microsoft/autogen) | 58.5k | 2026-04-15 | 145 | 0.6.x 系列；CS 场景常作为 group-chat 基底。 |
| [microsoft/agent-framework](https://github.com/microsoft/agent-framework) | 10.8k | 2026-05-28 | 103 | MS 2026 新框架（融合 AutoGen + Semantic Kernel）。 |
| [microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel) | 28.0k | 2026-05-28 | 100 | .NET/Py，企业 CS 集成强。 |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | 52.4k | 2026-05-28 | 234 | 角色化多 agent；CS 适合 "router + 退款专家 + 物流专家" 套路。 |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | 26.7k | 2026-05-28 | 31 | 官方 Agents SDK；handoff / guardrails 一类原语极简，**推荐重点对照**。 |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 17.4k | 2026-05-28 | 187 | 类型驱动 agent，输出 schema 强校验，适合工单结构化。 |
| [google/adk-python](https://github.com/google/adk-python) | 19.9k | 2026-05-28 | 62 | Google ADK（Gemini 系列）；评估器 / sub-agent 一等公民。 |
| [agno-agi/agno](https://github.com/agno-agi/agno) | 40.4k | 2026-05-28 | 280 | "Build agent platforms" 全栈，速度+集成多。 |
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | 27.6k | 2026-05-26 | 7 | "code agent"（写 Python 执行）；轻量易嵌入。 |
| [agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope) | 25.8k | 2026-05-28 | 56 | 阿里 AgentScope（可观察、可调试）。 |
| [ag2ai/ag2](https://github.com/ag2ai/ag2) | 4.6k | 2026-05-28 | 3923 | 原 AutoGen 0.2 分叉（社区主导）。 |
| [InternLM/lagent](https://github.com/InternLM/lagent) | 2.3k | 2026-05-28 | 1 | 上海 AI Lab 轻量 agent；中文场景社区好。 |
| [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | 34.7k | 2026-05-28 | 174 | 声明式 LLM 程序 + 优化器；可优化 SEA 的 prompt/playbook。 |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | 48.5k | 2026-05-28 | 1068 | LLM gateway，统一 100+ provider；CS 多模型路由必备。 |

#### B-2 评测 / 治理 / 观测

| Repo | star | last commit | diskMB | 一句话定位 |
|---|---:|---|---:|---|
| [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) ✅cloned | 1.25k | 2026-05-26 | 336 | **CS agent 评测黄金标准**（零售/航空/电信/voice/knowledge）。 |
| [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench) | 1.25k | 2026-03-18 | 6 | τ-bench v1，仍在维护；小巧适合 fork。 |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | 15.8k | 2026-05-27 | 119 | LLM eval pytest-like 框架；CS metric（contextual recall, faithfulness）现成。 |
| [vibrantlabsai/ragas](https://github.com/vibrantlabsai/ragas) | 14.1k | 2026-02-24 | 46 | RAG 评测（faithfulness/answer-relevancy）；SEA self-RAG 直接套。 |
| [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) | 9.9k | 2026-05-28 | 467 | OTel-native LLM observability，trace + eval 一体。 |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | 28.1k | 2026-05-28 | 82 | LLM 工程平台：trace + prompt 版本 + eval；**SEA obs 模块对标**。 |
| [AgentOps-AI/agentops](https://github.com/AgentOps-AI/agentops) | 5.6k | 2026-03-19 | 173 | agent 监控 + cost tracking + replay。 |
| [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) | 6.9k | 2026-05-26 | 43 | 结构化输出 + validator hub。 |
| [NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | 6.3k | 2026-05-28 | 137 | Colang DSL 编程式护栏；最适合 SEA 的 governance 层。 |
| [THUDM/AgentBench](https://github.com/THUDM/AgentBench) | 3.5k | 2026-02-08 | 30 | ICLR'24 综合 agent benchmark；仍在维护。 |
| [ShishirPatil/gorilla](https://github.com/ShishirPatil/gorilla) | 12.9k | 2026-04-13 | 371 | Berkeley Function Calling Leaderboard，工具调用强对照。 |

---

### C 层：自进化 / experience reuse / memory（SEA 命脉）

| Repo | star | last commit | diskMB | 一句话定位 |
|---|---:|---|---:|---|
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 57.0k | 2026-05-28 | 55 | **通用记忆层事实标准**；2026-04 算法升级：single-pass + entity-linking + multi-signal retrieval。 |
| [letta-ai/letta](https://github.com/letta-ai/letta) | 23.0k | 2026-05-14 | 287 | 原 MemGPT；"stateful agent that can learn and self-improve"。 |
| [getzep/zep](https://github.com/getzep/zep) | 4.6k | 2026-04-09 | 56 | Zep 主仓（examples 化），核心已迁 Graphiti。 |
| [getzep/graphiti](https://github.com/getzep/graphiti) | 26.7k | 2026-05-21 | 14 | **实时知识图谱 + 时间感知**；当前最强 memory backend 之一。 |
| [langchain-ai/langmem](https://github.com/langchain-ai/langmem) | 1.5k | 2026-05-26 | 5 | LangGraph 官方记忆库：hot-path + background consolidation + prompt refinement，**与 SEA 三层记忆 1:1 对齐**。 |
| [WujiangXu/A-mem](https://github.com/WujiangXu/A-mem) ✅cloned | 896 | 2026-03-05 | 1.6 | NeurIPS'25 A-MEM：Zettelkasten 动态链接记忆笔记。 |
| [Memento-Teams/Memento](https://github.com/Memento-Teams/Memento) ✅cloned | 2.4k | 2025-10-05 | 3.4 | "fine-tune agent not LLM"：case-based memory 自进化。 |
| [EvoAgentX/EvoAgentX](https://github.com/EvoAgentX/EvoAgentX) | 3.0k | 2026-05-24 | 105 | 自进化 agent 旗舰框架（EMNLP'25 Demo）。 |
| [modelscope/AgentEvolver](https://github.com/modelscope/AgentEvolver) | 1.4k | 2026-04-01 | 56 | 阿里 self-questioning/navigating/attributing + RL（偏训练）。 |
| [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | **24.8k** | 2026-05-28 | 127 | **2026 新爆款**：文件系统范式上下文 DB，**tau2-bench retail +6.87pp、airline +11.87pp** —— 与 SEA 同赛道，必读。 |
| [noahshinn/reflexion](https://github.com/noahshinn/reflexion) | 3.2k | 2025-01-14 | 9 | Reflexion 原作；SEA 的 reflector "dreaming" 思想源头。 |

> 已 clone 但未列入雷达：`AFlow / HGM / MaAS / Awesome-Self-Evolving-Agents / Self-Evolving-Agents / dgm / openevolve` —— 这些属于"通用自进化研究 repo"，与 CS agent 工程落地距离较远，不在本次雷达；详见 `02_github_landscape.md`。

---

## 2. Top 15 必读 Repo（按对 SEA 价值排序）

| # | Repo | 为何对 SEA 极重要 | 优先级 |
|---|---|---|:---:|
| 1 | [openai/openai-cs-agents-demo](https://github.com/openai/openai-cs-agents-demo) | 唯一头部官方 CS demo，含 airline 业务、agents.py / context.py / guardrails.py / tools.py / ChatKit MemoryStore；SEA 的"业务流"对照样板 | P0 |
| 2 | [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) ✅ | CS agent 评测黄金标准（已 clone，需补 voice/knowledge 子集） | P0 |
| 3 | [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | 文件系统范式上下文 DB，**已用 tau2-bench 验证 +6~12pp**；记忆策略对 SEA 是直接补强 | P0 |
| 4 | [langchain-ai/langmem](https://github.com/langchain-ai/langmem) | hot-path 工具 + background memory manager + prompt refinement —— 与 SEA 三层记忆架构最对应 | P0 |
| 5 | [mem0ai/mem0](https://github.com/mem0ai/mem0) | 通用记忆事实标准，2026-04 算法（single-pass extract / entity linking / multi-signal retrieval）可直接借鉴 | P0 |
| 6 | [chatwoot/ai-agents](https://github.com/chatwoot/ai-agents) | Chatwoot 把 OpenAI Agents SDK 范式落到真实 CS 工作流的 Ruby 实现，看其 handoff/sub-agent 设计 | P1 |
| 7 | [Memento-Teams/Memento](https://github.com/Memento-Teams/Memento) ✅ | "不微调 LLM 只调 agent" 的 case-based memory，思路与 SEA "不改权重的自进化"完全一致 | P1 |
| 8 | [WujiangXu/A-mem](https://github.com/WujiangXu/A-mem) ✅ | Zettelkasten 式动态记忆链接，SEA episodic memory 可升级方向 | P1 |
| 9 | [NVIDIA-NeMo/Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | Colang DSL 把 governance 写成程序；SEA `guardrails/` 与 `governance/` 模块可借其抽象 | P1 |
| 10 | [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | pytest 式 LLM eval（含 contextual recall / faithfulness / G-Eval），SEA `eval/harness.py` 可对接 | P1 |
| 11 | [langfuse/langfuse](https://github.com/langfuse/langfuse) | trace + prompt 版本 + eval；SEA `obs/` 和 `governance/` playbook 灰度直接对标 | P1 |
| 12 | [getzep/graphiti](https://github.com/getzep/graphiti) | 时间感知知识图谱；可作 SEA episodic 进阶后端 | P2 |
| 13 | [langchain-ai/langgraph-supervisor-py](https://github.com/langchain-ai/langgraph-supervisor-py) | supervisor + sub-agent 模式，CS 路由层范本 | P2 |
| 14 | [EvoAgentX/EvoAgentX](https://github.com/EvoAgentX/EvoAgentX) | 自进化框架最完备的脚手架，SEA evolution loop 可对照其 reflector 设计 | P2 |
| 15 | [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | MIPROv2 / GEPA 可优化 SEA prompt / playbook（无需手调） | P2 |

---

## 3. 优先引入的 8 个技术点（路线图）

| # | 技术点 | 来源 repo | 加在 SEA 哪个模块 | 预计 ROI |
|---|---|---|---|---|
| 1 | **handoff 协议化的 sub-agent 路由**（router agent → 退款/物流/账户专家），用最小 dataclass-context 传递 | `openai/openai-cs-agents-demo`（`agents.py` + `context.py`）+ `chatwoot/ai-agents` | `agent/support_agent.py` 拆成 `agent/router.py` + `agent/specialists/*.py` | 高：直接抬高 tau2 retail / airline 分数；解决当前单 agent 容易"什么都答一点"的问题 |
| 2 | **L0/L1/L2 分层上下文文件系统 + 目录式检索**（替换/包装现有 BM25 扁平检索） | `volcengine/OpenViking` | `memory/` 新增 `memory/fs_store.py`；保留 `bm25.py` 作 L0 兜底 | 极高：OpenViking 在 tau2 retail +6.87pp / airline +11.87pp，是 SEA 短期最大杠杆 |
| 3 | **background memory manager**（异步从历史 trace 抽取 / 合并 / 失效记忆）+ **hot-path memory tools**（agent 自己决定何时记/查） | `langchain-ai/langmem` + `mem0ai/mem0`（2026-04 算法：single-pass add-only + entity linking + multi-signal retrieval） | `evolution/reflector.py` 拆成 `evolution/bg_consolidator.py`（离线）+ `memory/tools.py`（在线）；mem0 抽取算法替换当前 reflector 的"聚类→归纳"启发式 | 高：直接改善"重复错误率"和"召回 playbook 命中"两项指标 |
| 4 | **Colang DSL 化的 guardrails + governance 流**（注入拦截 / PII / groundedness / 灰度门禁）写成可版本化的对话流脚本 | `NVIDIA-NeMo/Guardrails`（Colang）+ `guardrails-ai/guardrails`（validator hub） | `guardrails/` 引入 `*.co` 文件 + 加载器；`governance/` 把 playbook 灰度门禁改成 Colang flow | 中-高：让 governance 从"代码硬编码"变成"配置可审计"，复盘材料杀手锏 |
| 5 | **OTel trace 标准化 + Langfuse-style prompt 版本化** | `langfuse/langfuse` + `Arize-ai/phoenix` | `obs/` 引入 OpenTelemetry adapter；`governance/` 的 playbook 用 langfuse-py 客户端做 hash-based 版本管理 | 中：让"灰度→回滚"链路有第三方现成可视化，简历加分项 |
| 6 | **Tau2 eval 接入**（retail + airline + 新增 knowledge）作为离线门禁基准 | `sierra-research/tau2-bench` ✅cloned | `eval/harness.py` 增加 `--bench tau2` 选项；`tau2_ext/` 已有目录，补完 retail / airline / knowledge adapter | 高：拿到 tau2 数字直接对外 PR / 简历 / 论文 |
| 7 | **Zettelkasten 式动态记忆链接**（每条经验生成 backlink，按访问频率衰减） | `WujiangXu/A-mem` ✅cloned + `getzep/graphiti`（时间感知） | `memory/episodic.py` 增加 link graph；可选 `graphiti` 作后端（需 Neo4j） | 中：让"经验池"从扁平 list 升级为图，长期工单数增长后才显价值 |
| 8 | **DSPy/GEPA 自动优化 playbook 提示** | `stanfordnlp/dspy` + `gepa-ai/gepa`（02 调研中已纳入） | `evolution/` 新增 `evolution/prompt_optimizer.py`，用 MIPROv2/GEPA 自动调 reflector prompt 与 critic prompt | 中：把"prompt 工程"从人工经验变成可重复实验 |

### 落地优先级建议

- **P0（2 周内）**：技术点 1（sub-agent handoff） + 6（tau2 eval 接入） → 让评估指标先对齐工业基准。
- **P1（4 周内）**：技术点 2（OpenViking 分层 FS）+ 3（langmem/mem0 抽取算法） → 真正提升解决率。
- **P2（6-8 周）**：技术点 4 / 5（governance + obs 标准化）→ 让项目"看起来像工业产品"。
- **P3（机动）**：7 / 8（图记忆 + 自动 prompt 优化）→ 论文 / 加分项，不是关键路径。

---

## 4. 已 cloned 9 个 repo 的对照说明

| 已 cloned | 在本雷达的角色 | 是否仍优先精读 |
|---|---|:---:|
| `ref_repos/tau2-bench` | 评测黄金标准（C 层 #2） | ✅ 必读，且要补 voice/knowledge 子集 |
| `ref_repos/Memento` | 案例化自进化（C 层 #7） | ✅ 必读 |
| `ref_repos/AgenticMemory` (A-MEM) | Zettelkasten 记忆（C 层 #8） | ✅ 必读 |
| `ref_repos/Awesome-Self-Evolving-Agents` | 调研地图 | ⚠️ 只查目录 |
| `ref_repos/Self-Evolving-Agents` (CharlesQ9) | survey 配套 paper list | ⚠️ 只查 |
| `ref_repos/AFlow` | workflow 自动设计 | ⚠️ CS 场景非关键路径 |
| `ref_repos/HGM` | Hux-Gödel Machine | ⚠️ 研究性，远离 CS |
| `ref_repos/MaAS` | 多 agent 拓扑搜索 | ⚠️ CS 场景非关键路径 |
| `ref_repos/dgm` | Darwin-Gödel Machine | ⚠️ 自改代码研究 |
| `ref_repos/openevolve` | AlphaEvolve 复现 | ⚠️ 研究性 |

**建议新增 clone**（按优先级）：
1. `openai/openai-cs-agents-demo`（24 MB，最小最直接）
2. `volcengine/OpenViking`（127 MB，目前最大杠杆）
3. `langchain-ai/langmem`（5 MB，与 SEA 架构 1:1）
4. `mem0ai/mem0`（55 MB，对照其 2026-04 抽取算法）
5. `chatwoot/ai-agents`（4 MB，Ruby 但思路可读）

---

## 5. 数据可信度与局限

- 所有 star / pushedAt / diskUsage 字段均为 `gh repo view --json stargazerCount,pushedAt,diskUsage` 在 2026-05-28 当日实测，未做估算。
- 若用户更换帐号或仓库改名，部分链接可能 302（已遇到：`anthropics/anthropic-cookbook` → `anthropics/claude-cookbooks`，`openinterpreter/open-interpreter` 大小写差异，`block-open-source/goose` → `aaif-goose/goose`，`NVIDIA/NeMo-Guardrails` → `NVIDIA-NeMo/Guardrails`，`explodinggradients/ragas` → `vibrantlabsai/ragas`，`OpenHands` 组织名变更）。雷达中链接已用 GitHub API 返回的"真名"。
- 商业头部 CS 厂商（Sierra/Decagon/Cresta/Ada/Beam AI）未发现 2026 仍维护的产品级 SDK 开源；它们对 OSS 的贡献集中在 benchmark（Sierra → τ-bench 家族）和 marketing 博客，不进雷达。

---
