"""Session-fallback for the stateful goldenmatch MCP tools (aggregator path)."""
import csv

from goldenmatch.core.agent import AgentSession
from goldenmatch.mcp import _session_ctx as ctx
from goldenmatch.mcp import _session_store as store
from goldenmatch.mcp.agent_tools import _dispatch


def _fixture(tmp_path):
    p = tmp_path / "in.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["name", "email"])
        for r in [["John Smith", "j@x.com"], ["Jon Smith", "j@x.com"],
                  ["Mary Jones", "m@y.com"], ["Karen White", "k@z.com"]]:
            w.writerow(r)
    return str(p)


def test_agent_deduplicate_persists_session(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    tok = ctx.set_current_session_id("sess-test")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        saved = store._STORE.get("sess-test")
        assert isinstance(saved, AgentSession)
        assert saved.result is not None
    finally:
        ctx.reset_current_session_id(tok)


def test_no_session_id_does_not_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
    assert store._STORE.get("sess-test") is None


def test_auto_configure_does_not_clobber_prior_dedupe(tmp_path, monkeypatch):
    """auto_configure has no .result; it must NOT overwrite a good dedupe session
    under the same id (the result-guard in _persist_session)."""
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    tok = ctx.set_current_session_id("sess-x")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        good = store._STORE.get("sess-x")
        assert good is not None and good.result is not None
        _dispatch("auto_configure", {"file_path": _fixture(tmp_path)}, AgentSession)
        assert store._STORE.get("sess-x") is good
        assert store._STORE.get("sess-x").result is not None
    finally:
        ctx.reset_current_session_id(tok)


def test_resolver_prefers_globals(monkeypatch):
    from goldenmatch.mcp import server as gm
    monkeypatch.setattr(gm, "_result", "GLOBAL_RESULT")
    monkeypatch.setattr(gm, "_config", "GLOBAL_CONFIG")
    monkeypatch.setattr(gm, "_engine", type("E", (), {"data": "GLOBAL_DATA"})())
    monkeypatch.setattr(gm, "_rows", [{"__row_id__": 0}])
    monkeypatch.setattr(gm, "_id_to_idx", {0: 0})
    rs = gm._resolve_run_state()
    assert rs.result == "GLOBAL_RESULT" and rs.config == "GLOBAL_CONFIG"
    assert rs.data == "GLOBAL_DATA"


def test_resolver_session_fallback_builds_row_ids(tmp_path, monkeypatch):
    import polars as pl
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))

    sess = AgentSession()
    sess.result = "R"; sess.config = "C"
    sess.data = pl.DataFrame({"name": ["a", "b"]})  # NO __row_id__
    store._STORE.put("s1", sess)
    tok = ctx.set_current_session_id("s1")
    try:
        rs = gm._resolve_run_state()
        assert rs.result == "R" and rs.config == "C"
        assert "__row_id__" in rs.data.columns   # augmented
        assert rs.rows and rs.id_to_idx           # built from the augmented frame
    finally:
        ctx.reset_current_session_id(tok)


def test_resolver_nothing_loaded(monkeypatch):
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import server as gm
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    tok = ctx.set_current_session_id("unknown")
    try:
        rs = gm._resolve_run_state()
        assert rs.result is None and rs.config is None and rs.data is None
    finally:
        ctx.reset_current_session_id(tok)


def test_resolver_caches_augmented_frame(tmp_path, monkeypatch):
    import polars as pl
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    sess = AgentSession()
    sess.result = "R"; sess.data = pl.DataFrame({"name": ["a", "b"]})
    store._STORE.put("s1", sess)
    tok = ctx.set_current_session_id("s1")
    try:
        rs1 = gm._resolve_run_state()
        rs2 = gm._resolve_run_state()
        assert rs1.data is rs2.data           # cached, not rebuilt
        assert rs1.rows is rs2.rows
        # a new run (new frame) invalidates the cache
        sess.data = pl.DataFrame({"name": ["x"]})
        rs3 = gm._resolve_run_state()
        assert rs3.data is not rs1.data
    finally:
        ctx.reset_current_session_id(tok)


def test_eight_tools_work_via_session(tmp_path, monkeypatch):
    """Aggregator path (globals None): after agent_deduplicate under a session id,
    the stateful tools return real data (no AttributeError/ColumnNotFoundError)."""
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(gm, "_rows", [])
    monkeypatch.setattr(gm, "_id_to_idx", {})
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))

    tok = ctx.set_current_session_id("s-e2e")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        assert "error" not in gm._tool_list_clusters(min_size=1, limit=100)
        exp = tmp_path / "exp.csv"
        r_exp = gm._tool_export_results(str(exp), "csv")
        assert "error" not in r_exp and exp.exists()
        r_md = gm._tool_match_record({"name": "John Smith", "email": "j@x.com"}, None, 5)
        assert "error" not in r_md   # the __row_id__ augmentation guard
        r_fd = gm._tool_find_duplicates({"name": "John Smith"}, 5)
        assert "error" not in r_fd
    finally:
        ctx.reset_current_session_id(tok)


def test_tools_clean_error_when_nothing_loaded(monkeypatch):
    from goldenmatch.mcp import server as gm
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(gm, "_rows", [])
    monkeypatch.setattr(gm, "_id_to_idx", {})
    tok = ctx.set_current_session_id("cold")
    try:
        res = gm._tool_list_clusters(min_size=1, limit=100)
        assert "error" in res and "AttributeError" not in str(res)
    finally:
        ctx.reset_current_session_id(tok)
