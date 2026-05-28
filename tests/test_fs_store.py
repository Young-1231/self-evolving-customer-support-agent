"""Tests for FsEpisodicStore (v2.5 R3, OpenViking-style L0/L1/L2)."""
from __future__ import annotations

import os
import time

import pytest

from seagent.memory.episodic import Case, EpisodicMemory
from seagent.memory.fs_store import (
    FsEpisodicStore,
    parse_markdown_case,
    render_markdown_case,
)


# ---------------------------------------------------------------------------
# round-trip
# ---------------------------------------------------------------------------

def _c(case_id, query, resolution, topic="account_security", esc=False, round_=1):
    return Case(
        case_id=case_id, query=query, resolution=resolution,
        should_escalate=esc, topic=topic,
        source_query_id=case_id, learned_round=round_,
    )


def test_markdown_roundtrip_preserves_case_fields():
    c = _c("c1", "我忘记密码了怎么重置", "点击忘记密码，链接15分钟内有效", esc=False)
    text = render_markdown_case(c)
    assert text.startswith("---\n")
    fm, body = parse_markdown_case(text)
    assert fm["case_id"] == "c1"
    assert fm["topic"] == "account_security"
    assert fm["should_escalate"] is False
    assert "忘记密码" in body
    assert "15分钟" in body


def test_add_save_load_roundtrip(tmp_path):
    root = str(tmp_path / "ep")
    store = FsEpisodicStore(root_dir=root, scheme="topic_date")
    store.add(_c("c1", "我忘记密码了怎么重置", "点击忘记密码，链接15分钟内有效",
                 topic="account_security"),
              metadata={"created_at": "2026-05-12"})
    store.add(_c("c2", "怎么开启两步验证", "进入设置>安全，打开两步验证",
                 topic="account_security"),
              metadata={"created_at": "2026-05-15"})
    # files exist in L0_<topic>/L1_<YYYY-MM>/L2_<id>.md
    expected = os.path.join(root, "L0_account_security", "L1_2026-05", "L2_c1.md")
    assert os.path.exists(expected)
    # reload from disk in a fresh store and check we can retrieve
    store2 = FsEpisodicStore(root_dir=root, scheme="topic_date")
    assert len(store2) == 2
    ps = store2.retrieve("密码记不住了怎么重新设置", top_k=1)
    assert ps and "15分钟" in ps[0].text


# ---------------------------------------------------------------------------
# schemes
# ---------------------------------------------------------------------------

def test_scheme_topic_date_uses_yyyymm_bucket(tmp_path):
    s = FsEpisodicStore(root_dir=str(tmp_path / "td"), scheme="topic_date")
    s.add(_c("c1", "q", "r", topic="billing"), metadata={"created_at": "2026-05-12"})
    assert s.bucket_of("c1") == ("billing", "2026-05")


def test_scheme_topic_date_falls_back_to_round(tmp_path):
    s = FsEpisodicStore(root_dir=str(tmp_path / "tdr"), scheme="topic_date")
    s.add(_c("c1", "q", "r", topic="billing", round_=3))
    assert s.bucket_of("c1") == ("billing", "round_003")


def test_scheme_topic_subtopic(tmp_path):
    s = FsEpisodicStore(root_dir=str(tmp_path / "ts"), scheme="topic_subtopic")
    s.add(_c("c1", "q", "r", topic="billing"), metadata={"subtopic": "refund"})
    assert s.bucket_of("c1") == ("billing", "refund")


def test_scheme_flat(tmp_path):
    s = FsEpisodicStore(root_dir=str(tmp_path / "fl"), scheme="flat")
    s.add(_c("c1", "q", "r", topic="billing"))
    s.add(_c("c2", "q2", "r2", topic="account_security"))
    assert s.bucket_of("c1") == ("all", "all")
    assert s.bucket_of("c2") == ("all", "all")


def test_invalid_scheme_raises():
    with pytest.raises(ValueError):
        FsEpisodicStore(root_dir=None, scheme="bogus")


# ---------------------------------------------------------------------------
# behavioural parity with EpisodicMemory
# ---------------------------------------------------------------------------

CORPUS = [
    _c("c1", "我忘记密码了怎么重置", "点击忘记密码，15分钟内有效",
       topic="account_security"),
    _c("c2", "怎么开启两步验证", "设置>安全，开启两步验证",
       topic="account_security"),
    _c("c3", "在哪里下载发票", "设置>账单>发票历史下载PDF",
       topic="billing"),
    _c("c4", "退款多久到账", "退款3-5个工作日到账",
       topic="billing"),
    _c("c5", "导出我的全部数据", "设置>数据导出选择全部",
       topic="data_export"),
]


