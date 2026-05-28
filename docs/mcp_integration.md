# v2.4 R6b — MCP Integration Guide

**Status:** shipped 2026-05-28 in `seagent.mcp` and `mcp_servers/`.
**Scope:** make our customer-support agent's tool layer protocol-compatible
with the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) —
the 2026 de-facto standard for LLM-tool interop (≈9k–17k servers, ≈97M
monthly downloads across the SDK ecosystem).

> Why MCP and not bespoke adapters? Each CRM (Zendesk / Intercom / our own)
> would otherwise need its own hard-coded `query_order` / `refund` bridge.
> MCP standardises the wire protocol so every backend looks the same to the
> agent, and every agent looks the same to the backend.

---

## 1. Protocol summary

| Aspect | What we implement |
| --- | --- |
| Transport | **stdio** (subprocess stdin/stdout) |
| Framing | **NDJSON** — one JSON object per line, `\n`-terminated, UTF-8 |
| RPC | **JSON-RPC 2.0** |
| Methods | `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `prompts/list`, `prompts/get`, `shutdown` |
| Error codes | `-32700` parse, `-32600` invalid request, `-32601` method not found, `-32602` invalid params, `-32603` internal, `-32000` tool execution (in-band as `ToolResult.isError`), `-32001` resource not found, `-32002` prompt not found |
| Protocol version string | `2025-06-18` (latest tagged spec date as of 2026-05) |

**Out of scope (intentional):** HTTP / SSE transport, server-initiated
notifications, sampling, roots, OAuth resource servers. Add them when a
backend forces our hand; for stdio + JSON-RPC every popular MCP SDK
interops.

### 1.1 Wire example

`initialize` request from client to server:

```json
{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"seagent.mcp.MCPClient","version":"0.1.0"}},"id":1}
```

`initialize` response:

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":false},"resources":{"listChanged":false,"subscribe":false},"prompts":{"listChanged":false}},"serverInfo":{"name":"order","version":"1.0.0"}}}
```

`tools/call` request:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"query_order","arguments":{"order_id":"ORD-001"}},"id":3}
```

`tools/call` response (note the `content` array and `structuredContent`
mirror — `structuredContent` is a 2025-06 draft addition that we forward
verbatim from any dict/list returned by the handler):

```json
{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"..."}],"isError":false,"structuredContent":{"found":true,"order_id":"ORD-001","status":"shipped",...}}}
```

### 1.2 Tool-execution error convention

Per MCP spec, a *handler* raising an exception is **not** a JSON-RPC error
— the response carries a normal `result` with `"isError": true` so the LLM
can see the failure message in-band and retry. Reserve JSON-RPC errors
for **protocol** violations (unknown method, missing params, etc.).

---

## 2. Modules and files

| Path | Purpose |
| --- | --- |
| `src/seagent/mcp/protocol.py` | Wire types + NDJSON codec + error codes |
| `src/seagent/mcp/server.py` | `MCPServer` base + `serve_stdio()` |
| `src/seagent/mcp/client.py` | `MCPClient` subprocess driver |
| `src/seagent/mcp/tools.py` | `MCPToolset` aggregator + `with_mcp_tools()` wrapper |
| `mcp_servers/order_server.py` | `query_order`, `update_order` |
| `mcp_servers/user_server.py` | `query_user`, `authenticate` |
| `mcp_servers/refund_server.py` | `check_refund_policy`, `initiate_refund` |
| `mcp_servers/handoff_server.py` | `transfer_to_human_agents`, `list_pending_handoffs` |
| `tests/test_mcp.py` | 43 tests (protocol / server / client / 4 real servers / toolset) |
| `scripts/mcp_demo.py` | End-to-end demo with JSON-RPC trace printout |

**Zero third-party dependencies.** We do not import the official `mcp`
package. If you want to consume one of our servers from an upstream MCP
client (Claude Desktop, Cursor, mcp-cli, ...) it Just Works because we
speak the protocol.

---

## 3. Built-in server catalogue

(Generated from the live `tools/list` response — see `scripts/mcp_demo.py`.)

| Qualified name | Description |
| --- | --- |
| `order.query_order` | Look up an order by its id. Returns order record or an error. |
| `order.update_order` | Update the status of an order. Allowed statuses: processing, shipped, delivered, cancelled, returned. |
| `user.query_user` | Look up a user profile by id. |
| `user.authenticate` | Verify a user by email-or-phone and a shared secret. Returns `{authenticated: bool, user_id?: str}`. |
| `refund.check_refund_policy` | Return whether a refund of the given amount for the given reason is auto-approvable (no human review needed). |
| `refund.initiate_refund` | Initiate a refund. Subject to policy check; returns `refund_id` on success, or `{ok: false, reason: ...}` when the policy rejects the request. |
| `handoff.transfer_to_human_agents` | Hand the conversation off to a human agent in the given department, with a short context summary. Returns a ticket id; `urgent=true` bypasses the normal queue. |
| `handoff.list_pending_handoffs` | Return a snapshot of the current handoff queue (debug / ops). |

All backends are **mocked in-process** — swap for real backends in
production (see §5 below).

---

## 4. Using MCP from a SupportAgent / SpecialistAgent

Constraint (v2.4 R6b): we are **not allowed to modify** the v2.3-frozen
`SupportAgent` / `SpecialistAgent` classes (they back the c21/v2.1/v2.2/v2.3
ablation reproductions). The integration is therefore **composition**:

```python
from seagent.mcp import MCPClient, MCPToolset, with_mcp_tools

