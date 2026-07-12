from goldenmatch.mcp import _session_ctx as ctx


def test_set_get_reset():
    assert ctx.current_session_id() is None
    tok = ctx.set_current_session_id("sess-1")
    try:
        assert ctx.current_session_id() == "sess-1"
    finally:
        ctx.reset_current_session_id(tok)
    assert ctx.current_session_id() is None


def test_key_from_context_with_session():
    class _Sess: ...
    class _Ctx:
        session = _Sess()
    class _Server:
        request_context = _Ctx()
    key = ctx.session_key_from_context(_Server())
    assert key is not None and key.startswith("sess-")


def test_key_from_context_absent_or_raising():
    class _NoCtx:
        @property
        def request_context(self):
            raise LookupError("no active request")
    assert ctx.session_key_from_context(_NoCtx()) is None

    class _NoneSession:
        class request_context:  # noqa: N801
            session = None
    assert ctx.session_key_from_context(_NoneSession()) is None


def test_key_from_real_request_ctx():
    """Drive session_key_from_context through the SDK's actual request_ctx var,
    not just a fake server -- proves the call_tool wiring will resolve a key."""
    import mcp.server.lowlevel.server as low
    from goldenmatch.mcp._session_ctx import session_key_from_context
    from mcp.server.lowlevel.server import Server

    srv = Server("t")
    # No active request -> request_context raises LookupError -> None.
    assert session_key_from_context(srv) is None
    # Push a fake RequestContext carrying a session onto the SDK's ContextVar.
    class _Sess: ...
    class _Ctx:
        session = _Sess()
    tok = low.request_ctx.set(_Ctx())  # the var Server.request_context returns
    try:
        assert session_key_from_context(srv).startswith("sess-")
    finally:
        low.request_ctx.reset(tok)
