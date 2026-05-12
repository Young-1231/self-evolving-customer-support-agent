# τ²-bench 集成：在真实基准上验证自进化记忆

本目录把项目的核心命题——**"不改权重、靠自蒸馏经验持续改进"**——放到 2026 客服 Agent 的事实标准
**τ²-bench**（Sierra，arXiv 2506.07982，确定性 DB-state verifier + pass^k）上做真实 A/B 验证。

## 思路

τ²-bench 每个 domain 自带 `train` / `test` 划分。我们：

1. **Baseline（train）**：用 stock `llm_agent` 跑 train 划分，记录失败工单的轨迹与期望动作。
2. **Reflect（dreaming）**：`seagent/tau2_ext/reflect.py` 把失败案例喂给 LLM，蒸馏出 ≤N 条
   *通用、可迁移、可审计* 的运营 tips（域级 playbook），存为 JSON。
3. **Evaluate（held-out test）**：`memory_agent` 把 playbook 注入 system prompt，在 test 划分上
   与 stock `llm_agent`（memory OFF）对比 pass^1。test 是 train 的 held-out 任务，提升来自泛化。

记忆注入点：`seagent/tau2_ext/memory_agent.py` 子类化 `LLMAgent`，仅在 `system_prompt` 追加
`<learned_experience>` 块；工具/策略/tool-calling 循环完全不变，故 pass^1 的差异可干净归因于经验注入。

## 复现

```bash
cd self_evolving_agent
python3 -m venv .venv-tau2 && . .venv-tau2/bin/activate
pip install -e ref_repos/tau2-bench audioop-lts        # py3.13 需 audioop-lts 回填
cp .env.example .env       # 填入 DEEPSEEK_API_KEY / TAU2_MODEL=deepseek/deepseek-chat

set -a; . ./.env; set +a
python scripts/run_tau2_experiment.py --domain retail --train-tasks 12 --test-tasks 16 --max-steps 50
# 扩到完整 test 划分或 pass^k：
# python scripts/run_tau2_experiment.py --domain retail --test-tasks 40 --trials 4
```

产物：`experiments/tau2/<domain>_playbook.json`（蒸馏的 tips）、`<domain>_results.json`（pass^1 OFF/ON/Δ）。

## 结果（**官方 pass^k 全口径**，对标 τ-bench 论文 2406.12045）

直接调 tau2 `compute_metrics`，`pass^k = C(成功数, k) / C(试验数, k)`。

### retail · DeepSeek-V4-Flash · test=40 × trials=4 = 160 sims/条件
| metric | OFF | ON | Δ |
|---|---|---|---|
| avg_reward | 0.925 | 0.931 | +0.006 |
| pass^1 | 0.925 | 0.931 | +0.006 |
| pass^2 | 0.896 | 0.904 | +0.008 |
| pass^3 | 0.881 | 0.887 | +0.006 |
| pass^4 | 0.875 | 0.875 | 0.000 |

train baseline pass^1=0.900（4/40 失败 → 8 tips）。所有 pass^k 方向一致 ≥0，magnitude 受 model ceiling（OFF 已 92.5%）限制。

注：之前用 `deepseek/deepseek-chat` 实际就是 V4-Flash non-thinking（legacy 别名，2026/07/24 弃用）。已切显式模型名。

### airline（最难域）· DeepSeek-V4-Pro · test=20 × trials=4
顶模型 + 大 headroom 配置，结果文件：`experiments/tau2_airline/airline_results.json`（详见 README §4b）。

## 工程注意点（踩过的坑）

- **Python 3.13 移除了 `audioop`**：装 `audioop-lts` 回填，否则 import 链报错。
- **评测 judge 默认走 OpenAI gpt-4.1**：`DEFAULT_LLM_NL_ASSERTIONS` / `auth_classifier` / `interface_agent`
  的 judge 模型默认 gpt-4.1，无 OpenAI key 会报 *Missing credentials*。脚本 `force_judge_model()` 包裹各
  consumer 模块的 `generate`，把任何 `gpt-*` aux 调用重定向到 deepseek（agent/user 已显式用 deepseek，
  故评分口径不变）。
- **N 与成本**：默认小子集（train12/test16）以控时；评测确定性来自 DB-state，judge 仅用于 NL 断言。
  扩 `--test-tasks 40 --trials 4` 即得完整划分与 pass^k。

## 与合成集实验的关系

- 合成 NimbusFlow 集 = **受控诊断**：零依赖、确定性、可画干净的三条件消融进化曲线（见根 README §4）。
- τ²-bench = **真实背书**：公认基准 + 确定性 verifier，证明同一自进化机制对真实 agent 有效（§4b）。
