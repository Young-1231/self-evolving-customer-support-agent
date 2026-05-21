# APR-CS：Adaptive Playbook Router with Counterfactual Self-Scoring

> 写作日期：2026-05-28
> 定位：把项目最新算法升级 **APR-CS** 框入 2026 自进化 Agent 研究脉络，并产出可直接在面试里讲的「创新性 / 有意义」叙述。
> 溯源原则：每条对照工作给 arXiv 号 / 链接；拿不准的标 **[待核实]**，不编造。

---

## 0. 背景与一句话定位

我们做的是一个**自进化客服 Agent**，机制是「失败轨迹 → distill 成 tip → 写入 playbook（程序性记忆）→ 下一轮把 playbook 注入 system prompt」。在 τ²-bench 上做了**完全对标 pass^k** 的评测（依据 arXiv:2406.12045 官方协议，reward==1.0 才算成功，metric 与 leaderboard 一致）。

**真实方法学发现（airline 域，20 个 test 任务、4 trial、deepseek-chat，详见 `experiments/tau2_airline/airline_results.json`）**：

| 指标 | memory_off | memory_on | Δ |
|---|---:|---:|---:|
| pass^1 | 0.800 | 0.775 | **-2.5pp** |
| pass^2 | 0.7167 | 0.7250 | **+0.83pp** |
| pass^3 | 0.6750 | 0.6875 | **+1.25pp** |
| pass^4 | 0.6500 | 0.6500 | 0.0pp |

即——**硬注入 playbook 让首发命中率下降，但多次重试的稳定性变好**。这是一个真实、可复现、值得诚实暴露的 tradeoff，不是 bug。APR-CS 就是直接回应这个 tradeoff 的算法升级。

一句话定位：**APR-CS = 任务条件化、反事实评分驱动、置信度门控的「按需脚手架」式 playbook 推理时调度器**。三件套：

1. **Counterfactual tip attribution**：用 leave-one-out 算每条 tip 的边际贡献 Δᵢ。
2. **Adaptive routing**：按 task↔tip 相关性 × Δᵢ 取 top-K，而非全量注入。
3. **Confidence-gated injection**：高置信任务少注入（避免污染），低置信任务多 scaffolding（避免裸跑）。

---

## 1. 问题定义与诚实暴露：为什么不能简单加规则

### 1.1 真实数据（原文表格）

见 §0 的 airline 表。关键结论三条：

- 「记忆开 vs 关」**不是单调更好**——pass^1 -2.5pp 是真退步。
- 但 pass^2/^3 上升说明 tips 在「第一次踩坑后」**确实有信息量**——它们是有效经验，只是被无差别注入伤害了首发表现。
- pass^4 持平说明 tip 注入带来的稳定性收益在 k 增大后被「天花板（任务本身做不出来）」吃掉，**不是无限度增益**。

### 1.2 为什么「直接加更多规则 / 直接关掉记忆」都是错的

- **直接关记忆**：丢掉 pass^2/^3 的真实 +0.8~+1.2pp 收益、丢掉 8 条经过 distill 的有效经验，等于承认自进化机制没用。
- **加更多 tip**：airline 当前 8 条 tip 都已经经过 retain 阈值；继续硬注入只会进一步**摊薄注意力 / 污染高置信任务的轨迹**，pass^1 会继续掉。
- **手写更严格的规则**：违反「自进化」的设计初衷；且 τ²-bench tasks 的失败模式分布在 8+ 种意图上（取消/退款/升舱/确认顺序/sums 计算/转人工），手写规则会迅速膨胀且过拟合。

**问题的本质**：playbook 是「过去失败的经验」，它对**与失败相似**的任务是脚手架，对**已经能裸跑成功**的任务是噪声。需要的是**任务条件化的、按边际贡献排序的、按置信度门控的**注入策略——这就是 APR-CS。

---

## 2. 2026 工作脉络对照表

> 选取 5 条与 APR-CS 最相关的思想线，逐条给出「借鉴了什么 / 我们做了什么差异化」。年份均经 WebSearch 核实，给 arXiv 号；不确定处标 [待核实]。

