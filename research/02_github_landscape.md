# 自进化 Agent 方向 GitHub 开源生态调研

> 调研日期：2026-05-28
> 目标：定位 self-evolving / self-improving agent 方向最新、最活跃的开源仓库，并产出"建议 clone 的核心仓库清单"。
> 数据来源：GitHub API（`gh repo view`）实测 star/最近 push/磁盘体积 + WebSearch / WebFetch。star 与体积为 2026-05-28 实测值，可能有小幅波动。
> 磁盘约束：目标机仅剩 ~27GB，下表 diskMB 为 GitHub 报告的 repo 体积（`diskUsage`），不含运行时依赖与模型权重。所有 clone 建议均用 `git clone --depth 1` 进一步压缩。

---

## 一、按自进化方向分类的仓库矩阵

自进化方向编码：
- **[综述/Awesome]** 资源汇编、survey 配套
- **[框架]** 综合性自进化 Agent 框架
- **[优化器]** prompt / workflow 自动优化
- **[进化搜索]** 进化式自改代码 / open-ended evolution
- **[自动设计]** 自动 multi-agent 设计 / 拓扑搜索
- **[记忆]** Agent 长期记忆系统
- **[RL自进化]** 基于 RL 的自我进化推理

### 1. 综述 / Awesome-list（与两篇核心 survey 配套）

| 仓库 | URL | star | 最近活跃 | 体积 | 说明 |
|---|---|---|---|---|---|
| EvoAgentX/Awesome-Self-Evolving-Agents | https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents | ~2.2k | 2026-05-16 活跃 | 2.3MB | **survey arXiv:2508.07407 官方配套 awesome-list**。分类清晰（优化/记忆/设计/RL/代码/安全/评测），是本次调研的"地图"。强烈建议 clone。 |
| CharlesQ9/Self-Evolving-Agents | https://github.com/CharlesQ9/Self-Evolving-Agents | ~1.2k | 2025-10-15 | 0.4MB | **survey arXiv:2507.21046（"What/When/How/Where to Evolve"）配套**。纯 paper 列表，体积极小。 |
| XMUDeepLIT/Awesome-Self-Evolving-Agents | https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents | ~200 | 2026-05-24 活跃 | 16MB（含图） | 另一份 self-evolving agents survey 配套，仍在更新，可交叉对照。 |

> 两篇核心 survey：**2507.21046**（A Survey of Self-Evolving Agents: What/When/How/Where）、**2508.07407**（A Comprehensive Survey of Self-Evolving AI Agents，EvoAgentX 团队，配套 EvoAgentX 框架）。

### 2. 综合自进化 Agent 框架

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| EvoAgentX/EvoAgentX | https://github.com/EvoAgentX/EvoAgentX | ~3.0k | 2026-05-24 高频 | 105MB | [框架] | **本方向旗舰开源框架**（EMNLP'25 Demo，arXiv:2507.03616）。单 prompt 自动生成多 agent workflow + 自动评估器 + 自进化算法（内含 TextGrad/AFlow/MIPRO 等优化器封装）+ 长短期记忆 + HITL。是简历项目最值得对标/复用的脚手架。 |
| modelscope/AgentEvolver | https://github.com/modelscope/AgentEvolver | ~1.4k | 2026-04-01 | 56MB | [框架/RL] | 阿里 ModelScope 出品（arXiv:2511.10395）。"高效自进化 agent system"，强调 self-questioning / self-navigating / self-attributing 三机制 + RL 训练。偏训练，依赖较重。 |
| lsdefine/GenericAgent | https://github.com/lsdefine/GenericAgent | ~12k | 2026-05-27 爆发增长 | 28MB | [框架/进化] | 2026 上半年新晋热门（star 增速极快）。从 3.3K 行 seed 自生长"技能树"，号称 6x 更省 token 实现系统级控制。代码量小、思路新颖，但偏工程实验性，需核实成熟度。 |

