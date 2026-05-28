"""mcp_servers.handoff_server — transfer-to-human-agent queue.

Tools
-----
  * ``transfer_to_human_agents(department, summary, urgent)`` -> ticket id
  * ``list_pending_handoffs()``                               -> queue snapshot
"""
from __future__ import annotations

import time
import uuid

from . import _path_bootstrap  # noqa: F401
_path_bootstrap.ensure_seagent_on_path()

from seagent.mcp import MCPServer  # noqa: E402


_DEPARTMENTS = {"billing", "refund", "technical", "account", "fraud", "general"}
_QUEUE: list = []


def build_server() -> MCPServer:
    srv = MCPServer(name="handoff", version="1.0.0")

    @srv.tool(
        name="transfer_to_human_agents",
        description="Hand the conversation off to a human agent in the given "
                    "department, with a short context summary.  Returns a "
                    "ticket id; setting urgent=true bypasses the normal queue.",
        input_schema={
            "type": "object",
            "properties": {
                "department": {"type": "string", "enum": sorted(_DEPARTMENTS)},
                "summary":    {"type": "string"},
                "urgent":     {"type": "boolean", "default": False},
            },
            "required": ["department", "summary"],
        },
    )
    def transfer_to_human_agents(department: str, summary: str, urgent: bool = False):
        if department not in _DEPARTMENTS:
            return {"ok": False, "reason": f"unknown_department: {department!r}"}
        tid = f"TKT-{uuid.uuid4().hex[:8]}"
        rec = {
            "ticket_id": tid,
            "department": department,
            "summary": summary,
            "urgent": bool(urgent),
            "created_at": time.time(),
        }
        if urgent:
            _QUEUE.insert(0, rec)
        else:
            _QUEUE.append(rec)
        return {"ok": True, "ticket_id": tid, "queue_position": _QUEUE.index(rec) + 1}

    @srv.tool(
        name="list_pending_handoffs",
        description="Return a snapshot of the current handoff queue (debug / ops).",
        input_schema={"type": "object", "properties": {}},
    )
    def list_pending_handoffs():
        return {"queue": list(_QUEUE)}

    return srv


def main() -> None:
    build_server().serve_stdio()


if __name__ == "__main__":
    main()
