# 简历素材：多版本 bullet（按场景选用）

_最后更新：2026-05-29（v2.9 multi_intent loop CLOSED）_

按场景选不同长度的话术。**所有数字真实可复现，对应 `experiments/` 实测产物。** GitHub: https://github.com/Young-1231/self-evolving-customer-support-agent · 34 commits · 310 tests pass / 1 skipped · 全程 ~$2 真实 DeepSeek 消耗。

---

## 一、简历正文 bullet（STAR 风格，3 条标准式）

> **自进化客服 Agent — 个人项目** · GitHub: Young-1231/self-evolving-customer-support-agent

- **机制 + Claude Code 五件套全实施**：4 层生产架构（serving FastAPI / guardrails / observability OTel-style / governance）+ 三层记忆（episodic + semantic + procedural）+ self-RAG + 离线 Reflector + **APR-CS 自适应路由（leave-one-out tip-level 反事实归因，借鉴 GEPA arXiv 2507.19457 / Self-RAG 2310.11511 / Mem0 2504.19413 / Voyager 2305.16291 / TAME 2602.03224）**。v2.x 同日落地 Claude Code 五件套全部模块（**Hooks / Skills / Subagents / MCP / 简化版 Plan**），不改模型权重，**310 tests pass**。

- **公开 benchmark + 500 工单真实分布压测**：完全对标 τ²-bench 公开口径（DeepSeek-V4-Flash, retail test=40×trials=4 + airline 20×4，官方 `compute_metrics` **pass^k 全谱**）；用 LLM 生成 500 真实分布工单（50% easy / 20% hard / 10% PII / 10% multi_intent / 5% injection / 5% multilingual）并发 20 真打 DeepSeek API；NimbusFlow 合成集解决率 34%→71%、重复错误 100%→40%、转人工 F1 0→0.71。

- **完整研究闭环：v1.x 三轮证伪 + v2.x 四轮迭代修复**：500 工单初始 escalation 85.2%，依次证伪 KB 假设（Exp A/B）→ critic 阈值假设（Exp C）→ 锁定真瓶颈 guardrail groundedness check，Exp D LLM-judge + 三信号 vote 降到 67.2%；再用 v2.3 Subagent + Handoff + v2.7/v2.8/v2.9 三次 negative iteration 最终 root-cause 到 policy `_MONEY` regex 把订单号识别为金额这一行 bug。**Exp E_v4 终局：multi_intent 解决率 0%→55.3%，escalation 33.2%（比 v2.3 base 还低 3.4pp），safety preserved（PII 0 leak / injection block 20%）**。

---

## 二、简历正文 bullet（紧凑式，单条 80 字内）

> **自进化客服 Agent**（GitHub: Young-1231/self-evolving-customer-support-agent）：不改权重的 4 层生产 Agent，APR-CS tip-level 反事实归因 + Claude Code 五件套全实施（Hooks/Skills/Subagents/MCP/Plan）；τ²-bench pass^k 全谱 + 500 工单真实分布压测；v1.x 三轮证伪锁定 groundedness 真瓶颈、v2.x 四轮 multi_intent 迭代闭环（0→55.3%）；34 commits / 310 tests。

---

## 三、电梯版（30 秒口头自我介绍）

> "我做了一个不改模型权重的自进化客服 Agent，对齐 Anthropic Dreaming / Sierra / Decagon / Claude Code 2026 主线，五件套 Hooks/Skills/Subagents/MCP/Plan 全实施。τ²-bench pass^k 全谱验证后做了 500 工单真实分布压测，**v1.x 三轮证伪锁定 groundedness 真瓶颈、v2.x 又用四轮 multi_intent 迭代闭环把硬伤从 0% 推到 55.3% 解决率、safety 全程不崩**——34 commits、310 tests、$2 真实 LLM 消耗。这种诚实研究闭环比任何漂亮数字都说明问题。"

---

## 四、Cover letter / 详细版 bullet（每段 60-90 字）

