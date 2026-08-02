"""The MCP HTTP transport default-jails file-path arguments to a root.

goldenmatch is local-first: over stdio a trusted local caller may reference
absolute paths (opt-in containment via GOLDENMATCH_ALLOWED_ROOT). But the
network-exposed HTTP transport must contain path arguments by default -- an
authenticated MCP client should not be able to read/write outside the allowed
root (the goldenpipe/infermap sibling posture). ``run_server_http`` sets the
module-level ``_HTTP_JAIL_ROOT`` to GOLDENMATCH_ALLOWED_ROOT-or-CWD; these tests
drive that state directly (starting a real uvicorn server is out of scope).
"""
import pytest
from goldenmatch.mcp import server as mcp_server


@pytest.fixture
def _reset_jail():
    saved = mcp_server._HTTP_JAIL_ROOT
    yield
    mcp_server._HTTP_JAIL_ROOT = saved


def test_stdio_default_allows_absolute_path(tmp_path, monkeypatch, _reset_jail):
    # stdio transport: _HTTP_JAIL_ROOT is None -> local-first, absolute path OK
    # (this is what preserves the existing agent_deduplicate output_path behavior).
    monkeypatch.delenv("GOLDENMATCH_ALLOWED_ROOT", raising=False)
    mcp_server._HTTP_JAIL_ROOT = None
    result = mcp_server._safe_path_or_error(str(tmp_path / "out.csv"))
    assert not isinstance(result, dict), "stdio must not jail an absolute path"


def test_http_jail_allows_inside_root(tmp_path, _reset_jail):
    mcp_server._HTTP_JAIL_ROOT = str(tmp_path)
    inside = tmp_path / "sub" / "ok.csv"
    result = mcp_server._safe_path_or_error(str(inside))
    assert not isinstance(result, dict)
    assert result == inside.resolve()


def test_http_jail_rejects_escape(tmp_path, _reset_jail):
    root = tmp_path / "jail"
    root.mkdir()
    mcp_server._HTTP_JAIL_ROOT = str(root)
    result = mcp_server._safe_path_or_error(str(tmp_path / "escape.csv"))
    assert isinstance(result, dict) and "error" in result


def test_http_jail_rejects_traversal(tmp_path, _reset_jail):
    root = tmp_path / "jail"
    root.mkdir()
    mcp_server._HTTP_JAIL_ROOT = str(root)
    result = mcp_server._safe_path_or_error(str(root / ".." / ".." / "etc" / "passwd"))
    assert isinstance(result, dict) and "error" in result


def test_http_jail_error_is_generic(tmp_path, _reset_jail):
    # A network client must not learn the resolved path or the server's root.
    root = tmp_path / "jail"
    root.mkdir()
    mcp_server._HTTP_JAIL_ROOT = str(root)
    result = mcp_server._safe_path_or_error(str(tmp_path / "secret.csv"))
    assert isinstance(result, dict)
    assert result["error"] == "path is outside the allowed root"
    assert str(tmp_path) not in result["error"]
    assert "secret.csv" not in result["error"]


def test_http_jail_rejects_nul(_reset_jail):
    mcp_server._HTTP_JAIL_ROOT = "/tmp"
    result = mcp_server._safe_path_or_error("a\x00b.csv")
    assert isinstance(result, dict) and result["error"] == "invalid path"
