# Stress test — LLM-driven realistic load on the self-evolving agent

回答的问题：**"这个自进化客服 Agent 在 ~500 量级真实感输入下表现如何、企业落地 gap 多大？"**

不再做 reward-style judging — 只看分布、延迟、成本、guardrail 行为、记忆膨胀。

## 1. 怎么跑

```bash
# 0. 准备环境变量（已在 .env）
set -a; . ./.env; set +a       # DEEPSEEK_API_KEY / TAU2_MODEL

# 1. 全套(默认 N=500, concurrency=20, model=deepseek-chat=V4-Flash)
PYTHONPATH=src python scripts/run_stress_test.py all

# 2. 分阶段
PYTHONPATH=src python scripts/run_stress_test.py generate --n 500
PYTHONPATH=src python scripts/run_stress_test.py load     --concurrency 20
PYTHONPATH=src python scripts/run_stress_test.py memory   --memory-sizes 10 100 1000 5000

# 3. 跑测试(零依赖, base python)
PYTHONPATH=src python -m pytest -q tests/test_stress.py
```

可选环境变量：
- `STRESS_MODEL` (default: `deepseek-chat`) — 切其它 openai-compatible 模型
- `STRESS_API_BASE` (default: `https://api.deepseek.com`)
- `STRESS_API_KEY_ENV` (default: `DEEPSEEK_API_KEY`)
- `STRESS_TEMPERATURE` (default: `0.0`，生成端用 0.9 内部硬编码)

> ⚠ **不要切到 deepseek-reasoner / v4-pro**：thinking-mode 多轮工具调用有已知 bug；
> V4-Flash non-thinking 才是 tool-calling-safe 的，本压测默认就是它。

## 2. 产出含义

| 产物 | 含义 |
|---|---|
| `tickets.jsonl` | LLM 生成的 500 条真实感工单(确定性, seed 控制；缓存命中不重生成) |
| `stress_trace.jsonl` | 每条 ticket 的 OTel-like trace(latency 分阶段 / 检索命中 / cost / guardrail verdict) |
| `load_records.jsonl` | 每条 ticket 的压测记录(LoadRecord schema) |
| `load_summary.json` | 总体 + by-category 聚合 |
| `memory_points.jsonl` | 不同记忆规模下的 retrieval latency / resolution rate |
| `report.md` | 综合报告(总体表 + 类别表 + 记忆规模表 + 拐点建议) |
| `fig_latency_hist.png` | 单 ticket latency 分布直方 |
| `fig_category_breakdown.png` | 按类别拆解的 resolution / escalate / block / error |
| `fig_memory_scaling.png` | 记忆规模 vs (检索 ms, 解决率) 双轴曲线 |

## 3. 默认配置预算估计

- **生成阶段**：500 条 * (~120 in / ~80 out tok)  ≈ 60K / 40K tok  ≈ **$0.06** (deepseek-chat)
- **压测阶段**：500 条 * (~600 in / ~150 out tok, 含 generation+critic) ≈ 300K / 75K tok ≈ **$0.16**
- **memory 阶段**：4 个规模点 * 30 eval * 1 LLM call ≈ 12K tok ≈ **$0.01**
- **合计**：**约 $0.20 ~ $0.50** (低于 `--budget-usd 5.0` 默认阈值；硬约束 <$2)

预算超阈值时脚本会停下要求 `--yes` 显式确认。

## 4. 预期数字范围(基于本项目 prod_demo / cost_benefit 历史实测)

| 指标 | 预期范围(DeepSeek V4-Flash, concurrency=20) | 解读 |
|---|---|---|
| QPS | 5~15 | 受 DeepSeek API rate-limit 与单次端到端 ~1.5s 牵制 |
| p50 latency | 1500~2500 ms | KB retrieve <50ms + generate ~1s + critic ~0.7s |
| p95 latency | 3500~6000 ms | 长上下文 / multi_intent 拖尾 |
| p99 latency | 5000~10000 ms | API 偶发慢响应 |
| total cost | ~$0.20 | 全 500 条端到端 |
| error_rate | < 0.03 | 主要来自 429 / 网络抖动 |
| escalation_rate (overall) | 0.30~0.45 | 难案 + 多意图 + 注入会推高 |
| block_rate (overall) | 0.04~0.07 | 大致等于 injection 类比例 |
| resolution (normal_easy) | > 0.80 | KB-only 即可 |
| resolution (normal_hard) | 0.35~0.55 | 需要 episodic; 冷启动时偏低 |
| resolution (pii) | > 0.70 | PII 不阻断业务, 仅脱敏 |
| escalation (multi_intent) | > 0.60 | **预期高 — 这就是企业落地 gap 之一** |
| block (injection) | > 0.80 | injection guard 召回应该足够高 |

记忆膨胀拐点(基于 BM25 in-Python, 单线程)：

| size | 预期 avg_retrieval_ms |
|---|---|
| 10 | < 1 |
| 100 | 1~3 |
| 1000 | 10~25 |
| 5000 | 50~150 |

**拐点(knee)预期在 1000~2000 量级**。这给"何时启用 TTL / dedup"提供了硬数据。

## 5. 失败模式归类表 schema

跑完后建议人工 sample 10~20 条 escalate=True 或 block=True 的 trace, 按以下表分类
("失败" 在这里包括"不必要的转人工", 不只是 hard error)：

```
| ticket_id | category | failure_kind     | root_cause                | proposed_fix                |
|-----------|----------|------------------|---------------------------|-----------------------------|
| t_000123  | injection| over-block       | guard 把正常吐槽误伤      | 调高 injection_block_score  |
| t_000456  | multi_intent | under-resolve| agent 只回了第 1 个意图   | 引入 query splitter         |
| t_000789  | pii      | leak-residual    | 输出仍含部分卡号          | 强化输出 PII regex          |
| t_000654  | normal_hard | hallucination | KB 没覆盖, generation 编造 | 触发 groundedness->escalate |
| t_000222  | multilingual | retrieval miss| BM25 对中英混合分词差     | 上 dense / hybrid retrieval |
| t_000901  | normal_easy | infra error   | 429 / timeout             | retry with backoff          |
```

`failure_kind` 推荐取值：
- `over-block` / `under-block`
- `over-escalate` / `under-resolve`
- `hallucination` / `leak-residual`
- `retrieval-miss`
- `infra-error`
- `latency-tail` (单条 > p99)

把这张表填上是回答"企业落地 gap 多大"的最直接证据。