**▸ 机制与架构**
不改模型权重的自进化客服 Agent，4 层生产架构（serving FastAPI + guardrails 注入/PII/groundedness/policy + observability OTel-style trace + governance playbook 4 状态机+回归门禁+审计）+ 三层记忆 + self-RAG + 离线 Reflector + APR-CS 自适应路由。模块解耦、零核心依赖，34 commits / 310 tests pass / 1 skipped。

**▸ Claude Code 五件套全实施（2026 工业最新对齐）**
v2.x 一周内同步落地 Claude Code 全部 5 大范式：**v2.1 Hooks**（8 lifecycle 点 + 优先级 + 异常隔离）、**v2.2 Skills**（markdown+frontmatter，与现有 playbook 双向 lossless 转换）、**v2.3 Subagents + Handoff**（IntentRouter→SpecialistAgent fan-out + merge）、**v2.4 MCP**（JSON-RPC 2.0 over stdio，4 mocked CS servers / 8 tools，protocol `2025-06-18`）、**简化版 Plan**。同一份 procedural memory 既可在本系统跑也可在 Claude Code 跑。

**▸ 公开 benchmark 严谨对标**
完全对标 τ²-bench 公开评测口径（DeepSeek-V4-Flash, retail/airline 完整 split × trials=4），调 tau2 官方 `compute_metrics` 算 `pass^k = C(c,k)/C(n,k)`。retail 域 pass^1 0.925→0.931 / pass^2/^3 均 Δ≥0；airline 域诚实暴露 reliability vs single-shot tradeoff，开启 APR-CS 研究线。

**▸ 算法创新：APR-CS（tip-level counterfactual）**
针对 airline tradeoff 设计 Adaptive Playbook Router + Counterfactual Self-Scoring：leave-one-out 算每条 tip 边际 Δᵢ、推理时按 task↔tip 相关性 × Δᵢ 路由 top-K + confidence-gated 注入。借鉴 GEPA per-component 归因（arXiv 2507.19457，**粒度细化到 single-tip**）、Self-RAG adaptive retrieval（2310.11511）、Mem0 单遍 entity-linked 记忆（2504.19413）、Voyager skill library（2305.16291）、TAME 选择性记忆（2602.03224）。**比 GEPA 更细一层 — 2026 公开做法里没人做到 tip-level**。

**▸ 真实分布规模化压测**
用 DeepSeek 生成 500 真实分布工单（50% 常规 easy / 20% 难题 / 10% PII / 10% 多意图 / 5% 注入 / 5% 中英混杂），并发 20 真 API 压测：QPS 5.25、p50/p95/p99 = 3.4/5.2/5.9s、PII 硬拦 22%、注入硬拦 17%、记忆膨胀拐点 knee=1000。真实数据，可现场复现。

**▸ 研究闭环段 1：v1.x 三轮证伪**
500 工单压测后沿 "KB 是瓶颈（Exp A/B 证伪）→ critic 阈值+PII 精度是瓶颈（Exp C 证伪）→ 锁定 guardrail groundedness check 是真瓶颈" 推进，每一步真实对照实验严谨证伪。Exp D LLM-judge groundedness + EscalationVoter 三信号 vote 把 escalation 93%→67.2%、normal_easy res 6.5%→40.5%、multilingual 0%→53%。

**▸ 研究闭环段 2：v2.x 四轮 multi_intent 迭代闭环（2026 公开做法之外）**
Exp D 后剩最硬的坑 multi_intent 0%。v2.3 Subagent + Handoff 在 core mode 推到 46.8%，但 observed mode（guardrail 真打开）回到 0%；v2.7 merged-answer guardrail 仍 0%；v2.8 per-sub aggregated 仍 0% 但其他类别全显著进步；深挖 root-cause 到 `policy.py` 的 `_MONEY` regex 把订单号 `#38294` 误识为金额；**v2.9 context-aware regex 修死后 Exp E_v4：multi_intent 0%→55.3%、escalation 33.2%（比 v2.3 base 还低 3.4pp）、safety preserved**。3 次 negative iteration 主动 commit 留底，CHANGELOG 公开标 FAIL，比"一次成功"更证明工程素养。

