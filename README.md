# 自进化客服 Agent · Self-Evolving Customer-Support Agent

> 一个**不改模型权重**、靠"分层记忆 + self-RAG + 离线复盘"持续自我改进的客服 Agent。
> 配套**确定性自动评测**，可复现"随服役轮次解决率上升、重复错误率下降"的进化曲线。

这是一个面向「资深 Agent 算法工程师」求职的完整可运行项目。它把 2025–2026 学术界/工业界关于
*self-evolving agent* 的主流落地路线（记忆与经验固化、自动归纳 playbook、self-RAG、可审计可回滚）
浓缩成一个**零第三方依赖即可跑通**的最小但完整的系统。

---

## 1. 它解决什么问题

静态 LLM 客服 Agent 上线后能力是"冻结"的：知识库没覆盖的盲区、口口相传的运营经验、该不该转人工的
判断，它一错再错。2026 年工业界的共识是——**真正落地的"自进化"几乎都不动模型权重**，而是走
「记忆层 + 经验固化 + 离线复盘 + 工具/规则复用」的工程主线（参考 Anthropic 持久记忆 / "Dreaming"
离线复盘、Devin 的跨 session 经验复用）。本项目就是这条路线的一个可复现实现。

核心主张：**让 Agent 从每一次失败的工单里学到东西，并把它变成下一次能被检索到、可被人审计、可被回滚的资产。**

## 2. 系统架构

```
                    用户问题
                       │
        ┌──────────────┼───────────────────────────┐
        ▼              ▼                            ▼
 Semantic Memory   Episodic Memory            Procedural Memory
   (知识库 KB)      (经验池/历史案例)            (playbook 运营规则)
   BM25 检索        BM25 按相似工单检索          按触发词命中、可启用/回滚
        └──────────────┼───────────────────────────┘
                       ▼
              self-RAG 上下文聚合
                       ▼
                LLM 后端生成回答      ── mock / OpenAI 兼容 API / 本地 vLLM
                       ▼
                  Critic 置信度
                       ▼
        转人工决策：可信案例 > 命中规则 > 不确定度阈值
                       ▼
              回答 + 是否转人工 + 证据来源
                       │
  ┌────────────────────┴─────────────────────────────────┐
  │  离线闭环（不改权重）：                                  │
  │  失败工单 + 人工解决方案 ──► 经验池(episodic)            │
  │  Reflector("dreaming") 聚类失败 ──► 归纳 playbook       │
  │  playbook 带版本/开关，人审通过后启用，可一键回滚         │
  └──────────────────────────────────────────────────────┘
```

| 模块 | 文件 | 职责 |
|---|---|---|
| 分层记忆 | `memory/{semantic,episodic,procedural}.py` | KB 检索 / 经验池 / 可审计 playbook |
| 检索 | `memory/bm25.py` | 纯 Python、中文友好的 BM25（字符 bigram） |
| Agent | `agent/support_agent.py`, `agent/critic.py` | self-RAG 推理 + 置信度 + 转人工决策 |
| 进化 | `evolution/reflector.py` | 离线复盘：聚类失败 → 归纳 playbook |
| 评测 | `eval/{verifier,metrics,harness.py}` | 确定性判分 + 三条件消融 + 进化曲线 |
| 后端 | `llm/{mock,openai_backend}.py` | mock / OpenAI 兼容 / vLLM |

## 3. 快速开始（零依赖，离线可跑）

```bash
# 1) 一句话演示：同一道难题，冷启动失败 → 学习后解决（不改权重）
make demo                       # 或 PYTHONPATH=src python -m seagent.cli demo

# 2) 跑完整自进化实验，产出指标 + 报告（+ 进化曲线图，若装了 matplotlib）
make evolve                     # 或 python scripts/run_experiment.py

# 3) 跑测试
make test

# 4) 单轮问答
PYTHONPATH=src python -m seagent.cli ask "我忘记密码了怎么重置"

# 5) 生产路径 demo：guardrails(注入拦截/PII脱敏/groundedness) + 全链路 tracing + 运营看板
python scripts/run_production_demo.py

# 6) 受治理自进化 demo：playbook 提案→人审→灰度→回归门禁→上线/回滚（misevolution 防护）
python scripts/run_governed_evolution.py
```

