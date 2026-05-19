# Agent 运营报告 (Ops Report)

- 数据源: `/root/autodl-tmp/self_evolving_agent/scripts/../experiments/prod_demo/traces/prod_demo.jsonl`
- 回合数: **4**

## 核心指标

| 指标 | 值 |
| --- | --- |
| 回合数 (turns) | 4 |
| 自助解决率 deflection | 50.0% |
| 转人工率 escalation | 50.0% |
| 平均时延 avg latency (ms) | 0.91 |
| p50 时延 (ms) | 1.042 |
| p95 时延 (ms) | 1.309 |
| 平均检索命中 avg hits | 5.25 |
| guardrail 拦截率 | 25.0% |
| 异常率 error rate | 0.0% |
| 总 token | 2022 |
| 总成本 (USD) | $0.000495 |
| 平均成本/回合 (USD) | $0.000124 |

## 处置拆分

- 自助解决 (deflected): **2** (50.0%)
- 转人工 (escalated): **2** (50.0%)

## Guardrail 命中 Top

| verdict | 次数 |
| --- | --- |
| block | 1 |

## 近期异常 Turn

| turn | 时延ms | conf | escalate | verdict | error |
| --- | --- | --- | --- | --- | --- |
| 4 | 1.042 | 0.7346629794801682 | Y | allow | - |
| 3 | 0.118 | 0.0 | Y | block | - |
