"""Playbook 发布生命周期状态机 + 审计日志。

自进化产出的每一条 playbook 都被当作一份**受治理的发布资产**，而不是直接写进
ProceduralMemory 就生效。它必须依次穿过：

    proposed → approved → canary → active

任一环节都可被人为打回(rollback)或淘汰(deprecate)。状态机如下：

    proposed --approve--> approved --promote_to_canary--> canary --activate--> active
       |                     |                               |                   |
       +--------- rollback / deprecate (从任意非终态打回/淘汰) -------------------+

  - rolled_back : 灰度/线上出问题被回滚的终态(可重新 propose 新版本)；
  - deprecated  : 主动下线、不再使用的终态。

设计要点(对齐 misevolution arXiv 2509.26354 的防护)：
  - **不污染 ProceduralMemory**：治理状态单独存 JSON
    (``experiments/governance/playbook_registry.json``)，ProceduralMemory 只
    负责"已被授权生效"的 playbook 的检索；谁在何时把哪条规则推进到哪个状态，
    全部落在独立审计日志(JSONL)里，可追溯、可问责。
  - 每条记录带 created_at / approver / version / parent_version 等审计字段，
    回滚时记录回滚原因，形成完整的发布血缘。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..memory.procedural import Playbook, ProceduralMemory


class LifecycleState(str, Enum):
    """playbook 在治理流程中的状态(用字符串枚举，便于 JSON 序列化)。"""

    PROPOSED = "proposed"        # Reflector 刚产出，等待人审
    APPROVED = "approved"        # 人审通过，等待灰度
    CANARY = "canary"            # 灰度中(小流量/影子，未全量授权)
    ACTIVE = "active"            # 全量生效(已写入 ProceduralMemory)
    ROLLED_BACK = "rolled_back"  # 因 SLO 回归等被回滚(终态)
    DEPRECATED = "deprecated"    # 主动下线(终态)


# 允许的状态转移图：from -> {可达 to}
_TRANSITIONS: Dict[LifecycleState, set] = {
    LifecycleState.PROPOSED: {
        LifecycleState.APPROVED,
        LifecycleState.ROLLED_BACK,
        LifecycleState.DEPRECATED,
    },
    LifecycleState.APPROVED: {
        LifecycleState.CANARY,
        LifecycleState.ROLLED_BACK,
        LifecycleState.DEPRECATED,
    },
    LifecycleState.CANARY: {
        LifecycleState.ACTIVE,
        LifecycleState.ROLLED_BACK,
        LifecycleState.DEPRECATED,
    },
    LifecycleState.ACTIVE: {
        LifecycleState.ROLLED_BACK,
        LifecycleState.DEPRECATED,
    },
    LifecycleState.ROLLED_BACK: set(),   # 终态
    LifecycleState.DEPRECATED: set(),    # 终态
}


def _now() -> str:
    """UTC ISO8601 时间戳(治理审计统一用 UTC，避免时区歧义)。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlaybookRecord:
    """治理侧的一条 playbook 发布记录(与业务侧 Playbook 解耦)。"""

    playbook_id: str
    topic: str
    state: str = LifecycleState.PROPOSED.value
    version: int = 1
    parent_version: Optional[int] = None   # 上一版本号(回滚/迭代血缘)
    proposer: str = "reflector"            # 提案者(默认来自自进化 Reflector)
    approver: Optional[str] = None         # 人审批准者(谁拍的板)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    rollback_reason: Optional[str] = None
    # 提案时携带的 playbook 业务载荷(用于 activate 时写入 ProceduralMemory)
    payload: Dict[str, Any] = field(default_factory=dict)