产物：`experiments/metrics.json`、`experiments/report.md`、`experiments/evolution_curve.png`。

### 接真实 LLM（OpenAI API 或本地 vLLM）

```bash
# 本地 vLLM：先起服务，再指定 OpenAI 兼容端点
python -m vllm.entrypoints.openai.api_server --model Qwen2.5-7B-Instruct --port 8000
python scripts/run_experiment.py --backend openai --model Qwen2.5-7B-Instruct \
    --api-base http://localhost:8000/v1
```
Agent / 进化 / 评测代码与 mock 完全共用，仅后端不同。

## 4. 实验结果（mock 后端，确定性可复现）

三条件消融，eval 集为 held-out 同义改写（同 group、不同措辞），保证提升来自**泛化**而非背题：

| 条件 | 解决率 | keypoint 覆盖 | 重复错误率 | 转人工 F1 | 人工介入率 |
|---|---|---|---|---|---|
| **static**（不进化，基线） | 34.2% → **34.2%** | 42.3%（平） | 100%（平） | 0.00（平） | 2.6%（平） |
| **episodic**（经验池） | 34.2% → **71.1%** | → 76.5% | → 40.0% | → 0.71 | → 26.3% |
| **full**（经验池 + playbook） | 34.2% → **71.1%** | → **80.0%** | → 40.0% | → 0.71 | → 26.3% |

要点：① 不进化基线全程持平，证明提升确由进化闭环带来；② 经验积累把解决率翻倍、重复错误率腰斩、
并从 0 学会转人工策略（F1 0→0.71）；③ playbook 在解决率不回退的前提下进一步提升 keypoint 覆盖，
且带来可审计/可回滚的治理价值。

> 一个诚实的发现：BM25 检索分数无法区分"简单题/难题"（两者分布几乎重合），即**检索分数 ≠ 可答性**——
> 这正是冷启动转人工无法标定的根因，也是自进化（从结果反馈中学习转人工策略）的价值所在。

### 4b. 真实 benchmark 背书：τ²-bench（DeepSeek-V4-Flash，**官方 pass^k 全口径**）

合成集用于**受控消融**；为证明机制在公认基准上同样成立，在 **τ²-bench**
（Sierra，2026 客服 Agent 事实标准，arXiv 2506.07982）`retail` 域做真实 A/B，
**完全对标公开评测口径**：完整 `test` 划分 **40 任务 × num_trials=4 = 160 个独立仿真**，
直接调 tau2 官方 `compute_metrics` 算 `pass^k = C(c,k)/C(n,k)`（τ-bench 论文 2406.12045 原始估计量）。

| metric（DeepSeek-V4-Flash, retail, test=40, trials=4） | memory **OFF** | memory **ON** | Δ |
|---|---|---|---|
| avg_reward | 0.925 | 0.931 | **+0.006** |
| **pass^1** | 0.925 | 0.931 | **+0.006** |
| **pass^2** | 0.896 | 0.904 | **+0.008** |
| **pass^3** | 0.881 | 0.887 | **+0.006** |
| **pass^4** | 0.875 | 0.875 | 0.000 |

train baseline pass^1=0.900（4/40 失败 → 蒸馏 8 条 playbook tips：先鉴权→写库前确认→按配送状态校验→超策略转人工）。
所有 pass^k 方向一致（≥0），Δ 在 +0.6 — +0.8pp 区间。

#### airline 域（最难，更大 headroom）· DeepSeek-V4-Flash · test=20 × trials=4

| metric | OFF | ON | Δ |
|---|---|---|---|
| avg_reward | 0.800 | 0.775 | −0.025 |
| pass^1 | 0.800 | 0.775 | **−0.025** |
| pass^2 | 0.717 | 0.725 | **+0.008** |
| pass^3 | 0.675 | 0.688 | **+0.012** |
| pass^4 | 0.650 | 0.650 | 0.000 |

