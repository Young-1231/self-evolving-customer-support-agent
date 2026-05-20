"""Tests for the alternative (dense-style) retrievers used in the ablation."""
from __future__ import annotations

import pytest

from seagent.data import KBDoc
from seagent.llm.base import Passage
from seagent.memory.dense import (
    HybridRetriever,
    TfidfCosineRetriever,
    char_ngrams,
    sentence_transformers_available,
)


def _docs():
    return [
        KBDoc("kb1", "重置密码", "account",
              "如果忘记密码，请点击登录页的“忘记密码”链接，通过邮箱验证码重置。"),
        KBDoc("kb2", "导出账单", "billing",
              "进入账单页面，选择月份后点击导出，可下载 PDF 格式的发票。"),
        KBDoc("kb3", "two factor", "security",
              "Enable two factor authentication in security settings using an authenticator app."),
    ]


def test_char_ngrams_nonempty():
    grams = char_ngrams("reset password 重置密码")
    assert grams  # word tokens + char n-grams
    assert "reset" in grams  # word token preserved
    assert any(len(g) >= 2 for g in grams)


def test_tfidf_returns_passages_and_hits_relevant_doc():
    r = TfidfCosineRetriever(_docs())
    hits = r.retrieve("我忘记了登录密码怎么重置", top_k=2)
    assert hits and all(isinstance(p, Passage) for p in hits)
    assert all(p.source == "kb" for p in hits)
    assert hits[0].ref == "kb1"  # password-reset doc ranked first
    assert all(0.0 <= p.score <= 1.0 for p in hits)
    # scores are sorted descending
    assert hits == sorted(hits, key=lambda p: -p.score)


def test_tfidf_english_query_hits_english_doc():
    r = TfidfCosineRetriever(_docs())
    hits = r.retrieve("how do I turn on two factor authentication", top_k=1)
    assert hits and hits[0].ref == "kb3"


def test_hybrid_returns_passages_and_hits_relevant_doc():
    r = HybridRetriever(_docs())
    hits = r.retrieve("怎么导出我的发票账单", top_k=2)
    assert hits and all(isinstance(p, Passage) for p in hits)
    assert hits[0].ref == "kb2"
    assert all(p.score > 0.0 for p in hits)


def test_top_k_respected():
    r = TfidfCosineRetriever(_docs())
    assert len(r.retrieve("密码", top_k=1)) <= 1
    assert len(r.retrieve("账单导出 PDF", top_k=3)) <= 3


def test_empty_corpus_is_safe():
    r = TfidfCosineRetriever([])
    assert r.retrieve("anything", top_k=3) == []


def test_embedding_retriever_importorskip():
    if not sentence_transformers_available():
        pytest.skip("sentence-transformers not installed; skipping real-vector retriever")
    st = pytest.importorskip("sentence_transformers")  # noqa: F841
    from seagent.memory.dense import EmbeddingRetriever

    try:
        r = EmbeddingRetriever(_docs())
    except Exception as e:  # model not cached locally / offline
        pytest.skip(f"embedding model unavailable: {e}")
    hits = r.retrieve("forgot my login password", top_k=1)
    assert hits and isinstance(hits[0], Passage)
