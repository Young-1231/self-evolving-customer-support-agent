"""Offline tests for the tau2-bench memory integration.

Skipped automatically when tau2 is not installed (i.e. in the dependency-free
core test run). Run inside the tau2 venv to exercise them:
    PYTHONPATH=src .venv-tau2/bin/python -m pytest -q tests/test_tau2_ext.py
"""
import os

import pytest

pytest.importorskip("tau2", reason="tau2-bench not installed in this environment")

from seagent.tau2_ext.experience import PLAYBOOK_ENV, load_playbook, save_playbook
from seagent.tau2_ext import reflect


def test_playbook_round_trip(tmp_path):
    p = tmp_path / "pb.json"
    tips = ["Authenticate the user before any account change.", "Confirm refund amount first."]
    save_playbook(str(p), tips, meta={"domain": "retail"})
    assert load_playbook(str(p)) == tips
    assert load_playbook(None) == []


def test_memory_agent_injection(tmp_path, monkeypatch):
    from tau2.environment.tool import as_tool
    from seagent.tau2_ext.memory_agent import MemoryAugmentedLLMAgent, register

    register()  # idempotent registry insertion
    from tau2.registry import registry
    assert "memory_agent" in registry.get_agents()

    def noop():
        "noop tool"
        return "ok"

    p = tmp_path / "pb.json"
    save_playbook(str(p), ["Always verify identity first."])

    monkeypatch.setenv(PLAYBOOK_ENV, str(p))
    on = MemoryAugmentedLLMAgent(tools=[as_tool(noop)], domain_policy="POLICY", llm="x")
    assert "<learned_experience>" in on.system_prompt
    assert "verify identity" in on.system_prompt

    monkeypatch.delenv(PLAYBOOK_ENV, raising=False)
    off = MemoryAugmentedLLMAgent(tools=[as_tool(noop)], domain_policy="POLICY", llm="x")
    assert "<learned_experience>" not in off.system_prompt


def test_parse_tips_formats():
    raw = "1. First tip\n2) Second tip\n- Third tip\nnot a tip line"
    tips = reflect.parse_tips(raw, max_tips=8)
    assert tips == ["First tip", "Second tip", "Third tip"]
    assert reflect.parse_tips("", 8) == []
