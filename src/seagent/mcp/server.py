"""MCPServer base class — declare tools/resources/prompts, serve over stdio.

Subclassing pattern (see ``mcp_servers/order_server.py`` for a full example)::

    from seagent.mcp import MCPServer

    class OrderServer(MCPServer):
        def __init__(self):
            super().__init__(name="order", version="1.0.0")

            @self.tool(
                name="query_order",
                description="Look up an order by id",
                input_schema={
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            )
            def query_order(order_id: str):
                return {"order_id": order_id, "status": "shipped"}

    if __name__ == "__main__":
        OrderServer().serve_stdio()

The handler may return either:
  * a ``ToolResult`` (full control over content blocks + structured output)
  * a ``str`` (wrapped as a text block)
  * any JSON-serializable dict/list (wrapped as a text block via ``json.dumps``
    and also placed in ``structuredContent``).
"""
from __future__ import annotations

import json
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, IO, List, Optional

from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROMPT_NOT_FOUND,
    RESOURCE_NOT_FOUND,
    TOOL_EXECUTION_ERROR,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    Tool,
    ToolResult,
    decode_message,
    encode_message,
)


# -- Protocol version we speak ----------------------------------------------
MCP_PROTOCOL_VERSION = "2025-06-18"  # latest tagged spec date as of 2026-05


@dataclass
class _ToolEntry:
    tool: Tool
    handler: Callable[..., Any]


@dataclass
class _ResourceEntry:
    uri: str
    name: str
    description: str
    mime_type: str
    handler: Callable[[], Any]  # returns text content


@dataclass
class _PromptEntry:
    name: str
    description: str
    arguments: List[Dict[str, Any]]
    handler: Callable[..., Any]


