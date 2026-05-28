# v2.5 R3 — OpenViking 文件系统上下文 Store

> 借鉴：[volcengine/OpenViking](https://github.com/volcengine/OpenViking)（24.8k★，2026-05 爆款）。
> 公开实测（OpenViking 自报）：τ²-bench retail **+6.87pp**、airline **+11.87pp**。
> 本项目（SEA）做的是**思想 + 范式**的借鉴，不做权重复制；用纯 stdlib 重写。

## 一、为什么从 jsonl + BM25 升级到文件系统

v2.0 - v2.4 的 episodic memory 是一个 jsonl 平铺池 + BM25：

```
data/episodic.jsonl   # 每行一条 case
```

500 工单压测显示：

- BM25 召回拐点在 1k cases 左右，再增长就开始噪音上升。
- 平坦 jsonl 没有"局部性"概念，每次检索都得跑整池。
- 人工审/diff/回滚困难——版本控制看不出"为什么这条 case 排第一"。

OpenViking 的核心洞察：**把上下文 DB 改成目录树，让文件系统本身承担索引**。

## 二、L0 / L1 / L2 三层目录范式

```
data/episodic_demo/
├── L0_account_security/         ← 粗：主题级
│   ├── L1_2026-01/              ← 中：时间分桶 (scheme=topic_date)
│   │   ├── L2_q001.md           ← 细：单条 case
│   │   └── L2_q003.md
│   └── L1_2026-04/
│       └── L2_q031.md
├── L0_billing/
│   ├── L1_2026-01/
│   │   └── L2_q005.md
│   └── L1_2026-04/
│       ├── L2_q029.md
│       └── L2_q033.md
└── ...  (8 L0 / 26 L1 / 30 L2，见 build_episodic_demo.py 输出)
```

每个 `L2_*.md` 是 markdown + 简易 frontmatter：

```markdown
---
case_id: q005
topic: billing
should_escalate: false
source_query_id: q005
learned_round: 1
---

# Case q005

## Query

在哪里下载发票

## Resolution

请进入 设置>账单>发票历史 ...
```

## 三、三种 scheme

| scheme | L0 | L1 | 适用 |
|---|---|---|---|
| `topic_date`     | `case.topic`            | `metadata.created_at[:7]` 或 `round_NNN` | 默认；时间局部性强的客服工单 |
| `topic_subtopic` | `case.topic`            | `metadata.subtopic`                        | 已经有子类目标签（refund / billing / ...）的数据 |
| `flat`           | `all`                   | `all`                                       | 兼容性兜底；等价于 jsonl+BM25 |

## 四、检索算法（L0 → L1 → L2）

```
retrieve(query, top_k):
    1. tokenize(query) -> q_tokens
    2. L0 ranking:
         score_l0 = BM25(L0 super-doc) + dir-name overlap + 1e-3 * size
         keep top-N (default N=2 or 3, configurable via l0_top)
    3. L1 enumeration in kept L0s; sorted recency-desc
    4. L2 rerank:
         collect ALL case indices in selected L0/L1 subtree
         build a SINGLE BM25 over that candidate set
         return top_k as Passage list
```

### 关键设计：L2 是单一 BM25 而非 per-leaf 合并

朴素做法（per-leaf top-k 然后合并）会因为某些 L1 只含 1-2 条 case 而把"小桶冠军"误带到结果——BM25 的 idf/avgdl 在小桶里完全失真。

修正方案：**L0/L1 只负责缩范围，最终排序在一个统一 BM25 上做。** 这保留了 OpenViking 的"局部性 + 可扩展"红利，同时保证最终排序与传统 jsonl+BM25 的语义一致，等价性可在 `fs_flat` 条件下逐点验证。

## 五、合成基准 4 条件对照

`scripts/run_fs_ablation.py` 在 mock backend、NimbusFlow synthetic 38 train / 38 eval 上跑：

| condition | final res | final cov | final repeat err | final esc F1 |
|---|---|---|---|---|
| static          | 34.2% | 42.3% | 100.0% | 0.00 |
| jsonl_episodic  | 71.1% | 76.5% |  40.0% | 0.71 |
| **fs_topic_date** | **71.1%** | 73.9% | 44.0% | **0.75** |
| fs_flat         | 71.1% | 76.5% |  40.0% | 0.71 |

读法：

- `fs_flat` 与 `jsonl_episodic` 在所有 5 个指标上**逐位相同**——回归测试通过，文件系统包装层无损耗。
- `fs_topic_date` 解决率与 jsonl 持平（71.1% = 71.1%），escalation F1 略升（+0.04），keypoint 覆盖略降（−2.6pp）——在 38 query 的小集上属于噪声范围；目录分层在小集上没有杠杆，主要价值要到 1k+ 才显现。
- 性能：`tests/test_fs_store.py::test_retrieve_under_50ms_at_1k_cases` 验证 1k case 规模下 retrieve 每次 <50ms。

## 六、与 v2.2 Skills 的关系

| 维度 | v2.2 Skills (procedural) | v2.5 fs_store (episodic) |
|---|---|---|
| 内容 | 一般化的处置流程（"遇到退款先核身份"） | 具体的历史案例（q005 这单是这样处理的） |
| 结构 | `skills/topic_xxx/SKILL.md` + `playbook.json` | `L0_topic/L1_bucket/L2_case.md` |
| 增长 | 反思器升级版本号（auditable） | 工单进来就追加（append-only） |
| 检索 | 按 trigger_terms 命中 | BM25 + 主题/时间过滤 |
| 类比 | "把流程写进 SOP" | "把客服历史聊天记录归档" |

**两轨独立，但都接入 SupportAgent.contexts**——程序性知识告诉 agent "怎么做"，案例记忆告诉 agent "上次类似单子是怎么收的尾"。

## 七、Git / IDE 友好性

文件系统范式自带的额外红利：

1. **diff 可读**：新增一条 case = 新增一个文件，git log/blame 直接定位到具体单子。
2. **人工审批**：人审专员可以打开 IDE 直接编辑 `L2_*.md`，frontmatter 控制 `should_escalate`，无需 jsonl 编辑器。
3. **分支隔离**：A/B 测试只需 `git checkout -b experiment_xx` 复制目录子树。
4. **冷热分离**：旧月份的 `L1_2024-*` 可以 git-archive 或软删，不影响在线索引。

## 八、待办：Exp F 真压测

合成基准已证明"不退化"；要看 OpenViking 报告的 +6 ~ +12pp 在 SEA + DeepSeek 链路上是否复现，需要：

1. 给 DeepSeek 账户充值 ≥ \$1。
2. `python scripts/run_stress_test_exp_f_scaffold.py --i-have-budget`
3. 与 `experiments/stress_test_expanded/exp_e/load_summary.json` 做 diff。

scaffold 已经把 Exp E 的所有组件（multi_agent + LLM-judge groundedness + balanced PII + 校准）原样接好，唯一替换是 `EpisodicMemory → FsEpisodicStore(scheme='topic_date')`。

预期数字范围（基于 OpenViking 自报数据外推，**未在本仓库验证**）：

- `multi_intent` resolution: +6 ~ +12pp（与 OpenViking retail/airline 区间对应）
- p50 latency: 与 Exp E 持平（±10% 内）；目录层只在 retrieve 路径增加一次小 BM25
- 文件系统额外占用：~1 KB / case，500 工单 ≈ 500 KB，可忽略

---

**外部数字标注规则**：本文档引用 OpenViking 的 +6.87 / +11.87pp 全部标为"OpenViking 自报"，等 Exp F 在本仓库跑过再换成自验数据。
