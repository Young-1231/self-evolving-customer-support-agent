"""mcp_servers.refund_server — initiate refunds with policy check.

Tools
-----
  * ``initiate_refund(order_id, amount, reason)``  -> {ok, refund_id?, reason?}
  * ``check_refund_policy(order_amount, reason)``  -> {allowed, max_amount, notes}
"""
from __future__ import annotations

import uuid

from . import _path_bootstrap  # noqa: F401
_path_bootstrap.ensure_seagent_on_path()

from seagent.mcp import MCPServer  # noqa: E402


# Per-reason refund policy.  ``cap`` is the per-order maximum auto-refund
# amount; anything over requires human review (the agent should call the
# handoff server).
_POLICY = {
    "defective":          {"cap": 1000.0, "notes": "full refund allowed up to $1000"},
    "wrong_item":         {"cap": 500.0,  "notes": "full refund allowed up to $500"},
    "late_delivery":      {"cap": 100.0,  "notes": "courtesy credit only"},
    "changed_mind":       {"cap": 50.0,   "notes": "restocking fee may apply"},
    "fraud":              {"cap": 0.0,    "notes": "must transfer to fraud team"},
}
_REFUNDED = {}  # order_id -> refund_id (no double-refund)


def build_server() -> MCPServer:
    srv = MCPServer(name="refund", version="1.0.0")

    @srv.tool(
        name="check_refund_policy",
        description="Return whether a refund of the given amount for the given "
                    "reason is auto-approvable (no human review needed).",
        input_schema={
            "type": "object",
            "properties": {
                "order_amount": {"type": "number"},
                "reason":       {"type": "string", "enum": sorted(_POLICY)},
            },
            "required": ["order_amount", "reason"],
        },
    )
    def check_refund_policy(order_amount: float, reason: str):
        pol = _POLICY.get(reason)
        if pol is None:
            return {"allowed": False, "reason_unknown": True}
        cap = float(pol["cap"])
        return {
            "allowed": float(order_amount) <= cap,
            "max_amount": cap,
            "notes": pol["notes"],
        }

    @srv.tool(
        name="initiate_refund",
        description="Initiate a refund.  Subject to policy check; returns "
                    "refund_id on success, or {ok: false, reason: ...} when "
                    "the policy rejects the request.",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount":   {"type": "number", "minimum": 0},
                "reason":   {"type": "string", "enum": sorted(_POLICY)},
            },
            "required": ["order_id", "amount", "reason"],
        },
    )
    def initiate_refund(order_id: str, amount: float, reason: str):
        if order_id in _REFUNDED:
            return {"ok": False, "reason": "already_refunded", "refund_id": _REFUNDED[order_id]}
        pol = _POLICY.get(reason)
        if pol is None:
            return {"ok": False, "reason": "unknown_reason"}
        if float(amount) > float(pol["cap"]):
            return {
                "ok": False,
                "reason": "exceeds_policy_cap",
                "cap": pol["cap"],
                "needs_human_review": True,
            }
        rid = f"REF-{uuid.uuid4().hex[:8]}"
        _REFUNDED[order_id] = rid
        return {"ok": True, "refund_id": rid, "order_id": order_id, "amount": float(amount), "reason": reason}

    return srv


def main() -> None:
    build_server().serve_stdio()


if __name__ == "__main__":
    main()