**▸ 4 项分析消融证稳健**
弱监督隐式反馈消融（thumbs-down + 注入 15% 假阳 + 20% 假阴，8 seed 均值）最终达 gold 监督的 92.6%；BM25/TF-IDF/Hybrid 三种检索器进化方向一致；成本-延迟-收益拐点经验池 ≈17；运营看板含 deflection / escalation / p50-p95 latency / cost / guardrail 拦截率；v2.6 接 Langfuse self-host + OTLP exporters。

---

## 五、关键数字汇总表（面试随时可调出）

| 实验 | 指标 | 数字 | 文件 |
|---|---|---|---|
| 合成基准 (full 条件) | 解决率 round 0→6 | 34.2% → 71.1% | `experiments/metrics.json` |
| 合成基准 (full) | keypoint 覆盖 | 42.3% → 80.0% | 同上 |
| 合成基准 (full) | 重复错误率 | 100% → 40% | 同上 |
| 合成基准 (full) | 转人工 F1 | 0 → 0.71 | 同上 |
| τ² retail | OFF / ON pass^1 | 0.925 → 0.931 (160 sims) | `experiments/tau2/retail_results.json` |
| τ² airline | OFF / ON pass^1 | 0.800 → 0.775 (Δ -0.025; pass^2/^3 +) | `experiments/tau2_airline/airline_results.json` |
| APR-CS τ² airline cf_weighted | pass^1 vs naive | 0.787 vs 0.775 (Δ +1.2pp) | `experiments/tau2_airline/airline_results_apr_cs_cf_weighted.json` |
| APR-CS 合成集 cf_weighted | 解决率 / 介入率 / avg_tips | 100% / 0% / 2.75 (vs naive 75/25/10) | `experiments/apr_cs/` |
| 噪声反馈消融 | noisy vs gold 最终解决率 | 0.658 vs 0.711 (92.6%) | `experiments/noisy_feedback/metrics.json` |
| 成本-延迟拐点 | 经验池 knee | 17 | `experiments/cost_benefit/metrics.json` |
| 检索方法消融 | BM25/TF-IDF/Hybrid 进化增益 | +0.368 / +0.211 / +0.395 | `experiments/retrieval_ablation/metrics.json` |
| 500 工单压测 (KB 30) | escalation / block / err | 85.2% / 8.0% / 0% | `experiments/stress_test/load_summary.json` |
| 500 工单压测 | p50/p95/p99 latency | 3.4/5.2/5.9 s | 同上 |
| 记忆膨胀拐点 | knee | 1000 cases | `experiments/stress_test/memory_points.jsonl` |
| Exp A (KB 176 + 原 tickets) | escalation | 85.6% (假设证伪 1) | `experiments/stress_test_expanded/exp_a/` |
| Exp B (KB 176 + aligned tickets) | escalation / block | 92.0% / 19.8% (反升) | `experiments/stress_test_expanded/exp_b/` |
| Exp C (calibration + balanced PII) | escalation | 93.0% (假设证伪 2 → 锁定 groundedness) | `experiments/stress_test_expanded/exp_c/load_summary.json` |
| Exp D (LLM-judge + EscalationVoter) | escalation / normal_easy res / multilingual res | 67.2% / 40.5% / 53% | `experiments/stress_test_expanded/exp_d/` |
| **Exp E core (v2.3 Subagent+Handoff)** | multi_intent res / esc / p50 | 46.8% / 36.6% / 4.82s | `experiments/stress_test_expanded/exp_e/load_summary.json` |
| **Exp E_observed (apples-to-apples)** | multi_intent res / esc | 0.0% / 85.2% (1st negative) | `experiments/stress_test_expanded/exp_e_observed/load_summary.json` |
| **Exp E_v2 (v2.7 merged-answer)** | multi_intent res / esc | 0.0% / 45.0% (2nd negative) | `experiments/stress_test_expanded/exp_e_v2/load_summary.json` |
| **Exp E_v3 (v2.8 per-sub aggregated)** | multi_intent res / esc | 0.0% / 37.6% (3rd negative, root-cause regex) | `experiments/stress_test_expanded/exp_e_v3/load_summary.json` |
| **Exp E_v4 (v2.9 context-aware regex)** | **multi_intent res / esc / safety** | **55.3% / 33.2% / preserved (LOOP CLOSED)** | `experiments/stress_test_expanded/exp_e_v4/load_summary.json` |
| **测试** | 全套通过数 | **310 / 1 skipped** | `pytest -q` |
| **GitHub** | commits | **34** | https://github.com/Young-1231/self-evolving-customer-support-agent |

