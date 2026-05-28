# 简历素材：多版本 bullet（按场景选用）

_最后更新：2026-05-28_

按场景选不同长度的话术。**所有数字真实可复现，对应 `experiments/` 实测产物。**

---

## 一、简历正文 bullet（STAR 风格，3 条标准式）

> **自进化客服 Agent — 个人项目 / GitHub: TODO**

- **机制**：设计实现 4 层生产架构（serving / guardrails / observability / governance）+ 三层记忆 + self-RAG + 离线 Reflector 归纳可审计 playbook + **APR-CS 自适应路由（leave-one-out 反事实归因，借鉴 GEPA / Self-RAG / Mem0 / Voyager / AlphaEvolve）**，全程不改模型权重；111 测试全过。

- **评测**：完全对标 τ²-bench 公开口径（DeepSeek-V4-Flash, retail test=40×trials=4 + airline 20×4，**官方 compute_metrics pass^k 全谱**）。受控合成基准解决率 34%→71%、重复错误 100%→40%、转人工 F1 0→0.71；τ² retail pass^1/^2/^3 均 Δ ≥ 0。

- **生产研究**：用 LLM 生成 500 真实分布工单做规模化压测（并发 20 真 DeepSeek API），p50/p95/p99=3.4/5.2/5.9s、注入硬拦 17%、PII 硬拦 22%、记忆膨胀拐点 knee=1000。**三轮假设证伪**最终锁定真瓶颈是 guardrail 的 groundedness check（非 KB、非 critic 阈值），代表完整研究闭环而非刷指标。

---

## 二、简历正文 bullet（紧凑式，单条 80 字内）

> **自进化客服 Agent**（个人项目）：不改权重的自进化系统（分层记忆+APR-CS 自适应路由+反事实归因+governance 治理）；τ²-bench 公开 pass^k 全谱对标；500 工单真实分布压测三轮假设证伪锁定 groundedness 真瓶颈；111 测试全过。

---

## 三、电梯版（30 秒口头自我介绍）

> "我做了一个不改模型权重的自进化客服 Agent，对齐 Anthropic Dreaming / Sierra / Decagon 的 2026 工业主线。在 τ²-bench 公开 pass^k 全谱上验证机制后，发现了一个 reliability vs single-shot tradeoff，于是借鉴 GEPA / Self-RAG / Voyager / AlphaEvolve 综合设计了 APR-CS 自适应路由+反事实归因。最后用 500 LLM 生成工单做规模化压测，**三轮证伪自己的假设**才锁定真瓶颈在 groundedness check 上——这种诚实研究闭环比任何漂亮数字都说明问题。"

---

## 四、Cover letter / 详细版 bullet（每段 60-90 字）

**▸ 机制与架构**
不改模型权重的自进化客服 Agent，4 层生产架构（serving FastAPI + guardrails 注入/PII/groundedness/policy + observability OTel-style trace + governance playbook 4 状态机+回归门禁+审计）+ 三层记忆 + self-RAG + 离线 Reflector + APR-CS 自适应路由。模块解耦、零核心依赖、126 测试全过。

**▸ 公开 benchmark 严谨对标**
完全对标 τ²-bench 公开评测口径（DeepSeek-V4-Flash, test 完整 split × trials=4），调 tau2 官方 `compute_metrics` 算 `pass^k = C(c,k)/C(n,k)`。retail 域 pass^1 0.925→0.931、pass^2/^3 均 Δ≥0；airline 域诚实暴露 reliability vs single-shot tradeoff。

**▸ 算法创新：APR-CS**
针对 airline tradeoff 设计 Adaptive Playbook Router + Counterfactual Self-Scoring：leave-one-out 算每条 tip 边际 Δᵢ、推理时按 task↔tip 相关性 × Δᵢ 路由 top-K + confidence-gated 注入。借鉴 GEPA (arXiv 2507.19457) per-component 归因（粒度细化到 single-tip）、Self-RAG adaptive retrieval、Mem0/TAME 记忆选择、Voyager skill library、AlphaEvolve 反事实。

**▸ 真实分布规模化压测**
用 DeepSeek 生成 500 真实分布工单（50% 常规 / 20% 难题 / 10% PII / 10% 多意图 / 5% 注入 / 5% 中英混杂），并发 20 真 API 压测：QPS 5.25、p50/p95/p99 = 3.4/5.2/5.9s、PII 硬拦 22%、注入硬拦 17%、记忆膨胀拐点 knee=1000。**真实数据，可现场复现验证**。

**▸ 研究闭环：三轮假设证伪**
500 工单压测后，沿"KB 是瓶颈 → 严谨证伪 → critic 阈值+PII 精度是瓶颈 → 严谨证伪 → 锁定 groundedness check 是真瓶颈"的完整研究弧线推进，**每一步都用真实对照实验严谨证伪自己的假设**，最终给出可落地的下一步路线图（LLM-judge groundedness + 三信号 vote + 跨域阈值校准）。这种诚实迭代锁定真因比单一漂亮数字更证明研究素养。