### 3. Prompt / Workflow 自动优化（优化器）

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| stanfordnlp/dspy | https://github.com/stanfordnlp/dspy | ~34.7k | 2026-05-27 高频 | 175MB | [优化器] | 声明式 LLM 程序框架，内置 MIPROv2 / BootstrapFewShot / **GEPA** 等优化器。生态标准件，但体积偏大。 |
| gepa-ai/gepa | https://github.com/gepa-ai/gepa | ~4.8k | 2026-05-22 活跃 | 111MB | [优化器/进化] | **GEPA（Genetic-Pareto，arXiv:2507.19457）官方实现**：反思式 prompt 进化 + Pareto 前沿，号称超过 MIPROv2/RL（AIME +12%）。核心算法值得精读，但体积偏大（多为 notebook/示例资产）。 |
| FoundationAgents/AFlow | https://github.com/FoundationAgents/AFlow | ~510 | 2025-12-25 | 1.9MB | [优化器/自动设计] | **AFlow（ICLR'25 Oral，arXiv:2410.10762）独立仓**：用 MCTS 在代码化 workflow 空间搜索最优工作流。体积极小、算法清晰，**强烈推荐精读代码**。（注意它也内嵌在 MetaGPT/examples/aflow）。 |
| zou-group/textgrad | https://github.com/zou-group/textgrad | ~3.6k | 2025-07-25 | 10MB | [优化器] | "文本梯度"反向传播（Nature 发表）。把 LLM 反馈当梯度优化 prompt/解。经典、代码精炼，自进化反馈机制的好参考。活跃度一般。 |
| google-deepmind/opro | https://github.com/google-deepmind/opro | 待核实 | 待核实 | 待核实 | [优化器] | "LLM as Optimizers"（OPRO）。经典 baseline，未实测元数据，**待核实**。 |
| beeevita/EvoPrompt | https://github.com/beeevita/EvoPrompt | 待核实 | 待核实 | 待核实 | [优化器/进化] | 进化算法做 prompt 优化。元数据**待核实**。 |

### 4. 进化式自改代码 Agent / Open-Ended Evolution

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| algorithmicsuperintelligence/openevolve | https://github.com/algorithmicsuperintelligence/openevolve | ~6.4k | 2026-03-18 活跃 | 6.7MB | [进化搜索] | **AlphaEvolve 最主流开源复现**（原 codelion/openevolve 迁此组织）。LLM 引导的整文件进化 + program database + 评估选择闭环。体积小、可直接运行，**强烈推荐**。 |
| jennyzzt/dgm | https://github.com/jennyzzt/dgm | ~2.1k | 2025-08-13 | 8.7MB | [进化搜索] | **Darwin Gödel Machine 官方实现**（arXiv:2505.22954）。agent 迭代改自己代码、经验证驱动进化，SWE-bench 20%→50%。自改代码方向的标杆，**强烈推荐精读**。注意需沙箱执行环境。 |
| metauto-ai/HGM | https://github.com/metauto-ai/HGM | ~390 | 2026-02-07 活跃 | 1.0MB | [进化搜索] | **Huxley-Gödel Machine**，DGM 的后续/改进。体积极小，适合对照 DGM 看演进。 |
| SakanaAI/AI-Scientist | https://github.com/SakanaAI/AI-Scientist | ~13.8k | 2025-12-19 | 114MB | [进化搜索/自动设计] | 全自动科研 agent（生成 idea→实验→写论文）。开放式探索的代表作。体积偏大，浅克隆看思路即可。 |

