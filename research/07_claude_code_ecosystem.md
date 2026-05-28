# 07 — Claude Code 生态 / OpenClaw / 2026 Agent Dev Tooling 革命 对客服 Agent 的启发

调研时间: 2026-05-28
范围: Claude Code (Anthropic) + Claude Agent SDK + MCP 生态 + OpenClaw + OpenHands +
Cursor/Windsurf/Aider/Cline + 国内同类 + agent observability/orchestration 工具。
目标: 提炼这一波 dev-tooling 革命可直接迁移到 **self-evolving-customer-support-agent** 的工程模式。

---

## 0. OpenClaw 是什么 — 认定结论

**确认存在, 不是拼写错误。** OpenClaw 是 2025 年 11 月由奥地利开发者 Peter Steinberger 启动、最初叫
**Clawdbot** 的自托管 AI agent, 因 Anthropic 商标投诉先后改名 Moltbot → OpenClaw
("the lobster way 🦞")。

要点 (来自 GitHub openclaw/openclaw, Wikipedia, KDnuggets 2026):
- **本质**: 跑在本机 (单 Node.js 进程 127.0.0.1:18789, 名为 Gateway) 的 personal agent,
  把 LLM 接到 20+ 种 IM (WhatsApp / Telegram / Slack / Discord / Signal / Feishu / WeChat / iMessage…),
  以**聊天对话**做主 UI, 后端能执行 shell、读写文件、浏览网页、收发邮件。
- **核心抽象**:
  - **SOUL.md** — 每个 agent 的 persona/policy/工具白名单声明 (类似 Claude Code 的 `AGENTS.md`).
  - **Skills (ClawHub)** — 公共 skill registry, 2026-02 已有 13,729 个 skill, awesome-list 5,400+。
  - **Multi-agent routing** — 一个 Gateway 把不同 channel/account 路由到隔离的 sub-agent (workspace + session)。
- **传播数据**: 2025-11 发布, 24 小时 9k star, 2026-02 突破 100k star (KDnuggets/Wikipedia
  口径甚至到 214k–355k, 数字有水分但量级在); 2026-02 Steinberger 加盟 OpenAI 领导
  Personal Agents, OpenClaw 转交独立基金会; 2026-03 中国限制党政机关使用 (安全顾虑)。
- **License**: MIT。GitHub: <https://github.com/openclaw/openclaw>。

**为什么对我们重要 (客服 agent 视角)**:
1. OpenClaw 验证了 "**IM-first agent**" 路线 — 不做 web UI, 把已有的 WhatsApp/微信/飞书当
   inbox, 这正是 B 端客服的实际形态。
2. SOUL.md + ClawHub Skills 是 "**社区贡献 playbook**" 的现成模板, 可对标本项目
   `governance/playbook` 模块的发布/审计流程。
3. Multi-channel routing → 一个 Gateway, 不同 tenant/skill 隔离 — 与我们多租户客服需求同构。

> 如果用户原意不是 OpenClaw, 备选 3 个最可能的误指: ① **OpenHands** (软件工程 agent, ex-OpenDevin,
> 65–72k star), ② **OpenAI Operator / Open Operator** (browser/computer use agent), ③ **OpenManus**
> (国产 Manus 复刻)。本报告默认按 OpenClaw 来写。

---

## 1. Claude Code (CLI + Agent SDK) 在 2026 H1 的能力地图

来源: code.claude.com docs / Anthropic engineering blog / VentureBeat (Tasks 发布) /
DEV/Medium 多份独立 walkthrough。

