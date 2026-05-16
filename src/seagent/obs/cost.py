"""Token 与成本估算(离线、零依赖)。

真实可观测性平台(Langfuse / Arize Phoenix / OpenLLMetry)会把每次 LLM 调用的
prompt/completion token 与美元成本记进 span。这里在没有 provider usage 字段、也不
想引 tiktoken 的前提下，用字符/词混合启发式做一个"够用"的近似：

  - 英文/代码：约 4 字符 ~= 1 token；
  - 中文等 CJK：约 1.6 字符 ~= 1 token(单字普遍切成 1~2 token)；

因此对 ASCII 与 CJK 分别计数再合并。这套估算的目标不是和 provider 账单逐分对齐，
而是在离线评测/CI 里给出量级正确、可横向比较的成本信号。价目表标注为"近似、可配"，
真实接入时应以 provider usage 回填覆盖。
"""
from __future__ import annotations

from typing import Dict, Tuple

# --- 价目表：单位为"美元 / 1K token"，(input, output) ----------------------
# 来源为各家公开定价的近似快照，仅用于离线量级估算；生产应从配置/账单回填。
# 标注 approx，因为定价随时间变动，且不同区域/批量档位会有差异。
PRICING_USD_PER_1K: Dict[str, Tuple[float, float]] = {
    # DeepSeek(本项目 openai-compatible 默认目标之一)
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
    # OpenAI
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "o4-mini": (0.0011, 0.0044),
    # Anthropic
    "claude-3-5-haiku": (0.0008, 0.004),
    "claude-3-5-sonnet": (0.003, 0.015),
    # 本地/mock：免费
    "mock": (0.0, 0.0),
    "local": (0.0, 0.0),
}

# 找不到模型时的兜底价(取一个便宜小模型量级，避免成本归零误导)
_DEFAULT_PRICE: Tuple[float, float] = (0.00015, 0.0006)


def estimate_tokens(text: str) -> int:
    """字符/词混合近似 token 数。

    ASCII 段按 ~4 char/token，CJK 段按 ~1.6 char/token，二者相加并向上取整。
    空串返回 0。这是一个确定性纯函数，便于测试与回归比较。
    """
    if not text:
        return 0
    ascii_chars = 0
    cjk_chars = 0
    for ch in text:
        # CJK 统一表意文字 + 常见全角假名区间，粗略覆盖中日韩
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7A3:
            cjk_chars += 1
        else:
            ascii_chars += 1
    tokens = ascii_chars / 4.0 + cjk_chars / 1.6
    return max(1, int(tokens + 0.999))  # 非空文本至少 1 token


def lookup_price(model: str) -> Tuple[float, float]:
    """返回 (input_price, output_price)，单位美元/1K token；未知模型回退默认价。"""
    if not model:
        return _DEFAULT_PRICE
    key = model.strip().lower()
    if key in PRICING_USD_PER_1K:
        return PRICING_USD_PER_1K[key]
    # 宽松前缀匹配：如 "gpt-4o-mini-2024-07-18" -> "gpt-4o-mini"
    for known, price in PRICING_USD_PER_1K.items():
        if key.startswith(known):
            return price
    return _DEFAULT_PRICE


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    """按价目表估算单次调用美元成本。token 数应来自 provider usage 或 estimate_tokens。"""
    in_price, out_price = lookup_price(model)
    cost = (max(0, in_tok) / 1000.0) * in_price + (max(0, out_tok) / 1000.0) * out_price
    return round(cost, 8)
