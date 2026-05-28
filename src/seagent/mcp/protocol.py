"""MCP wire protocol — JSON-RPC 2.0 over NDJSON.

We deliberately keep the data structures small and dependency-free.  No
``pydantic``, no third-party JSON-RPC libs.  If the user installs the
official ``mcp`` package, the *server entry scripts* may opt-in to using it
(see ``mcp_servers/*.py``); the core ``seagent.mcp`` package never imports
third-party modules.

Wire framing
------------
We use **newline-delimited JSON (NDJSON)**: each message is a single JSON
object on one line, terminated by ``\\n``.  No Content-Length header.

Why NDJSON instead of LSP-style Content-Length framing?
    * Easier to debug (you can `cat` a transcript).
    * Compatible with the majority of MCP stdio clients in the wild as of
      2026-05 (the spec allows both; NDJSON is the default for stdio).
    * Implementations that need Content-Length framing for binary blobs
      can wrap our codec — see ``encode_message`` / ``decode_message``.

Standard JSON-RPC error codes (https://www.jsonrpc.org/specification#error_object)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Union, IO


# -- Standard JSON-RPC 2.0 error codes ---------------------------------------
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# -- MCP-specific (server-defined) range: -32000 .. -32099 -------------------
TOOL_EXECUTION_ERROR = -32000
RESOURCE_NOT_FOUND = -32001
PROMPT_NOT_FOUND = -32002


# -- Wire types --------------------------------------------------------------
@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 request.

    A ``id == None`` value means *notification* (no response expected),
    per the JSON-RPC spec.  All MCP requests we support are non-notifications
    except ``initialized`` (a one-shot ack from the client after handshake).
    """
    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Union[int, str]] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JsonRpcRequest":
        if d.get("jsonrpc") != "2.0":
            raise ValueError(f"unsupported jsonrpc version: {d.get('jsonrpc')!r}")
        if "method" not in d or not isinstance(d["method"], str):
            raise ValueError("missing or invalid 'method'")
        return cls(
            method=d["method"],
            params=d.get("params"),
            id=d.get("id"),
        )

    @property
    def is_notification(self) -> bool:
        return self.id is None


@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


@dataclass
class JsonRpcResponse:
    id: Optional[Union[int, str]]
    result: Optional[Any] = None
    error: Optional[JsonRpcError] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error.to_dict()
        else:
            # JSON-RPC 2.0: result MUST be present (even if null) on success
            d["result"] = self.result
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JsonRpcResponse":
        if d.get("jsonrpc") != "2.0":
            raise ValueError(f"unsupported jsonrpc version: {d.get('jsonrpc')!r}")
        err = None
        if "error" in d and d["error"] is not None:
            e = d["error"]
            err = JsonRpcError(
                code=int(e.get("code", INTERNAL_ERROR)),
                message=str(e.get("message", "")),
                data=e.get("data"),
            )
        return cls(
            id=d.get("id"),
            result=d.get("result"),
            error=err,
        )


# -- MCP application-level types ---------------------------------------------
@dataclass
class Tool:
    """An MCP tool descriptor returned by ``tools/list``."""
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    def to_dict(self) -> Dict[str, Any]:
        # MCP wire form uses inputSchema (camelCase). Keep Pythonic snake_case
        # internally and translate on the boundary.
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Tool":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("inputSchema") or d.get("input_schema") or {"type": "object", "properties": {}},
        )


@dataclass
class ToolCall:
    """A tools/call request payload."""
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """A tools/call response payload.

    MCP returns a list of content blocks (text/image/...).  We expose a
    convenience ``text`` property that concatenates all text blocks for
    callers who don't care about multi-modal output.
    """
    content: List[Dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    structured: Optional[Any] = None  # structured_content (optional, 2026 draft)

    @property
    def text(self) -> str:
        out = []
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text", "")))
        return "\n".join(out)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"content": list(self.content), "isError": self.is_error}
        if self.structured is not None:
            d["structuredContent"] = self.structured
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolResult":
        return cls(
            content=list(d.get("content", []) or []),
            is_error=bool(d.get("isError", False)),
            structured=d.get("structuredContent"),
        )

    @classmethod
    def text_result(cls, text: str, structured: Any = None) -> "ToolResult":
        return cls(content=[{"type": "text", "text": text}], structured=structured)

    @classmethod
    def error_result(cls, message: str) -> "ToolResult":
        return cls(content=[{"type": "text", "text": message}], is_error=True)


# -- Encoding / decoding -----------------------------------------------------
def encode_message(obj: Union[JsonRpcRequest, JsonRpcResponse, Dict[str, Any]]) -> bytes:
    """Serialize a JSON-RPC message to its on-the-wire bytes (NDJSON, UTF-8)."""
    if isinstance(obj, (JsonRpcRequest, JsonRpcResponse)):
        payload = obj.to_dict()
    elif isinstance(obj, dict):
        payload = obj
    else:
        raise TypeError(f"cannot encode {type(obj).__name__}")
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (line + "\n").encode("utf-8")


def decode_message(stream: IO[bytes]) -> Optional[Dict[str, Any]]:
    """Read one NDJSON message from *stream*.  Returns None on EOF.

    Skips blank lines silently (some shells inject them).  Raises
    ``ValueError`` for malformed JSON so the caller can map it to
    ``PARSE_ERROR``.
    """
    while True:
        line = stream.readline()
        if not line:
            return None
        s = line.decode("utf-8", errors="replace").strip()
        if not s:
            continue
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed JSON-RPC line: {e}: {s[:200]!r}") from e
