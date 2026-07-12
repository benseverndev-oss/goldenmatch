"""Request-scoped MCP session id, threaded to tool handlers via a ContextVar
(so dispatch signatures stay `(name, args)`)."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "goldenmatch_mcp_session_id", default=None
)


def set_current_session_id(sid: str | None) -> Token:
    return _CURRENT_SESSION_ID.set(sid)


def reset_current_session_id(token: Token) -> None:
    _CURRENT_SESSION_ID.reset(token)


def current_session_id() -> str | None:
    return _CURRENT_SESSION_ID.get()


def session_key_from_context(server: Any) -> str | None:
    """Stable per-connection key for the active MCP session, or None.

    `id(session)` is stable for the life of one Streamable-HTTP connection and
    distinct across connections -- the isolation boundary we need. Returns None
    outside an active request (request_context raises LookupError) or when there
    is no session (stdio / standalone global path)."""
    try:
        ctx = server.request_context
        sess = getattr(ctx, "session", None)
        return f"sess-{id(sess)}" if sess is not None else None
    except Exception:
        return None
