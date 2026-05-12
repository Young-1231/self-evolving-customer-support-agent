"""Offline reflection for tau2-bench (the "dreaming" step on a real benchmark).

Given the baseline agent's FAILED training simulations, distill a small set of
general, transferable operating tips (a domain playbook). One consolidated LLM
call keeps cost low. The output is auditable text, not a weight update.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def _truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + " …"


def render_trajectory(messages: List[Any], max_chars: int = 1800) -> str:
    lines: List[str] = []
    for m in messages or []:
        role = getattr(m, "role", "?")
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for tc in tcs:
                lines.append(f"[agent->tool] {getattr(tc,'name','?')}({getattr(tc,'arguments',{})})")
        else:
            content = getattr(m, "content", None)
            if content:
                who = {"assistant": "agent", "user": "user", "tool": "tool"}.get(role, role)
                lines.append(f"[{who}] {_truncate(str(content), 220)}")
    return _truncate("\n".join(lines), max_chars)


def task_goal(task: Any) -> str:
    us = getattr(task, "user_scenario", None)
    instr = getattr(us, "instructions", None) if us else None
    for attr in ("task_instructions", "reason"):
        v = getattr(instr, attr, None)
        if v:
            return _truncate(str(v), 300)
    return _truncate(str(getattr(task, "description", "") or getattr(task, "purpose", "")), 300)


def expected_actions(task: Any) -> List[str]:
    ec = getattr(task, "evaluation_criteria", None)
    acts = getattr(ec, "actions", None) if ec else None
    return [getattr(a, "name", "?") for a in (acts or [])]


def build_reflection_prompt(domain_policy: str, cases: List[Dict], max_tips: int) -> str:
    blocks = []
    for i, c in enumerate(cases, 1):
        blocks.append(
            f"### Failed case {i} (task {c['task_id']}, reward={c['reward']})\n"
            f"User goal: {c['goal']}\n"
            f"Expected actions: {c['expected_actions']}\n"
            f"Agent trajectory:\n{c['trajectory']}"
        )
    cases_text = "\n\n".join(blocks)
    return (
        "You are reviewing a customer-service agent that FAILED the tasks below. "
        "The agent follows a fixed policy (excerpt) and calls tools.\n\n"
        f"=== POLICY (excerpt) ===\n{_truncate(domain_policy, 2500)}\n\n"
        f"=== FAILED CASES ===\n{cases_text}\n\n"
        f"Write at most {max_tips} concise, GENERAL operating tips that would help the agent "
        "avoid these failures on NEW, unseen tickets in this domain. Each tip must be: "
        "transferable (not task-specific IDs/values), actionable, and consistent with the policy. "
        "Focus on recurring mistakes (skipped verification, wrong tool order, policy violations, "
        "premature stopping, missing confirmations). "
        "Output ONLY a numbered list, one tip per line, no preamble."
    )


def parse_tips(text: str, max_tips: int) -> List[str]:
    tips = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = re.match(r"^(?:\d+[.)]|[-*])\s+(.*)$", line)
        if m and m.group(1).strip():
            tips.append(m.group(1).strip())
    return tips[:max_tips]


def distill_playbook(domain_policy: str, cases: List[Dict], model: str,
                     max_tips: int = 8, max_cases: int = 12) -> Dict[str, Any]:
    import litellm

    cases = cases[:max_cases]
    if not cases:
        return {"tips": [], "raw": "", "n_cases": 0}
    prompt = build_reflection_prompt(domain_policy, cases, max_tips)
    resp = litellm.completion(
        model=model, temperature=0.0, timeout=120,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.choices[0].message.content or ""
    return {"tips": parse_tips(raw, max_tips), "raw": raw, "n_cases": len(cases)}
