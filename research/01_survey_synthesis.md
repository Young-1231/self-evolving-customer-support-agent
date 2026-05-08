# 自进化 Agent 调研笔记（Self-Evolving Agents）

> 调研日期：2026-05-28
> 定位：以 2025 年底—2026 年 5 月的最新工作为主体；两篇奠基综述仅用于搭建分类骨架。
> 溯源原则：每条工作尽量给出 arXiv 号 / 链接，并标注年份归属（2024–2025 vs 2026 最新）。拿不准处显式标注「待核实」。

---

## 1. 定义与「为什么 2026 成为热点」

**自进化 Agent（Self-Evolving / Self-Improving Agent）** 指：在部署之后仍能基于与环境/任务/反馈的交互，**自主、持续地改进自身**（模型权重、上下文、记忆、提示、工具、架构或工作流），而无需人类对每一步进行重新设计或重新训练的智能体。其核心是把 Agent 从「离线知识的被动接收者」转变为「自身认知成长的主动参与者」，形成 *交互 → 反馈 → 自我修改 → 验证 → 归档* 的闭环。

**为什么 2026 成为热点（一段话）**：基础模型能力在 2024–2025 趋于平台期，单纯靠更大预训练的边际收益下降；与此同时 Agent 大规模落地暴露出「静态能力 vs. 开放世界持续变化」的根本矛盾——任务分布漂移、工具生态更新、长程任务需要积累经验。2025 年 5 月的 Darwin Gödel Machine（自改代码）、2025 年 7–8 月两篇大综述、以及 2025 下半年 test-time training / 经验驱动记忆等线索汇聚，使「让 Agent 自己变强」从零散 trick 升级为独立研究范式。进入 2026 年，研究重心已从「能不能自进化」转向「**如何安全、可验证、可评测地自进化**」，并涌现出大量带 benchmark、带安全分析的系统工作。

---

## 2. 分类框架（骨架来自两篇奠基综述，格子尽量填 2026 工作）

骨架参考：
- 综述 A：*A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve*（arXiv **2507.21046**，2025-07）—— 提出 What / When / How / Where 四问。
- 综述 B：*A Comprehensive Survey of Self-Evolving AI Agents*（arXiv **2508.07407**，2025-08）—— 提出统一反馈回路四组件：System inputs / Agent System / Environment / Optimisers，进化对象覆盖 foundation model、prompt、memory、tools、workflow、communication。

> 二者已被多个综述/资源列表沿用（如 GitHub `XMUDeepLIT/Awesome-Self-Evolving-Agents`、`EvoAgentX/Awesome-Self-Evolving-Agents`）。

### 2.1 What to evolve（进化什么）

| 进化对象 | 含义 | 2026 代表工作（链接见第 3 节） |
|---|---|---|
| **模型权重 (model)** | 在线/测试时微调更新参数 | TT-SI / TT-D（test-time training，2510.07841）；TTC-RL（test-time curriculum，2510.04786）；Tool-R0（2602.21320） |
| **上下文 (context)** | 演化系统输入/上下文工程 | ACE（Agentic Context Engineering 思路，见 2510.16079 生态）；GEPA 产出的文本组件（2507.19457） |
| **记忆/经验 (memory)** | 维护并精炼经验库 | ReMe 程序性记忆（2512.10696）；TAME 测试时记忆演化（2602.03224）；EvolveR 经验生命周期（2510.16079）；SSGM 记忆治理（2603.11768） |
| **提示 (prompt)** | 反思式提示进化 | GEPA（2507.19457，ICLR 2026 Oral）；MASS 块级提示优化（2502.02533 系） |
| **工具 (tools)** | 自造/自改工具 | Tool-Genesis（2603.05578）；Tool-R0（2602.21320）；Self-Evolved ABC / EDA（2604.15082） |
| **架构/拓扑 (architecture)** | 自动搜索多智能体结构 | AutoMaAS（2510.02669）；ABSTRAL（2603.22791）；HyperAgents/DGM-H（2603.19461） |
| **工作流 (workflow)** | 自动设计算子/流程 | AFlow / MaAS / MASS（2024–2025 基线）+ 2026 后续 |

