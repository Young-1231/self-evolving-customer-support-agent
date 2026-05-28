---
skill_id: pb_shipping_esc
name: 物流丢件转人工
description: 识别「物流丢件转人工」并转交对应人工团队
topic: shipping
triggers:
- 么办
- 怎么
- 物流
- 流丢
- 件怎
- 丢件
action: escalate
version: 1
enabled: true
created_round: 0
source_case_ids:
- q009
metadata:
  author: reflector
  reviewed_by: ops-team
  reviewed_at: '2026-05-28'
---

# Guidance

transfer_to_human_agents("logistics") 走丢件赔付。


## What to do

1. 转人工 (`transfer_to_human_agents`)