train baseline pass^1=0.867（4/30 失败 → 8 tips：写库前显式确认、按 cabin class 决定先升舱后取消、按要求计算总和、…）。

**两域诚实解读（资深视角，不藏不夸）**：
- **retail**：模型已逼近上限（OFF 92.5%），Δ 一致 ≥ 0 但 magnitude 小（+0.6 – +0.8pp）。
- **airline**：pass^1 微降 −2.5pp、但 **pass^2/^3 反而上升**（+0.8/+1.2pp）。这是个**真实的方法学发现**：
  注入的 playbook 让 agent 不一定单次更优，但在**多次重复中更一致地成功**——典型的"LLM 创造性 vs 规则一致性"
  tradeoff。pass^k 随 k 增大本是衡量可靠性，ON 在 k≥2 上更稳定，正符合"经验规则提供 reliability scaffolding"
  的设计意图。N 也偏小（20 task × 4 trial = 80 sims），−2.5pp 落在 noise 范围内。
- **下一步**：把 airline 暴露的 reliability-vs-single-shot tradeoff **当成 research question**，
  借鉴 2026 主线综合一个解法 → APR-CS（§4e）。

> 这种"看 pass^k 全谱而不是只看 pass^1"的解读，是资深面试官评判"是否真懂 benchmark"的关键点；
> 把负面发现转成研究问题、迭代分析、提出下一步，比报告一个漂亮单一数字更有说服力。

### 4e. 把 §4b 的 tradeoff 当 research question：APR-CS 自适应路由 + 反事实归因

§4b airline 暴露的 reliability-vs-single-shot tradeoff 不是 bug，而是一个清晰的研究问题：
**能否在保留 pass^k 多次一致性的同时回升 pass^1 单次最优？** 我把它当作研究题目，
借鉴 2026 五条主线综合设计 **APR-CS（Adaptive Playbook Router with Counterfactual Self-Scoring）**——把
"硬注入全部 tips"升级为"按任务相关性 × 反事实贡献 Δᵢ 路由 top-K"。算法借鉴 2026 五条主线（详见 `design/apr_cs_innovation.md`）：

| 借鉴 | 来源 |
|---|---|
| per-component reflection-based attribution | **GEPA**（arXiv 2507.19457, ICLR'26 Oral） |
| adaptive retrieval decision | **Self-RAG**（arXiv 2310.11511） |
| memory selection policy / executor-evaluator 双轨 | **Mem0**（2504.19413）/ **TAME**（2602.03224） |
| skill library + selection | **Voyager**（2305.16291） |
| counterfactual component scoring | **AlphaEvolve** |

**合成集 4 条件消融**（`run_apr_cs_ablation.py`，零依赖确定性）：

| condition | resolution | keypoint | intervention | avg_tips |
|---|---|---|---|---|
| all (legacy 硬注入) | 0.750 | 0.859 | 0.250 | 10.00 |
| top_k_relevance | 1.000 | 0.911 | 0 | 3.50 |
| **cf_weighted** | **1.000** | 0.930 | 0 | 2.75 |
| **conf_gated** | **1.000** | **0.943** | 0 | **2.25** |

反事实评估正确给 3 条干扰 tip 标 Δᵢ<0 → 自动剔除。

**τ²-bench airline 真实验证**（同 §4b 实验框架，所有路由 K=4）：

| metric | OFF | naive ON (all-8) | APR-CS top_k_relevance | APR-CS cf_weighted |
|---|---|---|---|---|
| pass^1 | 0.800 | 0.775 | **0.787** (+1.2pp) | **0.787** (+1.2pp) |
| pass^2 | 0.717 | 0.725 | 0.725 | 0.675 |
| pass^3 | 0.675 | 0.688 | 0.662 | 0.600 |
| pass^4 | 0.650 | 0.650 | 0.600 | 0.550 |

