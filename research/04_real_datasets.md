# 04 - 真实可下载数据集调研（自进化客服 Agent 项目）

> 调研日期: 2026-05-28 | 目标机器磁盘余量约 27GB，优先离线 / 小体积。
> 评分维度: 项目契合度 1-5（针对"可自动判分 verifier + train/eval + 可检索上下文 + 记忆/经验复用演示"）。
> 标注规则: 拿不准的事实标 **[待核实]**，不编造。

---

## 一、核心结论速览

我们的项目需要四件事同时成立：
1. 客观可自动判分的 verifier（无人工标注即可闭环）；
2. train/eval 划分 + 同类不同表述（演示泛化）；
3. 知识库/工具/数据库等可检索上下文；
4. 适合演示"记忆/经验复用带来持续改进"。

没有任何单一公开数据集 4 条全满。**τ-bench/τ²-bench 在 (1)(3) 上最强且最权威，SkillLearnBench 在 (4) 上最对题但极小且强依赖 LLM/Docker，Bitext 在 (2) 离线泛化演示上最省事**。详见下表与逐条分析。

---

## 二、候选数据集逐条分析

### 1. τ-bench / τ²-bench（Sierra）—— 客服 Agent 评测的事实标准

- **链接**: https://github.com/sierra-research/tau2-bench ；原始论文 arXiv:2406.12045 ；leaderboard taubench.com / artificialanalysis.ai/evaluations/tau2-bench
- **年份/现状**: 2024 首发（retail/airline 两域），2025-2026 演进为 **τ²-bench**。2026 年仍是客服/工具-用户交互 Agent 的**主流权威 benchmark**，leaderboard 截至 2026-04 已有 38 个模型条目，并新增 voice 全双工与 `banking_knowledge` 知识检索域。
- **任务形式**: 模拟"用户(LLM 模拟器) ↔ 客服 Agent(被测) ↔ 工具/数据库"的多轮对话。Agent 须在遵守 domain policy 的前提下调用工具改数据库（下单/改签/退款等）。
- **域**: mock, retail, airline, telecom, banking_knowledge（5 域）。每域任务数 **[待核实]**（原版 retail ~115、airline ~50 量级，τ² 有 75+ 任务修正）。
- **许可证**: MIT。
- **下载/运行**: GitHub clone + `uv sync`；任务数据随仓库（JSON）。
- **verifier 如何客观判分（关键优势）**: **确定性 DB 状态哈希比对**——在干净 DB 副本上重放 ground-truth 写操作并哈希，与 Agent 实跑后的 DB 哈希逐位比对，必须完全一致；另含 `r_output` 校验关键口头信息。**无部分分**（多传一个 item id 即记 0），并用 **pass^k** 衡量多次重试的稳定性。这正是"客观、无人工标注"verifier。
- **运行依赖**: **重**。必须 LLM（被测 Agent + 用户模拟器都要 LLM）、工具调用、用户模拟器、内置 DB。离线不可行（要 API）。
- **磁盘量级**: 数据本体很小（MB 级 JSON）；真正成本是 API 调用费与运行时。
- **契合度: 5/5**。理由: (1) verifier 是教科书级确定性判分；(3) 自带工具+DB+policy 可检索上下文；非常适合作为"自进化前后 pass^k 提升"的可信度背书。短板: 无现成 train/eval 泛化划分，且**强依赖联网 LLM**，不能做纯离线确定性 demo。

### 2. SkillLearnBench（CMU + Amazon AGI）—— 与"自进化/技能复用"最对题

- **链接**: arXiv:2604.20087 ；https://github.com/cxcscmu/SkillLearnBench ；项目页 cxcscmu.github.io/SkillLearnBench ；HF papers/2604.20087
- **年份/现状**: 2026-04 发布。**首个**面向"从 Agent 经验中自动生成技能(skill)并持续学习"的 benchmark——主题与本项目核心机制几乎一一对应。开源、新，但尚未成为"主流标准"（太新）。
- **任务形式**: 真实世界任务，跨 6 大类（软件工程/信息检索/生产力工具/数据分析/内容创作/工具杂项），评测 continual-learning 方法自动产出 skill 并复用。三层评测: 技能质量、执行轨迹、任务结果。
- **规模**: **极小**——20 个 skill-dependent 任务、15 子域、共约 **100 个 verified 实例**。
- **许可证**: MIT。
- **下载/运行**: GitHub clone；`pip install anthropic openai rich tomli dataclaw json-repair`。
- **verifier 如何判分**: 任务结果 pass-rate + **LLM-as-judge（GPT-5-mini）** 评技能质量/轨迹质量。注意: **判分含 LLM-as-judge，非纯确定性**，客观性弱于 τ-bench 的哈希比对。
- **运行依赖**: **很重**。强制 Docker；需 LLM（求解 Claude Sonnet 4.6、判分 GPT-5-mini）+ Anthropic/OpenAI API key；部分任务需 GitHub token、dataclaw CLI。离线不可行。
- **磁盘量级**: 数据本体极小（约百实例，MB 级）；Docker 镜像才是磁盘大头 **[待核实，可能数 GB]**。
- **契合度: 4/5**。理由: 主题完全对题（continual skill learning = 经验复用持续改进），其论文结论"外部反馈才能真改进、自反馈会递归漂移"可直接进我们的 related work / 设计论证。扣分: 样本太少不适合做主训练集，判分含 LLM 不够确定，依赖 Docker+联网。**最适合做"可信度背书 + 方法论引用 + 小规模真实验证"**，不适合做主 demo。

