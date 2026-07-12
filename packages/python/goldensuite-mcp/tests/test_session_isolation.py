"""Two MCP sessions are isolated through the aggregator dispatch."""
import csv


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