| 能力 | 何时引入 | 一句话说明 |
|---|---|---|
| **Skills** | 2025 Q4 起规模化 | Markdown + 资源文件夹, 两类: Capability Uplift (赋能力, 如 PDF/browser) / Encoded Preference (定流程, 如 NDA review). 比 system prompt 更结构化, 比 fine-tune 更便宜。 |
| **Subagents** | 2025 Q4 | `.claude/agents/*.md` (project) 或 `~/.claude/agents/*.md` (user), 各有独立 context window, 主 agent 只看 summary, 解决 context 污染。 |
| **Hooks** | 2025 Q4 起持续扩到 25 个 lifecycle 点 | 关键阻断点: `UserPromptSubmit` / `PreToolUse` / `PermissionRequest` / `Stop` / `SubagentStop`, 可强制注入 deterministic 检查/审计/合规逻辑。 |
| **Plan Mode + Tasks** | TodoWrite → **Tasks** v2.1.16 (2026-01-22) | Tasks 取代线性 TODO, 支持 DAG 依赖、跨 session 持久化 (`~/.claude/tasks/`)、subagent 协作。 |
| **Background / Async agents** | v2.0.60 (2026 Q1) | 主 session 继续工作, sub-task 后台跑, 完成回写。配合 `/loop` 实现轻量 cron。 |
| **Computer Use 集成** | 2026 Q1 | Claude Code 内可直接调用 desktop/browser GUI, 配 Playwright MCP 即得 headed 浏览器 agent。 |
| **MCP 客户端 + 服务器** | 全程一等公民 | `~/.claude/mcp.json` 注册外部工具/资源, 已成事实标准。 |
| **Agent SDK** (Python/TS) | 2025 末 GA, 2026 Q2 推出 **Managed Agents** | 把 Claude Code 的同一套 loop+tools+context-mgmt 暴露为库, agent loop 可托管在 Anthropic 端, 工具执行下沉到 Cloudflare/Modal/Vercel/Daytona/自有 infra。从 2026-06-15 起 SDK 用量与交互用量计费分离。 |
| **Plugins / Harnesses** | 2026 Q1–Q2 | 打包 skills+hooks+subagents+MCP 配置成一个可分发单元 (类似 OpenClaw 的 ClawHub)。 |

参考: <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/agent-sdk/overview>,
<https://code.claude.com/docs/en/agent-sdk/todo-tracking>,
<https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk>,
<https://venturebeat.com/orchestration/claude-codes-tasks-update-lets-agents-work-longer-and-coordinate-across>。

---

## 2. 同类工具生态 (2026-05 快照)

| 工具 | 类型 | 杀手锏 | 与 Claude Code 的差异点 | Star (April-2026 量级) |
|---|---|---|---|---|
| **OpenHands** (ex-OpenDevin) | Cloud + Local 软件工程 agent | Large-Codebase SDK + 并行 agent 协调, SWE-bench Verified 53%+ (Claude 4.5) / 72% (Claude 4 published) | 强在**软件工程多 agent 并行**, 自带 sandbox container | 65–72k |
| **Cursor** | IDE | Composer 2.5, BYOM 全开 | UX 王者, 但 agent 深度弱于 Claude Code | — |
| **Windsurf** | IDE | 自研 SWE-1.5 + Fast Context + Codemaps, 已被 Devin (Cognition) 集成 | 直接对标 Cursor, 价更低 | — |
| **Aider** | OSS CLI | 每次 AI 改动 = 一个 git commit, 全模型可选 | 极简, 无 skills/hooks 抽象 | ~35k |
| **Cline / Roo Cline** | VS Code 插件 | OSS BYOM (含 Ollama 本地), MIT | 插件路线, 不做 CLI/SDK | Cline ~40k |
| **Continue** | VS Code/JetBrains 插件 | OSS, 企业可控 | — | ~25k |
| **Gemini CLI / Antigravity 2.0** | Google CLI | Gemini 3.5 Flash, 大 context | 与 Claude Code 同构, 走 Google 模型栈 | — |
| **OpenClaw** | IM-first personal agent | 20+ IM channel, SOUL.md, ClawHub skills | UI 是聊天软件而非 IDE | 100k+ (2026-02) |
| **Hermes Agent** (Nous Research) | Self-improving agent | 5 Pillars: Memory / Skills / Soul / Crons / Self-Improvement, multi-memory (semantic+episodic+working) | 明确把"自进化"做成产品 | 22k+ (2026-02 起飞) |
| 国内: **Trae** (字节) / **CodeBuddy** (腾讯) / **通义灵码** (阿里) / **MoonshotCoder** | 商业 IDE/插件 | 本土模型 + 私有化 | 中国客户/数据合规优势 | — |