ts = MCPToolset()
ts.add_server("order",   MCPClient(["python", "-m", "mcp_servers.order_server"]))
ts.add_server("user",    MCPClient(["python", "-m", "mcp_servers.user_server"]))
ts.add_server("refund",  MCPClient(["python", "-m", "mcp_servers.refund_server"]))
ts.add_server("handoff", MCPClient(["python", "-m", "mcp_servers.handoff_server"]))

with ts:
    agent = build_my_specialist_agent(...)        # any existing v2.3 agent
    agent = with_mcp_tools(agent, ts)             # non-invasive wrapper

    # SupportAgent.handle(query) still works untouched:
    result = agent.handle("Refund ORD-002 please.")

    # New capability — typed tool calls:
    order = agent.call_tool("query_order", {"order_id": "ORD-002"}).structured
```

`with_mcp_tools` returns a proxy that `__getattr__`-delegates everything
to the wrapped agent. Existing orchestrator code that only calls
`agent.handle(...)` works unchanged.

### 4.1 Name resolution

`agent.call_tool("query_order", ...)` resolves to a single server when the
name is unambiguous. If two servers both expose `query_user` (e.g. a
Zendesk MCP and our own user server), call sites must use the qualified
form `"server.tool"` to disambiguate — the toolset raises a `KeyError`
otherwise.

---

## 5. Wiring real backends

Anything that speaks MCP stdio drops in. Two paths:

### 5.1 Use a 3rd-party MCP server

Most ecosystems already ship one. E.g.:

```python
ts.add_server("zendesk", MCPClient(["npx", "-y", "@zendesk/mcp-server"],
                                    env={"ZENDESK_API_TOKEN": "..."}))
ts.add_server("intercom", MCPClient(["intercom-mcp", "--workspace", "wks_..."]))
```

After `ts.start()`, those servers' tools appear in the same catalogue.
Names collide? Use the qualified form (`zendesk.query_ticket` vs
`intercom.query_ticket`).

### 5.2 Wrap your own CRM

Subclass `MCPServer`, register handlers, run as a script. The handler
talks to your DB / CRM in plain Python; MCP only standardises the
*envelope*:

```python
# mcp_servers/crm_server.py
from seagent.mcp import MCPServer
import my_company.crm as crm

srv = MCPServer(name="crm", version="1.0.0")

@srv.tool(
    name="query_account",
    description="Look up an account by id in the internal CRM.",
    input_schema={"type": "object",
                  "properties": {"account_id": {"type": "string"}},
                  "required": ["account_id"]},
)
def query_account(account_id: str):
    return crm.get_account(account_id).to_dict()

if __name__ == "__main__":
    srv.serve_stdio()
```

That's all. The handler return value can be a `str`, a JSON-serializable
dict/list, or a full `ToolResult` if you want to control content blocks.
Raised exceptions are auto-wrapped as `isError=true`.

---

## 6. Relationship with v2.2 Skills

| Layer | What it describes | File | Discovery |
| --- | --- | --- | --- |
| **Skills** (v2.2 R6a) | *What to do* — natural-language playbooks + structured steps. "When the user asks for a refund, look up the order, verify identity, check policy, refund or escalate." | `src/seagent/skills/` + `data/skills/*.md` | `SkillStore.match(query)` |
| **MCP** (v2.4 R6b) | *What tools to use* — typed RPC handles to external systems. `query_order`, `initiate_refund`, etc. | `src/seagent/mcp/` + `mcp_servers/*.py` | `MCPToolset.list_tools()` |

They are orthogonal. A skill's step `"call initiate_refund with amount=X"`
binds to a tool exposed by an MCP server at runtime. Adding a new
backend (a new CRM) requires only a new MCP server — **no skill
rewrites**. Adding a new policy (e.g. "double-check identity before
refunds over $500") requires only a skill update — **no MCP changes**.

---

## 7. Testing

`tests/test_mcp.py` covers (43 tests):

1. Protocol — encode/decode round-trip, JSON-RPC types, validation.
2. Server dispatch — `initialize`, `tools/list`, `tools/call` with the
   in-process dispatcher (BytesIO pipes), resources, prompts.
3. Subprocess round-trip — `MCPClient` against an inline fixture server.
4. End-to-end — each of the 4 real servers exercised via `MCPClient`,
   plus error paths (unknown tool, missing required arg, handler
   exception, validation failure).
5. `MCPToolset` aggregation + `with_mcp_tools` proxy.
6. Server-info smoke for each of the 4 servers.

```
PYTHONPATH=src:. python -m pytest tests/test_mcp.py -q
# 43 passed in <1s
```

Full suite (200 pre-existing + 43 new):

```
PYTHONPATH=src:. python -m pytest tests -q
# 243 passed, 2 skipped
```

No DeepSeek / OpenAI calls anywhere — verification is protocol-level
round-trips and mocked backends only.

---

## 8. Backwards compatibility — c21 / v2.1 / v2.2 / v2.3

The MCP layer adds files only. **Zero source files under `src/seagent/`
prior to `mcp/` were touched.** No imports were added to `support_agent`,
`multi_agent`, `hooks`, or `skills`. The c21/v2.1/v2.2/v2.3 reproductions
remain bit-identical.

---

## 9. Roadmap

- [ ] HTTP / SSE transport for cross-host backends.
- [ ] Resource subscription (push updates from the server, e.g. order
      status changes streamed back).
- [ ] OAuth resource-server handshake for vendor MCPs.
- [ ] An *optional* shim that promotes the official `mcp` package's
      types when it's installed — for users who want strict spec
      compliance with future protocol versions.
