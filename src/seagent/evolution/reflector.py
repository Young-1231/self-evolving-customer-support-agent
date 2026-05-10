"""Offline reflection ("dreaming"): turn accumulated failures into playbooks.

Inspired by Anthropic's "dreaming" idle-time replay and by experience-lifecycle
work such as EvolveR (arXiv 2510.16079): periodically replay the stored failed
cases, cluster them, and distill reusable, auditable playbooks. Crucially this
runs *between* tasks, not during a live conversation, and every playbook it
emits is a versioned, human-toggleable artifact (see ProceduralMemory) rather
than an opaque weight change -- the guardrail against misevolution.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from ..memory.bm25 import tokenize
from ..memory.episodic import Case
from ..memory.procedural import Playbook, ProceduralMemory

_STOP = set("的了吗呢吧啊呀我你他她它们怎么如何为什么是不有个请问能可以这那要一下".strip())


def _trigger_terms(cases: List[Case], top: int = 8) -> List[str]:
    cnt: Counter = Counter()
    for c in cases:
        for t in set(tokenize(c.query)):
            if len(t) >= 2 and t not in _STOP:  # prefer bigrams/words, drop fillers
                cnt[t] += 1
    return [t for t, _ in cnt.most_common(top)]


def _guidance(cases: List[Case], max_chars: int = 700) -> str:
    seen = set()
    parts: List[str] = []
    for c in cases:
        r = c.resolution.strip()
        if r and r not in seen:
            seen.add(r)
            parts.append(r)
        if sum(len(p) for p in parts) >= max_chars:
            break
    return " ".join(parts)[:max_chars]


class Reflector:
    def __init__(self, min_cluster: int = 2):
        self.min_cluster = min_cluster

    def reflect(self, cases: List[Case], procedural: ProceduralMemory, round_idx: int) -> int:
        """(Re)induce playbooks from all failed cases seen so far.

        Clusters by (topic, escalate_decision) so the escalation policy is never
        averaged across mixed cases. Returns the number of playbooks upserted.
        """
        clusters: Dict[Tuple[str, bool], List[Case]] = defaultdict(list)
        for c in cases:
            clusters[(c.topic, bool(c.should_escalate))].append(c)

        n = 0
        for (topic, escalate), group in clusters.items():
            # escalation rules are valuable even from a single precedent;
            # "answer" playbooks need a real cluster to justify a generalization.
            min_needed = 1 if escalate else self.min_cluster
            if len(group) < min_needed:
                continue
            pid = f"pb_{topic}_{'esc' if escalate else 'ans'}"
            procedural.upsert(
                Playbook(
                    playbook_id=pid,
                    topic=topic,
                    trigger_terms=_trigger_terms(group),
                    guidance=_guidance(group),
                    action="escalate" if escalate else "answer",
                    source_case_ids=[c.case_id for c in group],
                    created_round=round_idx,
                )
            )
            n += 1
        return n
