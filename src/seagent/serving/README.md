# Serving 服务层 + 线上反馈闭环

把自进化客服 agent 包成**可部署的线上业务服务**：FastAPI 服务化、多轮会话、
转人工(human handoff)、对齐真实 ticketing(Zendesk/Intercom) 的字段 schema，
并把**隐式/噪声反馈**(thumbs、是否解决、是否 reopen、CSAT)回流成自进化的学习信号。

## 模块

| 文件 | 职责 | 依赖 |
|------|------|------|
| `schema.py`   | Ticket / ChatTurn / AgentReply / Feedback 数据模型 | 纯 stdlib |
| `session.py`  | `SessionManager`：多轮会话状态、按 ticket 维护历史、handoff 标记 | 纯 stdlib |
| `feedback.py` | `FeedbackProcessor`：噪声反馈 -> 学习信号 / 待复盘 Case 草稿 / 代理指标 | 纯 stdlib |
| `app.py`      | FastAPI 应用，`create_app(agent)` 工厂，依赖注入 | **可选** FastAPI |
| `_demo_app.py`| 零依赖桩 agent 的 demo 装配入口(仅连通性验证) | 可选 FastAPI |

核心业务逻辑(schema/session/feedback) **不依赖 FastAPI**，可离线单测。
FastAPI 缺失时本包仍可正常 import，仅 `create_app` 会抛友好提示。

## 启动服务

```bash
# 可选依赖
pip install 'fastapi>=0.110' 'uvicorn[standard]'

# 默认 demo(桩 agent，0.0.0.0:8080)
bash src/seagent/serving/run_server.sh

# 自定义端口 / 指向你自己的装配模块
HOST=127.0.0.1 PORT=9000 SEAGENT_APP=mypkg.wiring:app \
  bash src/seagent/serving/run_server.sh
```

## 端点

| 方法 | 路径 | 说明 | 请求体关键字段 |
|------|------|------|----------------|
| POST | `/chat`     | 进一条客户消息 -> `AgentReply` | `customer_id, text, ticket_id?, subject?, channel?` |
| POST | `/feedback` | 上报反馈 -> 学习信号 | `ticket_id, kind, value?, turn_id?, comment?` |
| POST | `/handoff`  | 转人工 | `ticket_id, reason?` |
| GET  | `/healthz`  | 健康检查 | — |
| GET  | `/metrics`  | 工单状态分布 + 反馈代理指标 | — |

## 接真实 ticketing(Zendesk / Intercom)

字段对照(本项目 -> 厂商)：

```
Ticket.ticket_id   -> Zendesk ticket.id        / Intercom conversation.id
Ticket.channel     -> Zendesk via.channel       / Intercom source.type
Ticket.status      -> Zendesk status            / Intercom state(open/closed/snoozed)
Ticket.priority    -> Zendesk priority          / Intercom priority
Ticket.tags        -> Zendesk tags              / Intercom tags
ChatTurn           -> Zendesk comment           / Intercom conversation_part
Feedback(csat)     -> Zendesk satisfaction_rating / Intercom conversation_rating
Feedback(reopened) -> ticket.status 从 solved 变回 open 的 webhook 事件
```

集成方式：用各厂商的 **Webhook** 把"新消息 / 评分 / 重新打开"推到本服务的
`/chat`、`/feedback` 端点；agent 回复再经厂商 API 回写为工单 comment。

## 隐式反馈 -> 自进化 数据流

一句话：**线上的噪声/隐式反馈(点踩、reopen、低 CSAT)被转成待复盘失败案例草稿，
经人审补全正确解法后，喂给 reflector 聚成 playbook 并灰度上线——模型权重不动，
变的只有 agent 的记忆。**

```
   线上客户
      │  消息(/chat)
      ▼
 ┌──────────┐   AgentReply   ┌──────────────┐
 │ FastAPI  │───────────────▶│  SupportAgent│  (你的真实 agent，注入)
 │  app.py  │◀───────────────│   .handle()  │
 └────┬─────┘                └──────────────┘
      │ 隐式/显式反馈(/feedback): thumbs_down / reopened / 低 CSAT
      ▼
 ┌──────────────────┐
 │ FeedbackProcessor│  to_training_signal(): polarity / weight / is_failure
 │  (feedback.py)   │  proxy_metrics(): deflection / resolution(无 gold label 的代理)
 └────┬─────────────┘
      │ 负向信号 -> Case 草稿(resolution 留空，needs_human_review=True)
      ▼
 ┌──────────────────┐      不直接入库！
 │  待复盘队列       │  pending_review()
 └────┬─────────────┘
      │
      ▼
 ┌──────────────────┐   人工坐席/审核补全正确解法 + 决定是否采纳
 │ Governance/人审   │  (噪声反馈不可信，人审是防止经验池被污染的闸门)
 └────┬─────────────┘
      │ 通过 -> Case(**draft) 入 episodic 经验池
      ▼
 ┌──────────────────┐   按主题聚类失败案例 -> 蒸馏 playbook 规则
 │  Reflector        │  (evolution/ 里已有的离线进化逻辑)
 └────┬─────────────┘
      │ 新 playbook
      ▼
 ┌──────────────────┐   小流量灰度 -> 看 /metrics 代理指标是否回升
 │  灰度发布         │  回升则全量，回退则下线该规则
 └────┬─────────────┘
      │
      └───────────────▶ 下一轮线上反馈(闭环)
```

闸门设计要点：`FeedbackProcessor` 产出的 Case 草稿 `needs_human_review=True` 且
`resolution=""`、`learned_round=-1`，**永远不会直接写入经验池**——因为线上反馈
带噪(误点踩、客户自己操作失误也会 reopen)，必须人审后才采纳，否则进化会被噪声带偏。
