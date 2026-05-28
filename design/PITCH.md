# 5 分钟口头 Pitch（资深 Agent 算法工程师面试用）

_最后更新：2026-05-29（v2.9 multi_intent loop CLOSED）_

读法：先抓住前 30 秒，决定面试官要不要听下去；2-3 分钟把"研究弧线"画出来；最后 30 秒留一个能让对方追问的钩子。**所有数字都是实跑的，可现场打开 `experiments/` 验证；34 commits、310 tests 全过、$2 真实 DeepSeek 消耗。** GitHub: https://github.com/Young-1231/self-evolving-customer-support-agent

---

## 0 - 30 秒：抓住注意力

> "我做了一个**不改模型权重**的自进化客服 Agent，对齐 2026 工业主流（Anthropic Dreaming / Sierra / Decagon / Claude Code 生态）。整个项目最有意思的不是哪个指标涨了多少，而是它是一个**完整的研究闭环：v1.x 三轮假设证伪后锁定真瓶颈，v2.x 又用 4 轮 multi_intent 迭代修复把硬伤从 0% 推到 55.3% 解决率，全程 safety 没崩**。这种 narrative 比单一漂亮数字更能说明问题。"

（停顿。等对方问"展开讲"。）

---

## 30 秒 - 2 分钟：机制与创新点

> "机制层面是 2026 主流：三层记忆（episodic / semantic / procedural）+ self-RAG + 离线 Reflector 归纳可审计 playbook + governance 流水线（propose → 人审 → canary → 回归门禁 → 一键回滚）+ 4 层加固（serving / guardrails / observability / governance）。**v2.x 已把 Claude Code 五件套全部落地实施：Hooks / Skills / Multi-agent Subagents / MCP / 简化版 Plan**，不是 PPT 对齐而是源码级对齐。

> 创新点我会主动讲三个：
> 一是 **APR-CS（Adaptive Playbook Router + Counterfactual Self-Scoring）**。τ² airline 上注入全部 8 条 tips 让 pass^1 −2.5pp、但 pass^2/^3 反升 +0.8/+1.2pp——典型 reliability vs single-shot tradeoff。借鉴 GEPA per-component 归因（arXiv 2507.19457）、Self-RAG adaptive retrieval（2310.11511）、Mem0 entity-linked 单遍记忆（2504.19413）、Voyager skill library（2305.16291）、TAME 选择性记忆（2602.03224），合成出 leave-one-out **tip-level counterfactual**，把分数持久化进 playbook 元数据，推理时按 task↔tip 相关性 × Δᵢ 路由 top-K。**比 GEPA 的 per-component 粒度还细一层到 single-tip**。

> 二是 **v2.x multi_intent 4 轮迭代闭环**，这是 2026 没人公开做过的细节：v2.3 base 46.8%→v2.7 merged 0%→v2.8 per-sub 0%→v2.9 修 policy regex 后 **55.3%**，过程比结果有价值（后面展开）。

> 三是评测严谨度：τ²-bench retail + airline **完整 pass^k 全谱**，并用 **500 LLM 生成真实分布工单**做规模化压测（并发 20 真打 DeepSeek API）。绝大多数候选人只挑 pass^1 报漂亮数字。"

---

## 2 - 4 分钟：研究弧线（核心）

> "整个项目是**两段研究闭环串起来**的：v1.x 三轮证伪锁定真瓶颈 + v2.x 四轮迭代修硬伤。

### v1.x：三轮证伪（机制 → 公开 benchmark → 500 工单压测）

