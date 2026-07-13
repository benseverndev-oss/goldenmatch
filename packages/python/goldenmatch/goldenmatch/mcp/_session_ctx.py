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
    """Stable, unique-per-session key for the active MCP session, or None.

    Uses a uuid cached on the session object (lives exactly as long as the
    session, and -- unlike id() -- is never reused after GC, so a stored run
    can't leak to a later connection that reuses the freed address). Returns
    None outside an active request or when there is no session."""
    try:
        ctx = server.request_context
        sess = getattr(ctx, "session", None)
        if sess is None:
            return None
        sid = getattr(sess, "_gm_session_id", None)
        if sid is None:
            import uuid
            sid = f"sess-{uuid.uuid4().hex}"
            try:
                sess._gm_session_id = sid
            except Exception:
                return f"sess-{id(sess)}"  # session not settable (slots) -> best effort
        return sid
    except Exception:
        return None