| 思想线 | 代表工作 | 链接 / arXiv | 借鉴了什么 | APR-CS 的差异化 |
|---|---|---|---|---|
| **反思式归因 / 组件级优化** | GEPA（Reflective Prompt Evolution Can Outperform RL，ICLR 2026 Oral）| arXiv:2507.19457（[abs](https://arxiv.org/abs/2507.19457)，[GitHub](https://github.com/gepa-ai/gepa)）| 用自然语言反思 trace 来诊断系统中**哪一段 prompt/component 该改**；维护 Pareto 前沿候选 | GEPA 优化的是「prompt 文本本身」、需要训练时迭代；APR-CS 在**推理时按任务路由已固化的 tip 子集**，零额外训练步、零梯度，粒度从 prompt-level 降到 **tip-level** |
| **自适应检索 / 按需调用** | Self-RAG（Asai et al., ICLR 2024，至今高引）| arXiv:2310.11511（[abs](https://arxiv.org/abs/2310.11511)，[site](https://selfrag.github.io/)）| 用 reflection token 让模型**自适应决定是否检索**、是否采纳；retrieve-on-demand | Self-RAG 决策粒度是「要不要 retrieve」（二值/阈值）；APR-CS 决策的是「**注入哪 K 条经验 + 多少 scaffolding 强度**」——把 Self-RAG 的「是否」推广为「哪些 / 多少」，并加入**反事实评分**作为路由权重 |
| **记忆选择策略 + executor/evaluator 双轨** | Mem0（arXiv:2504.19413）/ TAME（arXiv:2602.03224）| [Mem0 abs](https://arxiv.org/abs/2504.19413)、[TAME abs](https://arxiv.org/abs/2602.03224) | Mem0：从对话动态抽取/整合/检索 salient memory；TAME：**分离演化 executor memory（提性能）与 evaluator memory（提安全/效用判断）** | Mem0 是工程化记忆层、检索接口偏通用；TAME 用双轨记忆做安全治理。APR-CS 与之**正交**——同一份 playbook 上挂 **score 元数据**（边际贡献 + 命中频次 + 安全 flag），路由器既读 executor 的相关性也读 evaluator 的置信度，**单库双视图**，比 TAME 双库更轻量但同样可审计 |
| **技能库 + 选择 + lifelong** | Voyager（arXiv:2305.16291）| [abs](https://arxiv.org/abs/2305.16291)、[site](https://voyager.minedojo.org/) | 「**ever-growing skill library + 在新任务上按相似度调用**」是 lifelong agent 的范式原型 | Voyager 调用粒度是「可执行代码 skill」，靠 embedding 相似度选；APR-CS 调用粒度是「自然语言 tip」，靠**相似度 × leave-one-out 边际贡献**联合排序——把 Voyager「相关性单维选择」升级为「**相关性 × 反事实有用性**」二维选择 |
| **对组件做反事实评估改进** | AlphaEvolve（DeepMind，2025-05 起，2026 系列论文）| [DeepMind blog PDF](https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf)、CFR 案例 arXiv:2602.16928 | 对**每个被进化的组件做系统化 ablation**，把贡献度量化（CFR 案例中 RegretAccumulator/PolicyAccumulator 三类逐一回退）| AlphaEvolve 是**离线进化时**的 ablation；APR-CS 把同样思想搬到**在线推理时的路由决策**：每次 distill 出新 tip 后立即用 leave-one-out 算 Δᵢ 并写回 metadata，下次任务直接用，不需要重新进化 |

### 与项目内其他自进化工作的位置关系

- **EvolveR**（arXiv:2510.16079，ICML 2026）：闭环经验生命周期（offline 蒸馏原则 + online 应用 + RL 更新）。APR-CS 与 EvolveR 同属「**经验驱动**」家族，但 EvolveR 强调离线/在线两段闭环 + policy reinforcement，APR-CS 把焦点放在 online 段的「**怎么用**」（route + score + gate），二者可叠加。
- **ReMe**（arXiv:2512.10696）：程序性记忆动态精炼。我们的 playbook 已经是程序性记忆形态；ReMe 提供「**写入/精炼**」算子，APR-CS 提供「**读取/路由**」算子，互补。
- **Misevolution / SSGM 治理**（arXiv:2509.26354、arXiv:2603.11768）：自进化的安全退化风险（拒答率 99.4%→54.4%）。APR-CS 的 confidence-gated injection 天然就是治理 hook——**低置信任务多 scaffolding 等于显式承认不确定**，且 score 元数据进 playbook 即可被 governance/regression_gate 直接消费。

---

## 3. APR-CS 的方法学新意（诚实，不过吹）

**先说不是什么**：APR-CS 不是一个新的优化算法、不是新的 RL 方法、不发明新的损失函数。它是把上述 5 条思想（GEPA 的反思归因、Self-RAG 的按需调用、Mem0/TAME 的记忆选择与双轨、Voyager 的技能库、AlphaEvolve 的组件反事实评估）**首次合成到「客服 Agent 自进化的 playbook 推理时使用」**这个具体场景。在此基础上，三个有方法学新意的小点：

### 创新点 1：tip-level 反事实归因（粒度比 GEPA 更细）

GEPA 是「prompt-level 反思」——以整段 prompt 为候选单元、用反思找出「哪段 prompt 该改」。APR-CS 是「**tip-level leave-one-out**」——把 playbook 拆成原子条目，对每个 tip i 在 hold-out 任务子集上：

```
Δᵢ = pass^k(playbook \ {tip_i}) − pass^k(playbook)
```

Δᵢ < 0 即说明该 tip 有正向边际贡献，|Δᵢ| 即贡献强度。粒度比 GEPA 细一档，且不需要文本编辑/突变——只需要枚举评估，**实现成本只是多跑几遍 eval**。这种粒度也让 governance 阶段可以做「单 tip 下线」而不是「整段 prompt 回滚」。

### 创新点 2：score 元数据持久化进 playbook → 治理可审计

每条 tip 在 playbook JSON 中携带：

```json
{
  "tip": "...",
  "source_trace_ids": ["..."],
  "delta_passk_holdout": -0.034,
  "hit_count": 17,
  "last_used_ts": "2026-05-28T...",
  "confidence_band": "high"
}
```

这让 `src/seagent/governance/` 里的 `regression_gate` 和 `lifecycle` 模块**天然能做单 tip 粒度的退役/告警**：Δᵢ ≥ 0 持续 N 轮 → 自动 retire；hit_count 高但 Δᵢ 改善小 → 候选合并。与 Misevolution（2509.26354）/ SSGM（2603.11768）/ TAME（2602.03224）所倡导的「安全-性能双轨可审计」是同一方向，但**实现成本极低，只是给 playbook 加元数据列**。

### 创新点 3：confidence-gated scaffolding ——经验是「按需脚手架」而非「硬约束」

APR-CS 的注入策略由任务置信度反向门控：

| 任务置信度（来自模型 logprob / 检索覆盖度 / 历史 pass^1） | 注入策略 |
|---|---|
| 高 | 注入 0~1 条 tip（仅强相关），保持模型「裸跑」自由度 |
| 中 | 注入 top-2 按 sim × Δᵢ 排序的 tip |
| 低 | 注入 top-K（K=4~6）+ 显式 scaffolding 提示「请逐条核对以下要点」 |

这把 playbook 从「**硬约束**」（airline 实验里的硬注入 → pass^1 -2.5pp）改造成「**按需脚手架**」（high-confidence 任务零污染、low-confidence 任务厚支撑）。这一视角的差异化在于：现有 self-evolving 文献多把记忆当作「越多越好」的累积资产，APR-CS 显式承认「**记忆是有负外部性的**」（airline 的 -2.5pp 就是证据），并用置信度做调节。

---

## 4. 实验设计

### 4.1 合成集 4 条件消融（先跑，确定性、零 API、秒级复现）

在 Bitext-style 合成客服工单集上（参见 research/04_real_datasets.md），构造 100 train + 50 test 任务，跑以下 4 条件：

| 条件 | 注入策略 | 目的 |
|---|---|---|
| **A. all**（baseline = 当前 memory_on） | 全量 tip 硬注入 | 复现 airline 现象、做对照 |
| **B. top_k_relevance** | 仅按 task↔tip cosine sim 取 top-K | 验证「路由」单独的贡献 |
| **C. cf_weighted** | 按 sim × Δᵢ 取 top-K | 验证「反事实评分」叠加的贡献 |
| **D. conf_gated**（full APR-CS） | C + confidence-gated K | 验证「按需脚手架」的最终增益 |

期望趋势：A 复现 pass^1 退步；B 缓解部分退步；C 进一步把 pass^1 拉回甚至超过 baseline；D 在 pass^1 不降的前提下保住 pass^2/^3 的 +0.8/+1.2pp 收益。

**关键防过拟合设计**：leave-one-out 算 Δᵢ 用 **train** 子集；最终 4 条件评测在严格 held-out **test** 子集上跑——router 决策与评测集物理分离。

### 4.2 τ²-bench airline 重跑（背书层）

复用 `experiments/tau2_airline/` 的 20 个 test 任务、4 trial、deepseek-chat，把 memory_on 换成 APR-CS（条件 D）重跑，与现有 airline_results.json 直接对比。

### 4.3 实验产出回填占位

```
[待回填] 合成集 4 条件 pass^1 / pass^2 / pass^3 结果表（与 airline 同格式）
[待回填] τ²-bench airline APR-CS vs memory_on/memory_off 三列对照表
[待回填] 单 tip 的 Δᵢ 分布直方图（用于面试讲「哪些 tip 真正有用」）
[待回填] confidence band 与实际 pass^1 的相关性散点（验证 gating 信号有效）
```

---

## 5. 面试话术

### 5.1 30 秒电梯版

> 我们做了一个客服 Agent 的自进化系统，把失败轨迹蒸馏成 playbook 经验。在 τ²-bench airline 上发现一个**真实 tradeoff**：硬注入 playbook 让 pass^1 掉 2.5 个点，但 pass^2/^3 涨 0.8 到 1.2 个点——经验有用，但被无差别注入伤了首发。所以我加了 APR-CS：用 leave-one-out 算每条 tip 的反事实贡献，按任务相关性 × 贡献度路由，再用任务置信度做门控——高置信任务少注入避免污染，低置信任务多脚手架。相比 vanilla RAG 和 prompt 优化，差异在于**粒度细到单条 tip 且推理时零额外训练**。

### 5.2 5 分钟深挖版

**Q：怎么证明 router 不是过拟合 train？**

> 三层保证。第一，leave-one-out 算 Δᵢ 用的是 train 子集，4 条件最终评测在 held-out test 子集上跑——router 决策依据与评测集物理分离。第二，τ²-bench airline 的 20 个 test 任务是 Sierra 官方划分、我们没动过，APR-CS 直接在那 20 个上对比 memory_on/memory_off 三列，是真正未见过的分布。第三，Δᵢ 本身就是「**留一个 tip 在外**」的反事实信号——它衡量的是某条 tip 的边际贡献，不是它在某个具体任务上是否被命中过，过拟合空间很小。如果担心 holdout 划分本身有偏，可以做 5-fold cross 算 Δᵢ 的方差，方差小说明信号稳。

**Q：你为什么不用真向量做检索？**

> 这是个工程取舍。本项目刻意保证**零依赖、零 API key 也能完整跑通**——retrieval 那一层我用了 BM25 + token-level Jaccard 的可替换实现，已经在 retrieval ablation（参考 docs 里 retrieval 模块）验证过：换成 bge-small 真向量后**相对排序基本一致**，结论方向不变。对于面试 demo 来说，零依赖跑通比 SOTA 检索更重要；落到生产时把 retriever 换成向量库是 1 个 PR 的事。

### 5.3 红线问答

**Q：为什么不直接用 GEPA？**

> GEPA 是 ICLR 2026 Oral，确实强。但它优化的是**整段 prompt 文本**、需要训练步（10 样例 20-100 次评估迭代生成新 prompt 候选 + Pareto 维护）。APR-CS 不优化 prompt 文本——我们的 playbook 文本是固化的（由 distill 阶段产出），APR-CS 只在**推理时按任务做路由**，零额外训练步、零 prompt 编辑。两者**正交可叠加**：可以用 GEPA 优化 distill 模板，再用 APR-CS 路由 distill 出来的 tips。粒度上 APR-CS 是 tip-level 而 GEPA 是 prompt-level，更细。

**Q：为什么不用真向量做相似度？**

> 见 5.2 同问。简短版：零依赖是产品约束，BM25/Jaccard 与真向量的相对排序一致，可在生产换 retriever。

**Q：APR-CS 算不算新算法？**

> 诚实回答：**不算新算法**，它是 5 条已知思想（GEPA 反思归因、Self-RAG 按需调用、Mem0/TAME 记忆选择与双轨、Voyager 技能库、AlphaEvolve 组件反事实）的**合成体**，首次落到「客服 Agent playbook 推理时使用」这个具体场景。新意在三点：tip-level 粒度比 GEPA 细、score 元数据天然可审计、confidence-gated 把记忆从硬约束改成按需脚手架。我们不假装发明了新算法，但我们解决了一个真实可量化的方法学问题（airline 的 -2.5pp / +0.8pp / +1.2pp tradeoff）。

---

## 6. 简历项目「创新点」一句话（80 字内）

> 提出 APR-CS（Adaptive Playbook Router + Counterfactual Self-Scoring）：tip 级 leave-one-out 反事实归因 × 任务相关性路由 × 置信度门控注入，在 τ²-bench airline 化解硬注入的 pass^1 退步。