> **第一轮**：合成 NimbusFlow 受控消融，static/episodic/full 三条曲线干净归因——解决率 34%→71%，重复错误 100%→40%。机制层面工作。
> **第二轮**：τ²-bench retail 完整 split（DeepSeek-V4-Flash, test=40×trials=4），调 tau2 官方 compute_metrics，pass^1 0.925→0.931。诚实承认 magnitude 小因为接近 ceiling，换 airline 域暴露 reliability tradeoff。
> **第三轮 APR-CS**：把 tradeoff 当 research question，airline cf_weighted 把 pass^1 推到 0.787 (+1.2pp)，但 pass^2/^3 反降。**第一次证伪**：自适应路由没解决 multi-trial 一致性。
> **第四轮 500 工单压测**：LLM 生成 500 真实分布工单，并发 20 真跑 DeepSeek。escalation **85.2%**。我以为 KB 是瓶颈。
> **第五轮**：用 Bitext（CDLA-Sharing 商业友好）把 KB 从 30 扩到 176 篇。Exp A 同 tickets escalation 几乎不动 85.6%；Exp B 对齐 tickets 反升到 92%。**第二次证伪**：KB 不是瓶颈。
> **第六轮**：定位到 critic 阈值跨域 + PII 精度。Exp C per-domain 阈值标定 + balanced PII。escalation **还是 93%**。**第三次证伪**。
> **第 4 次成功 Exp D**：真正锁定瓶颈是 **guardrail groundedness check 在跨域上失效**。引入 **LLM-judge groundedness + EscalationVoter 三信号 vote**，escalation 93%→**67.2%**，normal_easy res 6.5%→**40.5%**，multilingual 0%→**53%**。

### v2.x：4 轮 multi_intent 迭代闭环（修最后的业务硬伤）

> Exp D 后剩一个最硬的坑：**multi_intent 解决率仍是 0%**。v2.x 同日完成 6 个 ROADMAP 项目 + 4 轮 multi_intent 修复：

> - **v2.3 Subagent + Handoff（Exp E core）**：IntentRouter→SpecialistAgent fan-out + merge。multi_intent 0%→**46.8%**，escalation 67.2%→36.6%。但 core mode 绕开 guardrail，**没验证安全**。
> - **v2.x observed mode 第 1 次尝试（Exp E_observed）**：把 guardrail 真打开 apples-to-apples。multi_intent 直接回到 **0%**——per-sub 子 answer 单独过 guardrail 全被中毒。escalation 飙到 85.2%。**1st negative**。
> - **v2.7 merged-answer guardrail（Exp E_v2）**：第 2 次尝试，合并后过一次 guardrail。multi_intent 仍 **0%**——合并答案太长 groundedness fail + PII 累积 BLOCK。**2nd negative**。
> - **v2.8 per-sub aggregated（Exp E_v3）**：第 3 次尝试，per-sub 检查 + any-supported / majority-escalate 聚合。esc 降到 37.6%，pii / injection / multilingual / normal_easy 全显著进步——但 multi_intent 还是 **0%**。**3rd negative**。深挖发现 root cause：policy.py 的 `_MONEY` regex 把订单号 `#38294` 误识为金额→policy BLOCK。
> - **v2.9 context-aware money regex（Exp E_v4）**：加 order-id pattern + 强货币标识 + refund-context 上下文感知，policy false-positive 修死。**multi_intent 解决率 55.3%，escalation 33.2%（比 Exp E core 还低 3.4pp），safety preserved（injection block 20%, PII 0 leak）**。**闭环成功**。

> 这段我会强调：**4 次 retry 里 3 次 honest negative + 最后 root-cause 到一行 regex bug**——这种 debug 链条 + 主动写 CHANGELOG 标注 FAIL 的诚实，比"一次就成"更证明工程素养。"

---

## 4 - 5 分钟：客观评估 + 下一步

> "客观评估（按资深视角，自评 9.4/10）：
> - **作为简历项目**：超出绝大多数候选人，对齐 Anthropic Dreaming / Sierra / Decagon / Claude Code 同一条路。
> - **作为生产候选**：pre-pilot 级别。算法/治理/可观测三层做扎实，34 commits、310 tests、$2 真实 LLM 消耗，但缺真实生产流量、缺真集成、缺规模化运维。距 GA 还差 3-6 个月。
> - **最大短板**：单机 prototype + 没真实生产流量 + multi_intent 55.3% 仍有 ~45% 改进空间。剩余 0.6 分几乎只能靠真实生产流量补。

