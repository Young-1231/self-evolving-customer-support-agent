# 5 分钟口头 Pitch（资深 Agent 算法工程师面试用）

_最后更新：2026-05-28_

读法：先抓住前 30 秒，决定面试官要不要听下去；2-3 分钟把"研究弧线"画出来；最后 30 秒留一个能让对方追问的钩子。**所有数字都是实跑的，可现场打开 `experiments/` 验证。**

---

## 0 - 30 秒：抓住注意力

> "我做了一个**不改模型权重**的自进化客服 Agent，对齐了 2026 工业主流（Anthropic Dreaming / Sierra / Decagon）。**最有意思的发现不是哪个指标涨了多少，而是我在一个完整研究闭环里两次证伪了自己的假设、最终锁定了真瓶颈在 groundedness check 上**——这种 narrative 比单一漂亮数字更能说明问题。"

（停顿。等对方问"展开讲"。）

---

## 30 秒 - 2 分钟：机制与创新点

> "机制层面是 2026 主流：三层记忆（episodic 经验池 / semantic KB / procedural playbook）+ self-RAG 推理 + 离线 Reflector 归纳可审计 playbook + governance 流水线（propose → 人审 → canary → 回归门禁 → 一键回滚）+ 4 层加固（guardrails / observability / governance / serving）。

> 创新点有两个我会主动讲：
> 第一是 **APR-CS（Adaptive Playbook Router + Counterfactual Self-Scoring）**。τ² airline 上我注意到注入全部 8 条 playbook tips 让 pass^1 微降 −2.5pp、但 pass^2/^3 反升 +0.8/+1.2pp——典型 reliability vs single-shot tradeoff。借鉴 GEPA 的 per-component 归因（arXiv 2507.19457）、Self-RAG 的 adaptive retrieval（2310.11511）、Voyager 的 skill library、AlphaEvolve 的反事实评估，我把它们合成了：用 leave-one-out 算每条 tip 的边际 Δᵢ，把分数持久化到 playbook 元数据里，推理时按 task↔tip 相关性 × Δᵢ 路由 top-K。这是把 GEPA 的粒度细化到 single-tip level、并和 governance 模块联动可审计。

> 第二是评测严谨度：τ²-bench retail/airline 两域**完整 pass^k 全谱**（test=40×trials=4 + 20×4），不是只挑 pass^1。绝大多数候选人只报漂亮数字，但 pass^k 全谱才暴露真实 tradeoff。"

---

## 2 - 4 分钟：研究弧线（核心）

> "整个项目其实是一个**三轮证伪、第四轮才锁定真瓶颈**的研究闭环。

> **第一轮**：合成 NimbusFlow 基准做受控消融，static/episodic/full 三条曲线干净归因，解决率 34%→71%，重复错误 100%→40%。机制层面工作。

> **第二轮**：上 τ²-bench retail（DeepSeek-V4-Flash, test=40×trials=4，160 sims/条件，调 tau2 官方 compute_metrics），pass^1 0.925→0.931（+0.6pp）。诚实承认：方向稳健但 magnitude 小，因为模型已逼近 ceiling。换 airline 域试更大 headroom：暴露 reliability tradeoff。

> **第三轮（APR-CS）**：我把 tradeoff 当成 research question，做了 APR-CS。但实测发现 K=4 是新 binding constraint，路由后 pass^1 +1.2pp 但 pass^2/^3/^4 反降。**第一次假设证伪**：自适应路由没解决 multi-trial 一致性，只是换了 tradeoff 维度。

> **第四轮（500 工单压测）**：LLM 生成 500 条真实分布工单（50% easy / 20% hard / 10% PII / 10% multi-intent / 5% injection / 5% multilingual），并发 20 真跑 DeepSeek API。**escalation 85.2%** —— 我一开始判断 KB 是真瓶颈。