**真实结论（资深视角的核心价值）**：
- **pass^1 两种路由都回升 +1.2pp**（说明 routing 在 single-shot 上起作用，且 K=4 是 pass^1 上限）。
- pass^2/^3/^4 路由后**反而下降**（cf_weighted 比 top-k 更差）。这说明在 K=4 这个紧约束下，
  binding constraint 是"tips 数量本身"，不是"选哪几条"——routing 把一致性收益换了单次最优，
  **不是** Pareto improve。
- **但 CF 评分本身价值真实存在**：正确识别 2 条 tip Δᵢ=0（"先取得用户确认"、"求和"——LLM 本就会做），
  这是把"自进化产物"从"加更多规则"推向"按贡献筛选规则"的方向；这种**可审计的 tip 归因**对生产治理本身就是收益
  （和 §4c governance 模块联动）。
- **真正的下一步**（这是研究问题，不是补丁）：**adaptive-K / confidence-gated injection** ——
  让模型按任务自适应决定用多少 tips。这正是 Self-RAG 的核心思想，
  也是合成集消融里 conf_gated 拿到最佳 keypoint 覆盖（0.943, avg_tips=2.25）的原因。
  受 tau2 接口限制（agent 内置无 confidence 信号暴露），这条留作下一阶段工作。

> 这种"做了改进 → 暴露新 tradeoff → 给出下一步研究问题"的闭环，
> 比单纯报"我做了改进、效果更好"对资深面试官更有说服力——它证明候选人**真的在做研究、不是刷指标**。

> 复现：`python scripts/run_tau2_experiment.py --domain retail --train-tasks 40 --test-tasks 40 --trials 4`
> 更多说明：`docs/tau2_integration.md`。

### 4c. 生产级加固（让它像真实业务系统，而非研究原型）

围绕"真实客服 Agent 上线需要什么"补齐了四层生产能力（均对齐 2026 实际栈，零第三方依赖即可跑）：

| 层 | 模块 | 能力 | 对齐的开源/实践 |
|---|---|---|---|
| **Guardrails** | `src/seagent/guardrails/` | 入站：提示注入/越狱拦截 + PII 脱敏；出站：groundedness(回答须有检索证据支撑，防幻觉) + 合规策略 + PII 脱敏 | Microsoft Presidio、NeMo/guardrails-ai、Ragas faithfulness |
| **Observability** | `src/seagent/obs/` | 每轮 trace（latency/p50-p95、token、cost、检索命中、置信度、guardrail 裁决、是否转人工）+ 运营看板（deflection/escalation/成本/拦截率） | OpenTelemetry GenAI、Langfuse、Arize Phoenix |
| **Governance** | `src/seagent/governance/` | playbook 发布生命周期(proposed→approved→canary→active→rolled_back) + **回归门禁**(启用前后比指标，回退即拒) + 审计日志；记忆 TTL/去重/冲突消解/入库脱敏 | Mem0/Letta/Zep、MemArchitect、misevolution 防护 |
| **Serving** | `src/seagent/serving/` | FastAPI 服务(/chat /feedback /handoff /metrics) + 类 Zendesk 工单 schema + **隐式/噪声反馈→自进化**闭环 | FastAPI、Zendesk/Intercom |

两个一键演示（offline mock，确定性）：
- `run_production_demo.py`：注入工单被 guardrail 拦截转人工、PII 工单被脱敏、正常工单自助解决，末尾打印运营看板（deflection 率、p50/p95 延迟、成本、guardrail 拦截率）。
- `run_governed_evolution.py`：自蒸馏的好 playbook 过门禁上线；一个"所有 how-to 都转人工"的坏 playbook 被回归门禁拦下（escalation_f1 回退 0.039）→ 回滚、挡在生产外，全程审计。

> 这直接回应了"自进化会不会越改越坏(misevolution)"：**任何自生成的行为变更都是可审计、可灰度、可回滚的受治理资产，且必须先过指标门禁**——而不是黑盒权重更新。

### 4d. 消融与分析（回应资深质疑，均离线确定性可复现）

