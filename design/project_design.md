# 自进化客服 / 企业知识库 Agent —— 项目设计文档

> 文档版本：v1.0　|　撰写日期：2026-05-28
> 项目代号：**Self-Evolving Customer-Support Agent（SECSA）**
> 文档定位：面向"资深 Agent 算法工程师"求职的简历项目完整设计稿，供作者本人在面试中深聊使用。
> 溯源约定：关键论点尽量标注来源工作名 / arXiv 号，引用自配套调研笔记 `research/01_survey_synthesis.md`、`research/02_github_landscape.md`、`research/03_industry_and_jd.md`。

---

## 0. 阅读导航（文档目录）

1. 项目一句话定位 + 真实痛点
2. 与 2026 SOTA 的关系（借鉴谱系 + 差异化取舍）
3. 系统架构（分层记忆 + self-RAG + 进化闭环，含 ASCII 图）
4. 自进化机制详解（经验写入 / Reflector 复盘 / playbook 治理 / 防 misevolution）
5. 数据闭环与评测方法（数据集 / verifier / 指标 / 进化曲线 / 防作弊）
6. 工程化与可扩展性（后端抽象 / 可观测性 / 配置 / 接真实业务）
7. 局限与未来工作
8. 简历呈现（项目简介 + STAR bullet + 技术亮点 + 面试追问）

---

## 1. 项目一句话定位 + 真实痛点

### 1.1 一句话定位

**SECSA 是一个"不改模型权重、靠分层记忆 + self-RAG + 离线复盘固化 playbook"实现持续自进化的客服 / 企业知识库 Agent：它把每一次答错都转化为可检索的经验和可审计、可回滚的可复用规则，让解决率随服役时间单调上升、重复错误率单调下降，而无需任何微调。**

### 1.2 解决什么真实痛点

企业客服 / 知识库 Agent 上线后的三个核心痛点：

1. **静态能力 vs. 开放世界漂移**：基础模型一旦部署即"知识冻结"，但工单分布、产品政策、知识库条目持续变化（`research/01` §1：基础模型平台期 + 静态能力 vs 开放世界的根本矛盾）。
2. **重复犯同一类错**：传统 RAG Agent 没有跨 session 记忆，昨天答错的问题今天还会以同样方式答错，人工反复救火。
3. **微调路线太重、太慢、风险高**：垂直微调需要标注、算力、回归测试，迭代周期以周/月计，且改权重难审计、难回滚，一旦引入安全退化无法定位。

**本项目的核心判断（直接来自产业调研结论）**：到 2026 年中，真正在企业规模化创造价值的"自进化"**不是改权重**，而是落在 **"记忆层 + 经验固化 + 离线复盘 + 可审计可回滚"** 这条工程主线上（`research/03` §1.1 / §1.3）。最硬的价值证据：

- Anthropic Claude Managed Agents 持久记忆（2026-04 public beta）：**首轮错误率 ↓约 97%、成本 ↓27%、延迟 ↓34%**（`research/03` §1.2.A）。
- Anthropic "Dreaming"（2026-05-06 research preview）：离线读历史 session 挖掘"反复犯的错 + 收敛 workflow"，写成**人类可读、可 review/approve/reject 的 playbook**，**不改权重**；Harvey 任务完成率 ↑约 6×、Wisedocs 审核时间砍半（`research/03` §1.2.B）。

SECSA 正是把这套"被产业验证、但闭源"的范式，做成一个**可本地零依赖跑通、有确定性客观评测、能画出进化曲线**的完整开源式复现，用来在简历上证明 6 项能力（`research/03` 文末清单）：记忆工程、离线复盘固化、技能复用、self-RAG 反馈闭环、评测度量、生产级治理。

---

## 2. 与 2026 SOTA 的关系

本项目不追求"发明新算法"，而是**把 2026 多条 SOTA 思想做正确的工程组合与裁剪**，使之落到一个可验证、可治理的窄域生产场景。下表点名借鉴对象与本项目的取舍。