### 2.2 When to evolve（何时进化）

- **Test-time（推理时/在线）**：TT-SI、TTC-RL、TTRL、TAME、TIDE（评测）。这是 2026 最活跃的子方向。
- **Between-task（任务间）**：EvolveR（offline 原则抽取 + online 应用）、ReMe（任务后精炼程序性记忆）、SkillLearnBench 评测的技能持续生成。
- **Lifelong（终身/跨会话）**：StuLife / ELL 框架（2508.19005）；COLM 2026「Lifelong Agents」Workshop 主题；记忆演化综述（2605.06716）。

### 2.3 How to evolve（怎么进化）

- **基于反馈/反思（textual gradient）**：GEPA 读执行 trace、用自然语言诊断失败并维护 Pareto 前沿。
- **强化学习（RL / online RL）**：EvolveR 的 policy reinforcement、TTRL/TTC-RL、Tool-R0 共进化、CoMAS 交互奖励。
- **进化算法 / open-ended**：Darwin Gödel Machine 及 HyperAgents（archive + 采样 + 自改 + 验证）、AutoMaAS 架构搜索。
- **Memory-based / 经验复用**：ReMe、TAME、Mem0 / A-MEM 类记忆系统。
- **Self-play / 协同进化（reward-free）**：Multi-Agent Evolve（MAE）、SAGE、π-Play、CoMAS、Spontaneous Reward-Free Self-Evolution（2604.18131）。

### 2.4 Where to evolve（在哪进化）

- **Single-agent**：TT-SI、EvolveR、ReMe、DGM。
- **Multi-agent（协同/对抗共进化）**：MAE、SAGE、π-Play、CoMAS、AutoMaAS、ABSTRAL、HyperAgents。
- **Domain-specific**：推荐系统（2602.10226，称已在 YouTube 部署，**待核实部署细节**）、EDA/逻辑综述（2604.15082）、编程（SWE-Bench-CL）、漏洞挖掘（2604.20801）。

---

## 3. 【重点】2025 末 — 2026 最新进展专题

> 标注规则：年份取自 arXiv 编号月份（如 `25xx`=2025，`26xx`=2026）。`待核实` 表示该细节仅来自二手摘要、未逐字核对原文。

### 3.1 记忆 / 经验 自进化

1. **EvolveR — Self-Evolving LLM Agents through an Experience-Driven Lifecycle**
   - arXiv **2510.16079**（2025-10 提交，终稿 2026-05；**Accepted by ICML 2026**）
   - 核心：闭环「经验生命周期」——离线把交互轨迹蒸馏成可复用的*策略原则*，在线检索并应用；用 RL 机制迭代更新 agent。
   - 进化了什么：**问题求解策略 / 经验库**（新原则语义去重、持续评估）。
   - 机制：offline 原则抽取 + online 任务交互 + policy reinforcement。在多跳 QA 上超强 agentic 基线。

2. **ReMe — Remember Me, Refine Me: A Dynamic Procedural Memory Framework for Experience-Driven Agent Evolution**
   - arXiv **2512.10696**（2025-12）
   - 核心：**程序性记忆**（how-to 知识）让 agent 内化「做法」，减少重复试错。
   - 亮点：装备 ReMe 的小模型可超越无记忆机制的大模型。
   - 机制：动态精炼程序性记忆（写入 + 提炼）。

3. **TAME — Trustworthy Test-Time Evolution of Agent Memory with Systematic Benchmarking**
   - arXiv **2602.03224**（2026-02）
   - 核心：测试时记忆演化（Test-Time Memory Evolution）+ 系统化 benchmark；提出并应对 **Agent Memory Misevolution**（良性任务演化中安全对齐仍会退化）。
   - 进化了什么：分离演化 **executor memory**（提性能）与 **evaluator memory**（提安全/效用判断）。
   - 机制：记忆过滤 → 草稿生成 → 可信精炼 → 执行 → 双轨记忆更新 的闭环。