class PlaybookRegistry:
    """playbook 发布生命周期管理器 + 审计日志。

    Args:
        registry_path : 状态注册表 JSON 路径。默认
            ``<workdir>/governance/playbook_registry.json``。
        audit_path    : 审计日志 JSONL 路径。默认同目录 ``audit_log.jsonl``。
        procedural    : 可选，业务侧 ProceduralMemory。activate/rollback 时会
            把"已授权生效"的状态同步过去(upsert / set_enabled)，但治理状态本身
            绝不写进 ProceduralMemory，二者职责分离。
    """

    def __init__(
        self,
        registry_path: Optional[str] = None,
        audit_path: Optional[str] = None,
        procedural: Optional[ProceduralMemory] = None,
    ):
        self.registry_path = registry_path
        self.audit_path = audit_path
        self.procedural = procedural
        self.records: Dict[str, PlaybookRecord] = {}
        if registry_path and os.path.exists(registry_path):
            self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        with open(self.registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.records = {pid: PlaybookRecord(**rec) for pid, rec in data.items()}

    def _persist(self) -> None:
        if not self.registry_path:
            return
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        data = {pid: asdict(r) for pid, r in self.records.items()}
        with open(self.registry_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _audit(self, action: str, playbook_id: str, actor: str, **extra: Any) -> None:
        """追加一条审计日志：谁(actor)在何时(ts)对哪条(playbook_id)做了什么(action)。"""
        entry = {
            "ts": _now(),
            "action": action,
            "playbook_id": playbook_id,
            "actor": actor,
        }
        entry.update(extra)
        if self.audit_path:
            os.makedirs(os.path.dirname(self.audit_path), exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_audit_log(self) -> List[Dict[str, Any]]:
        """读回全部审计条目(供合规审查/回放)。"""
        if not self.audit_path or not os.path.exists(self.audit_path):
            return []
        out: List[Dict[str, Any]] = []
        with open(self.audit_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # ---------------- 状态机内部 ----------------
    def get(self, playbook_id: str) -> Optional[PlaybookRecord]:
        return self.records.get(playbook_id)

    def _transition(
        self, playbook_id: str, to: LifecycleState, actor: str, **audit_extra: Any
    ) -> PlaybookRecord:
        rec = self.records.get(playbook_id)
        if rec is None:
            raise KeyError(f"未知 playbook: {playbook_id}")
        cur = LifecycleState(rec.state)
        if to not in _TRANSITIONS[cur]:
            raise ValueError(f"非法状态转移: {cur.value} -> {to.value} (playbook={playbook_id})")
        rec.state = to.value
        rec.updated_at = _now()
        self._persist()
        self._audit(f"->{to.value}", playbook_id, actor, from_state=cur.value, **audit_extra)
        return rec

    # ---------------- 对外 API ----------------
    def propose(self, pb: Playbook, proposer: str = "reflector") -> PlaybookRecord:
        """Reflector 产出一条 playbook → 进入 proposed，登记进治理注册表。

        若同 id 已存在(迭代新版本)，version 自增、记录 parent_version 形成血缘，
        并重置为 proposed 重新走审批流。
        """
        prev = self.records.get(pb.playbook_id)
        version = (prev.version + 1) if prev else pb.version
        parent_version = prev.version if prev else None
        rec = PlaybookRecord(
            playbook_id=pb.playbook_id,
            topic=pb.topic,
            state=LifecycleState.PROPOSED.value,
            version=version,
            parent_version=parent_version,
            proposer=proposer,
            payload=asdict(pb),
        )
        self.records[pb.playbook_id] = rec
        self._persist()
        self._audit("propose", pb.playbook_id, proposer, version=version,
                    parent_version=parent_version, topic=pb.topic)
        return rec

    def approve(self, playbook_id: str, approver: str) -> PlaybookRecord:
        """人审通过 → proposed 转 approved，记录批准者(谁拍的板)。"""
        rec = self._transition(playbook_id, LifecycleState.APPROVED, approver)
        rec.approver = approver
        self._persist()
        return rec

    def promote_to_canary(self, playbook_id: str, actor: str = "release-bot") -> PlaybookRecord:
        """approved 转 canary，进入小流量灰度观察期(尚未全量授权)。"""
        return self._transition(playbook_id, LifecycleState.CANARY, actor)

    def activate(self, playbook_id: str, actor: str = "release-bot") -> PlaybookRecord:
        """canary 转 active：全量生效。

        此刻才把业务载荷写入 ProceduralMemory 并置为 enabled——即"授权生效"动作
        与治理状态机一一对应。门禁(regression_gate)应在调用本方法**之前**通过。
        """
        rec = self._transition(playbook_id, LifecycleState.ACTIVE, actor)
        if self.procedural is not None:
            pb = Playbook(**rec.payload)
            pb.enabled = True
            self.procedural.upsert(pb)
        return rec

    def rollback(self, playbook_id: str, actor: str, reason: str) -> PlaybookRecord:
        """从任意非终态回滚到 rolled_back(SLO 回归/线上事故时使用)。

        若该 playbook 已写入 ProceduralMemory，则同时 set_enabled(False) 立即止血。
        """
        rec = self._transition(playbook_id, LifecycleState.ROLLED_BACK, actor, reason=reason)
        rec.rollback_reason = reason
        self._persist()
        if self.procedural is not None:
            self.procedural.set_enabled(playbook_id, False)
        return rec

    def deprecate(self, playbook_id: str, actor: str, reason: str = "") -> PlaybookRecord:
        """主动下线一条 playbook(到 deprecated 终态)，同时在业务侧禁用。"""
        rec = self._transition(playbook_id, LifecycleState.DEPRECATED, actor, reason=reason)
        self._persist()
        if self.procedural is not None:
            self.procedural.set_enabled(playbook_id, False)
        return rec

    def list_by_state(self, state: LifecycleState) -> List[PlaybookRecord]:
        return [r for r in self.records.values() if r.state == state.value]