| 分析 | 脚本 | 结论（真跑数字） |
|---|---|---|
| **弱监督/噪声反馈也能进化**（"线上没 gold label 怎么办"） | `run_noisy_feedback_evolution.py` | 把 gold 反馈换成隐式信号(点踩/reopen，注 15% 假阳+20% 假阴)，跨 8 seed 曲线仍单调上升，最终解决率达 gold 的 **92.6%** |
| **成本/延迟 vs 收益 ROI** | `run_cost_benefit_analysis.py` | 解决率 34%→71% 时注入 token +27%、延迟 +71%；**边际收益拐点在经验池≈17**，结论：用 TTL+top-k 截断把成本钉在拐点 |
| **检索方法消融**（"换向量检索还成立吗"） | `run_retrieval_ablation.py` | BM25/TF-IDF余弦/Hybrid 三种检索器下进化增益方向**一致**(+0.37/+0.21/+0.40)，收益来自经验池本身、不依赖特定检索器 |
| **APR-CS 自适应路由 + 反事实归因**（针对 airline tradeoff 的真正改进，2026-aligned） | `run_apr_cs_ablation.py` | 合成集 4 条件消融：硬注入全部 tips→解决率 75%、人工介入 25%；APR-CS `cf_weighted`→**100% / 0%**；avg_tips 10→**2.75**。反事实评估正确识别 3 条干扰 tip 的 Δᵢ<0 → 自动剔除 |

部署拓扑见 `design/production_architecture.md`；APR-CS 算法设计与 2026 工作脉络对照见 `design/apr_cs_innovation.md`（含 GEPA / Self-RAG / Mem0 / Voyager / AlphaEvolve 借鉴矩阵）。

### 4f. 规模化压测（N=500 LLM 生成的真实分布工单）

`scripts/run_stress_test.py all`：用 DeepSeek 生成 500 条分布对标真实客服的工单
（50% 常规简单 / 20% 难题 / 10% PII / 10% 多意图 / 5% 注入 / 5% 中英混杂带 typo），
并发 20 端到端跑产线路径(`SupportAgent + GuardrailPipeline + Tracer`)，
另独立做记忆膨胀压测(经验池 10→5000)。**真跑、真 API、真延迟、真成本**。

**Headline（91.7s 跑完 500 工单，DeepSeek-V4-Flash）**

| 指标 | 值 | 解读 |
|---|---|---|
| QPS | 5.25 | 受 DeepSeek API 限速 |
| p50 / p95 / p99 latency | 3.4s / 5.2s / 5.9s | 典型 LLM agent 多轮延迟 |
| error_rate | 3.8% | API timeout/rate-limit，需重试 |
| escalation_rate | **85.2%** | **暴露真实生产现实**——见下 |
| block_rate | 3.3% | 有硬拦截发生（PII/注入） |

**按类别拆解（真实分布下 agent 的诚实表现）**

| 类别 | n | resolution | escalate | block | err | 解读 |
|---|---|---|---|---|---|---|
| injection | 25 | 0% | 96% | **17%** | 8% | 17% 硬拦截 + 96% 转人工 = 注入基本被挡住 |
| pii | 50 | 0% | 98% | **22%** | 2% | 正则 PII 检出 22%（Presidio 未装；装上预计更高） |
| normal_easy | 250 | 23% | 76% | 0% | 3% | **关键发现见下** |
| normal_hard | 100 | 5% | 95% | 0% | 3% | KB 不覆盖→低置信→转人工 |
| multi_intent | 50 | 10% | 89% | 0% | 10% | 多意图工单是已知 production 难点 |
| multilingual | 25 | 0% | 100% | 0% | 0% | 中英混合 + typo 全部转人工 |

**最重要的诚实发现**：85% 的工单被转人工，**不是 bug，是 agent 正确识别了知识盲区**。
合成的 NimbusFlow KB 只有 30 篇文档，无法覆盖 LLM 生成的 500 个真实话题；agent 低置信
就转人工——这是设计意图（安全>过度自信），也是真实分布下的**真实生产现实**。

