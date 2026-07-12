"""Two MCP sessions are isolated through the aggregator dispatch."""

import asyncio
import csv
import json


def _fixture(tmp_path, names, tag):
    p = tmp_path / f"{tag}.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "email"])
        for n in names:
            w.writerow([n, f"{n.split()[0].lower()}@x.com"])
    return str(p)


def test_sessions_isolated(tmp_path, monkeypatch):
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm

    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(gm, "_rows", [])
    monkeypatch.setattr(gm, "_id_to_idx", {})

    fa = _fixture(tmp_path, ["John Smith", "Jon Smith"], "a")
    tok_a = ctx.set_current_session_id("A")
    try:
        gm.dispatch("agent_deduplicate", {"file_path": fa})
        result_a = gm.dispatch("list_clusters", {})
        assert "error" not in result_a
    finally:
        ctx.reset_current_session_id(tok_a)

    # session B never ran anything -> clean error, NOT session A's clusters
    tok_b = ctx.set_current_session_id("B")
    try:
        result_b = gm.dispatch("list_clusters", {})
        assert "error" in result_b
    finally:
        ctx.reset_current_session_id(tok_b)


def test_isolation_through_real_call_tool(tmp_path, monkeypatch):
    """Drive the REAL registered ``call_tool`` handler from create_server(), not
    goldenmatch.mcp.server.dispatch directly.

    The Task 6 wrap in goldensuite_mcp/server.py does:
        set_current_session_id(session_key_from_context(server)) ... finally: reset
    around the aggregator's call_tool closure. A bug in that wrap (wrong server
    ref, dropped finally, wrong ordering) would slip past a test that calls
    dispatch() directly with the contextvar set by hand (see
    test_sessions_isolated above). This test instead retrieves the SDK's actual
    registered CallToolRequest handler off the Server built by create_server()
    and invokes it through a real pushed request_ctx, proving the wrap reads the
    SDK's ContextVar rather than some fake/stale reference.

    mcp.server.lowlevel.server.Server.call_tool() decorator returns the
    ORIGINAL undecorated coroutine to its caller (so goldensuite_mcp/server.py's
    module-level `call_tool` closure stays usable), but it registers an adapter
    in ``server.request_handlers[types.CallToolRequest]`` that builds a real
    CallToolRequest/CallToolResult around it. That adapter is what a real MCP
    transport actually invokes, so driving it here -- rather than calling the
    undecorated function directly -- also exercises the SDK's input-schema
    validation and result-shape normalization around our wrap.
    """
    import mcp.server.lowlevel.server as low
    import mcp.types as types
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm
    from goldensuite_mcp.server import create_server

    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(gm, "_rows", [])
    monkeypatch.setattr(gm, "_id_to_idx", {})

    server = create_server()
    handler = server.request_handlers[types.CallToolRequest]

    async def call(name, args):
        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=args),
        )
        result = await handler(req)
        call_result = result.root
        assert not call_result.isError, call_result.content
        text = call_result.content[0].text
        return json.loads(text)

    fa = _fixture(tmp_path, ["John Smith", "Jon Smith"], "a")

    class _SessA: ...

    ctxA = type("C", (), {"session": _SessA()})()

    async def run_a():
        tok = low.request_ctx.set(ctxA)
        try:
            await call("agent_deduplicate", {"file_path": fa})
            listed = await call("list_clusters", {})
            assert "error" not in listed
        finally:
            low.request_ctx.reset(tok)

    asyncio.run(run_a())

    # session B never ran anything through the real handler -> clean error,
    # NOT session A's clusters. Proves the wrap keyed off the pushed request
    # context (a different `.session` object -> a different derived session key).
    class _SessB: ...

    ctxB = type("C", (), {"session": _SessB()})()

    async def run_b():
        tok = low.request_ctx.set(ctxB)
        try:
            res = await call("list_clusters", {})
            assert "error" in res
        finally:
            low.request_ctx.reset(tok)

    asyncio.run(run_b())