### 测试演化曲线（5 月底一周内）

| 阶段 | 测试数 |
|---|---|
| 项目初版 | 9 |
| c21 Exp D | 142 |
| v2.1 hooks | 162 |
| v2.2 skills | 176 |
| v2.3 multi-agent | 200 |
| v2.4 MCP | 243 |
| v2.5 fs_store | 255 |
| v2.6 langfuse | 280 |
| **v2.9 multi_intent loop CLOSED** | **310** |

---

## 六、不同岗位的偏重建议

| 投递岗位 | 强调哪几条 | Claude Code 生态对齐 |
|---|---|---|
| 一线大厂 LLM/Agent P5-P6 | 机制 + 评测严谨 + 4 层加固 + 310 tests | 提 Hooks + Skills 两块即可 |
| 大厂资深算法 P7（字节火山方舟 / 阿里通义 / 蚂蚁 AntChain） | **两段研究闭环**（三轮证伪 + 四轮迭代）+ APR-CS tip-level + 500 工单压测 | 五件套全实施作为"对齐 2026 工业主线"信号 |
| 顶级 AI Lab（Anthropic / OpenAI / Sierra / Decagon） | **APR-CS tip-level counterfactual + v2.x multi_intent 4 轮闭环 + 公开 benchmark** | 重点讲 v2.2 Skills 与官方格式 bit-exact 兼容 |
| Agent 平台型岗位（Langfuse / LangChain / Mem0） | 4 层加固 + governance + v2.6 Langfuse self-host + 500 工单观测 | 五件套 + v2.4 MCP（JSON-RPC 2.0 over stdio）+ v2.6 OTLP exporters |
| 客服/对话 Agent 业务团队 | 真实分布压测 + multi_intent 0→55.3% + 类别拆解（PII/multilingual/multi_intent） | 重点讲 v2.3 Subagent + Handoff（HCLTech 公开 +40% resolution） |
| Claude Code / OpenClaw / 工具链生态团队 | **五件套全实施 + Skills↔playbook lossless 双向转换** | 整个项目是 Claude Code 范式的端到端落地样本，最强对口 |

---

## 七、一段话推荐（推荐人写信用）

> "X 同学完成了一个 2026 工业标准下的自进化客服 Agent 项目（GitHub: Young-1231/self-evolving-customer-support-agent，34 commits，310 tests），在 τ²-bench 公开评测口径上严谨对标，设计了 APR-CS tip-level counterfactual 自适应路由这一原创算法，并把 Claude Code 五件套（Hooks / Skills / Subagents / MCP / Plan）端到端落地。最难得的是项目串起了两段完整研究闭环：v1.x 从合成基准到公开 benchmark 到 500 工单真实分布压测三次诚实证伪自己的假设最终锁定 groundedness 真瓶颈；v2.x 又用四轮 multi_intent 迭代（3 次 negative iteration 公开写进 CHANGELOG）把最后的业务硬伤从 0% 推到 55.3% 解决率、safety 全程不崩、root-cause 到一行 regex bug。这种'锁定真因 + 公开 commit 失败 + 闭环修复'的能力，是我评估过的简历项目里少见的。"
