# Governance：把"自进化"当作受治理的发布资产

## 一句话

自进化规则**不是黑盒权重更新**，而是一份份可审计、可灰度、可回滚的**受治理资产**——
每条 playbook 必须走完 `提案 → 人审 → 灰度 → 可回滚` 才能上线生效。

## 为什么需要它(misevolution 防护)

学术上叫 *misevolution*（arXiv 2509.26354）：一个会自我改写行为的 Agent，可能在
"修好一个 case"的同时悄悄让整体变差、引入越权动作或泄露记忆。在真实客服业务里，
这等价于"一次没人审过的线上变更直接全量"——不可接受。本模块把自进化的每一步都
落到一个工程化、可问责的发布流程上：

| misevolution 风险 | 本模块的防护 |
|---|---|
| 自生成规则直接全量生效 | `lifecycle` 状态机：必须经人审 + 灰度才能 `active` |
| 改了局部、退化全局(SLO 回归) | `regression_gate` 发布门禁：activate 前比对关键指标，回退即拒绝 |
| 出事无法快速止血 | `rollback`：任意非终态一键回滚，同步禁用 ProceduralMemory 中的规则 |
| 谁改的、改了什么说不清 | 审计日志(JSONL)：每次状态变更记录 who / when / what / why |
| 客户 PII 沉淀进长期记忆被复用泄露 | `memory_hygiene.scrub_case`：入库前脱敏 |
| 经验池灌水/陈旧/自相矛盾 | `dedup` / `ttl_filter` / `detect_conflicts` |

## 三个组件

### 1. lifecycle.py — 发布生命周期状态机
```
proposed → approved → canary → active
   └────────── rollback / deprecate(从任意非终态)──────────┘
```
- `proposed` Reflector 刚产出，等待人审
- `approved` 人审通过(记录 approver，谁拍的板)
- `canary`   小流量灰度观察
- `active`   全量生效(**此刻才**写入 ProceduralMemory 并 enabled)
- `rolled_back` / `deprecated` 终态

治理状态单独存 `experiments/governance/playbook_registry.json`，**不污染**
ProceduralMemory；每次状态变更追加一条审计日志 `audit_log.jsonl`。

### 2. regression_gate.py — 发布门禁
启用候选 playbook 前后，在同一 eval 集上跑 `verify` + `aggregate`，逐项比对：
- `resolution_rate` / `keypoint_coverage` / `escalation_f1`(转人工 F1)越大越好；
- `human_intervention_rate`(转人工率)越小越好。

任一关键指标回退超过容忍阈值(SLO 回归)即 `GateResult.passed=False`，拒绝 activate。

### 3. memory_hygiene.py — 经验池治理
- `dedup`            近重复 case 合并(token Jaccard)
- `ttl_filter`       按 learned_round 过期遗忘
- `detect_conflicts` 同 topic 下矛盾解法标记(转人工决策相反 / 互斥短语)
- `scrub_case`       入库前 PII 脱敏(优先 guardrails.pii，缺失则正则兜底)

## 接入自进化闭环

Reflector 产出 playbook 之后，**不要**直接 `procedural.upsert`，改为：

```python
reg = PlaybookRegistry(registry_path=..., audit_path=..., procedural=procedural)

# 1) 提案
reg.propose(pb, proposer="reflector")
# 2) 人审(线上=人工；离线评测可程序化批准)
reg.approve(pb.playbook_id, approver="alice@oncall")
# 3) 灰度
reg.promote_to_canary(pb.playbook_id)
# 4) 门禁：启用前 vs 启用后跑 eval，回退即拦截
result = evaluate_playbook(pb, baseline_metrics, eval_fn)
# 5) 仅当门禁 PASS 才全量生效
if result.passed:
    reg.activate(pb.playbook_id)          # 写入 ProceduralMemory + enabled
else:
    reg.rollback(pb.playbook_id, "release-bot", result.reason)
```
线上观测到 SLO 回归时随时 `reg.rollback(...)` 即可即时止血。