### 5. 自动 multi-agent 设计 / 拓扑搜索

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| ShengranHu/ADAS | https://github.com/ShengranHu/ADAS | ~1.6k | 2025-01-28 | 11MB | [自动设计] | **ADAS（ICLR'25）官方实现**：Meta Agent Search，让 agent 用代码自动设计新 agent。自动设计方向奠基代表，代码可读。活跃度低但经典。 |
| bingreeky/MaAS | https://github.com/bingreeky/MaAS | ~270 | 2025-11-13 | 5.3MB | [自动设计] | **MaAS（ICML'25 Oral，arXiv:2502.04180）**：Agentic Supernet，按 query 难度动态采样多 agent 架构。思路新（从"找单一最优"到"优化架构分布"），体积小，**推荐精读**。 |
| tsinghua-fib-lab/AgentSquare | https://github.com/tsinghua-fib-lab/AgentSquare | ~230 | 2025-11-04 | 145MB | [自动设计] | 模块化设计空间（Planning/Reasoning/ToolUse/Memory）+ 模块进化&重组搜索（arXiv 同名）。思路好，但**体积大（145MB，含网页资产）**，建议浅克隆或只读论文/README。 |
| MASWorks/MASLab | https://github.com/MASWorks/MASLab | ~240 | 2025-07-25 | 2.3MB | [自动设计/框架] | 统一的 LLM 多 agent 系统代码库（survey 提及）。体积小，可作多 MAS 方法对比的统一脚手架。 |

### 6. Agent 记忆系统

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| mem0ai/mem0 | https://github.com/mem0ai/mem0 | ~57k | 2026-05-27 高频 | 55MB | [记忆] | 最主流的"通用记忆层"。产业标准件，API 成熟。可借鉴其记忆抽取/检索/更新管线设计。体积中等。 |
| letta-ai/letta | https://github.com/letta-ai/letta | ~23k | 2026-05-14 活跃 | 287MB | [记忆/框架] | 原 MemGPT。有状态 agent 平台，"可学习并随时间自我改进"的记忆架构。**体积大（287MB）**，建议浅克隆或只读文档。 |
| WujiangXu/AgenticMemory | https://github.com/WujiangXu/AgenticMemory | ~900 | 2026-03-05 活跃 | 1.7MB | [记忆] | **A-MEM（NeurIPS 2025）官方实现**：Zettelkasten 式 agent 记忆，动态组织/链接记忆笔记。体积极小、学术性强，**强烈推荐精读**。 |
| Agent-on-the-Fly/Memento | https://github.com/Agent-on-the-Fly/Memento | ~2.4k | 2025-10-05 | 3.4MB | [记忆/自进化] | "不微调 LLM 而微调 agent"——用 case-based memory 实现持续自进化。体积小，与"低成本自进化"主题高度契合，**推荐精读**。 |

### 7. 基于 RL 的自进化推理（偏训练，依赖重）

| 仓库 | URL | star | 最近活跃 | 体积 | 方向 | 说明 |
|---|---|---|---|---|---|---|
| Chengsong-Huang/R-Zero | https://github.com/Chengsong-Huang/R-Zero | ~800 | 2026-02-04 活跃 | 1.1MB | [RL自进化] | **R-Zero（ICLR2026，arXiv:2508.05004）**：从零数据自我进化推理 LLM（Challenger-Solver 自博弈）。仓库本体极小，但运行需训练资源。思路可读。 |

> RL 类（AgentEvolver / R-Zero / SPIRAL / ReTool 等）仓库本体不大，但**实跑需要 GPU 训练 + 可能拉取模型权重/数据集**，在 27GB 磁盘约束下不建议本地实跑，只读代码与论文。

---

## 二、磁盘风险提示

- **不要带权重/大数据 clone**：letta（287MB）、AgentSquare（145MB）、AI-Scientist（114MB）、gepa（111MB）、MetaGPT（180MB）、dspy（175MB）。这些用 `--depth 1` 仍偏大，且部分依赖会在 `pip install` 时拉取大模型，**优先浅克隆只看 README/核心目录**。
- **RL 训练类**（AgentEvolver / R-Zero）：仓库小但实跑会下载基座模型（数 GB~数十 GB），磁盘不够，**仅读代码**。
- 推荐统一用 `git clone --depth 1 <url>`，并对超 50MB 的仓库优先 `gh repo view` + WebFetch README 替代实 clone。

---

## 三、分级 clone 清单

### 【强烈建议 clone —— 小而精，可直接参考代码】
（全部 `--depth 1`，单仓 < 12MB，合计很小）

