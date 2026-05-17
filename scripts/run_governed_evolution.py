#!/usr/bin/env python
"""Governed self-evolution demo: a self-distilled playbook is NOT auto-applied.

It goes through a real release pipeline:
    Reflector proposes -> human approves -> canary -> REGRESSION GATE
    (eval before/after) -> activate (only if no metric regresses) -> rollback if needed.

This is the production answer to *misevolution*: every self-generated behavior
change is an auditable, gated, reversible asset -- never an opaque update.

Runs fully offline on the deterministic mock backend.
"""
from __future__ import annotations

import os
import sys
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config
from seagent.data import load_kb, load_queries, split_queries
from seagent.llm.factory import build_backend
from seagent.memory.semantic import SemanticMemory
from seagent.memory.episodic import EpisodicMemory, Case
from seagent.memory.procedural import ProceduralMemory, Playbook
from seagent.agent.support_agent import SupportAgent
from seagent.eval.verifier import verify, VerdictItem
from seagent.eval.metrics import aggregate, failed_groups
from seagent.governance import PlaybookRegistry, evaluate_playbook


def main():
    cfg = Config.load(os.path.join(os.path.dirname(__file__), "..", "configs", "default.yaml"))
    wd = os.path.join(os.path.dirname(__file__), "..", "experiments", "governance")
    os.makedirs(wd, exist_ok=True)
    backend = build_backend(cfg)
    kb = load_kb(cfg.kb_index)
    semantic = SemanticMemory(kb, cfg.score_norm_k)
    qs = split_queries(load_queries(cfg.queries))
    eval_q = sorted(qs["eval"], key=lambda q: q.id)

    # accumulated experience after some service
    episodic = EpisodicMemory(path=None, score_norm_k=cfg.score_norm_k)
    for q in qs["train"]:
        if q.difficulty == "hard":
            episodic.add(Case(case_id=q.id, query=q.query, resolution=q.resolution,
                              should_escalate=q.should_escalate, topic="general", source_query_id=q.id))

    proc = ProceduralMemory(path=None)
    reg = PlaybookRegistry(
        registry_path=os.path.join(wd, "playbook_registry.json"),
        audit_path=os.path.join(wd, "audit.jsonl"),
        procedural=proc,
    )

    def eval_with(proc_mem) -> List[VerdictItem]:
        agent = SupportAgent(cfg, backend, semantic, episodic, proc_mem)
        return [verify(q, agent.handle(q.query), cfg.coverage_threshold) for q in eval_q]

    # baseline metrics (no candidate playbook active)
    base_verdicts = eval_with(proc)
    base_failed = failed_groups(base_verdicts)
    baseline = aggregate(base_verdicts, base_failed)
    print(f"[gov] baseline resolution={baseline['resolution_rate']:.3f} "
          f"escalation_f1={baseline['escalation_f1']:.3f}")

    # two candidate playbooks: one helpful, one harmful (mislabels normal cases as escalate)
    good = Playbook(playbook_id="pb_billing_ans", topic="billing",
                    trigger_terms=["年付", "月付", "退款", "余额"],
                    guidance="年付转月付的差额以 account credit 形式保留，不退回原卡，下次续费自动抵扣。",
                    action="answer")
    # an over-cautious auto-generated rule: escalate every "how-to" question.
    # it fires broadly and tanks escalation precision -> the gate must catch it.
    bad = Playbook(playbook_id="pb_escalate_howto", topic="general",
                   trigger_terms=["怎么"],
                   guidance="遇到任何操作类（怎么…）问题一律转人工。", action="escalate")

    def eval_fn(candidate):
        cand_proc = ProceduralMemory(path=None)
        cand_proc.upsert(candidate)
        return eval_with(cand_proc)

    for cand in (good, bad):
        print(f"\n[gov] candidate {cand.playbook_id} (action={cand.action})")
        reg.propose(cand, proposer="reflector")
        reg.approve(cand.playbook_id, approver="ops-reviewer@nimbusflow")
        reg.promote_to_canary(cand.playbook_id)
        res = evaluate_playbook(cand, baseline, eval_fn, baseline_failed_groups=base_failed)
        print(f"      regression gate: passed={res.passed}  reason={res.reason}")
        if res.passed:
            reg.activate(cand.playbook_id, actor="release-bot")
            print(f"      -> ACTIVATED (written to ProceduralMemory, enabled)")
        else:
            reg.rollback(cand.playbook_id, actor="release-bot", reason=res.reason)
            print(f"      -> ROLLED BACK (blocked from production)")

    print(f"\n[gov] active playbooks in ProceduralMemory: {[p.playbook_id for p in proc.playbooks if p.enabled]}")
    print(f"[gov] registry -> {reg.registry_path}\n[gov] audit log -> {reg.audit_path}")


if __name__ == "__main__":
    main()
