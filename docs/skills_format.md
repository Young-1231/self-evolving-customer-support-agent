# Skills 文件格式 (v2.2 R6a)

> 对标 [Claude Code Skills](https://docs.claude.com/) 范式：一份 markdown 文件 =
> 一份可独立编辑、git-friendly、人审友好的 SOP，与项目原 `Playbook` 数据结构
> **双向无损互转**。

## 1. 为什么换成 markdown 而不是 jsonl？

v2.1 之前，procedural memory 存储为 `data/procedural.jsonl`（一行一条 Playbook）。
迁移到 markdown + frontmatter 的收益：

| 维度 | jsonl | markdown skill |
| --- | --- | --- |
| Diff/code review | 一行 JSON，难读 | 段落级 diff，审核者能直接读懂 SOP |
| 人工编辑 | 需要谨慎不破坏 JSON | 跟改 README 一样 |
| 互操作 | 项目私有格式 | 与 Claude Code Skills / ClawHub 概念对齐\* |
| 元数据扩展 | 改 dataclass + 迁移脚本 | 加 `metadata:` 子键即可 |
| 懒加载 | 必须全文加载 | `manifest.json` 先选，再加载 body |

\* ClawHub 的真实发布/拉取接口待真实接入后验证；本项目只在概念层对齐
（id / name / triggers / description / body）。

## 2. 文件格式

每个 skill 一份 `.md` 文件，文件名建议 `<skill_id>.md`。

```markdown
---
skill_id: pb_billing_ans
name: "年付月付退款"
description: "处理年付转月付时的差额退款咨询"
topic: billing
triggers: ["年付", "月付", "退款", "余额"]
action: answer            # 或 escalate
version: 1
enabled: true
created_round: 0
source_case_ids: ["q003", "q027"]
metadata:
  author: "reflector"
  reviewed_by: "ops-team"
  reviewed_at: "2026-05-15"
---

# Guidance

年付转月付的差额以 account credit 形式保留，不退回原卡，
下次续费自动抵扣。如果用户坚持要现金退款，转人工。

## When to apply

- 用户明确提到"年付"、"月付"、"差额"、"退款"
- 订阅类账单咨询（非一次性购买）

## What to do

1. 解释 account credit 机制
2. 告知下次续费抵扣
3. 若用户要现金退款 → 调用 `transfer_to_human_agents("billing")`

## What NOT to do

- 不要承诺具体金额
- 不要保证 24h 内到账
```

### Frontmatter 字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `skill_id` | str | yes | 唯一 id，等价于 `Playbook.playbook_id` |
| `name` | str | no | 人类可读名称 |
| `description` | str | no | 一句话描述（供 manifest 检索） |
| `topic` | str | no | 业务域，默认 `general` |
| `triggers` | list[str] | yes | 触发词（同 `Playbook.trigger_terms`） |
| `action` | "answer" \| "escalate" | yes | 检索命中后的动作；`escalate` 会让 agent 设置 `escalate_hint=True` |
| `version` | int | no | 单调递增；`SkillStore.upsert` 自动 +1 |
| `enabled` | bool | no | 灰度/回滚开关（governance 治理） |
| `created_round` | int | no | 进化轮次（审计） |
| `source_case_ids` | list[str] | no | 溯源到 episodic memory 的 case id |
| `metadata` | dict | no | 自由扩展（作者、审阅者、审阅时间、…） |

### Body 结构

- 强约束：必须含一个 `# Guidance` 一级标题区块；该区块的纯文本会被
  ``Skill.guidance()`` 抽出，作为 retrieval 的回答文本。
- 推荐子节：`## When to apply` / `## What to do` / `## What NOT to do`。
  这几节只供人审与未来 agent 在 plan 阶段引用，不影响检索。

如果 body 完全没有 `# Guidance`，回退把整段 body 作为 guidance。

## 3. 与 Playbook 的关系

```text
Reflector → Playbook ──playbook_to_skill──▶ Skill ──dump_skill──▶ .md 文件
                                                          │
                                          parse_skill ◀───┘
                                              │
                                              ▼
                                 SkillStore ──▶ ProceduralMemory(skill_store=...)
```

- ``skill_to_playbook(sk) == 原 Playbook``（业务字段无损）
- ``parse_skill(dump_skill(sk)) == sk``（包括 metadata 等扩展字段）
- 治理（``PlaybookRegistry``）与 Reflector 调用的接口**没有任何变化**

启用方式：

```python
from seagent.memory.procedural import ProceduralMemory
from seagent.skills import SkillStore

store = SkillStore("data/skills/")
proc  = ProceduralMemory(skill_store=store)   # 原 jsonl 接口完全保留
```

`skill_store=None`（默认）时严格走旧 jsonl 路径，向后兼容。

## 4. Manifest（懒加载用）

社区规模的 skill 库不可能每个都把 body 加载到 context，需要先用一份只含元数据
的 ``manifest.json``：

```bash
python -m seagent.skills.manifest data/skills/
```

生成 `data/skills/manifest.json`：

```json
{
  "version": "1",
  "skills": [
    {
      "skill_id": "pb_billing_ans",
      "name": "年付月付退款",
      "description": "处理年付转月付时的差额退款咨询",
      "topic": "billing",
      "triggers": ["年付", "月付", "退款", "余额"],
      "action": "answer",
      "version": 1,
      "enabled": true,
      "path": "pb_billing_ans.md"
    }
  ]
}
```

Agent 启动只需读 manifest（一次磁盘 IO），运行时按 triggers 粗筛、命中后再
lazy-load 对应 `.md` 文件——这是 Claude Code / ClawHub 的标准玩法。

## 5. 与 v2.1 Hooks / c21 Exp D 的关系

- Hooks 层（`src/seagent/hooks/`）拦截的是 agent step 的输入输出事件，与
  procedural memory 的存储格式无关；切到 SkillStore **零影响**。
- c21 Exp D（governance + regression_gate）只依赖 `Playbook` 数据结构和
  `ProceduralMemory.upsert / set_enabled` 接口；这两者都保留原签名。
- 检索分值公式（BM25 trigger overlap）逐字节复制自原 `ProceduralMemory.retrieve`，
  `tests/test_skills.py::test_procedural_memory_skill_store_behaves_like_classic`
  对同一组 playbook 做了"逐 query 同 ref 同 score 同 escalate_hint"的等价断言。

## 6. 与 PyYAML 的关系

`format.py` 优先用 PyYAML 解析/序列化；若环境没装 PyYAML（项目核心依赖为零），
回退到内置的**最小 YAML 子集 parser**（dict / list / str / int / float / bool）。
两条路径都被 `tests/test_skills.py` 覆盖。

## 7. CLI

```bash
# 生成 manifest
python -m seagent.skills.manifest data/skills/

# 端到端 demo（Reflector → markdown → SkillStore → 等价性验证）
python scripts/skills_demo.py
```

## 8. ClawHub 互操作

概念层对齐（id / name / description / triggers / body）：本项目生成的 .md
skill 文件可作为 ClawHub 发布草稿。但 ClawHub 的真实发布 API / 包格式 /
签名要求需要在真正接入时验证，本文不做承诺。
