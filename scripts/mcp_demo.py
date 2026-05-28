"""v2.4 R6b MCP demo — end-to-end customer-support flow over JSON-RPC.

Runs **fully offline** (no LLM calls — DeepSeek balance is exhausted).
The "agent" here is hard-coded scripted logic; the point is to exercise the
4 MCP servers + MCPToolset + JSON-RPC wire and print the conversation as a
machine-readable trace.

Steps:
  1. start the 4 servers as subprocesses via MCPToolset
  2. list_tools across all of them
  3. simulate a ticket:
        user: "My order ORD-002 hasn't arrived, please refund."
        -> query_order(ORD-002)
        -> query_user(<user_id from step a>)
        -> check_refund_policy(amount, reason='late_delivery')
        -> initiate_refund(... if within cap, else exceeds_policy_cap)
        -> transfer_to_human_agents(department='refund', summary=...)
  4. print the JSON-RPC trace + a final structured summary

Run::

    PYTHONPATH=src:. python scripts/mcp_demo.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT))  # so ``mcp_servers`` is importable

from seagent.mcp import MCPClient, MCPToolset  # noqa: E402
from seagent.mcp.protocol import encode_message, JsonRpcRequest  # noqa: E402


def _argv(module: str):
    return [sys.executable, "-m", f"mcp_servers.{module}"]


def _env():
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    parts = [str(REPO_ROOT), str(SRC)]
    env["PYTHONPATH"] = os.pathsep.join(parts + ([pp] if pp else []))
    return env


def _line(title: str = "", char: str = "-", width: int = 72) -> None:
    if title:
        pad = max(0, width - len(title) - 2)
        print(f"{char * 2} {title} {char * pad}")
    else:
        print(char * width)


def _trace_call(label: str, server: str, tool: str, args: dict, result_dict: dict) -> None:
    """Print a JSON-RPC-shaped trace of one tool call (educational)."""
    req = JsonRpcRequest(method="tools/call",
                         params={"name": tool, "arguments": args},
                         id="<auto>")
    print(f"[{label}] --> {server}.{tool}")
    print("    REQUEST :", json.dumps(req.to_dict(), ensure_ascii=False))
    print("    RESPONSE:", json.dumps({"jsonrpc": "2.0", "id": "<auto>", "result": result_dict},
                                      ensure_ascii=False))


def main() -> None:
    ts = MCPToolset()
    ts.add_server("order",   MCPClient(_argv("order_server"),   env=_env(), cwd=str(REPO_ROOT)))
    ts.add_server("user",    MCPClient(_argv("user_server"),    env=_env(), cwd=str(REPO_ROOT)))
    ts.add_server("refund",  MCPClient(_argv("refund_server"),  env=_env(), cwd=str(REPO_ROOT)))
    ts.add_server("handoff", MCPClient(_argv("handoff_server"), env=_env(), cwd=str(REPO_ROOT)))

    with ts:
        _line("v2.4 R6b — MCP demo", "=")
        print("Started 4 MCP servers over stdio (JSON-RPC 2.0, NDJSON framing).\n")

        # 1) catalog ---------------------------------------------------------
        _line("Tool catalog")
        catalog = ts.list_tools()
        for qual, t in catalog:
            print(f"  {qual:40s}  {t.description}")
        print(f"\n  -> {len(catalog)} tools across 4 servers\n")

        # 2) simulate a support ticket --------------------------------------
        _line("Simulated ticket")
        user_msg = "Hi, my order ORD-002 still hasn't arrived after 3 weeks. Refund please."
        print(f"  USER: {user_msg}\n")

        # a) query the order
        order_id = "ORD-002"
        tr = ts.call_tool("order.query_order", {"order_id": order_id})
        order = tr.structured
        _trace_call("step-1", "order", "query_order", {"order_id": order_id}, tr.to_dict())

        # b) query the user
        user_id = order["user_id"]
        tr = ts.call_tool("user.query_user", {"user_id": user_id})
        user = tr.structured
        _trace_call("step-2", "user", "query_user", {"user_id": user_id}, tr.to_dict())

        # c) policy check
        tr = ts.call_tool("refund.check_refund_policy",
                          {"order_amount": order["amount"], "reason": "late_delivery"})
        policy = tr.structured
        _trace_call("step-3", "refund", "check_refund_policy",
                    {"order_amount": order["amount"], "reason": "late_delivery"}, tr.to_dict())

        # d) refund (or transfer if exceeds cap)
        if policy["allowed"]:
            tr = ts.call_tool("refund.initiate_refund",
                              {"order_id": order_id, "amount": order["amount"], "reason": "late_delivery"})
            refund = tr.structured
            _trace_call("step-4a", "refund", "initiate_refund",
                        {"order_id": order_id, "amount": order["amount"], "reason": "late_delivery"},
                        tr.to_dict())
            final_msg = (
                f"Refund {refund['refund_id']} of ${order['amount']:.2f} initiated for "
                f"{user['name']} ({user['email']})."
            )
        else:
            # exceeds cap → courtesy credit was applied or we transfer to human
            summary = (
                f"User {user['name']} ({user['email']}) requests refund of "
                f"${order['amount']:.2f} for {order_id} (late delivery >3wk). "
                f"Auto-refund cap is ${policy['max_amount']:.2f}; escalate for approval."
            )
            tr = ts.call_tool("handoff.transfer_to_human_agents",
                              {"department": "refund", "summary": summary, "urgent": True})
            handoff = tr.structured
            _trace_call("step-4b", "handoff", "transfer_to_human_agents",
                        {"department": "refund", "summary": summary, "urgent": True},
                        tr.to_dict())
            final_msg = (
                f"Transferred to refund team — ticket {handoff['ticket_id']} "
                f"(queue position {handoff['queue_position']})."
            )

        # 3) final report ----------------------------------------------------
        print()
        _line("Resolution")
        print(f"  AGENT: {final_msg}")
        print()
        _line("", "=")


if __name__ == "__main__":
    main()