def _ids(passages):
    return [p.ref for p in passages]


def test_flat_scheme_matches_legacy_episodic_topk(tmp_path):
    legacy = EpisodicMemory(path=None)
    fs = FsEpisodicStore(root_dir=str(tmp_path / "flat"), scheme="flat")
    for c in CORPUS:
        legacy.add(c)
        fs.add(c)
    queries = [
        "密码忘了怎么重置",
        "两步验证怎么打开",
        "发票在哪里下载",
        "退款几天到账",
        "怎样把我的数据全部导出",
    ]
    for q in queries:
        a = _ids(legacy.retrieve(q, top_k=3))
        b = _ids(fs.retrieve(q, top_k=3))
        assert a and b, f"empty retrieval for {q!r}: legacy={a} fs={b}"
        # First hit must match (the strongest signal).  Allow tail to differ
        # — both sides should agree on the strongest, which is what callers
        # actually use.
        assert a[0] == b[0], f"top-1 disagrees on {q!r}: legacy={a} fs={b}"
        # tail overlap should be high
        common = set(a) & set(b)
        assert len(common) / max(len(set(a) | set(b)), 1) >= 0.5, (
            f"tail overlap too low on {q!r}: legacy={a} fs={b}"
        )


def test_topic_date_scheme_does_not_regress_topk(tmp_path):
    legacy = EpisodicMemory(path=None)
    fs = FsEpisodicStore(root_dir=str(tmp_path / "td"),
                         scheme="topic_date", l0_top=3)
    for c in CORPUS:
        legacy.add(c)
        # spread cases across months so the L1 bucket is non-trivial
        month = f"2026-0{(int(c.case_id[-1]) % 3) + 1}"
        fs.add(c, metadata={"created_at": f"{month}-15"})
    queries = [
        ("密码忘了怎么重置",      "c1"),
        ("两步验证怎么打开",       "c2"),
        ("发票在哪里下载",         "c3"),
        ("退款几天到账",           "c4"),
        ("怎样把我的数据全部导出", "c5"),
    ]
    for q, expected_top in queries:
        a = _ids(legacy.retrieve(q, top_k=3))
        b = _ids(fs.retrieve(q, top_k=3))
        assert a and b, f"empty: {q!r} legacy={a} fs={b}"
        # FS store should still surface the expected case on top because L0
        # filter picks the right topic.
        assert b[0] == expected_top, (
            f"fs top-1 wrong for {q!r}: got {b}, expected {expected_top}"
        )


def test_l0_filter_narrows_search_space(tmp_path):
    fs = FsEpisodicStore(root_dir=str(tmp_path / "ns"),
                         scheme="topic_date", l0_top=1)
    for c in CORPUS:
        fs.add(c, metadata={"created_at": "2026-05-01"})
    # querying with a strong topical hint ("billing"-flavoured CJK) should
    # only score cases in the billing L0.
    q = "billing 发票 退款"
    selected = fs._select_l0(q, ["billing"])
    assert "billing" in selected
    # and only one (l0_top=1)
    assert len(selected) == 1


# ---------------------------------------------------------------------------
# scale / performance
# ---------------------------------------------------------------------------

def test_retrieve_under_50ms_at_1k_cases(tmp_path):
    fs = FsEpisodicStore(root_dir=None, scheme="topic_date", l0_top=2)
    topics = ["billing", "account_security", "mobile_app",
              "troubleshooting", "data_export"]
    for i in range(1000):
        topic = topics[i % len(topics)]
        fs.add(
            _c(f"c{i:04d}",
               f"用户问题 {topic} 编号 {i}",
               f"解决方案 {topic} 步骤 {i % 7}",
               topic=topic, round_=i // 100),
            metadata={"created_at": f"2026-0{(i % 9) + 1}-15"},
        )
    assert len(fs) == 1000
    t0 = time.perf_counter()
    for _ in range(20):
        ps = fs.retrieve("用户 billing 编号 42", top_k=3)
        assert ps, "should retrieve something at 1k scale"
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 20
    assert elapsed_ms < 50.0, f"retrieve too slow: {elapsed_ms:.1f}ms (target <50ms)"


def test_stats_reports_bucket_topology(tmp_path):
    fs = FsEpisodicStore(root_dir=str(tmp_path / "st"), scheme="topic_date")
    for c in CORPUS:
        fs.add(c, metadata={"created_at": "2026-05-01"})
    s = fs.stats()
    assert s["n_cases"] == 5
    assert s["scheme"] == "topic_date"
    assert s["n_l0"] >= 3
    assert s["n_l1"] >= 3
    assert sum(s["l0_sizes"].values()) == 5
