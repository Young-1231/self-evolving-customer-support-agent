# Expanded-KB stress test

## What this experiment answers

The prior 500-ticket pressure run (`experiments/stress_test/`) reported
**escalation_rate=85.2%** and **normal_easy resolution=23.2%** against the
hand-built 30-doc NimbusFlow KB. The headline finding was that the KB was the
suspected bottleneck. This experiment **tests that hypothesis directly** by
augmenting the KB with an open dataset and re-running the same load.

## Dataset

* **Source**: HuggingFace `bitext/Bitext-customer-support-llm-chatbot-training-dataset`
* **License**: CDLA-Sharing-1.0 (commercial-friendly, allows redistribution
  of derived works — required because we redistribute KB doc text inside
  this repository)
* **Size**: 27 intents × 11 categories ≈ 26,872 Q&A pairs (~18 MB CSV)
* **Why this one**: most-cited open customer-support corpus in 2024–2026
  academic / industrial RAG work; broad intent coverage; canonical
  responses are clean enough to drop into a KB unchanged.

## Ingestion

`src/seagent/datasets/bitext.py` implements:

1. `download_bitext(cache_dir)` — reentrant HF download via
   `huggingface_hub`. Honors the test-seam `downloader` for offline unit
   tests (so CI runs without the network).
2. `load_bitext_rows(csv_path)` — schema:
   `{flags, instruction, category, intent, response}`.
3. `bitext_to_kb_docs(rows, target_n=150)` — for each of the 27 intents
   pick ~5 diverse responses (length-filtered, Jaccard-deduped) → ~146
   variant docs; then emit one per-category "overview" doc (≤11) so
   category-level queries can still hit the KB. Final: 146 docs.

`scripts/expand_kb_from_bitext.py` runs the full pipeline and writes
`data/kb_expanded/{index.jsonl, kb_*.md, bx_*.md}` — totalling
**176 docs** (30 NimbusFlow + 146 Bitext).

The original `data/kb/index.jsonl` is **not** modified.

## Two controlled experiments

`scripts/run_stress_test_expanded.py` runs:

| run        | KB                              | Tickets                                    | Hypothesis |
|------------|---------------------------------|--------------------------------------------|------------|
| **Original** | NimbusFlow 30                  | 500 NimbusFlow (Chinese, SaaS)            | baseline (re-used from prior run) |
| **Exp A**    | NimbusFlow 30 + Bitext 150 = 176 | same 500 NimbusFlow                      | KB-only augmentation: should give a small lift if KB was the bottleneck |
| **Exp B**    | same 176                       | 500 mixed: 250 NimbusFlow + 250 generated e-commerce | KB + ticket distribution aligned: should give a larger lift |

The deliberate two-arm design separates *"more documents"* from *"the
right documents for the actual workload"*.

## Results

See `report_comparison.md` for the three-way table. Headline:

|                  | Original | Exp A   | Exp B   |
|------------------|---------:|--------:|--------:|
| escalation_rate  | 0.8524   | 0.8557  | 0.9196  |
| block_rate       | 0.0333   | 0.0268  | 0.1979  |
| normal_easy resolution | 0.232 | 0.244 | 0.1127 |
| avg_kb_hits      | —        | 3.942   | 3.984   |

### Interpretation

* **Exp A is essentially flat.** Adding 146 more KB docs raised
  `avg_kb_hits` to ≈ 3.94 (the top-k=4 ceiling — retrieval *is* finding the
  new docs) but resolution moved by 1.2 absolute points on `normal_easy`.
  **The KB was not the binding constraint.**
* **Exp B got worse, not better.** With 250 e-commerce English tickets
  added, block_rate jumped from 3.3% to 19.8%. The new English tickets
  contain realistic-format emails / phone numbers / PANs that fire the PII
  guardrail at a much higher rate than the synthetic Chinese NimbusFlow
  tickets did. And the LLM's confidence on stiff English Bitext-style
  templated answers does not clear `escalate_tau=0.5`, so even
  non-blocked tickets escalate.

### Honest takeaways (both are valuable findings)

1. **"More KB" alone is not the lever.** Future investment should go into
   critic calibration (`escalate_tau`) and PII guardrail precision, not
   into growing the KB further.
2. **Distribution mismatch in the guardrail is a real failure mode.** The
   PII regex was tuned against synthetic Chinese tickets and over-triggers
   on real English ones. This is exactly the kind of latent issue a
   diverse stress-test corpus is supposed to surface.

## Re-running

```bash
# 1) build the expanded KB (idempotent; uses HF cache)
PYTHONPATH=src python scripts/expand_kb_from_bitext.py

# 2) run the two experiments (DeepSeek backend)
PYTHONPATH=src python scripts/run_stress_test_expanded.py \
    --backend openai --concurrency 16

# Mock backend for offline smoke testing:
PYTHONPATH=src python scripts/run_stress_test_expanded.py \
    --backend mock --concurrency 4 --ecomm-n 5 --exp-b-total 10
```

## Costs (actual)

* Bitext download: free
* Exp A (500 agent calls): ~$0.13 wall
* Exp B (250 ticket gen + 500 agent calls): ~$0.20 wall
* **Total: ~$0.33**

## Files in this directory

```
README.md                  this file
report_comparison.md       three-way comparison (Original / Exp A / Exp B)
exp_a/
    load_summary.json      aggregate metrics
    load_records.jsonl     per-ticket records
    report.md              human-readable per-exp report
    traces/stress_trace.jsonl
exp_b/
    tickets.jsonl          the 500 mixed tickets actually run
    tickets_ecomm.jsonl    the 250 freshly-generated e-commerce tickets
    load_summary.json
    load_records.jsonl
    report.md
    traces/stress_trace.jsonl
```
