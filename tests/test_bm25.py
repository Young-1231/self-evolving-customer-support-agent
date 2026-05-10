from seagent.memory.bm25 import BM25, tokenize


def test_tokenize_mixed():
    toks = tokenize("重置密码 API token")
    assert "api" in toks and "token" in toks
    assert "密码" in toks  # CJK bigram
    assert "重" in toks     # CJK unigram


def test_bm25_ranks_relevant_doc_first():
    corpus = [tokenize(t) for t in [
        "如何重置登录密码 忘记密码 邮箱验证",
        "如何导出数据为 CSV 文件",
        "升级订阅套餐与账单说明",
    ]]
    bm = BM25(corpus)
    hits = bm.search("我忘记密码了怎么重置", top_k=3)
    assert hits and hits[0][0] == 0
    assert hits[0][1] > 0
