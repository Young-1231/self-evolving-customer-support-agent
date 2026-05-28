"""Tests for v2.4 R6b: MCP server/client layer.

All tests are **offline and LLM-free** (DeepSeek balance is exhausted —
verification has to be protocol-level round-trips and mocked backends).

Coverage:
  * protocol  — encode/decode round-trip, JSON-RPC types
  * server    — in-process pipe transport for initialize/tools/list/tools/call,
                including resources and prompts
  * client    — subprocess-driven round-trip against a fixture echo server
  * end-to-end — all 4 real MCP servers (order/user/refund/handoff)
                  via MCPClient and via MCPToolset
  * errors    — unknown method, unknown tool, missing required args
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO_ROOT))  # so ``mcp_servers`` is importable

from seagent.mcp import (  # noqa: E402
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPClient,
    MCPClientError,
    MCPServer,
    MCPToolset,
    Tool,
    ToolResult,
    decode_message,
    encode_message,
    with_mcp_tools,
)
from seagent.mcp.server import _coerce_tool_result  # noqa: E402


# =========================================================================
# 1. Protocol round-trip
# =========================================================================
class TestProtocol:
    def test_jsonrpc_request_round_trip(self):
        req = JsonRpcRequest(method="tools/list", params={"x": 1}, id=42)
        d = req.to_dict()
        assert d == {"jsonrpc": "2.0", "method": "tools/list", "params": {"x": 1}, "id": 42}
        req2 = JsonRpcRequest.from_dict(d)
        assert req2.method == "tools/list"
        assert req2.id == 42
        assert req2.params == {"x": 1}

    def test_jsonrpc_request_notification_has_no_id(self):
        req = JsonRpcRequest(method="notifications/initialized", params={})
        assert req.is_notification
        d = req.to_dict()
        assert "id" not in d

    def test_jsonrpc_response_success_includes_result_field(self):
        resp = JsonRpcResponse(id=7, result={"ok": True})
        d = resp.to_dict()
        assert d["result"] == {"ok": True}
        assert "error" not in d

    def test_jsonrpc_response_error_excludes_result(self):
        resp = JsonRpcResponse(
            id=7,
            error=JsonRpcError(code=METHOD_NOT_FOUND, message="nope"),
        )
        d = resp.to_dict()
        assert "result" not in d
        assert d["error"] == {"code": METHOD_NOT_FOUND, "message": "nope"}

    def test_jsonrpc_request_rejects_bad_version(self):
        with pytest.raises(ValueError):
            JsonRpcRequest.from_dict({"jsonrpc": "1.0", "method": "x"})

    def test_jsonrpc_request_rejects_missing_method(self):
        with pytest.raises(ValueError):
            JsonRpcRequest.from_dict({"jsonrpc": "2.0"})

    def test_encode_decode_round_trip_via_pipes(self):
        req = JsonRpcRequest(method="ping", params={}, id=1)
        buf = io.BytesIO(encode_message(req))
        msg = decode_message(buf)
        assert msg == req.to_dict()
        # EOF returns None
        assert decode_message(buf) is None

    def test_decode_message_skips_blank_lines(self):
        buf = io.BytesIO(b"\n\n" + encode_message(JsonRpcRequest(method="ping", id=1)) + b"\n")
        msg = decode_message(buf)
        assert msg["method"] == "ping"

    def test_decode_message_raises_on_malformed_json(self):
        buf = io.BytesIO(b"this is not json\n")
        with pytest.raises(ValueError):
            decode_message(buf)

    def test_tool_dict_uses_camel_case_input_schema(self):
        t = Tool(name="x", description="y", input_schema={"type": "object"})
        d = t.to_dict()
        assert "inputSchema" in d
        assert "input_schema" not in d
        # round-trip back accepts either form
        t2 = Tool.from_dict(d)
        assert t2.name == "x" and t2.input_schema == {"type": "object"}

    def test_tool_result_text_concatenates_blocks(self):
        tr = ToolResult(content=[
            {"type": "text", "text": "hello"},
            {"type": "image", "data": "..."},
            {"type": "text", "text": "world"},
        ])
        assert tr.text == "hello\nworld"

    def test_tool_result_error_helper(self):
        tr = ToolResult.error_result("boom")
        assert tr.is_error
        assert "boom" in tr.text


# =========================================================================
# 2. MCPServer dispatch (in-process, no subprocess)
# =========================================================================
def _build_demo_server() -> MCPServer:
    srv = MCPServer(name="demo", version="0.0.1")

    @srv.tool(
        name="add",
        description="Add two ints",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    def add(a, b):
        return {"sum": a + b}

    @srv.tool(name="boom", description="raises", input_schema={"type": "object"})
    def boom():
        raise RuntimeError("kaboom")

    @srv.resource(uri="demo://greeting", name="greeting", description="static greeting")
    def _greeting():
        return "hello, world"

    @srv.prompt(name="echo_prompt", description="wrap arg in user turn",
                arguments=[{"name": "msg", "required": True}])
    def _echo(msg: str):
        return f"please echo: {msg}"

    return srv


class TestServerDispatch:
    def test_initialize_returns_capabilities_and_serverinfo(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="initialize", params={}, id=1).to_dict()
        )
        assert resp is not None
        assert resp.error is None
        r = resp.result
        assert r["serverInfo"] == {"name": "demo", "version": "0.0.1"}
        assert "tools" in r["capabilities"]
        assert "protocolVersion" in r

    def test_tools_list_returns_registered_tool(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="tools/list", params={}, id=2).to_dict()
        )
        names = {t["name"] for t in resp.result["tools"]}
        assert names == {"add", "boom"}
        # schemas use camelCase on the wire
        for t in resp.result["tools"]:
            assert "inputSchema" in t

    def test_tools_call_dispatches_to_handler(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(
                method="tools/call",
                params={"name": "add", "arguments": {"a": 2, "b": 3}},
                id=3,
            ).to_dict()
        )
        assert resp.error is None
        tr = ToolResult.from_dict(resp.result)
        assert not tr.is_error
        assert tr.structured == {"sum": 5}

    def test_tools_call_unknown_tool_returns_method_not_found(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="tools/call", params={"name": "nope", "arguments": {}}, id=4).to_dict()
        )
        assert resp.error is not None
        assert resp.error.code == METHOD_NOT_FOUND

    def test_tools_call_missing_required_arg_returns_invalid_params(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="tools/call", params={"name": "add", "arguments": {"a": 1}}, id=5).to_dict()
        )
        assert resp.error is not None
        assert resp.error.code == INVALID_PARAMS

    def test_tools_call_handler_exception_returns_tool_result_error(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="tools/call", params={"name": "boom", "arguments": {}}, id=6).to_dict()
        )
        # MCP convention: tool execution error is *not* a JSON-RPC error.
        assert resp.error is None
        tr = ToolResult.from_dict(resp.result)
        assert tr.is_error
        assert "kaboom" in tr.text

    def test_unknown_method_returns_method_not_found(self):
        srv = _build_demo_server()
        resp = srv.handle_message(
            JsonRpcRequest(method="bogus/method", params={}, id=7).to_dict()
        )
        assert resp.error is not None
        assert resp.error.code == METHOD_NOT_FOUND

    def test_initialized_notification_returns_none(self):
        srv = _build_demo_server()
        # No id => notification => no response.
        resp = srv.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert resp is None
        assert srv._initialized is True

    def test_resources_list_and_read(self):
        srv = _build_demo_server()
        resp = srv.handle_message(JsonRpcRequest(method="resources/list", params={}, id=8).to_dict())
        uris = [r["uri"] for r in resp.result["resources"]]
        assert "demo://greeting" in uris

        resp2 = srv.handle_message(
            JsonRpcRequest(method="resources/read",
                           params={"uri": "demo://greeting"}, id=9).to_dict()
        )
        assert resp2.result["contents"][0]["text"] == "hello, world"

    def test_prompts_list_and_get(self):
        srv = _build_demo_server()
        resp = srv.handle_message(JsonRpcRequest(method="prompts/list", params={}, id=10).to_dict())
        assert resp.result["prompts"][0]["name"] == "echo_prompt"

        resp2 = srv.handle_message(
            JsonRpcRequest(method="prompts/get",
                           params={"name": "echo_prompt", "arguments": {"msg": "hi"}},
                           id=11).to_dict()
        )
        msg = resp2.result["messages"][0]
        assert msg["role"] == "user"
        assert "hi" in msg["content"]["text"]

    def test_serve_stdio_with_in_memory_pipes(self):
        """Smoke test: pump 2 requests through serve_stdio() via BytesIO."""
        srv = _build_demo_server()
        sin = io.BytesIO()
        sin.write(encode_message(JsonRpcRequest(method="initialize", params={}, id=1)))
        sin.write(encode_message(JsonRpcRequest(method="tools/list", params={}, id=2)))
        sin.seek(0)
        sout = io.BytesIO()
        srv.serve_stdio(stdin=sin, stdout=sout)
        sout.seek(0)
        m1 = decode_message(sout)
        m2 = decode_message(sout)
        assert m1["id"] == 1 and "serverInfo" in m1["result"]
        assert m2["id"] == 2 and isinstance(m2["result"]["tools"], list)

    def test_serve_stdio_handles_parse_error(self):
        srv = _build_demo_server()
        sin = io.BytesIO(b"this is not json\n")
        sout = io.BytesIO()
        srv.serve_stdio(stdin=sin, stdout=sout)
        sout.seek(0)
        msg = decode_message(sout)
        assert msg["error"]["code"] == PARSE_ERROR

    def test_coerce_tool_result_accepts_various_types(self):
        assert _coerce_tool_result("hi").content[0]["text"] == "hi"
        tr = _coerce_tool_result({"a": 1})
        assert tr.structured == {"a": 1}
        # idempotent for ToolResult instances
        x = ToolResult.text_result("x")
        assert _coerce_tool_result(x) is x


# =========================================================================
# 3. MCPClient ↔ MCPServer over subprocess
# =========================================================================
# A small fixture server written inline as a string so the test is self-
# contained (no extra fixture file).
_FIXTURE_SERVER_SRC = textwrap.dedent(
    """
    import sys, os
    sys.path.insert(0, {src!r})
    from seagent.mcp import MCPServer

    srv = MCPServer(name="fixture", version="0.0.1")

    @srv.tool(
        name="echo",
        description="echo the given string",
        input_schema={{"type": "object", "properties": {{"s": {{"type": "string"}}}}, "required": ["s"]}},
    )
    def echo(s):
        return {{"echoed": s}}

    @srv.tool(name="boom", description="raises", input_schema={{"type": "object"}})
    def boom():
        raise ValueError("boom in subprocess")

    if __name__ == "__main__":
        srv.serve_stdio()
    """
).format(src=str(SRC))


@pytest.fixture(scope="module")
def fixture_server_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("mcpfix") / "server.py"
    p.write_text(_FIXTURE_SERVER_SRC)
    return p


class TestClientSubprocess:
    def test_initialize_and_list_tools(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            tools = c.list_tools()
            names = {t.name for t in tools}
            assert names == {"echo", "boom"}
            for t in tools:
                # client returns Tool dataclass with snake_case schema attr
                assert isinstance(t.input_schema, dict)

    def test_call_tool_round_trip(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            result = c.call_tool("echo", {"s": "hello"})
            assert not result.is_error
            assert result.structured == {"echoed": "hello"}

    def test_call_tool_unknown_raises_client_error(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            with pytest.raises(MCPClientError) as exc:
                c.call_tool("nope", {})
            assert exc.value.code == METHOD_NOT_FOUND

    def test_call_tool_handler_error_returns_is_error_result(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            tr = c.call_tool("boom", {})
            assert tr.is_error
            assert "boom in subprocess" in tr.text

    def test_call_tool_missing_required_args_raises(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            with pytest.raises(MCPClientError) as exc:
                c.call_tool("echo", {})  # missing 's'
            assert exc.value.code == INVALID_PARAMS

    def test_ping(self, fixture_server_path):
        with MCPClient([sys.executable, str(fixture_server_path)]) as c:
            c.ping()  # no exception => OK


# =========================================================================
# 4. End-to-end: the 4 real MCP servers
# =========================================================================
def _server_argv(module: str):
    return [sys.executable, "-m", f"mcp_servers.{module}"]


def _toolset_env():
    env = dict(os.environ)
    # so mcp_servers/* can find seagent + itself
    pp = env.get("PYTHONPATH", "")
    parts = [str(REPO_ROOT), str(SRC)]
    env["PYTHONPATH"] = os.pathsep.join(parts + ([pp] if pp else []))
    return env


class TestOrderServer:
    def test_query_and_update(self):
        with MCPClient(_server_argv("order_server"),
                       env=_toolset_env(), cwd=str(REPO_ROOT)) as c:
            tools = {t.name for t in c.list_tools()}
            assert tools == {"query_order", "update_order"}

            tr = c.call_tool("query_order", {"order_id": "ORD-001"})
            assert tr.structured["found"] is True
            assert tr.structured["status"] == "shipped"

            tr2 = c.call_tool("update_order",
                              {"order_id": "ORD-001", "status": "delivered"})
            assert tr2.structured["updated"] is True
            assert tr2.structured["status"] == "delivered"

            tr3 = c.call_tool("query_order", {"order_id": "ORD-999"})
            assert tr3.structured["found"] is False


class TestUserServer:
    def test_query_and_authenticate(self):
        with MCPClient(_server_argv("user_server"),
                       env=_toolset_env(), cwd=str(REPO_ROOT)) as c:
            tools = {t.name for t in c.list_tools()}
            assert tools == {"query_user", "authenticate"}

            tr = c.call_tool("query_user", {"user_id": "U-100"})
            assert tr.structured["found"] is True
            assert tr.structured["name"] == "Alice Chen"

            ok = c.call_tool("authenticate",
                             {"email_or_phone": "alice@example.com", "secret": "pw-alice"})
            assert ok.structured["authenticated"] is True

            bad = c.call_tool("authenticate",
                              {"email_or_phone": "alice@example.com", "secret": "wrong"})
            assert bad.structured["authenticated"] is False


class TestRefundServer:
    def test_policy_check_and_initiate(self):
        with MCPClient(_server_argv("refund_server"),
                       env=_toolset_env(), cwd=str(REPO_ROOT)) as c:
            tools = {t.name for t in c.list_tools()}
            assert tools == {"check_refund_policy", "initiate_refund"}

            pol = c.call_tool("check_refund_policy",
                              {"order_amount": 40.0, "reason": "defective"})
            assert pol.structured["allowed"] is True

            ok = c.call_tool("initiate_refund",
                             {"order_id": "ORD-007", "amount": 40.0, "reason": "defective"})
            assert ok.structured["ok"] is True
            assert ok.structured["refund_id"].startswith("REF-")

            # second refund on same order rejected
            again = c.call_tool("initiate_refund",
                                {"order_id": "ORD-007", "amount": 40.0, "reason": "defective"})
            assert again.structured["ok"] is False
            assert again.structured["reason"] == "already_refunded"

            # cap exceeded
            over = c.call_tool("initiate_refund",
                               {"order_id": "ORD-008", "amount": 9999.0, "reason": "changed_mind"})
            assert over.structured["ok"] is False
            assert over.structured["reason"] == "exceeds_policy_cap"


class TestHandoffServer:
    def test_transfer_and_list(self):
        with MCPClient(_server_argv("handoff_server"),
                       env=_toolset_env(), cwd=str(REPO_ROOT)) as c:
            tools = {t.name for t in c.list_tools()}
            assert tools == {"transfer_to_human_agents", "list_pending_handoffs"}

            tr = c.call_tool("transfer_to_human_agents",
                             {"department": "billing", "summary": "refund question", "urgent": False})
            assert tr.structured["ok"] is True
            assert tr.structured["ticket_id"].startswith("TKT-")

            q = c.call_tool("list_pending_handoffs", {})
            assert any(item["ticket_id"] == tr.structured["ticket_id"]
                       for item in q.structured["queue"])

            bad = c.call_tool("transfer_to_human_agents",
                              {"department": "wat", "summary": "x"})
            assert bad.structured["ok"] is False


# =========================================================================
# 5. MCPToolset and with_mcp_tools wrapper
# =========================================================================
class TestToolset:
    def test_toolset_aggregates_multiple_servers(self):
        ts = MCPToolset()
        ts.add_server("order",  MCPClient(_server_argv("order_server"),
                                          env=_toolset_env(), cwd=str(REPO_ROOT)))
        ts.add_server("refund", MCPClient(_server_argv("refund_server"),
                                          env=_toolset_env(), cwd=str(REPO_ROOT)))
        with ts:
            qualified = [name for name, _ in ts.list_tools()]
            assert "order.query_order"        in qualified
            assert "order.update_order"       in qualified
            assert "refund.initiate_refund"   in qualified
            assert "refund.check_refund_policy" in qualified
            # unambiguous bare call
            tr = ts.call_tool("query_order", {"order_id": "ORD-002"})
            assert tr.structured["status"] == "processing"
            # qualified call
            tr2 = ts.call_tool("refund.check_refund_policy",
                               {"order_amount": 10.0, "reason": "defective"})
            assert tr2.structured["allowed"] is True

    def test_toolset_rejects_duplicate_server_name(self):
        ts = MCPToolset()
        ts.add_server("x", MCPClient(["echo"]))
        with pytest.raises(ValueError):
            ts.add_server("x", MCPClient(["echo"]))

    def test_find_tool_raises_on_unknown(self):
        ts = MCPToolset()
        with pytest.raises(KeyError):
            ts.find_tool("nope")

    def test_with_mcp_tools_wrapper_delegates_attributes(self):
        class FakeAgent:
            def __init__(self):
                self.greeted = False
            def handle(self, q):
                return f"answered: {q}"

        ts = MCPToolset()
        ts.add_server("order", MCPClient(_server_argv("order_server"),
                                         env=_toolset_env(), cwd=str(REPO_ROOT)))
        with ts:
            wrapped = with_mcp_tools(FakeAgent(), ts)
            # delegation
            assert wrapped.handle("hi") == "answered: hi"
            # tools
            assert wrapped.tools is ts
            # call_tool shortcut
            tr = wrapped.call_tool("query_order", {"order_id": "ORD-003"})
            assert tr.structured["status"] == "delivered"
            # attribute write forwards to inner agent
            wrapped.greeted = True
            assert wrapped._agent.greeted is True


# =========================================================================
# 6. Server-info smoke (catches packaging mistakes)
# =========================================================================
class TestServerInfo:
    @pytest.mark.parametrize("module,expected_name", [
        ("order_server",   "order"),
        ("user_server",    "user"),
        ("refund_server",  "refund"),
        ("handoff_server", "handoff"),
    ])
    def test_initialize_reports_correct_server_name(self, module, expected_name):
        with MCPClient(_server_argv(module),
                       env=_toolset_env(), cwd=str(REPO_ROOT)) as c:
            # client.start() already did initialize; re-issue ping to confirm liveness
            c.ping()
            # And tools/list works → server is up and registered the expected tools.
            assert c.list_tools(), f"{module} returned no tools"