### 3. ABCD（Action-Based Conversations Dataset）

- **链接**: arXiv:2104.00783 ；GitHub: asappresearch/abcd **[待核实仓库现状]**
- **年份/现状**: 2021（NAACL）。非 2026 新作，但作为"带 action 约束的任务型客服对话"经典集仍被引用。
- **任务形式**: 10K+ 人-人客服对话，55 种用户意图，每意图需满足 policy 约束的 action 序列；30 域、231 slot。
- **许可证**: **[待核实，通常 MIT/研究用]**。
- **verifier**: 有标注 action 序列与 Agent flow，可对"预测 action / 下一步"做**确定性比对**（accuracy / action-sequence match），不需 LLM judge。
- **运行依赖**: 轻，可离线（纯文本 JSON）。无强制工具/模拟器。
- **磁盘量级**: 数十 MB 级 **[待核实]**。
- **契合度: 3.5/5**。理由: action 序列可确定性判分 + 有 policy 约束，适合离线 demo；但它是静态语料，无活的 DB/工具环境，"经验复用持续改进"需自己搭闭环。

### 4. Bitext Customer-Support（HuggingFace）—— 最省事的离线泛化演示集

- **链接**: https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset ；同步 GitHub: bitext/customer-support-llm-chatbot-training-dataset ；Kaggle 镜像
- **年份/现状**: 持续维护的开源集，社区常用作客服意图/SFT 数据。非 benchmark，是**训练/语料**集。
- **任务形式**: 26,872 条 Q/A 对，**27 intents / 10 categories**，每 intent 约 1000 条；30 实体类型；**12 种语言变体 tag（COLLOQUIAL / 拼写错误 / 礼貌度等）**——天然提供"同类不同表述"。
- **许可证**: **CDLA-Sharing-1.0 [待核实，请在 HF 页确认]**（允许商用/分享）。
- **下载/运行**: `datasets.load_dataset(...)` 或直接下 CSV；**完全离线可用**。
- **verifier 如何判分**: 数据自带 `intent`/`category` 标签 → 把任务设为**意图分类/路由**即可**确定性判分**（预测 intent == 标签）；答案文本可做检索/生成参考。**无需 LLM judge**。
- **运行依赖**: 极轻，纯数据，无需 LLM/工具/模拟器。
- **磁盘量级**: 单 CSV，**约 10–30 MB 级**（3.57M tokens）。
- **契合度: 4/5**（针对离线确定性 demo）。理由: (1) intent 标签 = 现成客观 verifier；(2) 同 intent 多表述 + 变体 tag = 直接演示泛化；(2) 易 train/eval 切分。短板: 单轮、无工具/DB、无多轮 policy 闭环，"经验复用"要靠我们在分类/路由任务上自己造闭环（如错例进记忆 → 下轮命中率提升）。**体积与离线性对 27GB 机器最友好**。

### 5. 记忆类: LoCoMo / LongMemEval / MSC

- **LoCoMo**: GitHub snap-research/locomo（`data/locomo10.json`，仅 **10 段超长对话**，1540 问）；HF 镜像 Percena/locomo-mc10 约 **492MB**（含 MC 版）。许可 **CC BY-NC 4.0（仅非商用）**。ACL 2024。判分: QA 答案比对（single/multi-hop/temporal），部分需 LLM judge 或 EM/F1。
- **LongMemEval**: GitHub xiaowu0162/LongMemEval（ICLR 2025）；HF xiaowu0162/longmemeval。**LongMemEval-S 约 278MB**（500 问 / 每问 ~48 session / ~115K tokens）；cleaned 版约 **3.03GB**。判分: 5 类记忆能力 QA，多用 LLM judge / 答案匹配。
- **MSC（Multi-Session Chat）**: Meta/ParlAI 经典多会话记忆集，2021，**[规模/许可待核实]**。
- **现状**: 2026 记忆 benchmark 圈仍以 LoCoMo + LongMemEval + 新增 **BEAM（1M/10M token 规模）** 为主三件套（来源 mem0.ai 2026 综述）。
- **契合度: 3/5**。理由: 直接对应"长期记忆"，但它们测的是"对历史对话的 QA 记忆"，**不是"经验复用提升任务成功率"**，且偏 LLM-judge、偏研究 QA，离我们的"客服任务自改进闭环"有距离。可作记忆能力侧评测的补充背书。LoCoMo 的 CC BY-NC 商用受限需注意。

### 6. 其它 / 经验复用方法（非数据集，但可引用）

