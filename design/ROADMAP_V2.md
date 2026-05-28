# v2.0 路线图：综合三份 2026 调研后的引入清单

_最后更新：2026-05-28_
_数据来源：`research/05_2026_github_radar.md` + `research/06_china_community_pulse.md` + `research/07_claude_code_ecosystem.md`_

## TL;DR

调研 42 个真实 GitHub repo + 23 条中文社区检索 + Claude Code 完整生态后，**v2.0 推荐 6 个引入点**，按 ROI 排序，全部加起来约 2-3 周工作量。最快、最高杠杆的是 **R1 + R6**（共 ~3 天，对简历显著加分）。

---

## 一、参考项目雷达（Top 10 必读）

| Repo | 星量 | 类别 | 对本项目的价值 |
|---|---|---|---|
| **openai/openai-cs-agents-demo** | 6.4k | A 产品级 | 官方 CS demo：handoff 协议 + ChatKit MemoryStore + guardrails，对标 SEA 业务流 |
| **volcengine/OpenViking** ⭐ | **24.8k** | C 记忆 | 2026 新爆款：L0/L1/L2 文件系统上下文，**已在 τ²-bench retail +6.87pp / airline +11.87pp** |
| **mem0ai/mem0** | 57k | C 记忆 | 2026-04 新算法（single-pass + entity linking）可换我 reflector |
| **langchain-ai/langmem** | 1.5k | C 记忆 | hot-path tools + background memory manager，1:1 对齐我 dreaming |
| **sierra-research/tau2-bench** ✅ | 1.25k | B 评测 | 已 clone，2026-05 扩到 voice + knowledge |
| **openclaw/openclaw** | **100k+** | A 渠道 | Peter Steinberger 项目，IM Gateway 接 20+ 渠道，SOUL.md + 13k Skills |
| **Anthropic Claude Code** | — | B 框架 | Skills / MCP / Hooks / Plan Mode / Subagents 5 大范式 |
| **DeepSeek-V4-Flash 文档** | — | 模型 | 国产 tool-calling 强 + 1M context + 便宜 |
| **chatwoot / Botpress** | 10k+ | A 产品 | 国际 CS 平台 OSS，可对接渠道层 |
| **deepset-ai/haystack** | 19k | B 框架 | RAG-first 框架，CS 场景模板 |

---

## 二、v2.0 引入清单（6 项，按 ROI 排序）

### 🥇 R1: Hooks 化 guardrail/audit（1.5 天）

**学自**: Claude Code 25 个 lifecycle hooks（`PreToolUse` / `PostToolUse` / `Stop` / `SubagentStop` / `Notification` …）

**问题**: 现在 guardrail/audit/calibration 都硬编码在 SupportAgent._handle_observed 里。生产里客户要塞自己的合规/审计/日志钩子时只能改源码。

**做法**: 新增 `src/seagent/hooks/` 模块，提供 `HookRegistry` 用 dict-of-list 注册 `pre_input` / `post_input` / `pre_generation` / `post_generation` / `pre_output_guard` / `post_output_guard` / `on_escalate` / `on_block` 等钩子。把 §4i Exp D 的 LLM-judge + EscalationVoter 都改成 hook 实现——**核心代码不动，只是把现有逻辑落到 hook 注册表**。

**ROI**:
- ✅ 简历亮点："Claude Code 风格的 hook-based agent extension"
- ✅ 客户接入零侵入（plugin 模式）
- ✅ 现有 c21 Exp D 数字不动，但叙事升级

**工作量**: 1.5 天 + 2 天单测

---

### 🥈 R2: Subagent + Handoff（专家路由，3 天）

**学自**: openai-cs-agents-demo + Claude Code Subagents（HCLTech 公开数据：case resolution +40%）

**问题**: 我们 500 工单压测里 **multi_intent 解决率 0-10%**——单 agent 处理多意图工单天然弱。

**做法**: 把 `agent/support_agent.py` 拆成：
- `agent/router.py`：用 LLM 做 intent classification → 决定 handoff 给哪个 specialist
- `agent/specialists/{refund.py, account.py, billing.py, technical.py}`：每个专家 agent 只懂自己的域 + 调用自己的工具子集
- `agent/handoff.py`：standard handoff protocol（参考 openai-cs-agents-demo）

**ROI**:
- ✅ multi_intent 解决率预期 0% → 30%+（最大的剩余坑）
- ✅ 对接真实业务 (Zendesk 也有 routing/skill-group 概念，天然映射)
- ✅ 简历："multi-agent orchestration + handoff，HCLTech 验证 +40%"

**工作量**: 3 天（含 multi-agent 测试 + 接入 §4i Exp D 跑 Exp E）

---

### 🥉 R3: OpenViking 风格 L0/L1/L2 文件系统记忆（3 天）

**学自**: volcengine/OpenViking（**24.8k★，τ²-bench retail +6.87pp / airline +11.87pp 实测**）

**问题**: 我们 episodic 是 jsonl + BM25，500 工单压测拐点在 1k cases。OpenViking 用文件系统目录结构当 context store，扩展性好得多。

**做法**:
- 新建 `memory/fs_store.py`：把经验池组织成 `episodic/<topic>/<date>/<case_id>.md`
- 检索时按目录层级缩范围（类似 grep -r）
- BM25 仍可在叶子层用；目录层用 topic 过滤
- 对应 τ²-bench airline 重跑（**预期 pass^1 0.787 → 0.85+**）

**ROI**:
- ✅ 真实 τ²-bench 实测可加 +7-12pp（OpenViking 论文级数据）
- ✅ 记忆膨胀到 10k+ cases 仍可用
- ✅ 简历："采用 OpenViking 文件系统上下文范式，τ²-bench airline +12pp"