数据来源: artificialanalysis.ai/agents/coding, DEV 30+ CLI map,
<https://github.com/Zijian-Ni/awesome-ai-agents-2026>。

---

## 3. 这一波带火的 9 个技术点 (附实现 + 客服 agent 引入方式)

### T1. **Skill 抽象**(Markdown + 资源包, 可复用、可分发)
- **带火者**: Claude Code Skills (2025 Q4) → OpenClaw ClawHub (13,729 skills) → Hermes Agent
- **本质**: 把"做某类事的提示词 + 必需文件 + 工具白名单 + 触发条件"打包成一个目录,
  agent 按需懒加载, 不污染主 context。
- **代表 repo**:
  - <https://github.com/VoltAgent/awesome-openclaw-skills> (5,400+)
  - <https://github.com/mergisi/awesome-openclaw-agents> (162 模板)
  - <https://github.com/anthropics/claude-agent-sdk-python>
- **客服 agent 引入**: 我们现在的 `evolution/playbook` 已是同源思想 (从 trace 学到 → playbook), 但
  缺**懒加载 + 触发条件 + 分发协议**。
  - 改造: `playbook/*.md` 增加 YAML frontmatter `triggers: ["intent=refund", "tier=enterprise"]`,
    路由层只把命中的 playbook 注进上下文, 避免长 prompt 把 normal_easy 拖垮 (Exp C 23%→6.5% 退化可能正是 prompt 噪声)。
  - 文件命名对齐 `skills/refund-eu-vat/SKILL.md` 这种 Claude Code 风格, 便于以后用社区工具评测/分发。

### T2. **MCP (Model Context Protocol) 全面普及**
- **2026 现状**: 97M monthly SDK downloads (vs launch 100k), 9.4k–17k MCP server, 78% 企业 AI 团队
  有 ≥1 MCP-backed agent 上线 (digitalapplied/WorkOS 2026 调研)。Claude / ChatGPT / Gemini /
  Cursor / Windsurf / Zed / JetBrains / Vercel AI SDK / OpenAI Agents SDK 全部原生支持。
- **客服侧关键 MCP**:
  - **Zendesk MCP** (Zendesk Relate 2026 官方发布, both client + server) —
    <https://www.zendesk.com/marketplace/apps/support/1191848/mcp-server/>
  - **Salesforce Agentforce MCP** — <https://www.salesforce.com/agentforce/mcp-support/>
  - Composio Zendesk MCP, Merge Zendesk MCP, Peliqan 跨系统 (Zendesk + Salesforce + Stripe + Pipedrive)
- **客服 agent 引入**:
  - 把现在 `src/seagent/tau2_ext` 里硬编码的工具调用换成 **MCP client**, 即可零代码接入
    真 Zendesk / Salesforce / Intercom, 让 demo 不再只跑合成 τ²-bench。
  - 自己也 export 一个 **MCP server** (`seagent-mcp`), 把 `playbook lookup` / `groundedness check` /
    `escalate` 暴露给外部 agent (e.g. 让 Claude Code 当人工坐席的 copilot 直接调我们)。

### T3. **Hooks (lifecycle-deterministic 插桩)**
- **带火者**: Claude Code 25 个 hook 点; OpenHands 也有 pre/post-action 回调。
- **意义**: 把"模型可能错的事"(PII redact / 合规黑名单 / 配额) 从 prompt 提示**降级到代码强制**。
- **客服 agent 引入**: 我们的 `guardrails/` 已是 hook 思想, 但调用是**硬编码在 SupportAgent.step()**,
  无法被外部审计/合规团队替换。
  - 抽出 `HookRegistry`, 配 `hooks.yaml`:
    ```yaml
    pre_tool_use:
      - module: seagent.guardrails.pii
      - module: customer.compliance.gdpr  # 客户自带
    post_response:
      - module: seagent.guardrails.groundedness_llm
      - module: seagent.audit.langfuse_trace
    ```
  - 这把 §4i Exp D 想要的 "groundedness LLM judge + 三信号 vote" 自然落到 `post_response` 钩子,
    并且未来可以让企业客户自己塞行业合规钩子, 不改我们的核心代码。

