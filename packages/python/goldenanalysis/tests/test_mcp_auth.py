"""Fail-closed HTTP-auth guard on the goldenanalysis MCP server (run_server_http).

Binding a non-loopback host without GOLDENANALYSIS_MCP_TOKEN is refused at startup so
the public remote MCP is never started unauthenticated by accident.
"""
import pytest

try:
    from goldenanalysis.mcp.server import resolve_http_auth_token
    HAS_MCP = True
except ImportError:  # pragma: no cover - mcp optional dep
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp not installed")

_TOKEN = "GOLDENANALYSIS_MCP_TOKEN"
_ALLOW = "GOLDENANALYSIS_MCP_ALLOW_PUBLIC"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_TOKEN, raising=False)
    monkeypatch.delenv(_ALLOW, raising=False)


def test_loopback_without_token_is_allowed():
    for host in ("127.0.0.1", "localhost", "::1"):
        assert resolve_http_auth_token(host) is None


def test_public_bind_without_token_is_refused():
    with pytest.raises(RuntimeError):
        resolve_http_auth_token("0.0.0.0")


def test_public_bind_with_token_returns_it(monkeypatch):
    monkeypatch.setenv(_TOKEN, "s3cr3t-token")
    assert resolve_http_auth_token("0.0.0.0") == "s3cr3t-token"


def test_allow_public_opt_out(monkeypatch):
    monkeypatch.setenv(_ALLOW, "1")
    assert resolve_http_auth_token("0.0.0.0") is None