**工作量**: 3 天 + Exp F τ² 重跑（1 天）

---

### 4️⃣ R4: 国产模型适配 + 国内 Agent 平台集成（2 天）

**学自**: research/06 国内社区调研——国内招聘最看的差异点

**问题**: 我们已支持 DeepSeek，但没专门的 Qwen / GLM / Kimi / 豆包 driver；也没接 Coze / 阿里百炼。**国内招聘官打开 GitHub 看到一个"只用 OpenAI/DeepSeek"的项目会扣分**。

**做法**:
- `llm/providers/{qwen, glm, kimi, doubao}.py`：4 个国产 driver，全部 OpenAI 兼容端点
- `examples/coze_integration.md` + `examples/bailian_integration.md`：最小 demo 接 Coze 工作流 / 阿里百炼 agent，把我们的 SupportAgent 包成 Coze 插件
- README 加"已接入国产模型与 Agent 平台"小节
- 加一份中文 KB（CrossWOZ 子集或 RiSAWOZ）做对照测试

**ROI**:
- ✅ 国内招聘"差异化加分项"（一线大厂、字节/阿里/腾讯特别看）
- ✅ 工作量小，但故事感强
- ✅ 简历："默认支持国产模型 Qwen/GLM/Kimi/豆包 + Coze/百炼平台集成"

**工作量**: 2 天

---

### 5️⃣ R5: Langfuse self-host trace（1 天）

**学自**: Claude Code 生态 + research/07 推荐 ROI 极高

**问题**: 现在 obs/ 自己写 trace JSONL + dashboard markdown。没有 UI。Exp D 出数据后我用 grep 看 "哪类 intent 在哪类 KB 上 groundedness 误杀"——慢。

**做法**:
- `obs/exporters/langfuse.py`：可选导出（环境变量控制），把我现有的 Trace 结构按 Langfuse SDK 上报
- 提供 `docker-compose.yml` 一键起 Langfuse self-host
- README 加"截图：Exp D 的 trace 在 Langfuse 里长什么样"

**ROI**:
- ✅ 视觉化报告（招聘官看截图比看 JSON 直观 10×）
- ✅ 接 OpenTelemetry GenAI 标准，企业上线必备
- ✅ 简历："Langfuse 全链路可观测，OpenTelemetry GenAI 语义约定"

**工作量**: 1 天

---

### 6️⃣ R6: MCP server 化 + Subagent 之间用 MCP 通信（2 天）

**学自**: Claude Code MCP 2026 已 97M 月下载、9.4k-17k server 数

**问题**: 现在工具（如查工单/查订单/查用户）是硬编码方法。生产里要接真实 Zendesk/Intercom/CRM，每家都要适配。

**做法**:
- 把假设的 CS 工具（query_order / query_user / refund_request / handoff_to_human）做成 **MCP server**（`mcp_servers/{order, user, refund, handoff}.py`）
- SupportAgent 通过 MCP client 调用——任何企业自己接自己的真实 ticketing/CRM 只需符合 MCP 协议
- README："本项目所有工具均符合 Model Context Protocol，可一键接 Zendesk MCP / Intercom MCP / 自家 CRM"

**ROI**:
- ✅ 真实生产部署的最大障碍（集成）瞬间标准化
- ✅ "MCP" 是 2026 简历高频关键词
- ✅ 简历："工具层全部 MCP server 化，pluggable to Zendesk/Intercom/任意 CRM"

**工作量**: 2 天

---

## 三、可选 / 暂不做（B 类）

- **R7: Browser/Computer use** — Anthropic Computer Use API，让 agent 真操作 Zendesk 网页。技术酷但客服场景不刚需，留作 phase 2。
- **R8: OpenClaw IM 接入** — 把 SEA agent 通过 OpenClaw 接到微信/飞书。趣味性强但偏 demo 不偏论文，简历加分有限。
- **R9: Voice agent** — τ²-bench 已扩到 voice 域，但工作量大（需 STT/TTS）。
- **R10: Hermes Agent 风格"自造环境"** — 学术前沿，太重。

---

## 四、组合套餐（按时间预算）

| 时间预算 | 套餐 | 工作量 | 简历影响 |
|---|---|---|---|
| **3 天**（周末） | R1 Hooks + R5 Langfuse | ~2.5d | 中等：架构现代化 + 可视化 |
| **1 周** | R1 + R2 Subagent + R5 Langfuse | ~5.5d | 高：multi_intent 解决 + 视觉化 |
| **2 周** | R1+R2+R3+R5 | ~8d | 极高：τ² +12pp 真数字 + multi-agent |
| **2-3 周** ⭐ | **R1+R2+R3+R4+R5+R6** 全部 | ~12.5d | 顶级：所有调研推荐点都进，"我把 2026 整个 agent 生态都梳理过并实施了" |

---

## 五、最后建议

我推荐你按 **R1 → R6 → R2 → R5 → R3 → R4** 的顺序做（**先架构升级 hooks/MCP，再业务能力 subagent，再观测，再 OpenViking，最后国内适配**）。这样每一步都是上一步的乘数效应。

如果只能选 **1 个先做**，做 **R2 (Subagent + Handoff)** —— 它直接修复 500 工单压测里 multi_intent 0% 这个最大业务硬伤，跑 Exp E 出真数字。

如果只能选 **3 个组合 ROI 最高**，做 **R1 + R2 + R3**（hooks 现代化 + multi-agent 解决业务硬伤 + OpenViking 拿 +12pp 真数字）—— 大约 7-8 天，加完简历能从 8.5/10 推到 9.0/10。
