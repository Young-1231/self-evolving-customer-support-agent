# -*- coding: utf-8 -*-
"""Self-check for the NimbusFlow dataset."""
import json, os, collections

BASE = "/root/autodl-tmp/self_evolving_agent/data"
KB_DIR = os.path.join(BASE, "kb")
EVAL_DIR = os.path.join(BASE, "eval")

errors = []
def check(cond, msg):
    if not cond:
        errors.append(msg)

# ---- (a) index.jsonl ----
idx = {}
with open(os.path.join(KB_DIR, "index.jsonl"), encoding="utf-8") as f:
    lines = [l for l in f if l.strip()]
for i, line in enumerate(lines):
    obj = json.loads(line)
    for k in ("doc_id", "title", "topic", "text"):
        check(k in obj and obj[k], "index line %d missing/empty field %s" % (i, k))
    check(obj["doc_id"] not in idx, "duplicate doc_id %s" % obj["doc_id"])
    idx[obj["doc_id"]] = obj

# md <-> index one-to-one + text consistency
md_files = [f for f in os.listdir(KB_DIR) if f.endswith(".md")]
check(len(md_files) == len(idx), "md count %d != index count %d" % (len(md_files), len(idx)))
ALLOWED_TOPICS = {"billing","account_security","integrations_api","data_export",
                  "permissions","mobile_app","troubleshooting","general"}
for did, obj in idx.items():
    p = os.path.join(KB_DIR, did + ".md")
    check(os.path.exists(p), "missing md for %s" % did)
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    body = raw.split("---", 2)[-1].strip()
    check(body == obj["text"].strip(), "text mismatch for %s" % did)
    check(obj["topic"] in ALLOWED_TOPICS, "bad topic %s in %s" % (obj["topic"], did))
    n = len(obj["text"])
    check(150 <= n <= 400, "doc %s length %d out of 150-400" % (did, n))

ALL_TEXT = "\n".join(o["text"] for o in idx.values())

# ---- (b) queries.jsonl ----
qs = []
with open(os.path.join(EVAL_DIR, "queries.jsonl"), encoding="utf-8") as f:
    for line in f:
        if line.strip():
            qs.append(json.loads(line))

REQ = ("id","split","group","query","required_keypoints","gold_doc_ids",
       "should_escalate","difficulty","resolution")
ids = set()
groups = collections.defaultdict(list)
for q in qs:
    for k in REQ:
        check(k in q, "query %s missing field %s" % (q.get("id"), k))
    check(q["id"] not in ids, "dup query id %s" % q["id"])
    ids.add(q["id"])
    check(q["split"] in ("train","eval"), "bad split %s" % q["id"])
    check(q["difficulty"] in ("easy","hard"), "bad difficulty %s" % q["id"])
    check(isinstance(q["should_escalate"], bool), "escalate not bool %s" % q["id"])
    check(2 <= len(q["required_keypoints"]) <= 4, "keypoints count %s" % q["id"])
    for did in q["gold_doc_ids"]:
        check(did in idx, "%s references unknown doc %s" % (q["id"], did))
    # resolution must contain all keypoints
    for kp in q["required_keypoints"]:
        check(kp in q["resolution"],
              "resolution of %s missing keypoint '%s'" % (q["id"], kp))
    groups[q["group"]].append(q)

# each group >=1 train + >=1 eval, consistent keypoints
for g, members in groups.items():
    splits = {m["split"] for m in members}
    check("train" in splits and "eval" in splits, "group %s missing train/eval" % g)
    kpsets = {tuple(sorted(m["required_keypoints"])) for m in members}
    check(len(kpsets) == 1, "group %s has inconsistent keypoints" % g)
    diffs = {m["difficulty"] for m in members}
    check(len(diffs) == 1, "group %s mixed difficulty" % g)

# ---- easy: every keypoint found in some gold doc text ----
for q in qs:
    if q["difficulty"] == "easy":
        check(len(q["gold_doc_ids"]) > 0, "easy %s has no gold" % q["id"])
        gold_text = "\n".join(idx[d]["text"] for d in q["gold_doc_ids"])
        for kp in q["required_keypoints"]:
            check(kp in gold_text,
                  "EASY %s keypoint '%s' NOT in gold text" % (q["id"], kp))

# ---- hard: at least one keypoint NOT fully matchable in its gold docs ----
for q in qs:
    if q["difficulty"] == "hard":
        gold_text = "\n".join(idx[d]["text"] for d in q["gold_doc_ids"])
        unmatched = [kp for kp in q["required_keypoints"] if kp not in gold_text]
        check(len(unmatched) >= 1,
              "HARD %s: all keypoints found in gold (not actually hard)" % q["id"])

# ---- escalate: must include a 转人工/团队 style keypoint ----
ESC_HINTS = ("转接人工","转人工","团队","人工")
for q in qs:
    if q["should_escalate"]:
        has = any(any(h in kp for h in ESC_HINTS) for kp in q["required_keypoints"])
        check(has, "escalate %s lacks human-handoff keypoint" % q["id"])

# ---- Stats ----
print("="*60)
print("SELF-CHECK RESULT:", "PASS" if not errors else "FAIL (%d errors)" % len(errors))
for e in errors:
    print("  ERR:", e)
print("="*60)

print("KB docs:", len(idx))
topic_count = collections.Counter(o["topic"] for o in idx.values())
print("KB by topic:", dict(topic_count))
print()
print("Queries total:", len(qs))
print("Groups:", len(groups))
by_split = collections.Counter(q["split"] for q in qs)
by_diff = collections.Counter(q["difficulty"] for q in qs)
by_topic_q = collections.Counter()
# topic of a query = topic of its first gold doc, else 'general/hard'
print("by split:", dict(by_split))
print("by difficulty:", dict(by_diff),
      "(easy %.0f%%, hard %.0f%%)" % (100*by_diff['easy']/len(qs), 100*by_diff['hard']/len(qs)))
esc = sum(1 for q in qs if q["should_escalate"])
print("escalate queries:", esc, "(%.0f%%)" % (100*esc/len(qs)))

# topic coverage per query (via gold docs)
covered_topics = set()
for q in qs:
    for d in q["gold_doc_ids"]:
        covered_topics.add(idx[d]["topic"])
print("topics covered by queries' gold docs:", sorted(covered_topics))

# train/eval split detail per difficulty
print()
print("split x difficulty:")
for s in ("train","eval"):
    row = collections.Counter(q["difficulty"] for q in qs if q["split"]==s)
    print("  %-5s: %s" % (s, dict(row)))
