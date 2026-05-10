"""OpenAI-compatible backend (works with the OpenAI API and local vLLM).

Selected via ``backend: openai`` in the config. Point ``api_base`` at a vLLM
server (e.g. http://localhost:8000/v1) to run fully on-prem. This path is not
exercised by the offline test suite, but it shares the exact agent/evolution
logic with the mock backend.
"""
from __future__ import annotations

import os
import re
from typing import List

from .base import LLMBackend, Passage

_SYS = (
    "你是 NimbusFlow 的资深客服助手。只依据下面提供的「参考资料」回答用户问题，"
    "不要编造资料中没有的信息。回答简洁、准确、可执行。"
)


def _format_contexts(contexts: List[Passage]) -> str:
    lines = []
    for i, p in enumerate(sorted(contexts, key=lambda x: -x.score), 1):
        tag = {"kb": "知识库", "episodic": "历史相似案例", "playbook": "运营手册规则"}.get(p.source, p.source)
        lines.append(f"[{i}] ({tag}) {p.text.strip()}")
    return "\n".join(lines) if lines else "（无相关资料）"


class OpenAIBackend(LLMBackend):
    name = "openai"

    def __init__(self, model: str, api_base: str | None, api_key_env: str, temperature: float = 0.0):
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("backend=openai 需要安装 openai SDK: pip install openai") from e
        self.model = model
        self.temperature = temperature
        kwargs = {}
        if api_base:
            kwargs["base_url"] = api_base
        api_key = os.environ.get(api_key_env, "EMPTY")  # vLLM accepts any key
        self._client = OpenAI(api_key=api_key, **kwargs)

    def _chat(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""

    def generate_answer(self, query: str, contexts: List[Passage]) -> str:
        user = f"参考资料：\n{_format_contexts(contexts)}\n\n用户问题：{query}\n\n请给出客服回答："
        return self._chat(_SYS, user).strip()

    def judge_confidence(self, query: str, answer: str, contexts: List[Passage]) -> float:
        prompt = (
            f"用户问题：{query}\n回答：{answer}\n\n"
            "请评估该回答是否完整、准确地解决了用户问题，只输出一个 0 到 1 之间的小数（1=非常有把握）。"
        )
        out = self._chat("你是严格的质量评审，只输出一个数字。", prompt)
        m = re.search(r"(\d*\.?\d+)", out)
        if not m:
            return -1.0
        try:
            return max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            return -1.0