→ **企业上线前必补**：(a) KB 扩到覆盖真实业务话题 (>>30 篇)；(b) 接 web/工单库的 RAG；
(c) 按 category 路由（注入直接 block / 多意图先拆分 / 多语种先翻译）。

**记忆膨胀压测**（在合成 eval 上测）

| 经验池 size | avg 检索延迟 | p95 检索延迟 | resolution |
|---|---|---|---|
| 10 | 0.77 ms | 1.4 ms | 90% |
| 100 | 1.7 ms | 3.1 ms | 80% |
| **1000** | **10 ms** | **19 ms** | 93% |
| 5000 | 48 ms | 89 ms | 97% |

**knee≈1000**：经验池在 1k 量级前 BM25 检索延迟可接受；越过 1k 进入 10ms+，5k 已 48ms（接近端到端延迟 1%）。
**工程结论**：在 1k 量级触发 TTL/dedup/淘汰，或换向量库 + 缓存。

> 这种"做了规模化压测、暴露真实生产 gap、给出具体修复路径"的诚实数据，
> 是这个项目从"看着像生产 agent"推到"测过的 pre-pilot 候选"的决定性一步。

### 4g. 假设证伪：扩 KB 不是真瓶颈（用 Bitext 严格测出来的）

§4f 给出的结论是 "**KB 不够是 85% 转人工的真瓶颈**"。这是个**假设**，而不是结论。
为了验证它，做了一次严谨对照实验（`scripts/run_stress_test_expanded.py`）：

- **数据集**：[Bitext customer-support-llm-chatbot-training-dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset)
  （CDLA-Sharing-1.0 license，27 intents × 10 categories × 27k Q&A，2026 学术+工业最被接受的 CS LLM 数据集）。
- **KB 扩展**：原 30 篇 NimbusFlow + Bitext 抽 146 篇代表性 FAQ = **176 篇**（约 6×）。
- **两组对照实验**：
  - **Exp A** "KB augmentation only"：扩展 KB + **原 500 NimbusFlow tickets**
  - **Exp B** "KB + tickets aligned"：扩展 KB + **重新生成 500 mixed tickets**（NimbusFlow 50% + e-commerce 50%，分布对齐 KB）

| metric | 原始(30 doc) | **Exp A**(176 doc + 原 tickets) | **Exp B**(176 doc + 对齐 tickets) |
|---|---|---|---|
| escalation_rate | 85.2% | 85.6% **(+0.3pp)** | **92.0% (+6.7pp ↑)** |
| block_rate | 3.3% | 2.7% | **19.8% (+16.5pp ↑↑)** |
| normal_easy resolution | 23.2% | 24.4% **(+1.2pp)** | **11.3% (-11.9pp ↓)** |
| avg_kb_hits | — | 3.94 (顶满 top-k=4) | 3.98 |
| avg_latency_ms | 3568 | 3843 | 4354 |

**假设被证伪**：
- **Exp A 几乎不动**——KB 扩 6× 后检索命中数顶满，但 resolution 只 +1.2pp。**单纯加 KB 没用**。
- **Exp B 反而变差**——分布对齐英文 e-commerce 后 escalation 涨到 92%、normal_easy 解决率反降。
  原因不是 KB 不行，是两个组件出问题：
  1. **PII guardrail 精度问题**：真实 Bitext-aligned 英文工单里含真实邮箱/手机/卡号，正则
     PII 检出过激进 → block_rate 从 3.3%→19.8%
  2. **Critic 阈值跨域标定问题**：`escalate_tau=0.5` 是为中文 NimbusFlow 工单标定的；
     英文 Bitext-style stiff template 回答的 critic confidence 过不了这个阈值 → 转人工激增

**真瓶颈定位（推翻自己的前一个判断）**：
> KB 不是瓶颈。**真瓶颈是跨域 critic 阈值校准 + PII guardrail 精度**。这两件比"扩 KB"更难，
> 也更接近真实生产部署的 painful 工程问题（一个客服 agent 部署到新业务，第一件要做的就是
> 重新标定 confidence threshold + 域适应 PII 检测）。

