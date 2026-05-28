"""IntentRouter — LLM-driven multi-intent splitter.

Given a raw customer query, returns a list of ``SubIntent`` records.  Single-
intent queries return a length-1 list (we never force a split).  Multi-intent
queries return one SubIntent per detected ask, each with a rewritten focused
sub_query suitable for direct specialist dispatch.

Design notes
------------
* **One LLM call**: the router asks the model to emit JSON
  ``{intents: [{label, sub_query, confidence}, ...]}``.  We tolerate code
  fences and stray prose by extracting the first JSON object via brace
  matching.
* **Caching**: a process-local LRU keyed on the verbatim query string.  Two
  identical workers will only call the LLM once for a duplicate ticket.
* **Conservative fallback**: any parse failure / API error -> a single
  fallback intent with label='general' and the original query.  This means
  router errors degrade the system to ``SpecialistAgent('general')``, never
  block the request.
* **Label set is configurable**.  The router *prompts* the LLM with the
  allowed labels; if the LLM emits something else we map to 'general'.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_LABELS = ("refund", "billing", "account", "technical", "general", "unknown")

# Keep cache bounded; multi_intent queries can be long, don't want unbounded growth.
_CACHE_MAX = 1024


@dataclass
class SubIntent:
    """One detected intent inside a (possibly multi-intent) ticket."""

    label: str               # one of the configured intent labels
    sub_query: str           # rewritten focused query for downstream specialist
    confidence: float = 0.0  # router's self-reported confidence in [0,1]

    def to_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "sub_query": self.sub_query, "confidence": self.confidence}


def _build_router_prompt(query: str, labels: Sequence[str]) -> str:
    label_list = ", ".join(labels)
    return (
        "你是客服工单的意图分析器。请把用户工单拆解为一个或多个独立子意图。\n"
        f"允许的 label：{label_list}\n"
        "规则：\n"
        "  - 单意图工单只输出 1 个子意图（不要硬拆）。\n"
        "  - 多意图工单（一条工单含 2~3 个并列问题）按问题顺序输出。\n"
        "  - sub_query 必须重写为单一聚焦问题，保留必要上下文。\n"
        "  - confidence 取 0~1 之间小数，表示你对该 label 的把握。\n"
        "  - 严格只输出一个 JSON 对象，禁止额外文字、markdown 代码块或注释。\n"
        '  - 输出格式：{"intents":[{"label":"<label>","sub_query":"...","confidence":0.x}, ...]}\n\n'
        f"用户工单：\n{query}\n\nJSON："
    )


_BRACE_RE = re.compile(r"\{")


def _extract_json_obj(text: str) -> Optional[str]:
    """Find the first balanced ``{...}`` block in ``text``.

    Handles models that wrap output in ```json ... ``` or add trailing prose.
    Returns ``None`` if no balanced object can be found.
    """
    if not text:
        return None
    for m in _BRACE_RE.finditer(text):
        depth = 0
        in_str = False
        esc = False
        for i in range(m.start(), len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[m.start(): i + 1]
        # if we got here this open-brace was unbalanced, try the next one
    return None


class IntentRouter:
    """Route a query to one or more SubIntents via a structured LLM call.

    Parameters
    ----------
    backend:
        Anything exposing a ``_chat(system, user) -> str`` method (the
        OpenAIBackend does).  When ``None`` the router operates in a
        *no-llm* mode that always returns a single ``general`` intent,
        which is what offline tests can rely on.
    labels:
        Allowed intent labels; default ``DEFAULT_LABELS``.
    cache:
        Disable the in-memory cache by passing ``False`` (tests rely on
        this to count LLM calls).
    """

    def __init__(
        self,
        backend: Any = None,
        labels: Sequence[str] = DEFAULT_LABELS,
        cache: bool = True,
    ) -> None:
        self.backend = backend
        self.labels = tuple(labels)
        self._cache_enabled = bool(cache)
        self._cache: Dict[str, List[SubIntent]] = {}
        self._cache_lock = threading.Lock()
        # observability counters (read by tests / orchestrator stats)
        self.n_calls = 0
        self.n_cache_hits = 0
        self.n_parse_fail = 0

    # ---- public ----
    def route(self, query: str) -> List[SubIntent]:
        if not query or not query.strip():
            return [SubIntent(label="general", sub_query=query or "", confidence=0.0)]

        if self._cache_enabled:
            with self._cache_lock:
                hit = self._cache.get(query)
            if hit is not None:
                self.n_cache_hits += 1
                # shallow copy so caller mutations don't poison the cache
                return [SubIntent(**si.to_dict()) for si in hit]

        intents = self._route_impl(query)
        if self._cache_enabled and intents:
            with self._cache_lock:
                if len(self._cache) >= _CACHE_MAX:
                    # cheap FIFO eviction
                    self._cache.pop(next(iter(self._cache)))
                self._cache[query] = [SubIntent(**si.to_dict()) for si in intents]
        return intents

    # ---- internals ----
    def _route_impl(self, query: str) -> List[SubIntent]:
        if self.backend is None:
            return [SubIntent(label="general", sub_query=query, confidence=0.5)]

        prompt = _build_router_prompt(query, self.labels)
        sys_msg = "你是严格的 JSON 输出器，禁止任何额外文本。"
        try:
            self.n_calls += 1
            raw = self.backend._chat(sys_msg, prompt)  # type: ignore[attr-defined]
        except Exception:
            self.n_parse_fail += 1
            return [SubIntent(label="general", sub_query=query, confidence=0.0)]

        parsed = self._parse(raw, query)
        if not parsed:
            self.n_parse_fail += 1
            return [SubIntent(label="general", sub_query=query, confidence=0.0)]
        return parsed

    def _parse(self, raw: str, original_query: str) -> List[SubIntent]:
        if not raw:
            return []
        blob = _extract_json_obj(raw) or raw
        try:
            data = json.loads(blob)
        except Exception:
            return []
        intents_raw = data.get("intents") if isinstance(data, dict) else None
        if not isinstance(intents_raw, list) or not intents_raw:
            return []
        out: List[SubIntent] = []
        for item in intents_raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "general")).strip().lower()
            if label not in self.labels:
                label = "general"
            sub_q = str(item.get("sub_query", "")).strip()
            if not sub_q:
                sub_q = original_query
            try:
                conf = float(item.get("confidence", 0.0))
            except Exception:
                conf = 0.0
            conf = max(0.0, min(1.0, conf))
            out.append(SubIntent(label=label, sub_query=sub_q, confidence=conf))
        return out