### T4. **Plan Mode / Tasks (DAG 任务分解 + 跨 session 持久化)**
- **带火者**: Claude Code v2.1.16 (2026-01) Tasks 取代 TodoWrite, 支持 DAG/依赖/持久化。
- **直接对应我们的痛点**: senior_review.md 里 "**multi_intent / multilingual 几乎全转人工**" — 这正是
  缺一个 task 分解器。一条工单 "退款 + 改地址 + 投诉物流" 必须能拆成 3 个 sub-task 各自走对应 playbook,
  再用 supervisor pattern 合并回复。
- **客服 agent 引入**:
  - 在 `agent/` 新增 `task_planner.py`, 复刻 TaskCreate/TaskUpdate/TaskList 4 个工具, 让 LLM 自主拆。
  - DAG 节点 ↔ sub-agent (subagent 模式), 每个 sub-agent 只看自己 sub-task + 对应 playbook, 主 agent
    最后做 stitching。
  - 期望对 multi_intent 解决率从 0% → 30–50%, ROI 很高。

### T5. **Subagent 模式 (隔离 context, 只回 summary)**
- **带火者**: Claude Code subagents, OpenHands 并行 agent。
- **客服引入**: 退款专家、物流专家、技术支持专家、合规审查者 — 每个是一个 subagent, 主 supervisor
  做 dispatch。HCLTech 报告**动态 handoff 让 case resolution 快 40%** (beam.ai 2026)。
  - 与 T4 的 task DAG 自然组合: 每个 task node 由对应 subagent 执行, supervisor 处理 handoff。
  - 注意 "infinite handoff loop" 是已知坑 — 配 hook 限制最大 handoff 次数。

### T6. **Background / Long-running tasks (agent dreaming)**
- **带火者**: Claude Code v2.0.60 background agents + `/loop` cron + Anthropic Managed Agents。
- **客服引入**: 夜间离线**复盘**当天 trace, 自动跑 `evolution/playbook` 更新; 这其实是我们的
  自进化闭环, 但目前是手工触发, 应该用 cron + background task 自动化。
  - 直接 `from claude_agent_sdk import ...` 把"复盘 agent"挂成 background, 每晚 02:00 触发,
    新 playbook 进灰度池, 48 小时后无 alarm 才推全量 (governance/release 模块已有 gating)。

### T7. **Cost-aware model routing / cascade**
- **2026 数据**: 80/20 路由 (cheap handle 80%, frontier handle 20%) 可减少 45–85% 成本而保 95% 质量
  (tianpan.co, maviklabs)。
- **典型 OSS**: RouteLLM, Martian, Portkey, OpenRouter; 商业: Mindra, Maxim AI。
- **客服引入**: 我们当前所有 turn 都用一个模型, 但客服请求长尾极重 — "你好" 用 Haiku/Qwen-7B 足够,
  退款条款仲裁才需 Opus。
  - 在 `llm/` 加 `router.py`: confidence-cascade, 先 cheap 跑 + critic 判分, 不达标再升级。
  - 与 §4i 的 critic / groundedness / policy 三信号 vote **天然兼容** — 升级触发条件直接复用 vote。
  - 期望生产成本下降 40–60%, 这是真要上线的硬需求。

### T8. **Computer Use / Browser MCP**
- **带火者**: Anthropic Computer Use (2024-10) → Claude Code 集成 (2026 Q1) → Playwright MCP server。
- **客服引入**: 真客服后台 (CRM/工单/物流) 多数无 API, 只有 web GUI — 接 Playwright MCP 让 agent 直接
  代客点界面 ("帮我改一下这个工单优先级"), 即可绕过对方系统集成。
  - 但**先做 T2 (MCP) + T1 (Skills) 更经济**; Computer Use 是兜底方案。

### T9. **Agent observability (Langfuse / Phoenix / LangSmith / Helicone)**
- **2026 共识**: 6 个生产级平台: LangSmith, Langfuse, Arize Phoenix, Helicone, Datadog LLM Obs,
  Honeycomb LLM。Phoenix 对 Claude Agent SDK 有 OOB 集成。