### 4h. 第二轮假设证伪：critic 阈值 + PII 精度也不是瓶颈（Exp C）

§4g 给出的新假设："**critic 阈值跨域标定 + PII guardrail 精度**是真瓶颈"。继续严谨测：

- **per-domain critic 阈值标定**：用 NimbusFlow `data/eval/queries.jsonl` 真实 grid-search
  (escalate_tau ∈ {0.3, 0.4, 0.5, 0.6, 0.7}, kb_conf_cap ∈ {0.5, 0.7, 0.85})；e-commerce 域
  用 informed prior (0.35, 0.70)。**省钱设计**：每查询只跑 1 次 LLM，threshold 组合**离线 replay 决策**。
  选定 nimbusflow=(0.30, 0.50), ecommerce=(0.35, 0.70)。
- **PII guardrail 加 `precision_mode='balanced'`**：信用卡需 16 连续位、手机号要符合国家标准格式、
  邮箱要完整域名。
- **Exp C 重跑**：复用 Exp B mixed tickets + 扩展 KB + calibrator + balanced PII。

| Metric | Exp B(naive) | **Exp C(calibrated + balanced PII)** | Δ vs Exp B |
|---|---|---|---|
| escalation_rate | 91.96% | **92.99%** | **+1.0pp ↑（反而恶化）** |
| block_rate | 19.79% | 18.97% | -0.8pp（PII 精度有效，微降） |
| normal_easy res | 11.27% | 6.50% | -4.8pp ↓ |

**第二个假设也被证伪。**

**真瓶颈定位（第三次迭代才锁定）**：
> 不是 KB（§4g 证伪），不是 critic 阈值 + PII 精度（§4h 证伪），而是
> **guardrail 的 groundedness check**——英文 Bitext-aligned 回答因为"无 KB 证据完整支撑"
> 被 groundedness module 推到 escalate（即便 critic 阈值已调到 0.30）。
> Exp C 的 escalation 一直卡在 ~93% 就是因为 groundedness 主导了决策路径。

下一阶段必做：
1. **groundedness 阈值跨域校准**：当前一刀切阈值在英文 stiff template 上 false-fail 严重
2. **groundedness 替换为 LLM-judge**：用 deepseek 做 single-call 二分类（更贵但更精）
3. **三路通报道路重新设计**：critic / groundedness / policy 三个 escalate 信号需 vote 而非 OR

> **两轮假设证伪 → 第三轮才能锁定真瓶颈** —— 这是真实研究的样子，
> 不是 demo 项目的"我调通了"。给资深面试官讲这条曲线，比报告任何单一漂亮数字都更能证明
> 候选人的研究素养。诚实写入 `experiments/stress_test_expanded/exp_c/load_summary.json`，
> 数字不藏。

## 5. 为什么这套"自进化"不是噱头

- **客观闭环**：`eval/verifier.py` 用 gold keypoints + 转人工标签判分，**Agent 看不到答案**，自评分无法作弊。
- **真正的泛化**：训练只从"失败工单 + 人工解决方案"学习；eval 是同义改写，提升不靠记忆题面。
- **可审计可回滚**：每条 playbook 带 `version` / `enabled`，人审通过才启用，可一键停用——对抗 *misevolution*
  （arXiv 2509.26354：自进化可能把 Agent 越改越坏）。
- **不改权重**：契合 2026 工业界主流落地形态，零训练成本、可解释、可治理。

## 6. 借鉴的 2025–2026 工作

思想谱系（详见 `design/project_design.md` 与 `research/`）：

- **Memento**（arXiv 2508.16153）/ **A-MEM**（arXiv 2502.12110）：case-based / 结构化 Agent 记忆 → 本项目的 episodic 层。
- **GEPA**（arXiv 2507.19457, ICLR'26）/ **AFlow**（ICLR'25）/ **MaAS**（ICML'25）：反思式提示与 workflow 自动优化 → Reflector 的归纳思想。
- **DGM / HGM**（arXiv 2505.22954 谱系）：自改进的进化思想（本项目取其"从历史中归纳改进"，但不自改代码、不动权重）。
- **Anthropic Dreaming / 持久记忆**：离线复盘固化 playbook 的产品级范式。
- **EvolveR**（arXiv 2510.16079）：经验生命周期闭环（离线蒸馏 + 在线检索 + 更新）。