- **MultiWOZ 2.x**: 经典任务型多域对话（餐厅/酒店/出租等），有 DB 与 slot 标注，可做 DST/对话状态确定性判分。2026 仍被用，但偏"对话状态跟踪"而非"agent 经验复用"。契合度 3/5，离线可用，体积百 MB 级 **[待核实]**。
- **DialogStudio**: Salesforce 聚合的对话数据集统一格式集合（含 MultiWOZ/ABCD 等），适合一站式取数。**[2026 维护状态待核实]**。
- **CER（Contextual Experience Replay, arXiv:2506.06698）/ AgentHER（arXiv:2603.21357, github alphadl/AgentHER）**: 不是数据集，是"经验复用/hindsight relabel"**方法论**，在 WebArena 等上验证。可作为我们机制设计与 related work 的直接对标参考。

---

## 三、候选对比小表

| 数据集 | 年份/2026主流 | verifier客观性 | train/eval+多表述 | 工具/DB上下文 | 经验复用契合 | 运行依赖 | 体积 | 离线 | 许可 | 契合度 |
|---|---|---|---|---|---|---|---|---|---|---|
| τ²-bench | 2024→2026 主流权威 | 极强(DB哈希,无人工) | 弱(无现成泛化划分) | 强(工具+DB+policy) | 中(pass^k看改进) | 重(LLM+用户模拟器) | 数据MB,跑费API | 否 | MIT | **5** |
| SkillLearnBench | 2026新/未成标准 | 中(LLM-as-judge) | 弱(仅~100例) | 中(真实任务+工具) | 强(continual skill) | 很重(Docker+LLM) | 数据MB,镜像数GB? | 否 | MIT | **4** |
| Bitext CS | 持续维护 | 强(intent标签) | 强(27intent+变体tag) | 弱(无工具/DB) | 需自建闭环 | 极轻(纯数据) | ~10-30MB | 是 | CDLA?待核实 | **4** |
| ABCD | 2021经典 | 强(action序列比对) | 中 | 中(action+policy) | 需自建闭环 | 轻 | 数十MB? | 是 | 待核实 | 3.5 |
| LoCoMo | 2024 | 中(QA,部分LLM) | 弱(10段对话) | 弱 | 中(长期记忆) | 中(常需LLM) | ~492MB | 半 | CC BY-NC(非商用) | 3 |
| LongMemEval-S | 2025 | 中(QA/LLM-judge) | 弱 | 弱 | 中(记忆) | 中 | 278MB(cleaned 3GB) | 半 | 待核实 | 3 |
| MultiWOZ 2.x | 经典/仍用 | 强(DST比对) | 中 | 中(DB+slot) | 弱 | 轻 | 百MB?待核实 | 是 | 待核实 | 3 |

---

## 四、最终建议

### ① 若只接一个真实数据集 → 首推 **τ²-bench**
- 理由: 它是 2026 客服 Agent 的**事实标准**，verifier 是**确定性 DB 哈希比对 + pass^k**，完美满足"无人工标注的客观判分"，且自带工具/DB/policy 这套可检索上下文。简历上"在 τ-bench 上把 pass^1 / pass^k 提升 X%"是**最硬的可信度背书**。
- **集成代价: 中偏高**。MIT、clone 即用、数据本体 MB 级（不吃 27GB 磁盘）；但**必须联网 LLM**（被测 Agent + 用户模拟器都要 API），有 token 成本，无法离线。需要写 Agent 接入层（policy + 工具 schema）。

### ② 强烈建议"合成集做确定性 demo + 真实集做背书"的混合方案 —— 推荐
分两层落地，正好规避磁盘/离线限制：

- **离线确定性 demo 层（主干，给面试官现场跑）**: 用 **Bitext CS（~20MB，离线，intent 标签=确定性 verifier）** 或自合成客服工单集，搭建"工单→分类/路由/解决"任务。自进化闭环 = 错例/成功轨迹写入记忆 → 下一轮命中率/成功率确定性上升。**完全离线、零 API、秒级可复现**，是 demo 主体。
- **真实背书层（说服力，跑一次留结果即可）**: 接 **τ²-bench** 跑小规模（如 retail 子集），展示"开记忆/经验复用 vs 关闭"的 pass^k 差异；再**引用 SkillLearnBench 论文结论**（外部反馈才能真改进、纯自反馈会递归漂移）佐证我们机制设计的合理性。τ²-bench 只需跑一次出图，不必常驻。

> 磁盘策略: 主用 Bitext(MB级) + τ²-bench(数据MB级)，二者都不压 27GB。**避免**直接下 LongMemEval-cleaned(3GB) / LoCoMo-mc10(492MB) 这类大件，除非确需记忆 QA 侧评测；若要做记忆评测，优先 LongMemEval-S(278MB)。

---

## 五、待核实清单
- Bitext 数据集确切许可证（疑似 CDLA-Sharing-1.0，需在 HF 页确认）。
- τ²-bench 各域确切任务数。
- SkillLearnBench Docker 镜像实际磁盘占用。
- ABCD 仓库现状与许可证；MultiWOZ 2.4 体积与许可；DialogStudio 2026 维护状态；MSC 规模/许可。
- LongMemEval 各版本许可证。