4. **From Storage to Experience: A Survey on the Evolution of LLM Agent Memory Mechanisms**（综述）
   - arXiv **2605.06716**（2026-05，最新）
   - 三大演化驱动：长程一致性、动态环境、持续学习；讨论主动探索与跨轨迹抽象。配套列表 `FeishuLuo/Evolving-LLM-Agent-Memory-Survey`。

5. **SSGM — Governing Evolving Memory in LLM Agents（Stability and Safety Governed Memory）**
   - arXiv **2603.11768**（2026-03）
   - 核心：从静态记忆存储 → 自适应自精炼记忆系统的范式转移，并提出稳定性与安全治理框架。

6. **Memory as Asset: From Agent-centric to Human-centric Memory Management**
   - arXiv **2603.14212**（2026-03）—— 记忆管理视角的转移（**待核实细节**）。

> 产业侧参照（非 arXiv，归 2024–2026 工程线）：**Mem0**（2025 广泛采用，AWS Agent SDK 独家记忆方；ECAI 2025 / arXiv 2504.19413 基准；2026-04 发布单遍分层抽取 + 多信号检索的新算法，时序查询 +29.6、多跳 +23.1）；**A-Mem: Agentic Memory for LLM Agents**（arXiv **2502.12110**，2025，属 2025 工作）。这两者代表「记忆即工程组件」路线，与上面学术的「记忆自进化」互补。

### 3.2 提示 / 工作流 自动优化

7. **GEPA — Reflective Prompt Evolution Can Outperform Reinforcement Learning**
   - arXiv **2507.19457**（Agrawal et al., 2025-07；**ICLR 2026 Oral**）；已并入 DSPy（`dspy.GEPA`）并有独立库。
   - 核心：Genetic-Pareto 反思式优化器——读 trace、自然语言诊断失败、维护多样化 Pareto 前沿提示候选。
   - 进化了什么：任意系统的**文本组件（prompt/代码等）**。
   - 效率：10 个训练样例、20–100 次评估即可改进；在 Qwen3-8B 上较 GRPO 高至 20%、较 MIPROv2 高约 13%。属「textual gradient」How 路线的代表。

8. **DSPy / MASS / MaAS / AFlow / ADAS（2024–2025 基线，2026 仍是对比基准）**
   - ADAS：LLM 元 agent 用代码写新拓扑；AFlow：MCTS 搜工作流（ICLR 2025 Oral）；MaAS：概率超网采样（ICML 2025 Oral）；MASS（arXiv **2502.02533**，2025）：块级提示优化 + 拓扑优化 + 工作流级优化协同。**这些是 2026 自动化 agent 设计的标准基线，需要在项目中作对照。**

### 3.3 自动化 Agent / 多智能体架构设计

9. **AutoMaAS — Self-Evolving Multi-Agent Architecture Search**
   - arXiv **2510.02669**（2025-10）
   - 核心：自进化的多智能体架构搜索（面向 LLM）。进化对象：**多智能体拓扑/架构**。

10. **ABSTRAL — Automated Multi-Agent System Design via Skill-Referenced Adaptive Search**（又名 Automatic Design of MAS Through Iterative Refinement and Topology Optimization）
    - arXiv **2603.22791**（2026-03）
    - 核心：技能引用 + 自适应搜索，迭代细化 + 拓扑优化自动设计 MAS。

11. **HyperAgents / DGM-H（Darwin Gödel Machine 的 2026 直接后续）**
    - arXiv **2603.19461**，**Meta Research，2026-03**
    - 核心：把*任务 agent* 与*元 agent（改进机制）*合并进**同一个可编辑代码库**，实现「不仅改任务表现，还改改进过程本身」的元认知自修改。解决了原 DGM 元机制手工固定、换域需重写的瓶颈。
    - 机制：三层嵌套循环（任务执行 / 评估反馈 / 元认知自修改）+ 历史成功变体 archive + 选亲本 + 改写任务与改进逻辑 + 验证归档。
    - 结果：把改进机制从 paper review/robotics **跨域迁移**到未见的奥数评分（improvement@50 = 0.630，基线为 0）；系统自发长出持久记忆、性能追踪、算力规划。