> 下一步如果让我接着做一周：**继续推 multi_intent 55%→70%**，路径是 (a) 子 answer cross-attention rerank、(b) handoff 决策从 LLM router 升级到 confidence-gated learned router、(c) playbook tip-level cf 重跑（v2.9 数据分布已变）。不是再开新方向，是把 v2.9 已经啃下来的硬骨头继续磨。"

---

## 收尾钩子（让对方追问）

> "如果你愿意我可以现场打开 `experiments/stress_test_expanded/exp_e_v4/load_summary.json` 对照 `exp_e_observed` 看 multi_intent 0%→55.3% 这条修复曲线；或者打开 `src/seagent/policy.py` 看 v2.9 那一行 regex 修复怎么把 4 轮 multi_intent loop 闭环掉的；或者我们聊聊为什么我**不建议**把这个 fork 成创业产品——Sierra $15.8B / Decagon $4.5B 已经吃透赛道。"

---

## 三类常见追问的标准答案

**Q1: "你这个跟 vanilla RAG 有什么区别？"**
> "四点。一是完整自进化闭环（失败 → Reflector 归纳 playbook → 人审 governance → canary → 回滚），不是单轮检索。二是 **APR-CS 反事实归因到 single-tip level**，比 GEPA 粒度更细。三是 4 层加固 + Claude Code 五件套全实施（Hooks/Skills/Subagents/MCP/Plan）。四是评测严谨度——pass^k 全谱 + 500 工单真实分布压测 + **v2.x 四轮 multi_intent 迭代过程公开可审计**，绝大多数 RAG 项目没这三层。"

**Q2: "怎么证明你的 APR-CS 和 multi-agent 路由不是过拟合？"**
> "三个反过拟合证据。一是 Counterfactual 在 train 上算、在 held-out test 上跑。二是研究弧线已经诚实暴露 APR-CS 在 airline 没 Pareto improve（pass^1 +1.2pp 但 pass^2/^3 反降）。三是 v2.x **4 轮迭代里 3 次 FAIL 都写进 CHANGELOG**——v2.7 merged 0% / v2.8 per_sub 0% / Exp E_observed 0% 全部 commit 留底。过拟合的项目通常只报漂亮数字，不会主动 commit 失败结果。"

**Q3: "你的'自进化'和直接微调路线本质区别在哪？"**
> "**时间尺度和可解释性**。微调更新权重要小时到几天、有训练成本、不可解释、出问题难回滚；in-context 自进化是分钟级、零训练成本、playbook + Skills 可审计可一键回滚、对 EU AI Act / 国内合规友好。两条路互补不互斥——Anthropic Dreaming 也是这条线。**v2.2 我把 playbook 直接落到 Claude Code Skills 格式（markdown + frontmatter）**，理论上同一份 procedural memory 既可在我系统跑也可在 Claude Code 跑，这是别人不会做的细节。"

---

## 演练 Tips

- **不要急着报数字**，先抛 narrative。数字穿插在故事里。
- **主动暴露局限**：Exp E_observed 0% / v2.7 / v2.8 三次 FAIL、85% 初始 escalation、APR-CS 没 Pareto improve、multi_intent 仍有 ~45% 空间。这些反而加分。
- **杀手锏是"两段研究闭环"**：v1.x 三轮证伪 + v2.x 四轮迭代修复。普通候选人只讲"我做了 ABC"，你讲"我以为是 A 不是；以为是 B 不是；锁定真因 C；C 验证后剩硬伤 D，4 次 retry 里 3 次失败、最后定位到一行 regex 闭环"——这是 senior+ 的标志。
- **Claude Code 五件套** 是 2026 工业最新对齐信号，一定要主动讲。
- **PROJECT_NARRATIVE.md** 是这个 pitch 的完整版（8000+字），用作面试官追问深聊脚本。
