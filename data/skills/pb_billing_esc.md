---
skill_id: pb_billing_esc
name: 现金退款转人工
description: 识别「现金退款转人工」并转交对应人工团队
topic: billing
triggers:
- 金退
- 我要
- 款投
- 退款
- 投诉
- 要现
- 现金
action: escalate
version: 1
enabled: true
created_round: 0
source_case_ids:
- q002
metadata:
  author: reflector
  reviewed_by: ops-team
  reviewed_at: '2026-05-28'
---

# Guidance

调用 transfer_to_human_agents("billing")。


## What to do

1. 转人工 (`transfer_to_human_agents`)