- **客服引入**: 我们 `obs/` 还在 print/jsonl 阶段。建议:
  - 默认接 **Langfuse self-host** (Postgres+ClickHouse, MIT) — OpenTelemetry 标准, 不绑框架。
  - 每个 turn 打 trace: prompt / tool calls / critic score / groundedness score / vote / action /
    cost (T7 cascade 决策一并记)。这给后续做 RFT/playbook mining 提供完整数据底座。

### T10. (Bonus) **Self-improving agent 形态化** — Hermes / MemSkill
- Hermes Agent 5 Pillars (Memory / Skills / Soul / Crons / Self-Improvement) 几乎是我们项目的镜像,
  说明这套架构在 2026 Q1 已是 emerging consensus。
- 启示: 把项目 README/PITCH 对齐这五个词的语言 (Soul ≈ playbook policy, Skills ≈ playbook,
  Memory ≈ trace store, Crons ≈ T6, Self-Improvement ≈ evolution loop), 找工作/找投资人时
  message 一致, 不必发明术语。

---

## 4. 针对本项目 (self-evolving-customer-support-agent) 的 Top 5 引入建议 — 按 ROI 排序

约束: 与正在进行的 §4i Exp D (LLM-judge groundedness + 三信号 vote) 路线**不冲突**, 优先选能放大 Exp D 收益的。

| # | 建议 | 借鉴自 | 落地位置 | 工作量 | 与 Exp D 关系 |
|---|---|---|---|---|---|
| **R1** | **HookRegistry 化 guardrail/audit** — `hooks.yaml` + 5 个 lifecycle 点 (`pre_tool_use` / `post_response` / `pre_escalate` / `pre_playbook_apply` / `on_stop`) | Claude Code hooks | `src/seagent/guardrails/__init__.py` + 新 `runtime/hooks.py` | **1.5 天** | **直接放大**: 把 §4i 的 groundedness LLM judge 和三信号 vote 从硬编码挪到 `post_response` hook, 解耦 → 后续企业客户可塞合规 hook 不改核心。Exp D 实验脚本只需改 `hooks.yaml`。 |
| **R2** | **Task Planner (DAG 拆分 multi_intent)** — 4 个工具 TaskCreate/Update/List/Get + supervisor subagent | Claude Code Tasks v2.1.16 + supervisor pattern | 新 `src/seagent/agent/task_planner.py` + 改造 `SupportAgent.step()` | **3 天** | **正交补强**: §4i Exp D 修的是 single-intent 的 groundedness, 但 senior_review §1.2 写明 multi_intent 是另一个 0% 坑。两者合起来才能把 escalation 从 93% 真正打到 ≤60%。R2 是 Exp D 之后下一阶段 (Exp E) 的主要 deliverable。 |
| **R3** | **MCP client/server 改造** — `seagent-mcp` server (export playbook/groundedness/escalate) + Zendesk/Salesforce MCP client | MCP 生态 (97M downloads) + Zendesk/Agentforce | `src/seagent/tau2_ext/` 重构 + 新 `src/seagent/mcp/` | **4 天** | 不直接影响 Exp D 数字, 但让 demo 从 τ²-bench 合成数据**跨越到真业务系统** — 找工作/PoC 时 narrative 完全不同 ("已对接 Zendesk MCP" vs "跑了个合成基准")。 |
| **R4** | **Cost-aware cascade router** — confidence-cascade `llm/router.py`, 复用 critic 分数做升级触发 | RouteLLM / Mindra / Maxim AI | 新 `src/seagent/llm/router.py` + 改 `llm/client.py` | **2 天** | **正向**: §4i 的 risk 1 写"groundedness LLM call +30% 成本", router 可以把 cheap turn 的 groundedness 用小模型, 抵消甚至倒赚 — 不让 Exp D 因为成本被否。 |
| **R5** | **Langfuse self-host trace 集成** — OpenTelemetry, 把所有 turn 的 critic/groundedness/vote/cost 写进去 | Langfuse + Phoenix | 改 `src/seagent/obs/` | **1 天** | **强放大**: Exp D 出数据后, 用 Langfuse UI 直接看 "哪类 intent 在哪类 KB 上 groundedness 误杀", 比看 jsonl 快 10×, 加速 §4j+ 的迭代。 |

