"""MCPClient — subprocess-based JSON-RPC 2.0 client over stdio.

Usage::

    from seagent.mcp import MCPClient

    with MCPClient(["python", "-m", "mcp_servers.order_server"]) as c:
        tools = c.list_tools()
        result = c.call_tool("query_order", {"order_id": "ORD-001"})
        print(result.text)

The client is **synchronous** (one in-flight request at a time).  MCP
permits concurrent ids; we don't need it for our agent's single-thread
tool-calling.  Bump to a thread + futures map if you need pipelining.

Stderr from the server is captured into ``MCPClient.stderr_log`` (a
deque of the last N lines), which is invaluable for debugging.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Sequence

from .protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Tool,
    ToolResult,
    decode_message,
    encode_message,
)


class MCPClientError(Exception):
    """Raised when the server returns a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """JSON-RPC 2.0 client that drives an MCP server subprocess over stdio.

    Parameters
    ----------
    server_cmd:
        argv list, e.g. ``["python", "-m", "mcp_servers.order_server"]``.
    env:
        optional dict added on top of the current environment.
    cwd:
        optional working directory.
    client_info:
        ``{"name": ..., "version": ...}`` reported in the ``initialize`` handshake.
    stderr_buffer_lines:
        keep this many tail lines of server stderr for diagnostics.
    """

    def __init__(
        self,
        server_cmd: Sequence[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        client_info: Optional[Dict[str, str]] = None,
        stderr_buffer_lines: int = 200,
        startup_timeout: float = 10.0,
    ) -> None:
        if not server_cmd:
            raise ValueError("server_cmd must be a non-empty argv list")
        self.server_cmd = list(server_cmd)
        self._env_extra = dict(env or {})
        self.cwd = cwd
        self.client_info = dict(client_info or {"name": "seagent.mcp.MCPClient", "version": "0.1.0"})
        self.startup_timeout = startup_timeout

        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.stderr_log: "deque[str]" = deque(maxlen=stderr_buffer_lines)
        self._stderr_thread: Optional[threading.Thread] = None
        self._stopped = False

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> Dict[str, Any]:
        """Launch the server subprocess and run the MCP initialize handshake.

        Returns the server's ``initialize`` result (capabilities + serverInfo).
        """
        if self._proc is not None:
            raise RuntimeError("MCPClient already started")
        env = dict(os.environ)
        env.update(self._env_extra)
        # Force unbuffered Python on the server side so we see output promptly.
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._proc = subprocess.Popen(
            self.server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=env,
            bufsize=0,
        )
        # Pump stderr in a background thread (otherwise it can block the server).
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name=f"mcp-stderr-{self.server_cmd[-1]}", daemon=True,
        )
        self._stderr_thread.start()

        # Send initialize
        init_result = self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": self.client_info,
            },
        )
        # Notify initialized (notification, no id)
        self._notify("notifications/initialized", {})
        return init_result

    def stop(self, timeout: float = 2.0) -> None:
        if self._stopped:
            return
        self._stopped = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            self._proc = None

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---- high-level API ----------------------------------------------------
    def list_tools(self) -> List[Tool]:
        result = self._request("tools/list", {})
        return [Tool.from_dict(t) for t in (result.get("tools") or [])]

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return ToolResult.from_dict(result)

    def list_resources(self) -> List[Dict[str, Any]]:
        return list(self._request("resources/list", {}).get("resources") or [])

    def read_resource(self, uri: str) -> List[Dict[str, Any]]:
        return list(self._request("resources/read", {"uri": uri}).get("contents") or [])

    def list_prompts(self) -> List[Dict[str, Any]]:
        return list(self._request("prompts/list", {}).get("prompts") or [])

    def get_prompt(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("prompts/get", {"name": name, "arguments": arguments or {}})

    def ping(self) -> None:
        self._request("ping", {})

    # ---- low-level transport ----------------------------------------------
    def _next_request_id(self) -> int:
        with self._id_lock:
            i = self._next_id
            self._next_id += 1
            return i

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError(
                f"MCP server process is not running "
                f"(returncode={self._proc.returncode if self._proc else None}); "
                f"last stderr lines: {list(self.stderr_log)[-5:]}"
            )
        req_id = self._next_request_id()
        req = JsonRpcRequest(method=method, params=params, id=req_id)
        with self._send_lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(encode_message(req))
            self._proc.stdin.flush()

            assert self._proc.stdout is not None
            # Read until we get a response with the matching id (we ignore
            # any server→client notifications for now).
            while True:
                msg = decode_message(self._proc.stdout)
                if msg is None:
                    raise RuntimeError(
                        f"MCP server closed stdout while waiting for response to "
                        f"{method!r}; last stderr lines: {list(self.stderr_log)[-5:]}"
                    )
                # notifications have no 'id' — drop them
                if "id" not in msg:
                    continue
                if msg.get("id") != req_id:
                    # mismatched id — skip (shouldn't happen in our sync model)
                    continue
                resp = JsonRpcResponse.from_dict(msg)
                if resp.error is not None:
                    raise MCPClientError(resp.error.code, resp.error.message, resp.error.data)
                return resp.result

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        req = JsonRpcRequest(method=method, params=params, id=None)
        with self._send_lock:
            self._proc.stdin.write(encode_message(req))
            self._proc.stdin.flush()

    def _pump_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, b""):
                try:
                    s = line.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    s = repr(line)
                self.stderr_log.append(s)
        except Exception:
            pass
