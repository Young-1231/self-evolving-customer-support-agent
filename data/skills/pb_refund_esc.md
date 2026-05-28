---
skill_id: pb_refund_esc
name: 退款仲裁转人工
description: 识别「退款仲裁转人工」并转交对应人工团队
topic: refund
triggers:
- 商家
- 家拒
- 款怎
- 退款
- 投诉
- 绝退
- 么投
- 怎么
action: escalate
version: 1
enabled: true
created_round: 0
source_case_ids:
- q011
metadata:
  author: reflector
  reviewed_by: ops-team
  reviewed_at: '2026-05-28'
---

# Guidance

transfer_to_human_agents("refund-arbitration")。


## What to do

1. 转人工 (`transfer_to_human_agents`)
