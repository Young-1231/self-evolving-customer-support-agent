"""LLM-as-judge groundedness check (P1, 真瓶颈修复入口).

§4h 通过 Exp C 二次证伪，锁定真瓶颈是确定性 n-gram groundedness 在跨域 stiff
template 回答上 false-fail 严重。本模块用 LLM 单次 binary classification 升级：
给定 (answer, top-k context passages)，输出 {supported, confidence, missing_claims}。

设计要点（生产视角）：
  * 默认模型走 deepseek/deepseek-chat（便宜 + tool-calling 强 + 多轮安全）；
  * 支持 batch（多条 answer 一次 judge）降成本；
  * 调用失败不抛——降级到 fallback `confidence=0.5`（中性，不主导决策）；
  * 配合 `seagent.calibration.DomainCalibrator` 做 per-domain 阈值校准；
  * 与现有 `seagent.guardrails.groundedness.GroundednessResult` 保持 schema 兼容。

scaffold 状态：核心接口已实现，单测覆盖；Exp D 调用入口待补
(`scripts/run_stress_test_exp_d.py`)。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from .groundedness import GroundednessResult

JUDGE_SYSTEM_PROMPT = (
    "You are a strict grounding judge. Given an AGENT_ANSWER and a set of CONTEXT_PASSAGES,"
    " decide whether every factual claim in the answer is supported by the context."
    " Respond in compact JSON only, schema:"
    ' {"supported": bool, "confidence": float in [0,1], "missing_claims": [str]}.'
    " 'confidence' is your certainty that the answer is fully grounded."
    " 'missing_claims' lists short phrases of unsupported claims (empty if fully grounded)."
    " Do NOT include any text outside the JSON."
)

JUDGE_USER_TEMPLATE = (
    "AGENT_ANSWER:\n{answer}\n\n"
    "CONTEXT_PASSAGES:\n{contexts}\n\n"
    "Return the JSON verdict now."
)


def _format_contexts(contexts) -> str:
    """Render Passage-like objects into a numbered list for the judge prompt."""
    lines = []
    for i, p in enumerate(contexts or [], start=1):
        text = getattr(p, "text", str(p))
        src = getattr(p, "source", "ctx")
        ref = getattr(p, "ref", "")
        head = f"[{i}] {src}/{ref}" if ref else f"[{i}] {src}"
        # cap each passage to ~600 chars to bound prompt size
        text = text[:600] + ("…" if len(text) > 600 else "")
        lines.append(f"{head}: {text}")
    return "\n".join(lines) if lines else "(no context)"


def _parse_verdict(raw: str) -> tuple[bool, float, List[str]]:
    """Tolerant JSON extraction. Falls back to neutral verdict on parse fail."""
    if not raw:
        return False, 0.5, ["empty judge response"]
    # try direct json first
    try:
        d = json.loads(raw)
    except Exception:
        # try to extract first {...} block
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return False, 0.5, ["judge response not json"]
        try:
            d = json.loads(m.group(0))
        except Exception:
            return False, 0.5, ["judge response malformed"]
    supported = bool(d.get("supported", False))
    try:
        conf = float(d.get("confidence", 0.5))
        conf = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        conf = 0.5
    missing = d.get("missing_claims") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    return supported, conf, [str(x)[:200] for x in missing][:8]


@dataclass
class LLMJudgeGroundedness:
    """LLM-backed groundedness check.

    Parameters
    ----------
    model:
        Provider/model id, e.g. ``"deepseek-chat"`` for direct OpenAI-compatible
        DeepSeek endpoint, or ``"deepseek/deepseek-chat"`` for litellm prefix.
    api_base:
        OpenAI-compatible base URL. Defaults to DeepSeek's endpoint.
    api_key_env:
        Env var holding the key (e.g. ``DEEPSEEK_API_KEY``).
    confidence_threshold:
        Below this the answer is treated as not grounded (default 0.55).
    temperature:
        0.0 by default for deterministic judging.
    timeout_s:
        Per-call HTTP timeout.
    """

    model: str = "deepseek-chat"
    api_base: Optional[str] = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    confidence_threshold: float = 0.55
    temperature: float = 0.0
    timeout_s: int = 30
    # lazily-initialised client (kept as field=None so dataclass repr stays clean)
    _client: object = None

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except Exception as e:
            raise RuntimeError(
                "groundedness_llm 需要 openai SDK; pip install openai"
            ) from e
        api_key = os.environ.get(self.api_key_env) or ""
        kwargs = {"api_key": api_key} if api_key else {}
        if self.api_base:
            kwargs["base_url"] = self.api_base
        self._client = OpenAI(**kwargs)
        return self._client

    def check(self, answer: str, contexts) -> GroundednessResult:
        """Single-shot LLM groundedness judgement.

        Falls back to a neutral (supported=False, score=0.5) verdict on any
        exception so the agent's outer loop never crashes on a flaky judge.
        """
        if not (answer or "").strip():
            return GroundednessResult(score=0.0, supported=False,
                                      unsupported_claims=["empty answer"])
        try:
            client = self._ensure_client()
            resp = client.chat.completions.create(
                model=self.model.replace("deepseek/", ""),
                temperature=self.temperature,
                timeout=self.timeout_s,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": JUDGE_USER_TEMPLATE.format(
                        answer=answer[:2000],
                        contexts=_format_contexts(contexts),
                    )},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            # Soft-fail: emit neutral verdict, agent's outer escalation logic
            # will still decide via critic / policy signals.
            return GroundednessResult(
                score=0.5, supported=False,
                unsupported_claims=[f"judge_error:{type(e).__name__}"],
            )
        supported, conf, missing = _parse_verdict(raw)
        # confidence_threshold gates the binary "supported" flag the outer
        # pipeline reads; we always return the raw conf as the score.
        final_supported = supported and conf >= self.confidence_threshold
        return GroundednessResult(
            score=conf,
            supported=final_supported,
            unsupported_claims=missing,
        )

    # ---------------------------- batch ---------------------------------
    def check_batch(self, items) -> List[GroundednessResult]:
        """Naive batch (single calls in a loop). Override in future for true
        multi-answer batching with a custom prompt + structured output."""
        return [self.check(a, c) for a, c in items]
