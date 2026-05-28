"""mcp_servers.user_server — query users and authenticate.

Tools
-----
  * ``query_user(user_id)``                  -> user profile
  * ``authenticate(email_or_phone, secret)`` -> auth result + user_id
"""
from __future__ import annotations

from . import _path_bootstrap  # noqa: F401
_path_bootstrap.ensure_seagent_on_path()

from seagent.mcp import MCPServer  # noqa: E402


_USERS = {
    "U-100": {"user_id": "U-100", "name": "Alice Chen",   "email": "alice@example.com",  "phone": "+1-555-0100", "tier": "gold"},
    "U-101": {"user_id": "U-101", "name": "Bob Smith",    "email": "bob@example.com",    "phone": "+1-555-0101", "tier": "silver"},
    "U-102": {"user_id": "U-102", "name": "Carol Jiang",  "email": "carol@example.com",  "phone": "+1-555-0102", "tier": "bronze"},
}
# pretend-hashed secrets keyed by email
_SECRETS = {
    "alice@example.com": "pw-alice",
    "bob@example.com":   "pw-bob",
    "carol@example.com": "pw-carol",
}


def _find_by_email_or_phone(s: str):
    s = (s or "").strip().lower()
    for u in _USERS.values():
        if u["email"].lower() == s or u["phone"] == s:
            return u
    return None


def build_server() -> MCPServer:
    srv = MCPServer(name="user", version="1.0.0")

    @srv.tool(
        name="query_user",
        description="Look up a user profile by id.",
        input_schema={
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    )
    def query_user(user_id: str):
        u = _USERS.get(user_id)
        if u is None:
            return {"found": False, "user_id": user_id}
        return {"found": True, **u}

    @srv.tool(
        name="authenticate",
        description="Verify a user by email-or-phone and a shared secret.  "
                    "Returns ``{authenticated: bool, user_id?: str}``.",
        input_schema={
            "type": "object",
            "properties": {
                "email_or_phone": {"type": "string"},
                "secret":         {"type": "string"},
            },
            "required": ["email_or_phone", "secret"],
        },
    )
    def authenticate(email_or_phone: str, secret: str):
        u = _find_by_email_or_phone(email_or_phone)
        if u is None:
            return {"authenticated": False, "reason": "user_not_found"}
        if _SECRETS.get(u["email"].lower()) != secret:
            return {"authenticated": False, "reason": "bad_secret"}
        return {"authenticated": True, "user_id": u["user_id"], "tier": u["tier"]}

    return srv


def main() -> None:
    build_server().serve_stdio()


if __name__ == "__main__":
    main()