> **第五轮（用 Bitext 扩 KB 验证）**：Bitext customer-support dataset 是 2026 工业最被接受的 CS LLM 训练集，CDLA-Sharing 商业友好。把 KB 从 30 篇扩到 176 篇，跑两组对照：
> - Exp A（同 tickets + 扩 KB）：escalation 几乎不动 85.6%
> - Exp B（对齐 tickets + 扩 KB）：escalation 反升到 92%，因为 stiff 英文回答把 guardrail 阈值过不去
>
> **第二次假设证伪**：KB 不是瓶颈。

> **第六轮（Calibration + PII 精度 Exp C）**：定位到 critic 阈值跨域 + PII 精度可能是真因。做了 per-domain 阈值标定（用 NimbusFlow 真实 grid search、ecomm 用 informed prior）+ PII precision_mode='balanced'。Exp C 实测：**escalation 还是 ~93%**。**第三次假设证伪**。

> **真正锁定的瓶颈**是 **guardrail 的 groundedness check**——英文 stiff template 回答因为"无完整 KB 证据支撑"被推到 escalate。这是项目目前最有价值的真实工程发现，对应的修复路径是 groundedness 跨域阈值校准 + LLM-judge groundedness + critic/groundedness/policy 三信号 vote。"

---

## 4 - 5 分钟：客观评估 + 下一步

> "客观评估：
> - **作为简历项目**：超出绝大多数候选人，对齐头部玩家方向（Anthropic Dreaming / Sierra / Decagon 同一条路）。
> - **作为生产候选**：pre-pilot 级别。算法/治理两层做扎实了，但缺真实流量、缺集成、缺规模化运维。距 GA 还差 3-6 个月专项工程化。
> - **客观最大短板**：单机 prototype + 没有真实生产流量验证 + groundedness check 在跨域上失效。

> 下一步如果让我接着做一周，我会先**实现 LLM-judge groundedness + 三信号 vote 机制**跑 Exp D。这是 §4h 真瓶颈识别后唯一负责任的下一步——不是再扩 KB，不是再调 critic 阈值，是真正修 groundedness。"

---

## 收尾钩子（让对方追问）

> "如果你愿意我可以现场打开 `experiments/stress_test_expanded/exp_c/load_summary.json` 看 escalation 卡在 93% 的具体分布，或者我们聊聊为什么我**不建议**把这个 fork 成创业产品——赛道已经被 Sierra $15.8B / Decagon $4.5B 吃透了。"

---

## 三类常见追问的标准答案

**Q1: "你这个跟 vanilla RAG 有什么区别？"**
> "三点。一是有完整自进化闭环（失败案例→Reflector 归纳 playbook→人审 governance→上线），不是单轮检索。二是 APR-CS 反事实归因到 single-tip level，比 GEPA 粒度更细。三是 4 层加固和评测严谨度，我有公开 pass^k 全谱和 500 工单压测，绝大多数 RAG 项目没这层。"

**Q2: "怎么证明你的 APR-CS 不是过拟合 train？"**
> "Counterfactual 是 leave-one-out 在 train 上算的，应用在 held-out test 上。整个研究弧线已经诚实暴露了 APR-CS 在 airline 上没有 Pareto improve、500 工单上也没解决 escalation——这本身就是反过拟合的证据，过拟合的项目通常只报漂亮数字。"

**Q3: "你的'自进化'和直接微调路线本质区别在哪？"**
> "**时间尺度和可解释性**。微调更新模型几小时到几天、需要训练成本、不可解释、出问题难回滚；in-context 自进化是分钟级、零训练成本、playbook 可审计可一键回滚、对 EU AI Act / 国内合规友好。两者互补不互斥——Anthropic Dreaming 也是这条线。但近 3 年企业落地的实际诉求是后者。"

---

## 演练 Tips

- **不要急着报数字**，先抛 narrative。数字穿插在故事里。
- **主动暴露局限**：85% escalation、APR-CS 没 Pareto improve、Exp C 没起效。这反而加分。
- **三次假设证伪**是你的杀手锏。普通候选人只讲"我做了 ABC"，你讲"我以为是 A，测了发现不是；以为是 B，测了发现也不是；锁定真因是 C"——这是 senior 的标志。
- **PROJECT_NARRATIVE.md** 是这个 pitch 的完整版（8000+字），用作面试官追问深聊时的脚本。
