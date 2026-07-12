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
