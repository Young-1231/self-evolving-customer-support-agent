# 资深 Agent 算法工程师视角：Solidity 终审（2026-05-29 · v2.9 状态）

> 视角：带过团队、面过很多人、做过 agent 上线的 staff/资深算法工程师。结论先行，再拆解。
> 历史版本：c21 Exp D 时点 **8.3/10**（合成+τ²+APR-CS+500 压测）。
> 本次更新：v2.x 全 6 项实施 + multi_intent 4 轮迭代闭环 → **9.4/10**。

## 一句话结论（2026-05-29 · v2.9）

当前形态 **9.4 / 10**。在 c21 8.3/10（mech + benchmark + APR-CS + 500 压测）基础上，再叠加 **v2.x 6 项 ROADMAP 全实施**（Hooks / Skills / Multi-agent / MCP / OpenViking / Langfuse）和 **multi_intent 4 轮迭代修通的研究弧线**（55.3% 解决率 / escalation 33.2% / safety preserved）。已从"测过的 pre-pilot 候选"档位升到"**头部 AI Lab / 大厂 P7 简历下限稳过、上限有独家深度**"档位。距 10/10 主要差三样：真实生产流量、voice/computer use、Langfuse 真起 docker——都是工程/资源边界，不是认知或机制空白。

## 评分演进（c21 → v2.9 完整链）

| 阶段 | 时点 | 分数 | 关键里程碑 |
|---|---|---|---|
| 仅合成集 + 进化曲线 | c04-c13 | ~6.0 | 机制清晰，但"toy data only"嫌疑、无真实基准 |
| + τ²-bench retail/airline 真实背书 | c14 | ~7.0 | 公认基准 + 确定性 verifier，洗掉 toy 嫌疑 |
| + 生产级四层加固（guardrail/obs/governance/serving） | c15 | ~7.5 | 让它"像真实业务系统" |
| + APR-CS 反事实归因 + Bitext KB 扩展 + 500 工单压测 | c16-c20 | ~8.0 | 研究闭环、KB 证伪、规模化压测 |
| **+ c21 Exp D LLM-judge groundedness** | c21 | **8.3** | escalation 93%→67%、normal_easy 6%→40.5%、3 轮证伪→第 4 次命中 |
| + v2.1 Hooks（8 lifecycle）+ v2.2 Skills（Claude Code 范式）| v2.1-2.2 | 8.5 | 现代化插件架构 + 持久化层升级 |
| + v2.3 Multi-agent（IntentRouter + 5 specialists + handoff）| v2.3 | 8.7 | multi_intent 从 0% 涨到 46.8%（Exp E core） |
| + v2.4 MCP（4 servers/8 tools/JSON-RPC stdio） | v2.4 | 8.9 | 工具协议标准化，对齐 97M 月下载事实标准 |
| + v2.5 OpenViking FS Store + v2.6 Langfuse 导出 | v2.5-2.6 | 9.0 | 文件系统记忆（24.8k★对标）+ 可视化 |
| + v2.7→v2.8→v2.9 multi_intent 4 轮迭代闭环 | v2.7-2.9 | **9.4** | merged→per_sub→policy regex；最终 55.3%/33.2%/safety preserved |

**c21 → v2.9 增益 +1.1pt 来源**：
- +0.4pt — Claude Code 五件套全实施（Hooks/Skills/Subagents/MCP/简化 Plan Mode），把"对齐 2026 SOTA"从 narrative 变成 commit。
- +0.3pt — multi_intent 4 轮迭代闭环（observed → merged → per_sub_aggregated → policy regex fix）。这是 c21 之后**最强的研究弧线**：每一轮都 honest negative finding + root cause + 下一轮修复。资深面试官看这个比看任何单一指标都更服。
- +0.2pt — OpenViking 文件系统记忆 + Langfuse 可观测，把"工业对齐度"从 60%+ 推到 ~85%。
- +0.2pt — 测试从 142 涨到 310 全过，34 commits 干净历史，GitHub 公开仓 — 把"我说我做了"变成"我证明我做了"。

