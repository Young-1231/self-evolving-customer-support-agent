---
skill_id: pb_billing_ans
name: 年付月付退款
description: 处理与「年付月付退款」相关的常见客服咨询
topic: billing
triggers:
- 付月
- 月付
- 额怎
- 怎么
- 么处
- 付差
- 处理
- 年付
action: answer
version: 1
enabled: true
created_round: 0
source_case_ids:
- q000
- q001
metadata:
  author: reflector
  reviewed_by: ops-team
  reviewed_at: '2026-05-28'
---

# Guidance

年付转月付差额走 account credit，下次续费抵扣。 余额以 account credit 保留，不退回原卡。
