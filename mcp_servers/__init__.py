"""mcp_servers/ — runnable MCP server entry points.

Each module in this package can be invoked via
``python -m mcp_servers.<name>`` to start a stdio MCP server.

Currently shipped:
  * ``order_server``    — order lookup and status updates
  * ``user_server``     — user profile lookup and authentication
  * ``refund_server``   — refund initiation with policy check
  * ``handoff_server``  — transfer-to-human-agent queue

All servers use mocked in-process state.  Swap the mocked backends for
real Zendesk / Intercom / CRM calls when wiring to production.
"""