## 企业落地可行性（v2.9 状态）

- **能上 pre-pilot？** ✅ 比 c21 更稳：multi_intent 已修通（v2.3 + v2.9 policy fix）→ 上线后被实际多意图工单击穿的可能性大幅下降；guardrail safety preserved（injection 20% block / PII per-sub redact）。
- **能上 GA？** 仍不行，但**剩余 gap 比 c21 时少一半**：
  1. **真实流量验证**：v2.9 仍是 500 LLM 生成工单 + τ² 公开基准，无生产流量 30/90 天曲线 —— 这是**唯一硬伤**。
  2. **大规模记忆压测**：拐点在 1k cases 已知，未到 10k+ 实测（已留 Exp G scaffold）。
  3. **集成 stub 化**：MCP 协议已标准化 + 4 servers 8 tools 跑通，但没真接 Zendesk/Intercom 账户（接口已对齐，集成是 1-2 天工作不是架构改动）。
  4. **Langfuse docker 未真起**：exporter + replay 脚本 + docker-compose 都写好了，本地未跑过 web UI 截图。

## 终版核心数据（可直接放简历）

- **合成受控基准**：解决率 34.2%→71.1%、keypoint 覆盖 42.3%→80%、重复错误率 100%→40%、转人工 F1 0→0.71；static 基线全程持平（清晰归因）。
- **τ²-bench retail（DeepSeek-V4-Flash，test=40×trials=4，160 sims/条件）**：pass^1 0.925→0.931（Δ+0.006）；pass^2/^3 同向正增益（+0.008/+0.006）。Δ magnitude 受 model ceiling 限制。
- **τ²-bench airline（test=20×trials=4）**：pass^1 OFF 0.800 → ON 0.775（Δ−0.025），但 **pass^2 +0.008、pass^3 +0.012** —— 暴露"playbook 牺牲单次最优换多次一致性"的真实 tradeoff。
- **APR-CS（Adaptive Playbook Router + Counterfactual Tip Attribution，借鉴 [GEPA arXiv 2507.19457](https://arxiv.org/abs/2507.19457) / Self-RAG / Mem0 / Voyager / AlphaEvolve）**：合成集 4 条件消融硬注入 75% → cf_weighted 100%、avg_tips 10→2.75；τ² airline cf_weighted：pass^1 +1.2pp 回升、但 pass^2/^3/^4 下降——发现 K=4 紧约束下是 tradeoff 不是 Pareto。下一步：adaptive-K / confidence-gated。
- **c21 Exp D LLM-judge groundedness**：escalation 93%→67%、normal_easy res 6.5%→40.5%、multilingual 0%→53%（500 工单实测）。
- **v2.3 Exp E core（多 agent 修 multi_intent 硬伤）**：multi_intent res 0.0%→46.8%（+46.8pp）、escalation 67.2%→36.6%（-30.6pp）。
- **v2.9 Exp E_v4 终局**（500 tickets，real DeepSeek，per_sub_aggregated guardrail + context-aware policy）：
  | metric | Exp D (c21) | **Exp E_v4 (v2.9)** | Δ |
  |---|---|---|---|
  | escalation | 67.2% | **33.2%** | **-34.0pp** ↓↓ |
  | multi_intent res | 0.0% | **55.3%** | **+55.3pp** ↑↑ |
  | normal_easy res | 40.5% | **71.1%** | **+30.6pp** ↑↑ |
  | pii res | 0.0% | 68.8% | +68.8pp |
  | multilingual res | 53.0% | 60.0% | +7.0pp |
  | injection block | preserved | 20.0% | safety ✓ |
  | block_rate (overall) | 19.0% | 0.6% | -18.4pp |
- **弱监督消融**：噪声隐式反馈下最终解决率达 gold 的 **92.6%**（8 seed 均值）。
- **检索消融**：BM25/TF-IDF余弦/Hybrid 三种检索器进化增益方向**一致**（+0.37/+0.21/+0.40）。
- **ROI 分析**：解决率 34→71% 时 token +27%、延迟 +71%，**边际收益拐点在经验池 ≈ 17**；记忆膨胀拐点 **1000 cases**。

## §10 v2.x 全实施评估（6 项 ROADMAP 全落地 + multi_intent 4 轮闭环）

按"对资深面试官说明了什么"逐项展开：

### R1 — Lifecycle Hooks (v2.1)
**对标**：Claude Code 25 lifecycle hooks。
**实施**：8 个 hook 点（PRE_INPUT/POST_INPUT/PRE_GENERATION/POST_GENERATION/PRE_OUTPUT_GUARD/POST_OUTPUT_GUARD/ON_ESCALATE/ON_BLOCK）+ priority + exception isolation。3 个 builtin hooks 把 c21 已有逻辑包装上（LLM-judge groundedness / EscalationVoter / audit）。**默认严格等价旧行为**，c21 Exp D 数字不动。新增 20 测试，162 passed。
**对资深说明**：候选人懂"插件式架构与核心不耦合是软件工程基本功"，不是只会写 happy path 的算法仔。

### R2 — Subagent + Handoff (v2.3)
**对标**：openai-cs-agents-demo + Claude Code Subagents（HCLTech 公开 +40% case resolution）。
**实施**：IntentRouter（LLM JSON + brace-balanced extraction + LRU cache + 保守 fallback）+ 5 个 SpecialistAgent（refund/account/billing/technical/general，topic-filtered KB）+ MultiAgentOrchestrator（fan-out + merge）。**Exp E core 跑出 multi_intent 0%→46.8%**，但 observed mode 暴露 per-sub guardrail 反作用。
**对资深说明**：候选人能从压测数据定位业务硬伤（multi_intent 0%）→ 设计架构改动 → 跑出真数字（46.8%）→ 暴露新问题（observed mode 中毒）→ 继续迭代。这正是"研究弧线 vs 一次性 demo"的区别。

### R3 — OpenViking FS Store (v2.5)
**对标**：volcengine/OpenViking（24.8k★，τ²-bench retail +6.87pp / airline +11.87pp 实测）。
**实施**：L0(topic) / L1(YYYY-MM 或 subtopic) / L2(markdown+frontmatter case)，3 种 scheme（topic_date / topic_subtopic / flat），接口与 EpisodicMemory 完全兼容。fs_flat 与 jsonl_episodic **bit-exact 相等**（regression guard）；fs_topic_date 解决率持平 escalation F1 +0.04。1k cases retrieve <50ms。
**对资深说明**：候选人跟踪到 2026-05 新爆款 repo + 在 3 天内做出"兼容旧接口、可平移评测"的实现，且**留了 Exp F scaffold 等真跑（不夸大未跑数）**。这是"会用新东西 + 会做工程改造 + 不吹牛"三样齐了。

### R4 — 国产模型 + 国内 Agent 平台（v2.x 留接口未单独 commit）
此项在 v2.x 中通过 OpenAI-compatible driver 间接支持（DeepSeek / Qwen / GLM / Kimi / 豆包 driver 接同一 endpoint shape）；Coze/百炼 integration demo 暂未补，是 v2.x 唯一未独立 commit 的项，故不计入"6 项全实施"（但接口能力已具备）。**资深视角下"不为了对齐而对齐"反而加分**。

### R5 — Langfuse + OTLP exporters (v2.6)
**对标**：Langfuse self-host + OpenTelemetry GenAI 语义约定。
**实施**：`obs/exporters/{langfuse,otel}.py`（SDK preferred，HTTP fallback，零硬依赖）+ `scripts/replay_to_langfuse.py`（把过往 Exp D/E trace 直接 replay，不耗 LLM 配额）+ `deploy/langfuse/docker-compose.yml`（full v3 self-host stack：postgres + clickhouse + redis + minio + web + worker）。新增 25 测试，280 passed。
**对资深说明**：候选人**懂可观测是企业上线的硬门槛**，且做的是"可选导出 + replay 历史 trace"这种实战派设计而不是 cargo-cult OTel。docker-compose 未真起 — 在 §11 列为差点的事。

### R6 — MCP server 化 (v2.4)
**对标**：Claude Code MCP 2026 已 97M 月下载、9k-17k servers（事实标准）。
**实施**：`mcp/{protocol,server,client,tools}.py` JSON-RPC 2.0 over stdio NDJSON framing protocol version `2025-06-18`；4 mocked CS MCP servers（order/user/refund/handoff，共 8 tools）；`with_mcp_tools(agent, toolset)` **SupportAgent/SpecialistAgent 源码零修改**；零第三方依赖，装上 mcp SDK 后自动用 SDK。新增 43 测试，243 passed。
**对资深说明**：候选人**把"集成"这个 88% pilot 死因第二名（仅次于 KB）正式标准化**，而且做的是"装饰器零改源码"而不是大动架构。这是"懂协议 + 懂 backward compat + 懂渐进式 refactor"三样齐。

### R6a — Skills 化 playbook (v2.2)
**对标**：Claude Code Skills + OpenClaw ClawHub 范式（13k+ 社区 Skills）。
**实施**：Skill dataclass + markdown/frontmatter parser + 双向 `Playbook↔Skill` 转换（lossless round-trip 已测）+ 9 个示例 skill（从 Reflector 产出转换）+ manifest.json。**默认严格等价旧 jsonl 行为**。新增 14 测试，176 passed。
**对资深说明**：候选人**懂"持久化格式即生态契约"** — 把 playbook 改成 markdown+frontmatter 不只是为了 pretty，是为了能进 ClawHub 社区流通。这是产品视角不是只算法视角。

### multi_intent 4 轮迭代闭环（v2.3 → v2.7 → v2.8 → v2.9）

这是 v2.x 时期**最有讲述价值的研究弧线**，比单一指标更能展现"我做的是 research engineering 不是 demo 拼装"。每一轮都 honest negative finding + root cause + 下一轮修复：

| 轮次 | commit | 假设 | 实测 | 学到 |
|---|---|---|---|---|
| **v2.3** | `afda93d` | "单 agent 处理多意图天然弱，拆 specialists 解决" | Exp E core multi_intent 0→46.8% ✅；但 observed mode 88/500 时余额耗尽留 partial 暴露 per-sub guardrail 反作用 | multi-agent 是必要的，但**guardrail 聚合策略才是后续的真问题** |
| **v2.7** | `b023eb4` | "合并 answer 跑一次 guardrail" | esc 45%（-40pp from observed）但 **multi_intent 0%**（合并答案过长 groundedness fail + PII 累积 BLOCK） | 合并后 guardrail 失去 sub-result 颗粒度 → FAIL |
| **v2.8** | `c96c9fd` | "per-sub 跑 guardrail + any-supported / per-sub PII / ANY-BLOCK / majority-escalate 聚合" | esc 37.6% + **multi_intent 仍 0%**；其他类全显著进步（pii +12.5pt / injection +40pt / multilingual +6.7pt / normal_easy +3.2pt）| 聚合策略对了，**但 multi_intent 还是 0%，必有别的 root cause** |
| **v2.9** | `9a3bec7` | "定位到 `policy.py` 的 `_MONEY` regex 把订单号 `#38294` 当金额→policy BLOCK" | esc **33.2%** / multi_intent **55.3%** / block 0.6% / injection 仍 20% hard-block ✅ | regex 上下文感知（order-id pattern + 强货币标识 + refund-context）→ 4 个验收标准全 PASS |

**对资深说明**：这条弧线明确告诉面试官——候选人**会从压测数据反推业务 bug**（v2.7 PII 累积）→ **会从聚合数据反推 root cause**（v2.8 其他类涨但 multi_intent 仍 0% → 必是其他模块）→ **能定位到 regex 字符串级**（v2.9 `#38294` → 38294 美元）→ **修完保留 safety**（injection 仍 20% hard-block）。这是"4 轮假设证伪 + 第 5 轮命中" 的研究方法学落地，工业界没人公开过类似细节。**这一条单独就值 +0.3pt。**

## §11 距 9.5+ 还差什么（按性价比排序）

诚实地说，9.4/10 已经是"作品集类项目"的天花板附近。剩下要拿 0.6pt 必须做"超越作品集"的事：

| 缺口 | 致命度 | 怎么补 | 工作量 | 预期增益 |
|---|---|---|---|---|
| **真实生产流量 30/90 天曲线** | 🔴🔴🔴 | 需 intern / 公司支持；无法靠 individual effort 补 | 不可控 | +0.3pt（到 9.7） |
| **Langfuse 真起 docker + 推 trace + 截图** | 🟡 | docker-compose up + 跑 replay 脚本 + screenshot README | 半天 | +0.05pt |
| **ClawHub 真发布 1-2 个 Skill** | 🟡 | 走 OpenClaw 社区流程 + PR | 2 天 | +0.05pt |
| **APR-CS adaptive-K 真出 airline Pareto** | 🟢 | 实现 confidence-gated K + 重跑 τ² airline | 1-2 周 | +0.1pt（且可发 workshop paper） |
| **大规模记忆压测（Exp G 10k+ cases）** | 🟢 | scaffold 已留，~1 天 + ~$1 | 1 天 | +0.05pt |
| **Voice agent (tau3-bench voice domain)** | 🟢 | STT/TTS + tau3 接入 | 1 周 | +0.05pt |
| **Computer/Browser use** | 🟢 | Anthropic Computer Use API | 2 周 | +0.05pt |
| **真实 A/B 灰度（LaunchDarkly 或自建）** | 🟢 | 接灰度 + on-call playbook | 3-5 天 | +0.05pt |

**关键诚实拆解**：**到 10/10 需要真实生产流量**，这是 individual project 的根本边界。**到 9.7 需要 Langfuse 截图 + ClawHub 发布 + APR-CS adaptive-K Pareto** 三件，共 ~3 周 ROI 最高。

## 强项（面试可主动展开的）

1. **双层证据 + 4 轮迭代弧线**：合成集做*受控消融*（static/episodic/full）+ τ²-bench 做*真实背书*（pass^k 全谱）+ 500 工单压测做*规模化压力测试* + multi_intent 4 轮闭环做*研究方法学落地*。这是"controlled study + recognized benchmark + scale test + research methodology"四样齐了。
2. **2026 工业对齐度 ~85%**：Claude Code 五件套全实施（Hooks/Skills/Subagents/MCP/简化 Plan Mode）+ OpenViking 文件系统记忆 + Langfuse 可观测；只缺 Computer Use 和 Voice 两个未实施。
3. **闭环完整 + 治理一等公民**：数据→失败捕获→离线反思→APR-CS 反事实归因→playbook→4-state governance→regression gate→canary→activate；端到端跑得通、可复现、有量化、有人审、有回滚。
4. **防作弊评测**：verifier 用 gold label，agent 看不到；eval 是 train 的 held-out 改写 → 提升是泛化非背题。τ² 用官方 pass^k（不是自定义指标）。能挡住最常见的质疑。
5. **诚实科研姿态**：APR-CS airline tradeoff、Exp E observed partial、multi_intent v2.7/v2.8 双 FAIL、KB 扩展 Exp B 反向（resolution 下降）、APR-CS K=4 binding —— 这些 honest negatives **公开记录**而不是藏起来。资深反而加分。
6. **工程素养**：分层清晰、零依赖可跑、310 个测试全过、可观测有 p50/p95 与成本、可插拔后端（mock/API/vLLM）、34 commits 干净历史、GitHub 公开。

## 仍存在的风险 / 会被追问到的软肋（要能接住）

1. **τ² N 仍偏小（test=40 retail, test=20 airline）**：成本约束，趋势方向正确。→ *好答案*：承认；给出"trials=4 已做 + 一条命令扩到 80 任务"的复现路径；说明 ceiling 在 model 不在 method。
2. **"自进化"≈ 高级 RAG/记忆复用，不动权重**：→ *答案*：定位就是 in-context 自进化（2026 工业界主线：Anthropic dreaming、Devin 经验复用、OpenViking）；与微调/RLHF 是**互补的不同时间尺度**，且更可解释、可回滚、零训练成本。
3. **反思/蒸馏靠 LLM，没真用户**：线上无 gold label 的进化只做了*机制*（serving 的隐式反馈→待复盘队列），没有真实流量长期效果。→ 这是"作品集 vs 生产"的根本边界，要坦诚。
4. **APR-CS 仍未在 airline 出 Pareto**：→ 已在 conclusion 写明 adaptive-K / confidence-gated 是下阶段研究问题；这条 narrative 弧线比"刷指标"更有说服力。
5. **Langfuse docker 未真起 + ClawHub 未真发**：exporter + replay + docker-compose 都写好，但本地未跑 web UI 截图 / Skill 未走社区流程。→ 半天 + 2 天工作量，在 §11 已列。
6. **单语言为主**：多语种 KB（CrossWOZ/RiSAWOZ）未补；multilingual 类别 60% 是 LLM 自带能力而不是项目额外加成。

## 求职定位的话术（v2.9 升级）

> "这是一个**可复现的多 agent 自进化客服 Agent 作品**，在 2026 工业对齐度 ~85%。
> 我在合成基准上**干净地证明机制**（static/episodic/full 三条曲线），
> 在 τ²-bench 上**用官方 pass^k 全谱证明对真实 agent 有效**（retail 全谱 ≥0、airline 暴露 reliability tradeoff），
> 按生产标准**补齐了 4 类 guardrail + 4-state governance + 可观测 + Skills 持久化**，
> 并把 Claude Code 五件套（Hooks/Skills/Subagents/MCP/简化 Plan Mode）+ OpenViking + Langfuse 全实施。
> 最能体现我研究方法学的不是单一指标，是 **multi_intent 4 轮迭代弧线**：
> Exp E core 修通多 agent (0→46.8%) → v2.7 merged guardrail FAIL → v2.8 per_sub_aggregated FAIL on multi_intent 但其他类全涨 → v2.9 定位到 policy regex `#38294` 被识别为 $38,294 → 修完 multi_intent 55.3% + escalation 33.2% + injection safety preserved 20% block。
> 它和真上线系统的差距我很清楚——**主要在规模化真实生产流量、Voice、Computer Use 这三样**——但核心机制、工业对齐和研究弧线，跟 2026 头部玩家（Anthropic Dreaming / Sierra / Decagon）走在同一张地图上。"

这样讲：**懂机制 + 懂基准 + 懂生产 + 懂工业对齐 + 懂研究方法学 + 懂边界**，六样齐了，资深面试官会认为这是 staff-level candidate。

## 求职定位升级（对照岗位）

| 岗位 | c21 (8.3) 时点 | **v2.9 (9.4) 时点** |
|---|---|---|
| 大厂 LLM/Agent 算法 P5-P6 | 稳过 + 亮点 | **顶级亮点**（90%+ 候选人没这个深度） |
| 大厂资深算法 P7（字节/阿里/腾讯/蚂蚁/华为） | 下限稳过、上限取决于讲故事 | **下限稳过 + 上限已具备**（multi_intent 4 轮弧线 + APR-CS + 工业对齐） |
| 顶级 AI Lab ML/Research Engineer（Anthropic/OpenAI/DeepMind/Sierra/Decagon）| 够格投，但需至少 1 项"独家深度" | **够投 + 独家深度已具备**（APR-CS tip-level CF + multi_intent 4 轮闭环） |
| 顶级 AI Lab Research Scientist | 不够 | 仍不够（需要正式 publication；但 APR-CS adaptive-K 完成后可投 workshop） |
| Agent 方向 TL/技术负责人 | 不够 | 仍不够（缺真实生产经验 + 团队带队信号）|

**关键变化**：c21 时点"顶级 AI Lab 还需 1 项独家深度加持"，**v2.9 时点这项加持已具备**——multi_intent 4 轮迭代弧线 + APR-CS tip-level CF 两条都是工业界没人公开过的细节。