**累计**: 11.5 工作日, 1 人 ~2.5 周可全部落地。

**不建议立刻做** (待 R1-R5 完成再评估):
- T8 Computer Use / Playwright MCP — 对客服 demo 价值不如 R3 直接接 Zendesk。
- OpenClaw IM gateway — IM 通道当下不是项目瓶颈, 但若 PoC 要进微信生态可考虑。
- R6+ Hermes Agent 风格的多记忆架构 (semantic + episodic + working) — 当前 trace store 够用,
  再做属于过度工程。

---

## 5. 与 §4i Exp D 的整合时序图

```
现在 (2026-05-28)
  └─ Exp D 跑通 (groundedness LLM judge + 三信号 vote)   ← 你正在做
        │
        ├─ R1 (HookRegistry 化)              [拆分依赖,  1.5d]    ← Exp D 同周做, 把 Exp D 的实现直接放进 hook
        ├─ R5 (Langfuse trace)               [观测,      1d]      ← 给 Exp D 数据分析提速
        ├─ R4 (Cost cascade router)          [成本,      2d]      ← 抵消 Exp D 的 +30% 成本
        │
Exp D 数据出来 (escalation ≤60% / normal_easy ≥30%) → 写 §4i README
        │
        ├─ R2 (Task Planner DAG)             [质量,      3d]      ← 解决 multi_intent 0%, 进 Exp E
        └─ R3 (MCP client/server)            [demo,      4d]      ← 对接 Zendesk MCP, 产出真业务 demo
```

---

## 6. 关键参考链接 (按 section)

**OpenClaw**:
- <https://github.com/openclaw/openclaw>
- <https://en.wikipedia.org/wiki/OpenClaw>
- <https://www.kdnuggets.com/openclaw-explained-the-free-ai-agent-tool-going-viral-already-in-2026>
- <https://github.com/VoltAgent/awesome-openclaw-skills>

**Claude Code / Agent SDK**:
- <https://code.claude.com/docs/en/hooks>
- <https://code.claude.com/docs/en/agent-sdk/overview>
- <https://code.claude.com/docs/en/agent-sdk/todo-tracking>
- <https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk>
- <https://github.com/anthropics/claude-agent-sdk-python>
- <https://venturebeat.com/orchestration/claude-codes-tasks-update-lets-agents-work-longer-and-coordinate-across>

**MCP 生态**:
- <https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/>
- <https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol>
- <https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026>
- <https://www.zendesk.com/marketplace/apps/support/1191848/mcp-server/>
- <https://www.salesforce.com/agentforce/mcp-support/>
- <https://www.merge.dev/blog/zendesk-mcp-claude-code>

**OpenHands / 其他 agent 平台**:
- <https://github.com/OpenHands/OpenHands>
- <https://github.com/OpenHands/software-agent-sdk>
- <https://www.openhands.dev/>

**Coding CLI 对比**:
- <https://artificialanalysis.ai/agents/coding>
- <https://dev.to/soulentheo/every-ai-coding-cli-in-2026-the-complete-map-30-tools-compared-4gob>

**Cost routing**:
- <https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades>
- <https://www.maviklabs.com/blog/llm-cost-optimization-2026>
- <https://mindra.co/blog/multi-model-routing-llm-orchestration-2026>

**Observability**:
- <https://laminar.sh/article/2026-04-23-top-6-agent-observability-platforms>
- <https://langfuse.com/faq/all/best-phoenix-arize-alternatives>
- <https://github.com/arize-ai/phoenix>

**Multi-agent orchestration**:
- <https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production>
- <https://www.dataiku.com/stories/blog/agent-orchestration-explained>

**Self-improving / Hermes**:
- <https://www.mindstudio.ai/blog/hermes-agent-five-pillars-memory-skills-soul-crons>
- <https://www.revolutioninai.com/2026/04/how-does-hermes-agent-work-explained.html>
