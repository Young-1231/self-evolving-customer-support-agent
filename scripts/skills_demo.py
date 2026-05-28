"""v2.2 R6a Skills 化 demo.

End-to-end 演示：
  1. 用一组合成的 NimbusFlow 失败案例跑 Reflector → 一批 ``Playbook``
  2. ``playbook_to_skill`` 落盘成 ``data/skills/*.md``（人审/git 友好）
  3. 重新构造 ``SkillStore`` + ``ProceduralMemory(skill_store=...)``
  4. 验证检索结果与原 ProceduralMemory(jsonl) 等价（无回归）
  5. 生成 ``manifest.json`` 供"懒加载"
  6. 打印一份招聘官友好的样例 .md skill 文件
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from copy import deepcopy

from seagent.evolution.reflector import Reflector
from seagent.memory.episodic import Case
from seagent.memory.procedural import ProceduralMemory
from seagent.skills import (
    SkillStore,
    dump_skill,
    generate_manifest,
    playbook_to_skill,
)


# 合成的 NimbusFlow 失败 case 集合（覆盖 6 个 topic × 2 种动作，最多 12 条）
NIMBUS_CASES = [
    ("billing", False, "年付月付差额怎么处理", "年付转月付差额走 account credit，下次续费抵扣。"),
    ("billing", False, "余额可以退款吗", "余额以 account credit 保留，不退回原卡。"),
    ("billing", True,  "我要现金退款投诉", '调用 transfer_to_human_agents("billing")。'),
    ("login",   False, "密码重置链接多久有效", "点击忘记密码，链接 15 分钟内有效。"),
    ("login",   False, "怎么改邮箱", "在账户设置 → 安全 → 修改邮箱。"),
    ("login",   True,  "账号被盗了怎么办", 'transfer_to_human_agents("security")。'),
    ("product", False, "怎么导出报表", "仪表盘 → 导出 → 选择 CSV/PDF。"),
    ("product", False, "API 限流是多少", "默认 100 req/min；联系销售可升级。"),
    ("shipping", False, "订单多久发货", "工作日 24h 内发货，节假日顺延。"),
    ("shipping", True,  "物流丢件怎么办", 'transfer_to_human_agents("logistics") 走丢件赔付。'),
    ("refund",  False, "七天无理由怎么申请", "订单页 → 申请退款 → 填写理由。"),
    ("refund",  True,  "商家拒绝退款怎么投诉", 'transfer_to_human_agents("refund-arbitration")。'),
]


def _build_cases():
    return [
        Case(case_id=f"q{i:03d}", query=q, resolution=r,
             topic=topic, should_escalate=esc)
        for i, (topic, esc, q, r) in enumerate(NIMBUS_CASES)
    ]


def main(skills_dir: str = None) -> int:
    skills_dir = skills_dir or os.path.join(ROOT, "data", "skills")
    os.makedirs(skills_dir, exist_ok=True)
    # 清掉之前的样例文件，保证 demo 可重复
    for name in os.listdir(skills_dir):
        if name.endswith(".md") or name == "manifest.json":
            os.remove(os.path.join(skills_dir, name))

    print(f"[demo] skills_dir = {skills_dir}")

    # 1. Reflector 产生 playbook
    cases = _build_cases()
    classic = ProceduralMemory(path=None)
    n = Reflector(min_cluster=1).reflect(cases, classic, round_idx=0)
    print(f"[demo] Reflector 产生 {n} 条 playbook")
    for pb in classic.playbooks:
        print(f"  - {pb.playbook_id} action={pb.action} triggers={pb.trigger_terms[:4]}")

    # 2. Playbook → Skill markdown 文件
    metadata = {
        "author": "reflector",
        "reviewed_by": "ops-team",
        "reviewed_at": "2026-05-28",
    }
    for pb in classic.playbooks:
        name = {
            "pb_billing_ans": "年付月付退款",
            "pb_billing_esc": "现金退款转人工",
            "pb_login_ans":   "密码与邮箱修改",
            "pb_login_esc":   "账号被盗转安全团队",
            "pb_product_ans": "产品使用 FAQ",
            "pb_shipping_ans": "发货时效说明",
            "pb_shipping_esc": "物流丢件转人工",
            "pb_refund_ans":  "无理由退款流程",
            "pb_refund_esc":  "退款仲裁转人工",
        }.get(pb.playbook_id, pb.playbook_id)
        description = {
            "answer":   f"处理与「{name}」相关的常见客服咨询",
            "escalate": f"识别「{name}」并转交对应人工团队",
        }[pb.action]
        sk = playbook_to_skill(pb, name=name, description=description, metadata=metadata)
        out = os.path.join(skills_dir, f"{sk.skill_id}.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(dump_skill(sk))
    print(f"[demo] 已写出 {len(classic.playbooks)} 份 markdown skill 到 {skills_dir}")

    # 3. 重新构造 SkillStore + ProceduralMemory(skill_store=)
    store = SkillStore(skills_dir)
    skilled = ProceduralMemory(skill_store=store)
    print(f"[demo] SkillStore 加载 {len(store)} 条 skill")

    # 4. 等价性 check（与 jsonl 版同一组 query 检索结果对比）
    print("\n[demo] 等价性验证 (skill_store vs 原 jsonl playbook):")
    queries = [
        "我想从年付转月付，差额怎么办？",
        "我密码记不住了怎么重置",
        "怎么把账单数据导出来",
        "物流丢件怎么赔",
        "我要现金退款不要 credit",
    ]
    classic_for_compare = ProceduralMemory(path=None)
    for pb in classic.playbooks:
        classic_for_compare.upsert(deepcopy(pb))
    ok = True
    for q in queries:
        a = classic_for_compare.retrieve(q, top_k=2)
        b = skilled.retrieve(q, top_k=2)
        match = [p.ref for p in a] == [p.ref for p in b]
        ok = ok and match
        print(f"  - {q!r:50s} jsonl={[p.ref for p in a]}  skills={[p.ref for p in b]}  {'OK' if match else 'MISMATCH'}")
    print(f"[demo] 等价性 = {'PASS' if ok else 'FAIL'}")

    # 5. manifest
    m = generate_manifest(skills_dir, write=True)
    print(f"\n[demo] manifest 已生成，含 {len(m.skills)} 条条目")
    for e in m.skills[:5]:
        print(f"  - {e.skill_id}: {e.name} ({e.action}) triggers={e.triggers[:3]} ...")

    # 6. 招聘官友好示例：打印 pb_billing_ans 全文
    print("\n" + "=" * 70)
    print("[demo] 招聘官友好示例 — data/skills/pb_billing_ans.md：")
    print("=" * 70)
    sample_path = os.path.join(skills_dir, "pb_billing_ans.md")
    if os.path.exists(sample_path):
        with open(sample_path, "r", encoding="utf-8") as f:
            print(f.read())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
