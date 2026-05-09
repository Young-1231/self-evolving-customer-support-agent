# NimbusFlow 自进化客服 Agent 合成数据集

本数据集用于演示一个"自进化客服 / 企业知识库 Agent"的训练与评测闭环。产品为虚构的
SaaS 协作办公产品 **NimbusFlow**（含账单/订阅、登录与安全、集成与 API、数据导出、
权限管理、移动端、故障排查等模块）。全部内容为合成数据，**完全离线、确定性可复现**
（由 `_gen.py` 生成，`_check.py` 自检）。

---

## 1. 目录结构

```
data/
├── kb/                      # 知识库（帮助中心文档）
│   ├── kb_001.md ... kb_030.md   # 30 篇 markdown 文档
│   └── index.jsonl          # 与 .md 一一对应的检索索引（每行一个 doc）
├── eval/
│   └── queries.jsonl        # 76 条查询（train/eval 各半）
├── README.md                # 本文件
├── _gen.py                  # 确定性生成脚本
└── _check.py                # 自检脚本（验证所有不变量）
```

---

## 2. 字段说明

### 2.1 KB 文档（`kb/kb_xxx.md`）

每篇 markdown 以 YAML frontmatter 开头，正文为 150–400 字的帮助文章：

```
---
doc_id: kb_001
title: 如何重置 NimbusFlow 登录密码
topic: account_security
---

正文……
```

- `doc_id`：唯一标识，`kb_001` … `kb_030`。
- `title`：文章标题。
- `topic`：取值之一 —
  `billing` / `account_security` / `integrations_api` / `data_export` /
  `permissions` / `mobile_app` / `troubleshooting` / `general`。

### 2.2 KB 索引（`kb/index.jsonl`）

每行一个 JSON 对象，**与 `.md` 文件一一对应、`text` 与正文完全一致**：

```json
{"doc_id": "kb_001", "title": "...", "topic": "account_security", "text": "<正文纯文本，去掉 frontmatter>"}
```

### 2.3 查询集（`eval/queries.jsonl`）

每行一个 JSON 对象：

| 字段 | 含义 |
|------|------|
| `id` | 查询编号，如 `q001` |
| `split` | `train` 或 `eval` |
| `group` | 同一底层问题的 train/eval 变体共享同一 group id（如 `g07`）。eval 是 train 的同义改写，用于考察泛化 |
| `query` | 用户提问（口语化，可含错别字/省略） |
| `required_keypoints` | 2–4 个**必须出现在正确回答里的关键事实点**（短语）。verifier 用子串/模糊匹配判分，因此短语简短、明确、可匹配 |
| `gold_doc_ids` | 正确答案所依据的 KB doc id 数组（hard 类可为空 `[]`） |
| `should_escalate` | 是否需要转人工（退款金额纠纷、账号被盗、企业合同条款等） |
| `difficulty` | `easy` 或 `hard` |
| `resolution` | 一段完整正确的回答文本，**自然包含全部 `required_keypoints`**。模拟"人工客服最终解决方案"，训练阶段作为反馈写入经验池 |

---

## 3. 规模统计

- **KB 文档**：30 篇
- **查询**：76 条（`train` 38 / `eval` 38），共 38 个 group（每组 1 train + 1 eval）
- **平均关键点数**：3.03 个 / 查询

### 3.1 KB 文档按 topic 分布

| topic | 篇数 |
|-------|------|
| billing | 4 |
| account_security | 4 |
| integrations_api | 4 |
| data_export | 3 |
| permissions | 3 |
| mobile_app | 3 |
| troubleshooting | 4 |
| general | 5 |
| **合计** | **30** |

### 3.2 查询按难度 / split 分布

| difficulty | train | eval | 合计 | 占比 |
|-----------|-------|------|------|------|
| easy | 18 | 18 | 36 | 47% |
| hard | 20 | 20 | 40 | 53% |
| **合计** | **38** | **38** | **76** | 100% |

- 需要转人工（`should_escalate=true`）：**14 条（18%）**，train/eval 各 7 条，全部与 hard 重叠。

### 3.3 查询按 topic 分布（按 gold 文档 topic 归类，无 gold 的退款/盗刷类计入 billing）