| SOTA 工作（来源） | 借鉴的核心思想 | 本项目如何采用 | 差异化 / 取舍 |
|---|---|---|---|
| **Memento**（`research/02` §6；arXiv 2508 系，"不微调 LLM 而微调 agent"） | case-based memory：把成功/失败轨迹存入 Case Bank，按 value 检索复用，online 持续学习而非梯度更新 | episodic 经验池本质就是 Case Bank；答错→写入人工 ground-truth case→下一轮按相似度检索复用 | Memento 用神经 case-selection policy；本项目用**确定性向量检索 + 规则打分**，零训练、可解释、可在 mock 后端跑通 |
| **A-MEM**（`research/01` §3.1 / `research/02` §6；arXiv 2502.12110，NeurIPS'25） | Zettelkasten 式结构化记忆：记忆带结构化属性、自动建链、动态演化 | semantic/episodic 记忆条目带结构化 schema（intent、产品域、keypoints、关联 playbook id），支持 link | 不做 A-MEM 的全自动链接演化（成本高、难审计）；只保留**结构化 schema + 显式关联**，把"演化"权交给离线 Reflector + 人审 |
| **GEPA**（`research/01` §3.2；arXiv 2507.19457，ICLR'26 Oral） | reflective prompt evolution：读执行 trace、用自然语言诊断失败、维护 Pareto 前沿、textual gradient | Reflector 读失败 trace 用自然语言归纳失败模式→产出 playbook（自然语言规则），是 textual gradient 的轻量化 | 不做 GEPA 的提示种群 + Pareto 搜索（评估开销大）；进化对象从"prompt"换成"**可审计的 playbook 条目**"，更适合企业治理 |
| **AFlow / MaAS**（`research/01` §3.2 / `research/02` §3,5） | 工作流/架构是可优化对象；MCTS / agentic supernet 搜索最优结构 | self-RAG 的检索→生成→critic→escalate 流程视为"固定 workflow"，进化发生在**记忆与规则层**而非拓扑 | 明确**不搜索拓扑**：窄域客服场景下结构稳定更重要，把自由度收敛到记忆/规则，降低不可控性与成本 |
| **DGM / HGM 谱系**（`research/01` §3.4 / `research/02` §4；arXiv 2505.22954） | 自改 + 经验证驱动的进化 + archive + 选亲本 | 借鉴"每次进化都必须经客观 verifier 验证才生效"的实证驱动原则；playbook 带版本即简化版 archive | **坚决不自改代码/权重**（DGM 需沙箱、风险高、企业不可控）；只进化数据与规则，进化产物必须过人审 |
| **Anthropic "Dreaming"**（`research/03` §1.2.B） | 离线空闲时段读历史 session，挖掘反复犯的错 + 收敛 workflow，固化成人类可读 playbook，人审后上线，不改权重 | **本项目自进化闭环的直接蓝本**：Reflector = dreaming 的离线复盘；playbook 人审/版本/回滚 = dreaming 的 review/approve | dreaming 闭源、无客观评测；本项目补上**确定性 verifier + 进化曲线**，让"是否真进化、是否作弊"可被量化 |
| **TAME / SSGM / Misevolution**（`research/01` §3.1/§3.8；arXiv 2602.03224 / 2603.11768 / 2509.26354） | 记忆自进化会带来安全退化（misevolution）；需双轨记忆 + 记忆治理 | playbook 启用开关 + 版本 + 回滚 + 人审 + 安全 lint，是 SSGM"稳定性与安全治理"的工程落地 | 不实现 TAME 的双轨可信精炼全流程（重）；用更轻的"人审闸门 + 回滚 + 重复错误率监控"防漂移 |
| **EvolveR / ReMe**（`research/01` §3.1；arXiv 2510.16079 / 2512.10696） | 经验生命周期：离线把轨迹蒸馏成可复用策略原则，在线检索应用；程序性记忆减少重复试错 | procedural 记忆层（playbook）= 程序性记忆；离线归纳 + 在线检索生效，正是 EvolveR 的经验生命周期 | 不做 EvolveR 的 policy reinforcement（RL）；用确定性 verifier 反馈替代 RL 信号，零训练 |
| **SkillLearnBench 的教训**（`research/01` §5.1；arXiv 2604.20087） | 仅靠自反馈会"递归漂移"，必须引入外部反馈才有真实进步 | 训练阶段引入**人工 ground-truth solution**作为外部信号；评测用**独立确定性 verifier**而非 agent 自评 | 这是本项目防作弊设计的理论依据：**不让 agent 自己给自己打分驱动进化** |

**一句话差异化**：本项目 = **Memento 的 case-memory 思想 + A-MEM 的结构化记忆 + dreaming 的离线复盘固化 + SSGM/Misevolution 的安全治理**，但**全程零训练、零权重更新**，并补齐了上述闭源/学术工作普遍缺失的一环——**确定性、可防作弊的客观评测 + 进化曲线**。

---

## 3. 系统架构

