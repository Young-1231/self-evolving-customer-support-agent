"""mcp_servers.order_server — query and update orders.

Mocked in-process order DB.  In production, swap ``_DB`` for a Zendesk
ticket lookup, Shopify Admin API call, or your own CRM client.

Tools
-----
  * ``query_order(order_id)`` -> order record
  * ``update_order(order_id, status)`` -> updated record

Both tools accept JSON Schema-validated input (shallow check in MCPServer).

Run::

    python -m mcp_servers.order_server
"""
from __future__ import annotations

from . import _path_bootstrap  # noqa: F401 — side effect: prepend src/ to sys.path

_path_bootstrap.ensure_seagent_on_path()

from seagent.mcp import MCPServer  # noqa: E402


# -- mocked backend ----------------------------------------------------------
_DB = {
    "ORD-001": {"order_id": "ORD-001", "user_id": "U-100", "status": "shipped",
                "items": ["Widget A"], "amount": 49.99, "currency": "USD"},
    "ORD-002": {"order_id": "ORD-002", "user_id": "U-101", "status": "processing",
                "items": ["Widget B", "Widget C"], "amount": 89.50, "currency": "USD"},
    "ORD-003": {"order_id": "ORD-003", "user_id": "U-102", "status": "delivered",
                "items": ["Subscription Pro"], "amount": 120.00, "currency": "USD"},
    "ORD-404": None,  # canonical "not found" id for tests
}
_ALLOWED_STATUSES = {"processing", "shipped", "delivered", "cancelled", "returned"}


def build_server() -> MCPServer:
    srv = MCPServer(name="order", version="1.0.0")

    @srv.tool(
        name="query_order",
        description="Look up an order by its id.  Returns order record or an error.",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order identifier, e.g. ORD-001."},
            },
            "required": ["order_id"],
        },
    )
    def query_order(order_id: str):
        rec = _DB.get(order_id)
        if rec is None:
            return {"found": False, "order_id": order_id}
        return {"found": True, **rec}

    @srv.tool(
        name="update_order",
        description="Update the status of an order.  Allowed statuses: "
                    "processing, shipped, delivered, cancelled, returned.",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(_ALLOWED_STATUSES)},
            },
            "required": ["order_id", "status"],
        },
    )
    def update_order(order_id: str, status: str):
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"invalid status: {status!r}; allowed: {sorted(_ALLOWED_STATUSES)}")
        rec = _DB.get(order_id)
        if rec is None:
            return {"updated": False, "order_id": order_id, "reason": "not_found"}
        rec["status"] = status
        return {"updated": True, **rec}

    return srv


def main() -> None:
    build_server().serve_stdio()


if __name__ == "__main__":
    main()