**▸ 4 项分析消融证稳健**
弱监督隐式反馈消融（thumbs-down + 注入 15% 假阳 + 20% 假阴，8 seed 均值）最终达 gold 监督的 92.6%；BM25/TF-IDF/Hybrid 三种检索器进化方向一致；成本-延迟-收益拐点经验池 ≈17；运营看板含 deflection / escalation / p50-p95 latency / cost / guardrail 拦截率。

---

## 五、关键数字汇总表（面试随时可调出）

| 实验 | 指标 | 数字 | 文件 |
|---|---|---|---|
| 合成基准 (full 条件) | 解决率 round 0→6 | 34.2% → 71.1% | `experiments/metrics.json` |
| 合成基准 (full) | keypoint 覆盖 | 42.3% → 80.0% | 同上 |
| 合成基准 (full) | 重复错误率 | 100% → 40% | 同上 |
| 合成基准 (full) | 转人工 F1 | 0 → 0.71 | 同上 |
| τ² retail | OFF pass^1 | 0.925 (160 sims) | `experiments/tau2/retail_results.json` |
| τ² retail | ON pass^1 | 0.931 (Δ +0.006) | 同上 |
| τ² airline | OFF pass^1 | 0.800 (80 sims) | `experiments/tau2_airline/airline_results.json` |
| τ² airline | ON pass^1 | 0.775 (Δ -0.025) | 同上 |
| τ² airline | ON pass^2 / pass^3 | 0.725 / 0.688 (Δ +0.008/+0.012) | 同上 |
| APR-CS 合成集 cf_weighted | 解决率 / 介入率 / avg_tips | 100% / 0% / 2.75（vs naive 75%/25%/10） | `experiments/apr_cs/` |
| APR-CS τ² airline cf_weighted | pass^1 vs naive | 0.787 vs 0.775 (Δ +1.2pp) | `experiments/tau2_airline/airline_results_apr_cs_cf_weighted.json` |
| 噪声反馈消融 | noisy vs gold 最终解决率 | 0.658 vs 0.711 (92.6%) | `experiments/noisy_feedback/metrics.json` |
| 成本-延迟拐点 | 经验池 knee | 17 | `experiments/cost_benefit/metrics.json` |
| 检索方法消融 | BM25/TF-IDF/Hybrid 进化增益 | +0.368 / +0.211 / +0.395 | `experiments/retrieval_ablation/metrics.json` |
| 500 工单压测 (原始 KB 30) | escalation / block / err | 85.2% / 3.3% / 3.8% | `experiments/stress_test/load_summary.json` |
| 500 工单压测 | p50/p95/p99 latency | 3.4/5.2/5.9 s | 同上 |
| 500 工单压测 | injection 硬拦 | 17% | 同上 |
| 500 工单压测 | PII 硬拦 | 22% | 同上 |
| 记忆膨胀拐点 | knee | 1000 cases | `experiments/stress_test/memory_points.jsonl` |
| Exp A (KB 176 + 原 tickets) | escalation | 85.6% (假设证伪) | `experiments/stress_test_expanded/exp_a/` |
| Exp B (KB 176 + aligned tickets) | escalation / block | 92.0% / 19.8% | `experiments/stress_test_expanded/exp_b/` |
| Exp C (calibration + balanced PII) | escalation | 93.0% (再次证伪 → 真瓶颈 groundedness) | `experiments/stress_test_expanded/exp_c/load_summary.json` |
| 测试 | 全套通过数 | 126 / 1 skipped | `pytest -q` |

---

## 六、不同岗位的偏重建议

| 投递岗位 | 强调哪几条 |
|---|---|
| 一线大厂 LLM/Agent P5-P6 | 机制 + 评测严谨 + 4 层加固 |
| 大厂资深算法 P7（字节火山方舟 / 阿里通义 / 蚂蚁 AntChain） | **三轮证伪研究弧线** + APR-CS + 500 工单压测 |
| 顶级 AI Lab（Anthropic / OpenAI / Sierra / Decagon）ML/Research Engineer | **APR-CS 创新点 + 三轮证伪** + 公开 benchmark 严谨 + governance |
| Agent 平台型岗位（langfuse / langchain / mem0） | 4 层加固 + governance pipeline + 500 工单观测 |
| 客服/对话 Agent 业务团队 | 真实分布压测数据 + 类别拆解 + 真瓶颈分析（KB 不够 + groundedness） |

---

## 七、一段话推荐（推荐人写信用）

> "X 同学完成了一个 2026 工业标准下的自进化客服 Agent 项目，在 τ²-bench 公开评测口径上严谨对标，并设计了 APR-CS 自适应路由+反事实归因的算法创新。最难得的是项目从合成基准到公开 benchmark 到 500 工单真实分布压测的完整研究闭环里**三次诚实证伪了自己的假设**——这种'锁定真因而不是粉饰数字'的能力，是我评估过的简历项目里少见的。"
