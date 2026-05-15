# Guardrails 安全模块

把自进化客服 Agent 当成真实业务系统来加固：**入站**做提示注入/越狱检测与 PII 识别，
**出站**做 groundedness（回答必须有检索证据支撑）、合规策略与 PII 脱敏。

设计原则：默认**零第三方依赖**（纯 Python/正则，CI 与离线 mock backend 都依赖这一点），
重型能力（Presidio、LLM-judge）做**可选增强**——装了就用，没装则确定性回退。

## 各 guard 的真实业务用途与借鉴项目

| 文件 | guard | 业务用途 | 借鉴的开源项目 |
|------|-------|----------|----------------|
| `pii.py` | `redact_pii` | 入库经验前脱敏、出站回答兜底脱敏、日志去标识化。覆盖邮箱/中国与国际手机号/身份证/银行卡/IP/显式自报姓名。 | **Microsoft Presidio**（analyzer 找实体 + anonymizer 替换占位符的两段式；装了则 try-import 增强） |
| `groundedness.py` | `check_groundedness` | 出站前逐句校验回答是否有检索证据支撑，无支撑句标为潜在幻觉，据此转人工。 | **Ragas** 的 faithfulness / groundedness（拆原子陈述判定可推得性；这里用字符 n-gram 重叠做确定性实现，预留 LLM-judge 回调） |
| `injection.py` | `detect_injection` | 入站第一道闸，拦截"忽略以上指令""泄露 system prompt""你现在是 DAN""解除安全限制"等中英文注入/越狱，防记忆投毒。 | **guardrails-ai** / **NVIDIA NeMo Guardrails** 的 input rail |
| `policy.py` | `check_output_policy` | 独立于 groundedness 的合规闸：禁止超额退款承诺、法律保证、泄露内部系统信息；规则可配置。 | **NeMo Guardrails** output rail + **guardrails-ai** validator 体系 |
| `pipeline.py` | `GuardrailPipeline` | 编排上述四项为 `check_input` / `check_output` 两道闸，输出 `GuardrailReport`，决策 allow / rewrite / escalate / block。 | **NeMo Guardrails** 的 input/output rails 分层 + guardrails-ai 的 fix/reask/escalate 动作语义 |

## 决策动作语义

- `allow`：放行原文。
- `rewrite`：内容需改写（如降级为保守话术、追加免责声明）。
- `escalate`：转人工（典型触发：groundedness 不足，疑似幻觉）。
- `block`：硬拦截（强注入攻击、退款超额承诺、内部信息泄露）。

## 接入 SupportAgent 的两个调用点

```python
from seagent.guardrails import GuardrailPipeline

guard = GuardrailPipeline()

# 调用点 1 —— 入站: handle() 最开始
in_report = guard.check_input(query)
if in_report.blocked:
    return AgentResult(query=query, answer="<请求被安全策略拦截>", escalate=True, confidence=0.0)
safe_query = in_report.redacted_text  # 用于入库/日志的脱敏版本

# ... 正常检索 + 生成 answer ...

# 调用点 2 —— 出站: 生成 answer 之后、return 之前
out_report = guard.check_output(answer, contexts)
final_answer = out_report.redacted_answer
escalate = escalate or out_report.action in ("escalate", "block")
```

## 可选增强

- 安装 `presidio-analyzer` 后，`redact_pii` 自动叠加 Presidio 的实体识别结果。
- 给 `check_groundedness(..., llm_judge=fn)` 传入回调即切换到 LLM 逐句判定（Ragas 风格）。

## 测试

```bash
PYTHONPATH=src python -m pytest -q tests/test_guardrails.py
```