class MCPServer:
    """Base class for an MCP server.  Override ``__init__`` and register
    tools/resources/prompts via the decorator factories.

    The class is intentionally small; everything dispatched in
    ``handle_message`` returns a ``JsonRpcResponse`` (or None for
    notifications), which keeps ``serve_stdio`` trivial.
    """

    def __init__(self, name: str, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._tools: Dict[str, _ToolEntry] = {}
        self._resources: Dict[str, _ResourceEntry] = {}
        self._prompts: Dict[str, _PromptEntry] = {}
        self._initialized = False

    # ---- registration ------------------------------------------------------
    def tool(
        self,
        name: str,
        description: str,
        input_schema: Optional[Dict[str, Any]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: register a tool handler.  Returns the function unmodified."""
        schema = input_schema if input_schema is not None else {"type": "object", "properties": {}}

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = _ToolEntry(
                tool=Tool(name=name, description=description, input_schema=schema),
                handler=fn,
            )
            return fn

        return deco

    def resource(
        self,
        uri: str,
        name: str,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
        def deco(fn: Callable[[], Any]) -> Callable[[], Any]:
            self._resources[uri] = _ResourceEntry(
                uri=uri,
                name=name,
                description=description,
                mime_type=mime_type,
                handler=fn,
            )
            return fn

        return deco

    def prompt(
        self,
        name: str,
        description: str = "",
        arguments: Optional[List[Dict[str, Any]]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        args = list(arguments or [])

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._prompts[name] = _PromptEntry(
                name=name,
                description=description,
                arguments=args,
                handler=fn,
            )
            return fn

        return deco

    # ---- dispatch ----------------------------------------------------------
    def handle_message(self, msg: Dict[str, Any]) -> Optional[JsonRpcResponse]:
        """Dispatch one decoded JSON-RPC request.  Returns the response, or
        None for notifications (no ``id`` field)."""
        try:
            req = JsonRpcRequest.from_dict(msg)
        except Exception as e:
            return JsonRpcResponse(
                id=msg.get("id"),
                error=JsonRpcError(code=INVALID_REQUEST, message=str(e)),
            )

        method = req.method
        params = req.params or {}

        try:
            if method == "initialize":
                result = self._on_initialize(params)
            elif method == "initialized" or method == "notifications/initialized":
                # client→server ack; no response (notification)
                self._initialized = True
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self._on_tools_list()
            elif method == "tools/call":
                result = self._on_tools_call(params)
            elif method == "resources/list":
                result = self._on_resources_list()
            elif method == "resources/read":
                result = self._on_resources_read(params)
            elif method == "prompts/list":
                result = self._on_prompts_list()
            elif method == "prompts/get":
                result = self._on_prompts_get(params)
            elif method == "shutdown":
                # graceful shutdown handshake — reply, caller closes stdin
                result = {}
            else:
                if req.is_notification:
                    return None
                return JsonRpcResponse(
                    id=req.id,
                    error=JsonRpcError(
                        code=METHOD_NOT_FOUND,
                        message=f"method not found: {method!r}",
                    ),
                )
        except _RpcException as e:
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(code=e.code, message=e.message, data=e.data),
            )
        except Exception as e:  # noqa: BLE001
            return JsonRpcResponse(
                id=req.id,
                error=JsonRpcError(
                    code=INTERNAL_ERROR,
                    message=f"internal error: {e}",
                    data={"trace": traceback.format_exc(limit=2)},
                ),
            )

        if req.is_notification:
            return None
        return JsonRpcResponse(id=req.id, result=result)

    # ---- method handlers ---------------------------------------------------
    def _on_initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Per MCP spec: server returns protocolVersion + capabilities + serverInfo.
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _on_tools_list(self) -> Dict[str, Any]:
        return {"tools": [e.tool.to_dict() for e in self._tools.values()]}

    def _on_tools_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcException(INVALID_PARAMS, "tools/call: 'name' missing or not a string")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise _RpcException(INVALID_PARAMS, "tools/call: 'arguments' must be an object")
        entry = self._tools.get(name)
        if entry is None:
            raise _RpcException(METHOD_NOT_FOUND, f"unknown tool: {name!r}")

        # Validate required arguments from the input schema (shallow check).
        schema = entry.tool.input_schema or {}
        required = schema.get("required") or []
        missing = [k for k in required if k not in args]
        if missing:
            raise _RpcException(
                INVALID_PARAMS,
                f"tools/call: missing required argument(s) for {name!r}: {missing}",
            )

        try:
            raw = entry.handler(**args)
        except TypeError as e:
            # mismatched kwargs etc.
            raise _RpcException(INVALID_PARAMS, f"tool {name!r} args invalid: {e}")
        except Exception as e:
            # Per MCP spec, tool execution errors should be returned as a
            # ToolResult with isError=True, *not* as a JSON-RPC error.  This
            # lets the LLM see the error message in-band.
            return ToolResult.error_result(f"{type(e).__name__}: {e}").to_dict()

        result = _coerce_tool_result(raw)
        return result.to_dict()

    def _on_resources_list(self) -> Dict[str, Any]:
        return {
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mimeType": r.mime_type,
                }
                for r in self._resources.values()
            ]
        }

    def _on_resources_read(self, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise _RpcException(INVALID_PARAMS, "resources/read: 'uri' missing or not a string")
        entry = self._resources.get(uri)
        if entry is None:
            raise _RpcException(RESOURCE_NOT_FOUND, f"unknown resource: {uri!r}")
        text = entry.handler()
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        return {
            "contents": [
                {"uri": uri, "mimeType": entry.mime_type, "text": text},
            ]
        }

    def _on_prompts_list(self) -> Dict[str, Any]:
        return {
            "prompts": [
                {"name": p.name, "description": p.description, "arguments": list(p.arguments)}
                for p in self._prompts.values()
            ]
        }

    def _on_prompts_get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise _RpcException(INVALID_PARAMS, "prompts/get: 'name' missing or not a string")
        args = params.get("arguments") or {}
        entry = self._prompts.get(name)
        if entry is None:
            raise _RpcException(PROMPT_NOT_FOUND, f"unknown prompt: {name!r}")
        rendered = entry.handler(**args) if args else entry.handler()
        if isinstance(rendered, str):
            messages = [{"role": "user", "content": {"type": "text", "text": rendered}}]
        elif isinstance(rendered, list):
            messages = rendered
        else:
            raise _RpcException(INTERNAL_ERROR, "prompt handler must return str or list")
        return {"description": entry.description, "messages": messages}

    # ---- I/O loop ----------------------------------------------------------
    def serve_stdio(
        self,
        stdin: Optional[IO[bytes]] = None,
        stdout: Optional[IO[bytes]] = None,
    ) -> None:
        """Read JSON-RPC messages from stdin, dispatch, write to stdout.

        Returns when stdin reaches EOF.  Safe to call repeatedly if the
        underlying streams have been reset (e.g. in tests via BytesIO pipes).
        """
        sin = stdin if stdin is not None else sys.stdin.buffer
        sout = stdout if stdout is not None else sys.stdout.buffer

        while True:
            try:
                msg = decode_message(sin)
            except ValueError as e:
                # Parse error → reply with id=null (we have no id)
                resp = JsonRpcResponse(
                    id=None,
                    error=JsonRpcError(code=PARSE_ERROR, message=str(e)),
                )
                sout.write(encode_message(resp))
                sout.flush()
                continue
            if msg is None:
                return  # EOF
            resp = self.handle_message(msg)
            if resp is not None:
                sout.write(encode_message(resp))
                sout.flush()


# -- helpers -----------------------------------------------------------------
class _RpcException(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _coerce_tool_result(raw: Any) -> ToolResult:
    """Accept str / dict / list / ToolResult and produce a ToolResult."""
    if isinstance(raw, ToolResult):
        return raw
    if isinstance(raw, str):
        return ToolResult.text_result(raw)
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        return ToolResult.text_result(text, structured=raw)
    # numbers / bools / None — stringify
    return ToolResult.text_result(json.dumps(raw, ensure_ascii=False))
