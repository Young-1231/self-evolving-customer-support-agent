# 下一阶段路线图 (P1)：LLM-judge Groundedness + 三信号 vote

_最后更新：2026-05-28_

## 背景

`§4h` 通过 Exp C 二次证伪，锁定**真瓶颈是 guardrail 的 groundedness check**。当前 groundedness 是确定性 n-gram 重叠实现，在英文 stiff template 回答上 false-fail 严重——critic confidence 调到 0.30 也救不回来，因为 groundedness 强制把 action 设为 ESCALATE。

下一阶段（约 1 周工作量）做三件事，把真瓶颈真正修掉，跑出 Exp D。

## 三件事

### 1. LLM-judge groundedness（已落 scaffold）
位置：`src/seagent/guardrails/groundedness_llm.py`

把 groundedness 从"n-gram 重叠"升级到"LLM 二分类 + 阈值校准"：
- 单次 LLM call，输入 (answer, top-k context passages)，输出 {supported: bool, confidence: float, missing_claims: [str]}
- 默认 model = deepseek/deepseek-chat（便宜、tool-calling 强）
- 可选 batch（4-8 条一起 judge 降成本）
- 阈值（confidence>=tau 视为 grounded）按域校准，复用 `seagent.calibration` 模块

**API**:
```python
from seagent.guardrails.groundedness_llm import LLMJudgeGroundedness
g = LLMJudgeGroundedness(model="deepseek-chat", api_base="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY")
result = g.check(answer, contexts)  # GroundednessResult(score, supported, missing_claims)
```

### 2. 三信号 vote 决策（critic / groundedness / policy → escalate）
位置：`src/seagent/agent/escalation_voting.py`（待实现）

把当前的 OR 逻辑（任一信号→escalate）改成可配置 vote：
- `vote_mode='majority'`：≥2 个信号 escalate 才转
- `vote_mode='weighted'`：每信号带权重（critic 0.4 / groundedness 0.4 / policy 0.2）
- `vote_mode='unanimous'`：3 个都 escalate 才转（最激进）
- 默认保留 `vote_mode='any'`（旧行为，向后兼容）

### 3. Exp D：重跑 Exp B mixed tickets + 全栈优化
脚本：`scripts/run_stress_test_exp_d.py`（待实现）

配置：
- KB: `data/kb_expanded/index.jsonl`（176 篇）
- tickets: `experiments/stress_test_expanded/exp_b/tickets.jsonl`（同 Exp B/C 复用）
- agent: `SupportAgent(calibrator + GuardrailPipeline(pii_precision_mode='balanced', groundedness_llm=True), vote_mode='majority')`
- 500 tickets, 并发 16, DeepSeek

期望（hypothesis to be tested, 不预设结论）：
- escalation 从 Exp C 93% 降到 ≤ 60%
- normal_easy resolution 从 6.5% 涨到 ≥ 30%
- block_rate 保持 ~15-20%（PII 保护不削弱）
- 总成本约 +30%（多一次 groundedness LLM call/turn）

## 评估指标 + 比对路径

| 指标 | Original | Exp A | Exp B | Exp C | **Exp D (期望)** |
|---|---|---|---|---|---|
| escalation_rate | 85.2% | 85.6% | 92.0% | 93.0% | **≤ 60%** |
| block_rate | 3.3% | 2.7% | 19.8% | 19.0% | **~15-20%** |
| normal_easy res | 23.2% | 24.4% | 11.3% | 6.5% | **≥ 30%** |
| cost / ticket | $0.00033 | $0.00033 | $0.00033 | $0.00033 | **~$0.00043** |

**如果 Exp D 仍然没改善**，意味着真瓶颈在更深的 stack（可能是 LLM 本身对英文 stiff template 缺乏强归纳能力，或者 e-commerce 数据集本身的 question/answer 配对就是低质的）——这种情况下要进入 **Phase P2**（更换基础数据 / 切换 base model）。

## 工作量估计

| 任务 | 时长 | 优先级 |
|---|---|---|
| LLM-judge groundedness 实现 + 单元测试 | 1.5 天 | P0 |
| 三信号 vote 模块 + 集成 SupportAgent + 测试 | 1 天 | P0 |
| 阈值校准扩展（让 calibrator 也校准 groundedness 阈值） | 0.5 天 | P1 |
| Exp D 跑 + 数据分析 + report | 0.5 天 | P0 |
| README §4i + senior_review 更新 | 0.5 天 | P0 |
| GitHub 发布 + portfolio 收尾 | 1 天 | P1 |
| **合计** | **~5 个工作日 / 1 周** | — |

## 风险与备选

- **风险 1**: LLM-judge groundedness 增加 ~30% LLM 成本和延迟。
  - 缓解：batch 4 条 / call，加 cache（同 answer 不重复 judge），confidence-gated 跳过（critic 高置信→跳 groundedness）
- **风险 2**: 三信号 vote 改激进了可能引入新的 safety issue（injection escape）。
  - 缓解：vote 只对 `escalate`，对 `block` 保持 OR 逻辑（任一硬拦保留）
- **风险 3**: Exp D 没改善，意味着 e-commerce 数据本身就难以由 30 篇 NimbusFlow 风格 KB 覆盖。
  - 备选：扩 KB 到 500+ 篇 e-commerce-specific 内容 + 重跑 Exp D'。

## 退出标准

完成下面 5 条任意 3 条，即可 mark 本阶段为完成并发布 v1.0：

- [ ] Exp D escalation < 70%
- [ ] Exp D normal_easy resolution > 25%
- [ ] 三信号 vote 实现且全测通过
- [ ] LLM-judge groundedness 在合成基准上不退化（与确定性 groundedness 对比）
- [ ] GitHub README + portfolio 发布