12. **Autogenesis — A Self-Evolving Agent Protocol**
    - arXiv **2604.15034**（2026-04）
    - 核心：Self-Evolution Protocol Layer（SEPL），定义「提议—评估—提交改进」的闭环算子接口，构建自进化多智能体系统。

13. **SEVerA — Verified Synthesis of Self-Evolving Agents**
    - arXiv **2603.25111**（2026-03）
    - 核心：Search + Verification + Learning 三阶段，合成候选参数化程序并**对硬约束做正确性证明**；做到零约束违反同时优于无约束/SOTA 基线。是「可验证自进化」这一 2026 新趋势的代表。

14. **AgentDevel — Reframing Self-Evolving LLM Agents as Release Engineering**
    - arXiv **2601.04620**（2026-01）
    - 核心：把自进化视为**发布工程**——agent 是可发布制品，改进外化为「回归感知的发布流水线」。工程化视角，对落地有借鉴。

### 3.4 自改代码 / 开放式进化（DGM 谱系）

15. **Darwin Gödel Machine（DGM，奠基级 2025 工作，2026 仍是核心基线）**
    - arXiv **2505.22954**（2025-05；ICLR 2026 Poster）；代码 `jennyzzt/dgm`。
    - 核心：迭代自改代码 + 用编程 benchmark 实证验证每次修改；维护生成式 agent archive，采样→自改→形成多样高质量 agent 树。
    - 结果：SWE-bench 20.0%→50.0%，Polyglot 14.2%→30.7%；自发学出更好的代码编辑工具、长上下文管理、peer-review 机制。
    - 关联：Gödel Agent（arXiv 2410.04444，2024）是更早的自指递归自改框架。

### 3.5 Test-time / 在线 self-improvement

16. **TT-SI / TT-D — Self-Improving LLM Agents at Test-Time**
    - arXiv **2510.07841**（UIUC，2025-10）
    - 核心：三步——自我觉察（找不确定样本）→ 自数据增广 → 测试时微调。TT-SI 平均 +5.48% 且用样本少 **68×**；TT-D 用更强模型蒸馏。

17. **TTC-RL — Learning on the Job: Test-Time Curricula for Targeted RL**
    - arXiv **2510.04786**（2025-10）
    - 核心：测试时为目标任务自动从大池里选最相关数据组装课程，继续 RL 训练，显著提升多任务 pass@1。

18. **TTRL — Test-Time Reinforcement Learning**（2025 线，相关基础）
    - 核心：利用预训练先验在测试时自我演化（Test-Time Scaling + Test-Time Training）；Qwen2.5-Math-7B 在 AIME 2024 pass@1 提升约 159%。（**具体 arXiv 号待核实**）

19. **Spontaneous, Reward-Free Self-Evolution via World Knowledge Exploration**
    - arXiv **2604.18131**（2026-04）
    - 核心：训练 agent 具备**内在元进化能力**，在任务执行前自发探索未见环境，摆脱对人定义奖励/规则的依赖。

20. **TIDE — Trajectory-based Diagnostic Evaluation of Test-Time Improvement in LLM Agents**
    - arXiv **2602.02196**（2026-02）—— 面向「测试时改进」的轨迹级诊断评测（属评测线，见第 4 节）。

### 3.6 工具自进化 / 自造工具

21. **Tool-Genesis — A Task-Driven Tool Creation Benchmark for Self-Evolving Language Agent**
    - arXiv **2603.05578**（2026-03）
    - 核心：评测 agent 能否仅从**抽象需求**（无预设规格）造出任务相关工具并解题。发现：SOTA 模型在一次性设定下难以产出精确工具接口/可执行逻辑，初始小瑕疵会沿管线放大。

22. **Tool-R0 — Self-Evolving LLM Agents for Tool-Learning from Zero Data**
    - arXiv **2602.21320**（2026-02）
    - 核心：通用**共进化**框架，LLM 由用户需求驱动、跨任意域自主学习开放式工具使用，零数据起步。