### 3.1 总体 ASCII 架构图

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    用户 / 工单输入 (ticket)                 │
                         └───────────────────────────┬──────────────────────────────┘
                                                     │ query
                                                     ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────┐
   │                              SELF-RAG 推理引擎 (在线主回路)                                │
   │                                                                                          │
   │   ┌──────────────┐    检索   ┌──────────────────────────────────────────────────────┐   │
   │   │  Retriever   │◄─────────►│              分层记忆 (Layered Memory)                 │   │
   │   │ (multi-route)│           │                                                       │   │
   │   └──────┬───────┘           │  ┌────────────┐ ┌────────────┐ ┌──────────────────┐   │   │
   │          │ context           │  │  SEMANTIC  │ │  EPISODIC  │ │   PROCEDURAL     │   │   │
   │          ▼                   │  │  KB 检索   │ │ 经验池/案例 │ │ playbook/技能规则 │   │   │
   │   ┌──────────────┐           │  │ (产品文档) │ │(已解决工单)│ │(从失败归纳,带版本)│   │   │
   │   │  Generator   │           │  └────────────┘ └────────────┘ └──────────────────┘   │   │
   │   │ (LLM 后端)   │           └──────────────────────────────────────────────────────┘   │
   │   └──────┬───────┘                                       ▲                                │
   │          │ draft answer + cited keypoints                │ 检索经验/playbook 生效          │
   │          ▼                                                │                                │
   │   ┌──────────────┐  confidence < τ  ┌───────────────┐    │                                │
   │   │   Critic     │─────────────────►│   ESCALATE    │    │                                │
   │   │ (置信度评估) │                   │  转人工 (人工兜底)│   │                                │
   │   └──────┬───────┘                  └───────┬───────┘    │                                │
   │          │ confidence ≥ τ                   │ 人工给出 ground-truth solution               │
   │          ▼                                   │           │                                │
   │   ┌──────────────┐                           │           │                                │
   │   │  最终回答     │                           │           │                                │
   │   └──────────────┘                           │           │                                │
   └──────────────────────────────────────────────┼───────────┼────────────────────────────────┘
                                                  │           │
        ┌─────────────────────────────────────────┘           │  写入经验 (case write)
        │  评测阶段: verifier 判分 (keypoint + escalate 决策)   │
        ▼                                                      ▼
   ┌───────────────────┐                          ┌────────────────────────────────┐
   │  Verifier (确定性) │                          │   经验写入 (episodic 经验池)     │
   │  keypoint 覆盖率 + │                          │   错误案例 + 人工解决方案存档     │
   │  escalate 决策正确 │                          └───────────────┬────────────────┘
   └─────────┬─────────┘                                          │
             │ 指标 / 进化曲线                                      │ (离线触发)
             ▼                                                     ▼
   ┌───────────────────┐         ┌─────────────────────────────────────────────────────┐
   │  进化曲线产出       │         │       离线 REFLECTOR ("Dreaming" 复盘进程)            │
   │ 解决率↑ keypoint↑   │◄────────│  1. 拉取失败案例 → 2. 聚类失败模式 →                 │
   │ 重复错误率↓ 人工率↓ │  下一轮  │  3. 归纳候选 playbook → 4. 人审(approve/reject/edit)→ │
   └───────────────────┘  eval    │  5. 写入 procedural 记忆(带版本+启用开关)             │
                                  └─────────────────────────────────────────────────────┘
```

### 3.2 模块职责表

| 模块 | 职责 | 关键设计点 | 对标 |
|---|---|---|---|
| **Retriever** | 多路检索：KB（semantic）+ 相似历史经验（episodic）+ 相关 playbook（procedural），融合排序 | 三路独立召回 + 加权融合；可报 recall / 命中率（`research/03` §2.2.3 强调必须能报检索指标） | self-RAG / Agentic RAG |
| **Generator** | 基于检索上下文生成草稿回答，显式抽取所覆盖 keypoints | 受 playbook 约束（playbook 作为 system 级 hint 注入）；后端可换 | self-RAG |
| **Critic** | 对草稿给置信度（覆盖度 + 检索支持度 + 一致性），低于阈值 τ 则 escalate | 置信度是分流依据，**不是评测得分**（防自评作弊） | self-RAG critic / TAME evaluator memory |
| **Escalate 决策** | 低置信度转人工，触发人工给 ground-truth | 转人工"该转/不该转"本身是评测维度 | 产业 self-RAG 分流（`research/03` §1.2.D） |
| **Semantic 记忆** | 企业知识库 / 产品文档检索层 | 结构化 chunk + embedding；相对静态 | A-MEM 结构化 |
| **Episodic 记忆** | 经验池：历史已解决工单 / 错误案例 + 人工方案 | 案例 schema：query、intent、产品域、正确 keypoints、是否曾答错 | Memento Case Bank |
| **Procedural 记忆** | playbook / 技能：从失败归纳的可复用规则 | 每条带 id、版本、启用开关、覆盖条件、来源失败簇、人审状态 | EvolveR 策略原则 / ReMe 程序性记忆 / dreaming playbook |
| **Reflector（离线）** | "Dreaming"复盘：聚类失败、归纳 playbook | 离线批处理；产物进人审队列，不直接上线 | Anthropic Dreaming |
| **Verifier（评测）** | 确定性判分：keypoint 覆盖率 + escalate 决策正确性 | 独立于 agent，规则判分，无 LLM 自评 | SkillLearnBench"需外部反馈" |
| **后端抽象层** | mock / OpenAI 兼容 / vllm 三选一 | 统一接口，mock 确定性可零依赖跑通 | Memento 支持 vLLM executor |

### 3.3 数据流时序（一轮 eval 的生命周期）

```
阶段 A —— 在线推理 (单条 ticket)
  1. ticket → Retriever 三路召回 (KB chunks + similar cases + matched playbooks)
  2. 融合上下文 → Generator 生成 draft + 自报 covered_keypoints
  3. Critic 计算 confidence
       ├─ confidence ≥ τ → 输出回答，进入阶段 B 判分
       └─ confidence <  τ → ESCALATE 转人工
  