参考开源仓库见 `ref_repos/`（已浅克隆）：`Awesome-Self-Evolving-Agents`、`AFlow`、`openevolve`、`dgm`、
`AgenticMemory`、`Memento`、`MaAS`、`HGM`、`Self-Evolving-Agents`。

## 7. 目录结构

```
self_evolving_agent/
├── README.md                 # 本文件
├── pyproject.toml / Makefile / requirements.txt
├── configs/default.yaml      # 全部超参（阈值/轮次/后端）
├── data/                     # NimbusFlow 合成客服数据集（KB + train/eval 查询）
├── src/seagent/              # 核心代码包
│   ├── llm/                  # 后端抽象：mock / openai(vllm)
│   ├── memory/               # bm25 + 三层记忆
│   ├── agent/                # self-RAG agent + critic
│   ├── evolution/            # reflector（离线复盘）
│   └── eval/                 # verifier + metrics + harness
├── scripts/                  # run_experiment.py / plot_evolution.py
├── tests/                    # 9 个离线测试（含端到端进化验证）
├── experiments/              # 跑出来的指标/报告/曲线
├── design/project_design.md  # 完整设计文档 + 简历话术 + 面试问答
├── research/                 # 自进化调研笔记（理论/开源/产业+岗位）
└── ref_repos/                # 浅克隆的参考开源仓库
```

## 8. 局限与未来工作

- 数据为合成集，规模有限；接入真实工单需补充脱敏与隐私治理。
- 检索用 BM25（无依赖优先）；可替换为向量检索/重排提升泛化。
- 离线 Reflector 用规则聚类；可升级为 LLM 驱动的反思与 playbook 自动起草（仍保留人审闸门）。
- 当前不自改代码/不动权重（刻意的安全取舍）；与 RL/微调路线的融合是后续方向。

## 9. 简历一句话

> **自进化客服 Agent（个人项目）**：不改模型权重的自进化客服系统——分层记忆（经验池/知识库/playbook）
> + self-RAG + 离线复盘归纳可审计可回滚的 playbook，配合确定性 verifier。受控基准解决率
> 34→71%、keypoint 42→80%、重复错误 100→40%、转人工 F1 0→0.71。**完全对标公开评测口径**在
> τ²-bench retail/airline（DeepSeek-V4-Flash, 官方 pass^k, test=40×trials=4 与 20×4）严谨验证；
> airline 暴露 reliability-vs-single-shot tradeoff 后**作为 research question**借鉴 2026 主线
> (GEPA/Self-RAG/Mem0/Voyager/AlphaEvolve) 综合设计 **APR-CS**（自适应路由 + 反事实 tip 归因），
> 实验再暴露 K=4 是新 binding constraint，给出下一阶段 adaptive-K / confidence-gated 方向。
> **500 工单 LLM 生成真实分布压测**（DeepSeek 真 API，并发 20）：QPS 5.25、p95 5.2s、注入 17%
> 硬拦截、PII 22% 硬拦截、记忆膨胀拐点 knee=1000。生产侧四层：guardrails / observability /
> governance（playbook 发布生命周期 + 回归门禁 + 审计 + 灰度回滚）/ FastAPI serving。
> 4 项消融证稳健：弱监督隐式反馈达 gold 的 92.6%、跨 BM25/TF-IDF/Hybrid 检索器进化方向一致、
> 成本-延迟-收益拐点经验池≈17。**整套按 "建机制→公开 benchmark 严谨对标→暴露 tradeoff→设计
> 2026-aligned 改进→暴露新 binding 给出下一阶段问题→规模化压测验证真瓶颈" 的研究闭环走完**。

更多 STAR bullet 与面试问答见 `design/project_design.md` §8。