23. **Self-Evolved ABC — Autonomous Evolution of EDA Tools（Multi-Agent）**
    - arXiv **2604.15082**（2026-04）
    - 核心：首个自进化逻辑综合框架，LLM agent 自主改进源代码，发现超越人工启发式的优化、学出新综合策略。领域落地代表。

### 3.7 多智能体 self-play / 协同进化（reward-free）

24. **MAE — Multi-Agent Evolve: LLM Self-Improve through Co-evolution**
    - arXiv **2510.23595**（2025-10）
    - 核心：从单个基座 LLM 实例化 Proposer/Solver/Judge 三角色，自奖励闭环，无需外部监督或领域真值，把 self-play 扩展到通用域。

25. **SAGE — Multi-Agent Self-Evolution for LLM Reasoning**
    - arXiv **2603.15255**（2026-03）
    - 核心：闭环四角色 Challenger（出题）/ Planner / Solver / Critic 共进化。

26. **π-Play — Multi-Agent Self-Play via Privileged Self-Distillation without External Data**
    - arXiv **2604.14054**（2026-04）
    - 核心：无外部数据的自蒸馏 self-play；较 Search-R1 在 Qwen3 系列 +6.2% / +5.2% / +14.5%。

27. **CoMAS — Co-Evolving Multi-Agent Systems via Interaction Rewards**
    - arXiv **2510.08529**（2025-10）—— 用交互式奖励信号驱动多智能体自进化。

28. **One Model, All Roles — Multi-Turn, Multi-Agent Self-Play RL**
    - arXiv **2602.03109**（2026-02，**待核实细节**）。

### 3.8 安全 / 可信（2026 新增独立子方向）

29. **Your Agent May Misevolve: Emergent Risks in Self-evolving LLM Agents**
    - arXiv **2509.26354**（2025-09）
    - 核心：定义 **Misevolution**——自进化偏离意图导致有害结果；沿 model/memory/tool/workflow 四路径评估。
    - 关键实证：连 Gemini-2.5-Pro 也受影响；一编程 agent 引入自身记忆后，对有害提示拒答率 **99.4%→54.4%**、攻击成功率 **0.6%→20.6%**。

30. **On Safety Risks in Experience-Driven Self-Evolving Agents**
    - arXiv **2604.16968**（2026-04）—— 经验积累不受控会使 agent 漂移进不安全区。

> 安全已成为 2026 标配章节：TAME（3.3）、SSGM（3.1）、SEVerA（可验证，3.3）都在回应这一风险。

---

## 4. 关键 Benchmark 与评测方式（2026 如何衡量「进化是否有效」）

| Benchmark | arXiv / 来源 | 年份 | 衡量什么 |
|---|---|---|---|
| **StuLife / ELL** | 2508.19005 | 2025-08（终稿 2026-01） | 经验驱动**终身学习**：模拟完整大学旅程（3 阶段 10 子场景）。即便 GPT-5 仅 17.9/100，凸显长期记忆与自驱动能力缺口。 |
| **SkillLearnBench** | 2604.20087；`cxcscmu/SkillLearnBench` | 2026-04 | 首个评测**持续技能生成**的 benchmark，20 个可验证、依赖技能的任务 / 15 子域；三层评估（技能质量 / 执行轨迹 / 任务结果）。关键发现：自反馈单独会致「递归漂移」，需外部反馈才有真实进步；更强 backbone 不必然产出更好技能。 |
| **SWE-Bench-CL** | 2507.00014 | 2025-07 | 把 SWE-Bench Verified 重构成按时间排序的持续学习序列，模拟开发者对项目的长期参与。 |
| **Tool-Genesis** | 2603.05578 | 2026-03 | 评测从抽象需求**自造工具**的能力。 |
| **TAME（含 benchmark）** | 2602.03224 | 2026-02 | 系统化评测测试时记忆演化的可信性（性能 vs 安全双轨）。 |
| **TIDE** | 2602.02196 | 2026-02 | **轨迹级诊断**评测测试时改进——不只看终点指标，看改进过程。 |
| **Misevolution 评测** | 2509.26354 | 2025-09 | 沿 model/memory/tool/workflow 量化安全退化（拒答率、攻击成功率）。 |
| **DGM 用的编程基准** | SWE-bench / Polyglot | 2025 | 用任务通过率衡量自改代码带来的能力增长。 |

