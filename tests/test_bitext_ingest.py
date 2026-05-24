"""Offline tests for the Bitext KB adapter.

Run:
    PYTHONPATH=src python -m pytest -q tests/test_bitext_ingest.py

Network is **never** touched: ``download_bitext`` takes a ``downloader``
seam, and the fixture CSV under tests/fixtures is what powers
``bitext_to_kb_docs`` end-to-end.
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict, List

import pytest

from seagent.datasets.bitext import (
    BITEXT_FILENAME,
    BITEXT_LICENSE,
    BITEXT_REPO,
    bitext_to_kb_docs,
    download_bitext,
    load_bitext_rows,
    stable_hash_docs,
)


# -- fixture ----------------------------------------------------------------
# Mirrors the real Bitext schema so the adapter is exercised faithfully.
# Layout: 4 intents across 3 categories, 5 variants each, varying length.
def _fixture_rows() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    intents = [
        ("ORDER",   "cancel_order"),
        ("ORDER",   "change_order"),
        ("REFUND",  "get_refund"),
        ("ACCOUNT", "edit_account"),
    ]
    for cat, intent in intents:
        for v in range(8):
            response = (
                f"To {intent.replace('_', ' ')} please follow the steps below. "
                f"Step 1: open the dashboard. Step 2: locate the order id "
                f"and confirm. Step 3: wait for the confirmation email. "
                f"Variant text #{v} pads the answer with intent-specific detail "
                f"so dedup and diversity filters get a workout: "
                f"{intent}-{v}-{cat}." * 2
            )
            rows.append({
                "flags":       "B",
                "instruction": f"i want to {intent.replace('_', ' ')} {v}",
                "category":    cat,
                "intent":      intent,
                "response":    response,
            })
    return rows


def _write_fixture_csv(path: str) -> None:
    rows = _fixture_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["flags", "instruction", "category", "intent", "response"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


@pytest.fixture
def fixture_csv(tmp_path) -> str:
    p = str(tmp_path / "bitext_fixture.csv")
    _write_fixture_csv(p)
    return p


# -- download_bitext --------------------------------------------------------
def test_download_bitext_uses_downloader_seam(tmp_path):
    """``downloader`` arg fully replaces hf_hub_download — no network."""
    calls: List[Dict[str, Any]] = []

    def fake_download(**kwargs) -> str:
        calls.append(dict(kwargs))
        p = os.path.join(kwargs["cache_dir"], "fake.csv")
        with open(p, "w") as f:
            f.write("flags,instruction,category,intent,response\n")
        return p

    out = download_bitext(str(tmp_path), downloader=fake_download)
    assert os.path.exists(out)
    assert len(calls) == 1
    assert calls[0]["repo_id"] == BITEXT_REPO
    assert calls[0]["filename"] == BITEXT_FILENAME
    assert calls[0]["repo_type"] == "dataset"


def test_download_bitext_reentrant(tmp_path):
    """Repeated calls don't blow up — fake downloader just returns the same path."""
    call_count = {"n": 0}
    target = os.path.join(str(tmp_path), "fake.csv")
    with open(target, "w") as f:
        f.write("flags,instruction,category,intent,response\n")

    def fake_download(**kwargs) -> str:
        call_count["n"] += 1
        return target

    a = download_bitext(str(tmp_path), downloader=fake_download)
    b = download_bitext(str(tmp_path), downloader=fake_download)
    assert a == b == target
    # Caller can decide to cache outside; we just assert the seam wires through.
    assert call_count["n"] == 2


def test_license_string_is_cdla_sharing():
    """Project depends on CDLA-Sharing-1.0 for commercial redistribution."""
    assert BITEXT_LICENSE == "CDLA-Sharing-1.0"


# -- load_bitext_rows -------------------------------------------------------
def test_load_bitext_rows_schema(fixture_csv):
    rows = load_bitext_rows(fixture_csv)
    assert rows, "fixture must produce rows"
    keys = set(rows[0].keys())
    assert keys == {"flags", "instruction", "category", "intent", "response"}
    # ALL rows must populate intent/category/response — otherwise the adapter
    # downstream silently drops them.
    for r in rows:
        assert r["intent"]
        assert r["category"]
        assert r["response"]


# -- bitext_to_kb_docs ------------------------------------------------------
def test_bitext_to_kb_docs_schema_and_uniqueness(fixture_csv):
    rows = load_bitext_rows(fixture_csv)
    docs = bitext_to_kb_docs(rows, target_n=40, min_len=80, max_len=4000)
    assert docs, "must yield at least one doc"
    # schema legality
    for d in docs:
        rec = d.to_record()
        assert set(rec.keys()) == {"doc_id", "title", "topic", "text"}
        assert rec["doc_id"]
        assert rec["title"]
        assert rec["topic"]
        assert len(rec["text"]) >= 50
    # doc_id uniqueness
    ids = [d.doc_id for d in docs]
    assert len(ids) == len(set(ids)), "doc_id must be unique"
    # bx_ prefix convention
    assert all(d.doc_id.startswith("bx_") for d in docs)


def test_bitext_to_kb_docs_is_deterministic(fixture_csv):
    rows = load_bitext_rows(fixture_csv)
    a = bitext_to_kb_docs(rows, target_n=40)
    b = bitext_to_kb_docs(rows, target_n=40)
    assert [d.to_record() for d in a] == [d.to_record() for d in b]
    assert stable_hash_docs(a) == stable_hash_docs(b)


def test_bitext_to_kb_docs_includes_overview(fixture_csv):
    rows = load_bitext_rows(fixture_csv)
    docs = bitext_to_kb_docs(rows, target_n=40)
    overview_ids = [d.doc_id for d in docs if "overview" in d.doc_id]
    assert overview_ids, "expected at least one per-category overview doc"


def test_bitext_to_kb_docs_per_intent_cap(fixture_csv):
    """target_n must roughly translate to a sensible per_intent allocation."""
    rows = load_bitext_rows(fixture_csv)
    # 4 intents in fixture, target_n=32 → per_intent ≈ 7 → cap body docs/intent
    docs = bitext_to_kb_docs(rows, target_n=32)
    body_docs = [d for d in docs if "overview" not in d.doc_id]
    from collections import Counter
    per_intent_counts = Counter(d.doc_id.rsplit("_", 1)[0] for d in body_docs)
    # nothing should exceed the budget (per_intent <= target_n // n_intents)
    for prefix, n in per_intent_counts.items():
        assert n <= 6, f"prefix {prefix} got {n} docs, expected <=6"


def test_bitext_to_kb_docs_rejects_empty():
    with pytest.raises(ValueError):
        bitext_to_kb_docs([], target_n=50)


def test_bitext_to_kb_docs_rejects_tiny_target(fixture_csv):
    rows = load_bitext_rows(fixture_csv)
    with pytest.raises(ValueError):
        bitext_to_kb_docs(rows, target_n=10)
