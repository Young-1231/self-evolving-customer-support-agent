"""v2.4 R6b — Model Context Protocol (MCP) tool layer.

This package is *strictly external* to the rest of ``seagent``.  Nothing under
``seagent.agent`` / ``seagent.memory`` / ``seagent.guardrails`` /
``seagent.multi_agent`` imports from here, which keeps the controlled-
ablation harnesses (Exp A→E from c21/v2.1/v2.2/v2.3) reproducible bit-for-bit.

What MCP gives us
-----------------
Hardcoded ``query_order(...)`` / ``refund(...)`` calls in a SupportAgent /
SpecialistAgent assume one specific backend (a mocked DB, or Zendesk, or
Intercom, or our own CRM).  Each integration is a bespoke adapter.

MCP standardises the wire protocol — JSON-RPC 2.0 over stdio — so any tool
provider (us, vendor, or 3rd party) can expose tools as a *server* and any
agent can consume them as a *client*.  Concretely:

    +-------------+   stdin    +------------------+
    | MCPClient   | ---------> | mcp_servers/...  |
    | (in agent)  | <--------- | order_server.py  |
    +-------------+   stdout   +------------------+
        JSON-RPC 2.0 messages, newline-framed

The protocol surface we implement is a deliberate subset of
https://modelcontextprotocol.io (2026 draft):

  * transport       — stdio with newline-delimited JSON (NDJSON)
  * methods         — initialize, tools/list, tools/call,
                      resources/list, resources/read,
                      prompts/list, prompts/get
  * errors          — standard JSON-RPC error codes
                      (-32700 / -32600 / -32601 / -32602 / -32603)

Out of scope: HTTP/SSE transport, server-initiated notifications,
sampling, roots.  Add them when a backend needs them.

Public API
----------
* ``protocol``   — wire types (JsonRpcRequest/Response/Error, Tool, ...)
* ``server``     — ``MCPServer`` base class with @tool / @resource / @prompt
                   decorators and ``serve_stdio()``
* ``client``     — ``MCPClient`` (subprocess + JSON-RPC) and context manager
* ``tools``      — ``MCPToolset`` to combine multiple clients; ``with_mcp_tools``
                   wrapper to attach a toolset to an existing SupportAgent /
                   SpecialistAgent without mutating the source class
"""
from .protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcError,
    Tool,
    ToolCall,
    ToolResult,
    encode_message,
    decode_message,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from .server import MCPServer
from .client import MCPClient, MCPClientError
from .tools import MCPToolset, with_mcp_tools

__all__ = [
    # protocol
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcError",
    "Tool",
    "ToolCall",
    "ToolResult",
    "encode_message",
    "decode_message",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    # server
    "MCPServer",
    # client
    "MCPClient",
    "MCPClientError",
    # tools
    "MCPToolset",
    "with_mcp_tools",
]