```bash
git clone --depth 1 https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents   # ~2.3MB  方向地图/survey 配套
git clone --depth 1 https://github.com/FoundationAgents/AFlow                    # ~1.9MB  MCTS workflow 自动优化(ICLR'25 Oral)
git clone --depth 1 https://github.com/algorithmicsuperintelligence/openevolve   # ~6.7MB  AlphaEvolve 主流复现
git clone --depth 1 https://github.com/jennyzzt/dgm                              # ~8.7MB  Darwin Gödel Machine 自改代码标杆
git clone --depth 1 https://github.com/WujiangXu/AgenticMemory                   # ~1.7MB  A-MEM 记忆(NeurIPS'25)
git clone --depth 1 https://github.com/Agent-on-the-Fly/Memento                  # ~3.4MB  低成本 case-memory 自进化
git clone --depth 1 https://github.com/bingreeky/MaAS                            # ~5.3MB  Agentic Supernet 拓扑搜索(ICML'25 Oral)
git clone --depth 1 https://github.com/metauto-ai/HGM                            # ~1.0MB  Huxley-Gödel Machine(DGM 演进)
```
合计预估 **~31MB**（`--depth 1` 通常更小，实际约 20–30MB），磁盘无压力。

### 【建议浅克隆 / 只读 README 即可】（体积偏大或偏工程化）
```bash
git clone --depth 1 https://github.com/EvoAgentX/EvoAgentX        # ~105MB 旗舰框架，建议只 clone 后看 src 结构，勿装全依赖
git clone --depth 1 https://github.com/ShengranHu/ADAS            # ~11MB  自动设计奠基(活跃度低)
git clone --depth 1 https://github.com/zou-group/textgrad         # ~10MB  文本梯度优化
git clone --depth 1 https://github.com/MASWorks/MASLab            # ~2.3MB 多 MAS 统一脚手架
git clone --depth 1 https://github.com/Chengsong-Huang/R-Zero     # ~1.1MB RL 自进化(只读代码,勿训练)
```
- gepa-ai/gepa（111MB）、stanfordnlp/dspy（175MB）：**只读 README + 在线看 GEPA 源码**，需要时 `pip install gepa/dspy` 而非 clone。
- mem0ai/mem0（55MB）：记忆管线参考，可浅克隆或读文档。

### 【仅记录链接】（产业标准件 / 体积过大 / 偏训练 / 待核实）
- letta-ai/letta（287MB，原 MemGPT）— 记忆平台，只读文档
- geekan/MetaGPT（180MB）— AFlow 母仓，看 examples/aflow 即可
- tsinghua-fib-lab/AgentSquare（145MB）— 只读论文/README
- SakanaAI/AI-Scientist（114MB）— 开放式科研 agent，只读思路
- modelscope/AgentEvolver（56MB，偏 RL 训练）— 只读代码
- lsdefine/GenericAgent（28MB，12k star 新晋热门）— 成熟度待核实，建议先读 README 再定
- google-deepmind/opro、beeevita/EvoPrompt — 元数据**待核实**
- 其他 survey 提及但未实测：MASWorks/MAS-GPT、bytedance-seed/m3-agent、SaFoLab-WISC/MetaAgent、spiral-rl/spiral 等 — **链接记录，按需核实**

---

## 四、给简历项目的可借鉴点小结

- **自进化闭环骨架**：EvoAgentX（生成 workflow → 自动评估 → 自进化 → 记忆）是最完整的对标范式。
- **优化器内核**：AFlow（MCTS over 代码化 workflow）+ GEPA（反思式 Pareto 进化）+ TextGrad（文本梯度），三种典型自优化范式，AFlow 代码最易读。
- **自改代码进化**：DGM / HGM（修改自身代码 + 经验证选择）+ OpenEvolve（program database + 进化）。
- **低成本自进化记忆**：A-MEM（结构化记忆组织）+ Memento（case-based，不动模型权重）——契合"无需训练即可进化"的轻量路线，最适合磁盘/算力受限的简历项目。
- **自动设计/拓扑**：ADAS（meta agent search）+ MaAS（agentic supernet）+ AgentSquare（模块化设计空间）。