**评测范式趋势（2026）**：
1. 从「单点任务准确率」→「**序列化 / 终身**评估」（StuLife、SWE-Bench-CL、SkillLearnBench）。
2. 从「只看终点」→「**过程 / 轨迹诊断**」（TIDE）。
3. 性能与**安全双轨**并列评估成为新常态（TAME、Misevolution）。
4. 新增「**自造能力**」维度（Tool-Genesis 评工具创造、SkillLearnBench 评技能生成）。

---

## 5. 现有方法的局限与开放问题（找切入点）

1. **自反馈的递归漂移（recursive drift）**：SkillLearnBench 明确指出仅靠自反馈会退化，需外部反馈才有真实进步。→ 切入点：低成本外部信号 / 验证器设计。
2. **Misevolution / 安全退化**：记忆积累会侵蚀安全对齐（拒答率 99.4%→54.4%）。→ 切入点：可证明约束（SEVerA 路线）、记忆治理（SSGM）、双轨评估器（TAME）。
3. **自造工具/技能质量不稳**：Tool-Genesis 显示一次性造工具的小瑕疵会沿管线放大；更强 backbone 不一定造出更好技能。→ 切入点：工具/技能的验证—修复闭环。
4. **元机制仍偏手工**：DGM 元机制固定、换域需重写；HyperAgents 才把元机制放进可编辑代码库——但开放式自改的稳定性、算力成本、安全边界仍未解。→ 切入点：受控的元进化 + 预算约束。
5. **评测碎片化**：benchmark 各管一段（记忆 / 技能 / 工具 / 代码），缺少统一的「长程、过程级、安全-性能联合」评测协议。→ 切入点：统一评测框架。
6. **reward-free 自进化的可靠性**：MAE / π-Play / Spontaneous（2604.18131）摆脱外部奖励很吸引人，但自奖励质量、是否会 reward hacking、能否泛化到真实开放任务，尚缺严谨验证。
7. **记忆「自进化」vs「工程化记忆」割裂**：学术（TAME/ReMe/EvolveR）与产业（Mem0/A-Mem）路线尚未打通；缺少把可进化记忆嵌入生产级系统并长期评测的工作。
8. **可验证 / 可回滚**：AgentDevel 把自进化当发布工程、SEVerA 加形式验证——「可验证、可回滚、回归感知」的自进化是 2026 刚起步、值得深耕的方向。

---

## 附：年份归属速查

- **2024**：Gödel Agent（2410.04444）。
- **2025**：DGM（2505.22954）、MASS（2502.02533）、A-Mem（2502.12110）、GEPA（2507.19457）、综述 A/B（2507.21046 / 2508.07407）、StuLife（2508.19005）、Misevolution（2509.26354）、AFlow/MaAS/ADAS、TT-SI（2510.07841）、TTC-RL（2510.04786）、EvolveR（2510.16079，终稿 2026）、AutoMaAS（2510.02669）、MAE（2510.23595）、CoMAS（2510.08529）、ReMe（2512.10696）。
- **2026（主体）**：AgentDevel（2601.04620）、One-Model-All-Roles（2602.03109）、TIDE（2602.02196）、TAME（2602.03224）、Tool-R0（2602.21320）、SSGM（2603.11768）、Memory-as-Asset（2603.14212）、SAGE（2603.15255）、HyperAgents/DGM-H（2603.19461）、ABSTRAL（2603.22791）、SEVerA（2603.25111）、Tool-Genesis（2603.05578）、π-Play（2604.14054）、Autogenesis（2604.15034）、Self-Evolved ABC（2604.15082）、On Safety Risks（2604.16968）、Spontaneous Reward-Free（2604.18131）、SkillLearnBench（2604.20087）、Vulnerability Harnesses（2604.20801）、记忆演化综述（2605.06716）、推荐系统自进化（2602.10226，部署细节待核实）。

> 注：凡标「待核实」者，建议在引用前下载原文逐字核对（部分细节来自检索摘要的二手转述）。