阶段 B —— 评测判分 (verifier，确定性)
  4. verifier 用 gold keypoints 计算 keypoint 覆盖率
  5. verifier 校验 escalate 决策是否正确 (该转未转 / 不该转却转 = 错)
  6. 汇总该轮指标

阶段 C —— 经验写入 (训练阶段，答错时)
  7. 若答错 → 获取人工 ground-truth solution
  8. 封装为 case 写入 episodic 经验池 (标记 was_wrong=true)

阶段 D —— 离线复盘 (Reflector, 批量触发)
  9. 拉取最近失败案例集合
  10. 按 (intent × 产品域 × 失败原因) 聚类失败模式
  11. 对高频失败簇归纳候选 playbook (自然语言规则 + 触发条件)
  12. 候选进人审队列 → approve/reject/edit
  13. approved playbook 写入 procedural 记忆 (version+1, enabled=true)

阶段 E —— 下一轮 eval
  14. 新 case + 新 playbook 通过检索在阶段 A 自动生效
  15. 重复 A→E，画进化曲线
```

---

## 4. 自进化机制详解

自进化闭环的核心原则（对标 dreaming，`research/03` §1.2.B）：**不改模型权重，只改"agent 能检索到的东西"（经验 + 规则），且每次进化都必须经客观验证、经人类审核、可版本化回滚。**

### 4.1 经验写入（episodic）

- **触发**：训练阶段，agent 对某 ticket 的回答被 verifier 判为未达标（keypoint 覆盖不足或 escalate 决策错误）。
- **动作**：调用人工接口获取该工单的 **ground-truth solution**（这是 SkillLearnBench 强调的"外部反馈"，`research/01` §5.1）。
- **写入内容（case schema）**：
  ```
  {id, query, intent, product_domain, gold_keypoints,
   correct_answer, was_wrong: true, root_cause_tag, ts, source: "human"}
  ```
- **生效方式**：下一轮 eval 时 Retriever 按相似度召回该 case，作为 few-shot 经验注入上下文——即"答错过的题，下次能检索到正确答案"。这是 Memento case-memory 的直接落地。

### 4.2 Reflector 如何聚类失败并归纳 playbook（离线 dreaming）

Reflector 是**离线批处理进程**（对应 dreaming 的"agent 空闲时段后台调度"），步骤：

1. **拉取失败集**：取 episodic 池中 `was_wrong=true` 的 case。
2. **聚类失败模式**：以 `(intent, product_domain, root_cause_tag)` 为主键聚类；同一簇代表"同一类反复犯的错"。可叠加 embedding 聚类做软分组。
3. **归纳候选 playbook**：对样本数 ≥ 阈值的高频失败簇，让 LLM 读该簇若干失败 trace + 人工正确方案，**用自然语言归纳出一条可复用规则**（textual gradient 思路，借鉴 GEPA / EvolveR 策略原则），形如：
   ```
   {playbook_id, title,
    trigger: {intent: "退款", product_domain: "订阅服务"},
    rule: "涉及订阅退款时，必须先确认是否过7天无理由期，并引用条款KB-1023；
           若已过期需说明按比例退款规则。",
    source_cluster: <cluster_id>, derived_from_cases: [...],
    version: 1, enabled: false, review_status: "pending"}
  ```
4. **入人审队列**：候选 playbook **默认 `enabled=false`、`review_status=pending`，绝不直接上线**。

### 4.3 playbook 的人审 / 版本 / 回滚 / 防 misevolution

这是本项目区别于"噱头自进化"的关键治理层（`research/03` §1.3 / §2.5："可审计可回滚是 2026 产业最看重的点"）。

| 治理维度 | 设计 | 对标 |
|---|---|---|
| **人审（HITL）** | 候选 playbook 必须经人 approve/reject/edit 才能 `enabled=true`。reject 记录原因供 Reflector 学习 | dreaming review；NVIDIA Verified Agent Skills（`research/03` §1.2.C） |
| **版本** | 每次修改 version+1，保留全部历史版本与 diff；记录 derived_from_cases 可溯源 | DGM archive 简化版 |
| **启用开关** | `enabled` 布尔位，可一键停用而不删除；停用后立即从检索层退出 | SSGM 记忆治理（arXiv 2603.11768） |
| **回滚** | 任一版本可回滚为当前生效版本；回滚即"把进化撤回"，配合曲线验证回滚是否恢复指标 | release engineering（AgentDevel，arXiv 2601.04620） |
| **安全 lint** | playbook 上线前过规则检查（禁止越权操作、禁止承诺无依据条款、禁止覆盖 escalate 安全红线） | Misevolution 防护（arXiv 2509.26354） |
| **misevolution 监控** | 每条 playbook 启用后跟踪其影响域的"重复错误率 / 攻击/越权拒答率"；若启用后某指标恶化即告警并建议回滚 | Misevolution 实证（拒答率 99.4%→54.4%，`research/01` §3.8）；On Safety Risks（arXiv 2604.16968） |

**防 misevolution 的核心逻辑**：自进化只发生在"可检索的规则与经验"层；每条规则**可单独定位、单独停用、单独回滚**；安全相关行为（如 escalate 红线、越权拒绝）受 lint 硬约束，playbook **不能覆盖安全红线**。这正面回应了 `research/01` §3.8 指出的"记忆积累侵蚀安全对齐"风险。

---

## 5. 数据闭环与评测方法

### 5.1 数据集设计：合成 NimbusFlow 客服数据集

- **场景**：虚构 SaaS 产品 "NimbusFlow"（订阅制工作流工具），覆盖账单/退款、功能用法、故障排查、账号权限、集成对接等多个 intent × product_domain。
- **构成**：
  - `kb/`：产品知识库文档（semantic 层检索源），每条带 doc_id。
  - `tickets_train.jsonl`：训练工单，每条含 `query, intent, product_domain, gold_keypoints[], should_escalate(bool), gold_answer`。
  - `tickets_eval.jsonl`：评测工单（与训练不重叠，含部分需 escalate 的难例 / 知识库未覆盖的"该转人工"样本）。
- **gold_keypoints**：每个工单的标准答案被拆成若干**必须命中的关键信息点**（如"确认7天无理由期""引用条款 KB-1023""说明按比例退款"），是确定性判分的锚点。
- **should_escalate**：人工标注该工单是否本就应转人工（KB 无依据 / 涉及越权 / 政策模糊），用于评测分流决策正确性。

### 5.2 verifier 判分逻辑（确定性，无 LLM 自评）

verifier **完全独立于 agent**，不调用任何会被进化影响的组件，规则判分：

1. **keypoint 覆盖率** = 命中 gold_keypoints 数 / gold_keypoints 总数。命中判定用确定性匹配（关键词/正则/规范化字符串匹配 + 可选 embedding 阈值，但阈值固定且不参与进化）。
2. **escalate 决策正确性**：对比 agent 实际是否 escalate 与 `should_escalate`：
   - 该转且转了 / 不该转且没转 → 正确
   - 该转却没转（漏转，最危险）、不该转却转了（过度转人工，损成本）→ 错
3. **单条是否"解决"**：定义为 `keypoint 覆盖率 ≥ 阈值 K 且 escalate 决策正确`（escalate 正确的难例视为"正确处理"）。

### 5.3 指标定义与进化曲线

每一轮 eval（在不断累积的经验/playbook 下重复跑同一 eval 集）记录：

| 指标 | 定义 | 期望趋势 |
|---|---|---|
| **解决率（resolution rate）** | 判为"解决"的工单占比 | ↑ 单调上升 |
| **keypoint 覆盖率** | 全体平均关键信息点命中率 | ↑ |
| **重复错误率（repeat-error rate）** | 在"此前已被写入经验/已有对应 playbook 的失败模式"上，本轮仍答错的比例 | ↓ 显著下降（核心卖点） |
| **人工介入率（escalation rate）** | 实际 escalate 占比 | ↓ 趋于合理（在保证不漏转前提下下降） |
| **漏转率（missed-escalation rate）** | should_escalate 但未转的比例（安全相关） | 保持极低，不因进化恶化 |

**进化曲线**：横轴为进化轮次（round 0 = 无经验无 playbook 的冷启动基线 → round N），纵轴为上述指标。对照组：
- baseline-0：纯 self-RAG，无 episodic / 无 playbook（冷启动）。
- +episodic only：只开经验池，不开 Reflector。
- +episodic +playbook（full）：完整自进化闭环。
通过三条曲线分离"经验复用"与"playbook 归纳"各自的贡献（消融实验，`research/01` 评测范式 §4：从单点准确率→序列化评估）。

### 5.4 如何防止"自我打分作弊"

这是面试高频深挖点（`research/03` §2.4：如何防 reward hacking / 评测泄漏），本项目四道防线：

1. **评分与决策分离**：Critic 的 confidence 只用于在线 escalate 分流，**绝不作为评测得分**；评测得分一律由独立 verifier 产出。
2. **gold 不进检索**：`gold_keypoints / gold_answer` 只供 verifier 使用，**绝不进入 episodic / semantic / procedural 记忆**，避免 agent"检索到答案"式泄漏。
3. **外部反馈驱动进化**：进化信号来自人工 ground-truth（外部），不是 agent 自评——直接回应 SkillLearnBench"仅自反馈会递归漂移"（`research/01` §5.1）。
4. **train/eval 隔离 + 失败模式溯源**：eval 集与 train 工单不重叠；重复错误率按"失败模式是否此前已固化"统计，防止靠记住具体 eval 样本刷分。

---

## 6. 工程化与可扩展性

### 6.1 LLM 后端抽象（三选一）

统一 `LLMBackend` 接口（`generate(prompt) -> text` / `embed(text) -> vec`），三实现：

| 后端 | 用途 | 特点 |
|---|---|---|
| **mock** | 默认，确定性、零依赖跑通整条闭环与 CI | 用规则/模板对合成数据产生可复现输出，保证评测与进化曲线可重跑、可对拍 |
| **openai 兼容 API** | 接 GPT/Claude 或任意 OpenAI 兼容网关 | 配置 base_url + api_key，生产/演示用 |
| **vllm** | 本地开源模型推理 | 对标 Memento 的 vLLM executor（`research/02` §6），私有化部署、控成本 |

mock 后端是关键工程决策：**让评审/面试官 `pip install` 后零成本一键跑出进化曲线**，证明闭环正确性而不被 API key / 算力卡住。

### 6.2 可观测性 / 日志

- 每条 ticket 全链路 trace：召回了哪些 KB/case/playbook、各路检索得分、draft、confidence、是否 escalate、verifier 结果。
- playbook 生命周期审计日志：创建/审核/启用/停用/回滚，含操作者与原因。
- 进化轮次快照：每轮记忆库与 playbook 集的版本指纹，保证实验可复现。
- 指标落盘为 jsonl + 自动出曲线图（对标 `research/03` §2.2.6 评测素养要求）。

### 6.3 配置

单一 YAML/CLI 配置：后端类型、检索 top-k 与三路权重、置信度阈值 τ、keypoint 解决阈值 K、Reflector 触发频率与失败簇最小样本数、人审是否强制、安全 lint 规则集。所有阈值显式可调且记录进 trace，便于消融。

### 6.4 如何接真实业务

- **接真实 KB**：替换 semantic 层数据源为企业向量库（产业标准件如 mem0 风格管线，`research/02` §6）；schema 不变。
- **接真实工单系统**：episodic 写入对接工单/CRM；ground-truth 来自客服坐席的最终解决记录。
- **接人工兜底**：escalate 对接真实人工坐席队列，人工解决后自动回流为新 case。
- **接 MCP / 多 agent**：Generator 可挂工具（查订单、查物流）走 MCP；procedural 层可升级为 NVIDIA"Verified Agent Skills"式技能治理（`research/03` §1.2.C / §2.2.7）。
- **可观测对接**：trace 接企业 observability/guardrail 栈。

---

## 7. 局限与未来工作（诚实）

结合 `research/01` §5 开放问题：

1. **自反馈天花板**：本项目进化质量上限受人工 ground-truth 质量约束；若人工标注本身有偏，经验池会放大偏差。SkillLearnBench 指出纯自反馈会递归漂移（`research/01` §5.1），本项目用人工外部信号缓解，但未做主动学习选样。
2. **playbook 归纳质量不稳**：LLM 归纳的规则可能过泛化/过具体（Tool-Genesis 指出一次性产物的小瑕疵会沿管线放大，arXiv 2603.05578）。当前靠人审兜底，未做自动"规则-验证-修复"闭环。
3. **misevolution 仅做轻量防护**：相比 TAME 双轨记忆 / SEVerA 形式验证（`research/01` §3.3/§3.8），本项目只做人审 + lint + 指标监控 + 回滚，未做可证明的硬约束。
4. **窄域、确定性评测的代价**：合成 NimbusFlow + keypoint 判分换来了可复现与防作弊，但牺牲了开放域真实性；真实工单的语义模糊会让 keypoint 匹配偏严。
5. **未进化拓扑/权重**：刻意不做（AFlow/MaAS 的拓扑搜索、DGM 的权重/代码自改），是取舍而非遗漏——窄域生产更要可控。
6. **未来工作**：①引入 TAME 式双轨（executor / evaluator 记忆分离）做更强安全；②playbook 自动回归测试（每次新 playbook 上线自动跑历史失败集，回归感知发布，对标 AgentDevel）；③多智能体扩展（见 §8.4 面试题）；④用 GEPA 式 Pareto 搜索优化 playbook 措辞；⑤主动学习选最有价值的工单求人工标注，降低标注成本。

---

## 8. 简历呈现（重点）

### 8.1 可直接放简历的项目简介（60–80 字）

> **自进化客服 Agent（个人项目）**：设计并实现不改模型权重的自进化客服系统——分层记忆（经验池/知识库/playbook）+ self-RAG 推理 + 离线复盘归纳可审计可回滚的 playbook，配合确定性 verifier 评测，实现解决率随服役轮次上升、重复错误率与人工介入率下降的完整闭环。

### 8.2 STAR 式量化 bullet（4–6 条）

> 说明：标 `【实验产出】` 的数字将由 NimbusFlow 数据集 + verifier 跑出真实值后回填；占位为预期方向。对标口径见 `research/03`（Anthropic 首轮错误 -97%、Devin PR 合并率 34%→67%）。

1. **（架构）** 设计并实现三层记忆（episodic 经验池 / semantic KB / procedural playbook）+ self-RAG（检索→生成→critic→低置信转人工）的自进化客服 Agent，全程零权重更新；借鉴 Memento case-memory 与 A-MEM 结构化记忆，在 mock/OpenAI/vllm 三后端统一抽象下一键跑通。
2. **（自进化闭环）** 实现"答错→写入人工 ground-truth 经验→离线 Reflector 聚类失败模式归纳 playbook→人审/版本/回滚后经检索生效"的 dreaming 式闭环，使**重复错误率 ↓XX%**、**解决率从 XX% 提升至 XX%**【实验产出】。
3. **（评测防作弊）** 自建 NimbusFlow 合成数据集与确定性 verifier（keypoint 覆盖率 + 转人工决策正确性判分），用评分/决策分离、gold 不进检索、外部反馈驱动四道防线杜绝自评作弊，产出可复现的多轮**进化曲线**（解决率↑/keypoint↑/重复错误率↓/人工介入率↓）。
4. **（安全治理）** 针对 misevolution 风险（参考 arXiv 2509.26354 记忆侵蚀安全对齐）设计 playbook 治理层：人审闸门 + 版本化 + 一键回滚 + 安全 lint + 启用后指标监控，把"自进化"做成可审计、可回滚的发布工程，**漏转率维持 <XX%** 不因进化恶化【实验产出】。
5. **（消融/度量）** 通过 baseline / +episodic / +playbook 三组消融分离经验复用与规则归纳的贡献，量化 playbook 带来的**额外解决率增益 +XX pp**【实验产出】；检索层可报 recall / 命中率等指标。
6. **（工程化）** 后端抽象 + 全链路 trace 可观测 + YAML 配置 + 自动出曲线，mock 后端零依赖支撑 CI 与一键复现；预留 MCP/多 agent、企业 KB / 工单系统接入点。

### 8.3 技术亮点清单（体现资深 / 框架级思维）

- **路线判断力**：明确选择"记忆+经验固化"而非"权重自改"，并能用 2026 产业证据（Anthropic dreaming / 持久记忆、Devin）论证为何这才是当前真落地形态（反噱头）。
- **分层记忆设计**：episodic/semantic/procedural 三层职责清晰，每层 schema 化、可检索、可治理——对标 A-MEM 结构化 + mem0 分层（`research/03` §1.2.A）。
- **进化对象的取舍**：把自由度收敛到"数据 + 自然语言规则"层，刻意不动拓扑/权重，换取可控、可审计、可回滚。
- **评测即护城河**：确定性 verifier + 四道防作弊 + 进化曲线 + 消融，正面解决学术/产业普遍缺失的"如何证明真进化、没作弊"。
- **安全治理内建**：misevolution 监控 + lint + 回滚，把安全做成系统一等公民而非事后补丁。
- **生产可落地**：三后端抽象、可观测、HITL、可接 MCP/企业系统——是系统而非 demo。

### 8.4 高频面试追问 + 参考答案要点

**Q1：你这套"自进化"和微调（SFT/RL）路线怎么取舍？为什么不微调？**
要点：微调改权重→难审计、难回滚、迭代周期长、需算力标注、易引入难定位的回归与安全退化。本项目把进化放在记忆+规则层：迭代以分钟计、每条改动可单独溯源/停用/回滚、零算力。产业 2026 主流（dreaming、持久记忆）也走这条路（`research/03` §1.3）。微调适合"能力本身不足"，记忆/经验固化适合"知识漂移 + 重复犯错"——本场景是后者。两者可叠加：未来可把高频稳定 playbook 蒸馏回模型。

**Q2：分层记忆为什么要分三层？episodic 和 procedural 区别是什么？**
要点：semantic=事实知识（KB，how-what，相对静态）；episodic=具体案例经验（Memento case bank，"这道题的正确答案"）；procedural=程序性/规则知识（ReMe/EvolveR，"这类问题该怎么处理的可复用规则"，how-to）。episodic 是点（单 case），procedural 是从一簇 episodic 失败归纳出的面（规则）。分层让检索可分别加权、让治理可分别施加（playbook 要人审，case 直接写）。

**Q3：self-RAG 的 critic 置信度，会不会被 agent 自己刷高来逃避转人工？这不就是自评作弊？**
要点：关键设计就是**评分与决策分离**——critic 的 confidence 只决定在线是否 escalate，**绝不进入评测得分**；评测一律由独立 verifier 用 gold 判。所以即便 critic 虚高，verifier 仍会判它漏转/答错，进化曲线会暴露。这正面回应 reward hacking（`research/03` §2.4）。

**Q4：怎么证明你的 agent 是"真的在进化"而不是记住了测试集 / 自己给自己打高分？**
要点：四道防线（§5.4）：评分/决策分离、gold 不进检索、外部人工反馈驱动、train/eval 隔离 + 失败模式溯源统计重复错误率。再加消融三曲线分离贡献。理论依据 SkillLearnBench：仅自反馈会递归漂移，必须外部反馈（`research/01` §5.1）。

**Q5：playbook 自动归纳出来的规则可能是错的 / 有害的，怎么防 misevolution？**
要点：①候选默认不启用，必须人审 approve；②安全 lint 硬约束，playbook 不能覆盖 escalate 红线/越权；③版本化 + 一键回滚；④启用后监控其影响域指标（重复错误率、漏转率、越权拒答率），恶化即告警回滚。引用 Misevolution（arXiv 2509.26354，记忆使拒答率 99.4%→54.4%）说明风险真实存在，SSGM/TAME 是学术对应（`research/01` §3.8/§3.1）。

**Q6：Reflector 怎么聚类失败、怎么保证归纳的 playbook 质量？**
要点：按 (intent × product_domain × root_cause_tag) 聚类 + embedding 软分组；只对样本数达阈值的高频簇归纳（避免对偶发噪声造规则）；归纳是 textual-gradient（读失败 trace + 人工正确方案，借鉴 GEPA/EvolveR）；质量靠人审 + 未来的自动回归测试（新 playbook 上线先跑历史失败集验证不回退，对标 AgentDevel release engineering）兜底。

**Q7：检索是 RAG 的命门，你怎么度量检索是否真有效？三路怎么融合？**
要点：能报 recall / 命中率 / MRR（`research/03` §2.2.3 明确"报不出 recall=没真度量过"）。三路（KB/经验/playbook）独立召回各自打分，加权融合，权重是可配阈值并记入 trace 做消融。难点：经验池增长后噪声召回上升，需相似度阈值 + 时间衰减 + 去重（A-MEM/EvolveR 的语义去重思想）。

**Q8：这套单 agent 方案怎么扩展到多智能体？为什么你现在没做？**
要点：扩展路径——可拆 Retriever/Generator/Critic 为协作角色，或按产品域分领域子 agent + supervisor 路由（Supervisor-Worker），playbook 升级为跨 agent 共享技能库（NVIDIA Verified Skills / MCP）。当前不做的理由：窄域客服结构稳定，多 agent 会引入编排复杂度、状态/超时/成本失控等生产 failure modes（`research/03` §2.2.5），违背"把自由度收敛、保可控"的设计原则。若要做，会先用 AFlow/MaAS 式自动设计验证拓扑收益是否覆盖复杂度成本——但那是另一个项目。

---

> 文末备注：本文所有 2026 时效性论点均可溯源至配套调研笔记的工作名 / arXiv 号；标 `待核实` 的二手细节（见 `research/01` 附录）在对外引用前应核对原文。
