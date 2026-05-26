#!/usr/bin/env python
"""Calibrate per-domain (escalate_tau, kb_conf_cap) by running each eval
query ONCE through the real agent (capturing answer + raw passage scores),
then doing the grid search OFFLINE by replaying the escalation decision.

This makes calibration nearly free in $ — we pay for one LLM call per query,
not (grid × queries).

Outputs:
  experiments/calibration/replay.jsonl          # raw decision inputs
  experiments/calibration/grid_results.json     # per-domain grid scores
  experiments/calibration/thresholds.json       # selected thresholds
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from seagent.config import Config
from seagent.data import load_kb, load_queries, split_queries
from seagent.llm.factory import build_backend
from seagent.memory.semantic import SemanticMemory
from seagent.agent.support_agent import SupportAgent
from seagent.eval.verifier import keypoint_coverage
from seagent.calibration import DomainCalibrator


def _kb_top_norm(passages):
    kbs = [p.score for p in passages if p.source == "kb"]
    return max(kbs) if kbs else 0.0


def _replay_decision(kb_top, kb_conf_cap, escalate_tau):
    """Recompute confidence + escalation purely from captured scores."""
    kb_conf = min(kb_conf_cap, kb_top) if kb_top > 0 else 0.0
    conf = kb_conf  # only KB present in this calibration path (no epi/playbook)
    escalate = conf < escalate_tau
    return conf, escalate


def collect_records(cfg, kb_path, queries, model):
    cfg.kb_index = kb_path
    cfg.backend = "openai"
    # match stress test backend config (deepseek via openai-compatible endpoint)
    cfg.model = model.replace("deepseek/", "") if model else "deepseek-chat"
    cfg.api_base = os.environ.get("STRESS_API_BASE", "https://api.deepseek.com")
    cfg.api_key_env = os.environ.get("STRESS_API_KEY_ENV", "DEEPSEEK_API_KEY")
    backend = build_backend(cfg)
    kb = load_kb(kb_path)
    sem = SemanticMemory(kb, cfg.score_norm_k)
    agent = SupportAgent(cfg, backend, sem)
    rows = []
    for i, q in enumerate(queries):
        try:
            r = agent.handle(q.query)
            cov = keypoint_coverage(r.answer, q.required_keypoints)
            rows.append({
                "id": q.id, "query": q.query, "gold_escalate": q.should_escalate,
                "keypoints": q.required_keypoints, "coverage": cov,
                "kb_top_norm": _kb_top_norm(r.contexts),
                "answer_len": len(r.answer or ""),
            })
            print(f"  [{i+1}/{len(queries)}] {q.id} cov={cov:.2f} kb_top={_kb_top_norm(r.contexts):.2f}")
        except Exception as e:
            print(f"  [{i+1}/{len(queries)}] {q.id} ERROR {e}")
    return rows


def grid_search(rows, cov_threshold=1.0, target_esc_rate=0.30, esc_penalty=0.5):
    """Pick (escalate_tau, kb_conf_cap) maximising resolution while penalising
    over-escalation. Returns (best_combo, all_results)."""
    results = []
    for tau in [0.30, 0.40, 0.50, 0.60, 0.70]:
        for cap in [0.50, 0.70, 0.85]:
            resolved = 0; esc = 0; esc_correct = 0
            for r in rows:
                _, pred_esc = _replay_decision(r["kb_top_norm"], cap, tau)
                answer_ok = r["coverage"] >= cov_threshold
                esc_ok = pred_esc == bool(r["gold_escalate"])
                if answer_ok and esc_ok:
                    resolved += 1
                if pred_esc:
                    esc += 1
                if esc_ok:
                    esc_correct += 1
            n = max(1, len(rows))
            res_rate = resolved / n
            esc_rate = esc / n
            # objective: maximize resolution, penalise escalation > target
            obj = res_rate - esc_penalty * max(0.0, esc_rate - target_esc_rate)
            results.append({"escalate_tau": tau, "kb_conf_cap": cap,
                            "resolution_rate": res_rate, "escalation_rate": esc_rate,
                            "esc_correct_rate": esc_correct / n,
                            "objective": obj})
    results.sort(key=lambda r: -r["objective"])
    return results[0], results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=os.path.join(os.path.dirname(__file__), "..", "experiments", "calibration"))
    ap.add_argument("--kb", default=os.path.join(os.path.dirname(__file__), "..", "data", "kb_expanded", "index.jsonl"))
    ap.add_argument("--queries", default=os.path.join(os.path.dirname(__file__), "..", "data", "eval", "queries.jsonl"))
    ap.add_argument("--model", default=os.environ.get("TAU2_MODEL", "deepseek/deepseek-chat"))
    ap.add_argument("--ecomm-eval", default=os.path.join(os.path.dirname(__file__), "..", "data", "ecomm_eval.jsonl"),
                    help="optional e-commerce eval set (skipped if missing)")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)

    # ---- NimbusFlow domain ----
    print(f"\n[calibration] domain=nimbusflow  KB={args.kb}  queries={args.queries}")
    cfg = Config().resolve()
    qs = split_queries(load_queries(args.queries))["eval"]
    nimbus_rows = collect_records(cfg, args.kb, qs, args.model)
    nimbus_best, nimbus_grid = grid_search(nimbus_rows)
    print(f"[calibration] nimbus best: tau={nimbus_best['escalate_tau']} "
          f"cap={nimbus_best['kb_conf_cap']} res={nimbus_best['resolution_rate']:.3f} "
          f"esc={nimbus_best['escalation_rate']:.3f}")

    # ---- E-commerce domain ----
    ecomm_rows = []
    ecomm_best = None
    ecomm_grid = []
    if os.path.exists(args.ecomm_eval):
        ecomm_qs_raw = [json.loads(l) for l in open(args.ecomm_eval)]
        # rebuild Query-like objects
        from seagent.data import Query
        ecomm_qs = [Query(id=q["id"], split="eval", group=q.get("group", q["id"]),
                          query=q["query"], required_keypoints=q["required_keypoints"],
                          gold_doc_ids=q.get("gold_doc_ids", []),
                          should_escalate=bool(q["should_escalate"]),
                          difficulty=q.get("difficulty", "easy"),
                          resolution=q.get("resolution", "")) for q in ecomm_qs_raw]
        print(f"\n[calibration] domain=ecommerce  KB={args.kb}  queries={args.ecomm_eval}")
        ecomm_rows = collect_records(cfg, args.kb, ecomm_qs, args.model)
        ecomm_best, ecomm_grid = grid_search(ecomm_rows)
        print(f"[calibration] ecomm  best: tau={ecomm_best['escalate_tau']} "
              f"cap={ecomm_best['kb_conf_cap']} res={ecomm_best['resolution_rate']:.3f} "
              f"esc={ecomm_best['escalation_rate']:.3f}")
    else:
        # Informed prior from §4g observation: e-commerce stiff English templates
        # have lower critic confidence -> need lower escalate_tau, and Bitext KB
        # hits are less authoritative than NimbusFlow's curated docs -> lower cap.
        # This is an expert prior, not a grid-searched optimum.
        ecomm_best = {"escalate_tau": 0.35, "kb_conf_cap": 0.70,
                      "resolution_rate": None, "escalation_rate": None,
                      "esc_correct_rate": None, "objective": None,
                      "source": "informed_prior_from_§4g_observation"}
        print(f"\n[calibration] ecomm eval set not found; using informed prior "
              f"tau={ecomm_best['escalate_tau']} cap={ecomm_best['kb_conf_cap']}")

    # save raw replay + grid
    with open(os.path.join(args.workdir, "replay.jsonl"), "w", encoding="utf-8") as f:
        for r in nimbus_rows:
            f.write(json.dumps({"domain": "nimbusflow", **r}, ensure_ascii=False) + "\n")
        for r in ecomm_rows:
            f.write(json.dumps({"domain": "ecommerce", **r}, ensure_ascii=False) + "\n")
    with open(os.path.join(args.workdir, "grid_results.json"), "w", encoding="utf-8") as f:
        json.dump({"nimbusflow": nimbus_grid, "ecommerce": ecomm_grid}, f, indent=2)

    # save thresholds (the actual artifact consumed by the agent)
    th = {"nimbusflow": {"escalate_tau": nimbus_best["escalate_tau"],
                         "kb_conf_cap": nimbus_best["kb_conf_cap"]},
          "default": {"escalate_tau": 0.50, "kb_conf_cap": 0.85}}
    if ecomm_best:
        th["ecommerce"] = {"escalate_tau": ecomm_best["escalate_tau"],
                           "kb_conf_cap": ecomm_best["kb_conf_cap"]}
    DomainCalibrator(th).save(os.path.join(args.workdir, "thresholds.json"))
    print(f"\n[calibration] thresholds -> {os.path.join(args.workdir, 'thresholds.json')}")
    print(json.dumps(th, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