| topic | 条数 |
|-------|------|
| general | 14 |
| account_security | 12 |
| billing | 12 |
| integrations_api | 10 |
| troubleshooting | 10 |
| data_export | 6 |
| permissions | 6 |
| mobile_app | 6 |

8 个 topic 全部覆盖。

---

## 4. 为什么这样设计能演示"自进化"

整套数据围绕一条可观测的**进化曲线**设计：base agent 起点弱 → 训练阶段把人工
`resolution` 沉淀为经验/playbook → eval 变体上准确率显著上升。

### 4.1 三类查询的角色

- **easy（约 47%）—— 检索即可答对的基线能力。**
  这类查询的 `required_keypoints` 能在其 `gold_doc_ids` 文档正文中**完整找到**
  （`gold_doc_ids` 非空）。base agent 只要会检索 KB 就能答对，用于标定"基线分"，
  也确保进化曲线有一个稳定的下界，不会因为全是难题而失真。

- **hard（约 53%）—— 知识库的覆盖盲区，是进化的主战场。**
  这类查询的 `required_keypoints` 中**至少有一个关键点故意不出现在任何 KB 文档里**
  （代表"口口相传的运营经验"，如年付转月付"不退回原卡"、webhook 用
  "5 分钟时间窗"防重放、429 要"读取 Retry-After 头"、唯一 Owner"无法直接退出"
  必须"先转移所有权"等）。`gold_doc_ids` 可为空或只含部分相关文档。
  因此 base agent 起初**必然答不全**（缺失的就是那条没写进文档的经验）。
  只有当训练阶段把对应 train 变体的 `resolution` 存入经验池、并归纳成 playbook 后，
  eval 变体才可能命中这些关键点而答对。这正是"自进化"带来增量的地方。

- **escalate（约 18%，全部叠加在 hard 上）—— 学会"何时不自己答"。**
  退款金额纠纷、账号被盗、2FA 失去恢复码、盗刷、企业合同条款、数据驻留合规等情形
  `should_escalate=true`，其 `required_keypoints` 必含"转接人工 / 账单团队 /
  安全团队 / 销售与法务团队"之类的转人工动作。考察 agent 能否学会正确转人工，
  而不是硬答或乱答。

### 4.2 group 机制让"学到的经验"可迁移、可度量

每个底层问题有一对 train/eval 变体共享同一 `group`，且**组内 `required_keypoints`
完全一致**（自检强制保证），只是 `query` 措辞不同（同义改写、错别字、省略）。

- 训练阶段只喂 train 变体的反馈（`resolution`）；
- 评测只看 eval 变体；
- 由于关键点一致而表述不同，**eval 上的提升只能来自泛化的经验，而非死记 query 文本**。

这样就能干净地画出"训练前 vs 训练后在 eval 上的关键点命中率/转人工正确率"对比，
即自进化曲线。hard 组的 eval 提升幅度，直接量化了"经验沉淀 + playbook 归纳"的价值。

### 4.3 verifier 友好

`required_keypoints` 均为简短、确定的短语，便于用子串/模糊匹配做确定性判分；
每条 `resolution` 都自然包含其全部关键点（自检保证），因此可直接作为"满分答案"
对照，无需人工评审即可离线评测。

---

## 5. 自检不变量（`_check.py` 全部通过）

1. `index.jsonl` 每条 `text` 非空、`doc_id` 唯一；与 `.md` 一一对应且 `text` 一致；
   `topic` 合法；正文 150–400 字。
2. `queries.jsonl` 每条 JSON 合法、9 个字段齐全；`id` 唯一；`split`/`difficulty` 取值合法；
   `should_escalate` 为布尔；`required_keypoints` 2–4 个；`gold_doc_ids` 指向真实文档。
3. 每条 `resolution` 自然包含其全部 `required_keypoints`。
4. 每个 group 至少 1 train + 1 eval，且组内关键点、难度一致。
5. **easy**：每个关键点都能在其 gold 文档正文中找到。
6. **hard**：至少一个关键点**无法**在其 gold 文档正文中完整匹配（确认"确实难"）。
7. **escalate**：`required_keypoints` 含转人工类动作短语。

复现与自检：

```bash
python _gen.py     # 生成 kb/*.md, kb/index.jsonl, eval/queries.jsonl
python _check.py   # 打印 SELF-CHECK RESULT: PASS 及统计
```
