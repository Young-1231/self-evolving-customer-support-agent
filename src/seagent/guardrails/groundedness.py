"""Groundedness / faithfulness 校验：回答的每句话必须有检索证据支撑。

业务用途：自进化客服最怕"自信地编造"——模型把检索不到的政策/数字凭空说出来。
出站前把回答切句, 逐句检查是否被任一 context(KB / episodic / playbook)支撑;
没有证据支撑的句子标为 ``unsupported_claims``(潜在幻觉), 据此决定改写或转人工。

借鉴：Ragas 的 faithfulness / groundedness 指标
(https://github.com/explodinggradients/ragas)——把答案拆成原子陈述, 判断每条
是否可由检索上下文推得。Ragas 默认用 LLM 做判定; 这里给出**零依赖的确定性实现**
(字符 n-gram 重叠, 复用 ``seagent.memory.bm25.tokenize`` 的分词思路), 并预留
可选 LLM-judge 回调接口, 默认走确定性路径以保证 CI 可复现。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..llm.base import Passage
from ..memory.bm25 import tokenize

# 句子切分: 中英文标点都作为边界(含换行/分号)。
_SENT_SPLIT = re.compile(r"[。！？!?;；\n]+|(?<=[.!?])\s+")

# 不承载事实、无需证据支撑的寒暄/客套句(避免误判为幻觉)。
# CJK 前缀不依赖 \b(中文字符间无词边界); 英文寒暄词单独用 \b 约束。
_BOILERPLATE = re.compile(
    r"^(?:(?:您好|你好|感谢|谢谢|不客气|很高兴|请稍等|抱歉|对不起)"
    r"|(?:hello|hi|thanks|thank you|you'?re welcome|please|sorry)\b)",
    re.IGNORECASE,
)


@dataclass
class GroundednessResult:
    score: float                 # 被支撑句子占比 [0,1]
    supported: bool              # score 是否达到阈值
    unsupported_claims: List[str] = field(default_factory=list)  # 缺证据的句子
    n_sentences: int = 0         # 参与判定的事实句数量


def _split_sentences(answer: str) -> List[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(answer or "")]
    return [s for s in parts if s]


def _is_factual(sent: str) -> bool:
    """寒暄/过短的句子不计入事实性判定。"""
    if len(sent) < 4:
        return False
    return not _BOILERPLATE.match(sent.strip())


def _overlap(sent_tokens: List[str], ctx_token_set: set) -> float:
    """句子 token 落在某个 context token 集合中的比例(类似 token recall)。"""
    if not sent_tokens:
        return 0.0
    hit = sum(1 for t in sent_tokens if t in ctx_token_set)
    return hit / len(sent_tokens)


def check_groundedness(
    answer: str,
    contexts: List[Passage],
    tau: float = 0.6,
    min_supported_ratio: float = 0.8,
    llm_judge: Optional[Callable[[str, List[Passage]], bool]] = None,
) -> GroundednessResult:
    """判断 ``answer`` 是否被 ``contexts`` 充分支撑。

    Args:
        answer: 待校验的回答。
        contexts: 检索到的证据段(``Passage`` 列表)。
        tau: 单句被判"支撑"所需的 token 重叠阈值(确定性路径)。
        min_supported_ratio: 达到多少比例的事实句被支撑才算整体 ``supported``。
        llm_judge: 可选 LLM-judge 回调 ``(sentence, contexts) -> bool``;
            提供时优先用它逐句判定(对齐 Ragas 的 LLM 路径), 否则走确定性重叠。

    Returns:
        ``GroundednessResult``。无事实句(纯寒暄)或无答案时视为 supported, score=1.0。
    """
    sentences = [s for s in _split_sentences(answer) if _is_factual(s)]
    if not sentences:
        return GroundednessResult(score=1.0, supported=True, unsupported_claims=[], n_sentences=0)

    if not contexts:
        return GroundednessResult(
            score=0.0, supported=False, unsupported_claims=list(sentences),
            n_sentences=len(sentences),
        )

    ctx_token_sets = [set(tokenize(c.text)) for c in contexts]

    unsupported: List[str] = []
    for sent in sentences:
        if llm_judge is not None:
            ok = bool(llm_judge(sent, contexts))
        else:
            stoks = tokenize(sent)
            ok = any(_overlap(stoks, cs) >= tau for cs in ctx_token_sets)
        if not ok:
            unsupported.append(sent)

    n = len(sentences)
    score = (n - len(unsupported)) / n
    return GroundednessResult(
        score=score,
        supported=score >= min_supported_ratio,
        unsupported_claims=unsupported,
        n_sentences=n,
    )
