"""A memory-augmented tau2-bench agent.

It subclasses the stock ``LLMAgent`` and injects a learned-experience playbook
into the system prompt. Everything else (tools, policy, tool-calling loop) is
unchanged, so any pass^k delta vs. ``llm_agent`` is attributable purely to the
injected experience -- a clean A/B of "does self-evolved memory help a real
agent on a real benchmark?".

The playbook file path is read from the SEAGENT_TAU2_PLAYBOOK env var, so the
same registered agent runs with memory ON (var points at a playbook) or OFF
(var unset / empty file).

APR-CS routing (2026-05)
------------------------
When ``SEAGENT_TAU2_ROUTE_MODE`` is set to a non-default value, the agent
re-writes the ``<learned_experience>`` block of its system prompt at the first
turn, keeping only the tips selected by :class:`PlaybookRouter`. Mode values:

* ``all``               (default) -- inject every tip, bit-exact legacy
  behaviour. The router is bypassed entirely.
* ``top_k_relevance``   -- keep the top-K tips by query overlap.
* ``cf_weighted``       -- top-K by ``relevance * max(0, Delta_i)``.
* ``conf_gated``        -- relevance ranking with an adaptive K driven by
  per-call confidence (Self-RAG style). Confidence is taken from
  ``SEAGENT_TAU2_CONFIDENCE`` (float) for offline experiments, or left ``None``
  in production (which degrades gracefully to ``top_k_relevance``).

``SEAGENT_TAU2_TOP_K`` controls K (default 4).
"""
from __future__ import annotations

import os
from typing import List, Optional

from tau2.agent.llm_agent import LLMAgent
from tau2.environment.tool import Tool

from ..evolution.router import (
    MODE_ALL,
    PlaybookRouter,
    RouterConfig,
    VALID_MODES,
)
from .experience import PLAYBOOK_ENV, load_playbook, load_playbook_with_scores

ROUTE_MODE_ENV = "SEAGENT_TAU2_ROUTE_MODE"
ROUTE_TOPK_ENV = "SEAGENT_TAU2_TOP_K"
ROUTE_CONF_ENV = "SEAGENT_TAU2_CONFIDENCE"

EXPERIENCE_BLOCK = (
    "<learned_experience>\n"
    "The following operating tips were distilled from past resolved/failed tickets "
    "in this domain. Treat them as high-priority guidance that complements the policy:\n"
    "{tips}\n"
    "</learned_experience>"
)


def _format_block(tips: List[str]) -> str:
    return EXPERIENCE_BLOCK.format(tips="\n".join(f"- {t}" for t in tips))


def _resolve_mode() -> str:
    mode = os.environ.get(ROUTE_MODE_ENV, MODE_ALL).strip() or MODE_ALL
    if mode not in VALID_MODES:
        # Be permissive: unknown modes degrade to legacy ``all`` so a typo in
        # the env var never silently breaks production behaviour.
        return MODE_ALL
    return mode


def _resolve_top_k() -> int:
    raw = os.environ.get(ROUTE_TOPK_ENV, "").strip()
    if not raw:
        return 4
    try:
        return max(0, int(raw))
    except ValueError:
        return 4


def _resolve_confidence() -> Optional[float]:
    raw = os.environ.get(ROUTE_CONF_ENV, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class MemoryAugmentedLLMAgent(LLMAgent):
    def __init__(self, tools: List[Tool], domain_policy: str, llm: str,
                 llm_args: Optional[dict] = None):
        super().__init__(tools=tools, domain_policy=domain_policy, llm=llm, llm_args=llm_args)
        # Always keep the legacy tip list available; load scores too so the
        # router can do counterfactual weighting when asked.
        path = os.environ.get(PLAYBOOK_ENV)
        self._tips: List[str] = load_playbook(path)
        _tips_with, self._scores = load_playbook_with_scores(path)
        # Defensive: keep _tips authoritative (legacy code path), but if the
        # scores-aware load saw something different (e.g. dedup) prefer it.
        if _tips_with and _tips_with != self._tips:
            self._tips = _tips_with
        self._route_mode = _resolve_mode()
        self._router: Optional[PlaybookRouter] = (
            PlaybookRouter(RouterConfig(k=_resolve_top_k()))
            if self._route_mode != MODE_ALL
            else None
        )
        self._routed_once = False

    # ---------------------------------------------------------- system prompt
    @property
    def system_prompt(self) -> str:
        # Legacy contract: always returns the full block when tips exist. The
        # router only narrows the *runtime* prompt (see generate_next_message);
        # the property remains stable so downstream code (and the existing
        # test_memory_agent_injection test) keeps passing.
        base = super().system_prompt
        if not self._tips:
            return base
        return base + "\n" + _format_block(self._tips)

    # ----------------------------------------------------- routed first-turn
    def generate_next_message(self, message, state):
        # When routing is disabled (default), fall through unchanged. This is
        # the regression-safety guarantee: SEAGENT_TAU2_ROUTE_MODE unset =>
        # bit-exact pre-APR-CS behaviour.
        if self._router is None or not self._tips or self._routed_once:
            return super().generate_next_message(message, state)

        # Only rewrite the system block once, on the first user-facing turn.
        # state.messages is the running history; "fresh" means it hasn't been
        # populated past the very first user turn yet.
        if len(state.messages) <= 1 and state.system_messages:
            query = _extract_query(message, state)
            selected = self._router.select(
                query=query,
                tips=list(self._tips),
                scores=self._scores or None,
                confidence=_resolve_confidence(),
                mode=self._route_mode,
            )
            new_block = _format_block(selected) if selected else ""
            sys_msg = state.system_messages[0]
            base_prompt = sys_msg.content
            # Strip any previously-injected block, then re-attach the routed
            # one (if any). The stripping is anchored on our exact marker so
            # we never touch unrelated text.
            stripped = _strip_experience_block(base_prompt)
            sys_msg.content = stripped if not new_block else stripped + "\n" + new_block
            self._routed_once = True

        return super().generate_next_message(message, state)


def _extract_query(message, state) -> str:
    """Best-effort query text for the router.

    Prefers the incoming user message; falls back to the most recent user
    message already on state; finally empty string (router degrades to
    relevance=0 for every tip and picks the first K by stable order).
    """
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    for m in reversed(getattr(state, "messages", []) or []):
        c = getattr(m, "content", None)
        if isinstance(c, str) and c.strip():
            return c
    return ""


def _strip_experience_block(prompt: str) -> str:
    """Remove a previously-appended <learned_experience>...</learned_experience>
    block (and the single leading newline we inserted), idempotently."""
    open_tag = "<learned_experience>"
    close_tag = "</learned_experience>"
    i = prompt.rfind(open_tag)
    if i < 0:
        return prompt
    j = prompt.find(close_tag, i)
    if j < 0:
        return prompt
    end = j + len(close_tag)
    # Trim the newline that join inserted before the block.
    start = i - 1 if i > 0 and prompt[i - 1] == "\n" else i
    return prompt[:start] + prompt[end:]


def create_memory_agent(tools, domain_policy, **kwargs):
    return MemoryAugmentedLLMAgent(
        tools=tools, domain_policy=domain_policy,
        llm=kwargs.get("llm"), llm_args=kwargs.get("llm_args"),
    )


def register() -> None:
    """Register the memory agent into tau2's global registry (idempotent)."""
    from tau2.registry import registry

    if "memory_agent" not in registry.get_agents():
        registry.register_agent_factory(create_memory_agent, "memory_agent")
