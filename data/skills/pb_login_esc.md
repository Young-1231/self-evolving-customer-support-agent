---
skill_id: pb_login_esc
name: 账号被盗转安全团队
description: 识别「账号被盗转安全团队」并转交对应人工团队
topic: login
triggers:
- 么办
- 怎么
- 被盗
- 号被
- 账号
- 盗了
- 了怎
action: escalate
version: 1
enabled: true
created_round: 0
source_case_ids:
- q005
metadata:
  author: reflector
  reviewed_by: ops-team
  reviewed_at: '2026-05-28'
---

# Guidance

transfer_to_human_agents("security")。


## What to do

1. 转人工 (`transfer_to_human_agents`)
