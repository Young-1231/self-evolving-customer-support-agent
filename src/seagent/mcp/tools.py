"""MCPToolset — combine multiple MCPClient instances under one name registry,
and ``with_mcp_tools`` — a non-invasive wrapper that attaches the toolset to
an existing SupportAgent or SpecialistAgent.

Design constraint (v2.4 R6b)
----------------------------
We are not allowed to modify ``seagent/agent/support_agent.py`` or
``seagent/multi_agent/specialist.py``.  Those classes are frozen at v2.3
(commit afda93d) so the c21/v2.1/v2.2/v2.3 numerical reproductions stay
bit-identical.

Therefore the integration is **composition, not inheritance**:

  * ``MCPToolset`` is a pure registry.  Tools are namespaced as
    ``<server_name>.<tool_name>`` to avoid collisions when two servers expose
    the same tool name (e.g. two CRMs both have ``query_user``).
  * ``with_mcp_tools(agent, toolset)`` returns a thin proxy that
    ``__getattr__``-delegates everything to the wrapped agent and adds
    ``.tools`` plus a ``call_tool`` method.  The proxy is duck-type-
    compatible with the original agent — orchestrator code that only ever
    calls ``agent.handle(query)`` works unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .client import MCPClient, MCPClientError
from .protocol import Tool, ToolResult


class MCPToolset:
    """Aggregates multiple MCP clients into a single tool registry.

    Tool names are namespaced as ``<server>.<tool>`` to avoid collisions.
    A non-namespaced ``call_tool("query_order", ...)`` is also supported
    when the name is unambiguous (raises if two servers both expose it).
    """

    def __init__(self) -> None:
        self._clients: Dict[str, MCPClient] = {}
        # tool_name -> list of server names that own a tool with this name
        self._owners: Dict[str, List[str]] = {}
        # ``<server>.<tool>`` -> Tool descriptor
        self._catalog: Dict[str, Tool] = {}
        self._started = False

    # ---- registration ------------------------------------------------------
    def add_server(self, server_name: str, client: MCPClient) -> None:
        if server_name in self._clients:
            raise ValueError(f"server_name {server_name!r} already registered")
        self._clients[server_name] = client

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Start every registered client and build the tool catalog."""
        if self._started:
            return
        for server_name, client in self._clients.items():
            client.start()
            for t in client.list_tools():
                key = f"{server_name}.{t.name}"
                self._catalog[key] = t
                self._owners.setdefault(t.name, []).append(server_name)
        self._started = True

    def stop(self) -> None:
        for client in self._clients.values():
            try:
                client.stop()
            except Exception:
                pass
        self._started = False

    def __enter__(self) -> "MCPToolset":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # ---- query -------------------------------------------------------------
    def list_tools(self) -> List[Tuple[str, Tool]]:
        """Return ``[(qualified_name, Tool), ...]`` sorted by qualified name."""
        return sorted(self._catalog.items(), key=lambda kv: kv[0])

    def find_tool(self, name: str) -> Tuple[str, str]:
        """Resolve ``name`` (qualified ``server.tool`` or bare ``tool``) into
        ``(server_name, tool_name)``.  Raises if ambiguous or unknown."""
        if "." in name:
            server, tool = name.split(".", 1)
            if f"{server}.{tool}" not in self._catalog:
                raise KeyError(f"unknown tool: {name!r}")
            return server, tool
        owners = self._owners.get(name) or []
        if not owners:
            raise KeyError(f"unknown tool: {name!r}")
        if len(owners) > 1:
            raise KeyError(
                f"tool {name!r} is ambiguous (provided by: {owners}); "
                f"use the qualified form '<server>.<tool>'"
            )
        return owners[0], name

    # ---- invocation --------------------------------------------------------
    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        server, tool = self.find_tool(name)
        client = self._clients[server]
        return client.call_tool(tool, arguments or {})


# -- non-invasive agent wrapper ---------------------------------------------
class _AgentWithTools:
    """Proxy that delegates everything to the wrapped agent and adds
    ``.tools`` (the MCPToolset) plus a ``call_tool`` shortcut.

    Why a proxy and not subclassing?  The original SupportAgent /
    SpecialistAgent classes are frozen (constraint).  A proxy preserves
    isinstance() checks via duck typing in the orchestrator (the
    orchestrator only does ``getattr(agent, 'handle')(query)``) and lets
    us add capabilities without changing the originals.
    """

    def __init__(self, agent: Any, toolset: MCPToolset) -> None:
        # Use object.__setattr__ to avoid going through __setattr__ proxy
        object.__setattr__(self, "_agent", agent)
        object.__setattr__(self, "tools", toolset)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        return self.tools.call_tool(name, arguments or {})

    # delegation ------------------------------------------------------------
    def __getattr__(self, item: str) -> Any:
        return getattr(self._agent, item)

    def __setattr__(self, key: str, value: Any) -> None:
        # forward attribute writes to the wrapped agent so existing call
        # sites like ``agent._retrieve = ...`` (the specialist's monkey-patch
        # in ``mode='observed'``) still work.
        setattr(self._agent, key, value)

    def __repr__(self) -> str:
        return f"<AgentWithTools agent={self._agent!r} tools={len(self.tools._catalog)}>"


def with_mcp_tools(agent: Any, toolset: MCPToolset) -> Any:
    """Wrap *agent* so it exposes ``agent.tools`` (MCPToolset) and
    ``agent.call_tool(name, args)`` while preserving all other behaviour.

    The toolset must be started (``toolset.start()`` or used as a context
    manager).  We do NOT auto-start it here because the caller usually
    wants to control the subprocess lifecycle (e.g. start once, share
    across many wrapped agents).
    """
    return _AgentWithTools(agent, toolset)
