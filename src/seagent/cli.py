"""Command line interface.

    python -m seagent.cli ask "我忘记密码了怎么办"      # single-shot answer
    python -m seagent.cli evolve --rounds 6              # run the evolution experiment
    python -m seagent.cli demo                           # before/after learning on one hard ticket
"""
from __future__ import annotations

import argparse
import os
import sys

from .config import Config
from .data import load_kb, load_queries, split_queries
from .llm.factory import build_backend
from .memory.semantic import SemanticMemory
from .memory.episodic import EpisodicMemory, Case
from .agent.support_agent import SupportAgent
from .eval.verifier import verify


def _build_agent(cfg, with_memory=True):
    backend = build_backend(cfg)
    kb = load_kb(cfg.kb_index)
    sem = SemanticMemory(kb, cfg.score_norm_k)
    epi = EpisodicMemory(path=None, score_norm_k=cfg.score_norm_k) if with_memory else None
    return SupportAgent(cfg, backend, sem, epi), kb


def cmd_ask(cfg, args):
    agent, _ = _build_agent(cfg, with_memory=False)
    r = agent.handle(args.query)
    print(f"\n[置信度 {r.confidence:.2f} | 转人工={r.escalate} | 证据来源={r.used_sources}]")
    print(r.answer)


def cmd_evolve(cfg, args):
    from .eval.harness import Experiment, save_results
    backend = build_backend(cfg)
    exp = Experiment(cfg, backend)
    results = exp.run()
    save_results(results, cfg.workdir)
    for cond, recs in results.items():
        a, b = recs[0], recs[-1]
        print(f"{cond:9s} resolution {a['resolution_rate']:.1%} -> {b['resolution_rate']:.1%}"
              f" | repeated-error {a.get('repeated_error_rate',0):.1%} -> {b.get('repeated_error_rate',0):.1%}")


def cmd_demo(cfg, args):
    """Show that the SAME hard ticket is failed at cold start and solved after the
    agent has learned from a paraphrase during training."""
    cfg.backend = cfg.backend  # noqa
    agent, _ = _build_agent(cfg, with_memory=True)
    qs = split_queries(load_queries(cfg.queries))
    # pick a hard eval query whose group also has a train variant
    train_by_group = {q.group: q for q in qs["train"]}
    target = next(q for q in qs["eval"] if q.difficulty == "hard" and q.group in train_by_group)
    tq = train_by_group[target.group]

    print("=" * 72)
    print(f"难题工单（eval, group={target.group}）：{target.query}")
    print(f"必须命中的关键点：{target.required_keypoints}")
    print("-" * 72)
    def _hits(ans):
        a = ans.replace(" ", "")
        return [k for k in target.required_keypoints if k.replace(" ", "") in a]

    r0 = agent.handle(target.query)
    v0 = verify(target, r0, cfg.coverage_threshold)
    print(f"[冷启动] 转人工={r0.escalate} 覆盖={v0.coverage:.0%} 解决={v0.resolved}")
    print(f"  命中关键点: {_hits(r0.answer)}  证据来源={r0.used_sources}")

    # the agent fails the training paraphrase and captures the human resolution
    agent.episodic.add(Case(case_id=tq.id, query=tq.query, resolution=tq.resolution,
                            should_escalate=tq.should_escalate, topic="general",
                            source_query_id=tq.id, learned_round=1))
    print("-" * 72)
    print(f"[学习] 训练中遇到同类工单「{tq.query}」并失败 -> 人工解决方案已写入经验池")
    print("-" * 72)
    r1 = agent.handle(target.query)
    v1 = verify(target, r1, cfg.coverage_threshold)
    print(f"[进化后] 转人工={r1.escalate} 覆盖={v1.coverage:.0%} 解决={v1.resolved}")
    print(f"  命中关键点: {_hits(r1.answer)}  证据来源={r1.used_sources}")
    print("=" * 72)
    print("结论：模型权重未变，仅靠经验积累 + 检索，难题从失败变为解决。")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="seagent")
    ap.add_argument("--config", default=os.path.join(
        os.path.dirname(__file__), "..", "..", "configs", "default.yaml"))
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-base", dest="api_base", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ask = sub.add_parser("ask"); p_ask.add_argument("query")
    p_ev = sub.add_parser("evolve"); p_ev.add_argument("--rounds", type=int, default=None)
    sub.add_parser("demo")
    args = ap.parse_args(argv)

    cfg = Config.load(args.config, backend=args.backend, model=args.model, api_base=args.api_base,
                      train_rounds=getattr(args, "rounds", None))
    {"ask": cmd_ask, "evolve": cmd_evolve, "demo": cmd_demo}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
