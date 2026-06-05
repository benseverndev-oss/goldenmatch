"""Wave 1 HTTP hardening: auth, SPA fallback, real health, A2A streaming/health.

Covers:
- 1.1 Web UI + REST API fail-closed bind + bearer auth + REST CORS allowlist
- 1.2 SPA catch-all fallback
- 1.3 Real health checks (web, REST, A2A)
- 1.4 A2A streaming advertised honestly
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── 1.1 / 1.3 Web UI ──────────────────────────────────────────────────────────
class TestWebFailClosed:
    def test_public_host_without_token_raises(self, monkeypatch):
        from goldenmatch.web.app import resolve_web_auth_token

        monkeypatch.delenv("GOLDENMATCH_WEB_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="unauthenticated"):
            resolve_web_auth_token("0.0.0.0")

    def test_loopback_without_token_allowed(self, monkeypatch):
        from goldenmatch.web.app import resolve_web_auth_token

        monkeypatch.delenv("GOLDENMATCH_WEB_TOKEN", raising=False)
        assert resolve_web_auth_token("127.0.0.1") is None

    def test_public_host_with_token_allowed(self, monkeypatch):
        from goldenmatch.web.app import resolve_web_auth_token

        monkeypatch.setenv("GOLDENMATCH_WEB_TOKEN", "secret")
        assert resolve_web_auth_token("0.0.0.0") == "secret"


def _web_client(tmp_path: Path):
    from fastapi.testclient import TestClient
    from goldenmatch.web.app import create_app
    from goldenmatch.web.state import AppState

    (tmp_path / "data.csv").write_text("id,name\n1,alice\n")
    return TestClient(create_app(AppState.from_project_dir(tmp_path)))


class TestWebAuthMiddleware:
    def test_api_requires_bearer_when_token_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_WEB_TOKEN", "secret")
        client = _web_client(tmp_path)
        # healthz is exempt
        assert client.get("/api/v1/healthz").status_code in (200, 503)
        # a real API route is gated
        assert client.get("/api/v1/project").status_code == 401
        ok = client.get("/api/v1/project", headers={"Authorization": "Bearer secret"})
        assert ok.status_code != 401

    def test_api_open_when_token_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_WEB_TOKEN", raising=False)
        client = _web_client(tmp_path)
        assert client.get("/api/v1/project").status_code != 401


class TestWebHealth:
    def test_healthz_ok_with_data(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_WEB_TOKEN", raising=False)
        client = _web_client(tmp_path)
        resp = client.get("/api/v1/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["data_csv"] is True

    def test_healthz_degraded_without_data(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from goldenmatch.web.app import create_app
        from goldenmatch.web.state import AppState

        monkeypatch.delenv("GOLDENMATCH_WEB_TOKEN", raising=False)
        client = TestClient(create_app(AppState.from_project_dir(tmp_path)))
        resp = client.get("/api/v1/healthz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"


# ── 1.2 SPA fallback ──────────────────────────────────────────────────────────
class TestSpaFallback:
    def test_unknown_route_serves_index_html(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from goldenmatch.web.app import SPAStaticFiles

        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<!doctype html><title>SPA</title>")
        (static / "app.js").write_text("console.log(1)")

        app = FastAPI()
        app.mount("/", SPAStaticFiles(directory=str(static), html=True), name="static")
        client = TestClient(app)

        # Real asset served normally.
        assert client.get("/app.js").status_code == 200
        # Unknown client-side route falls back to index.html.
        deep = client.get("/runs/some-run")
        assert deep.status_code == 200
        assert "SPA" in deep.text


# ── 1.1 / 1.3 REST matching API ───────────────────────────────────────────────
class TestRestFailClosed:
    def test_public_host_without_token_raises(self, monkeypatch):
        from goldenmatch.api.server import resolve_api_auth_token

        monkeypatch.delenv("GOLDENMATCH_API_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="unauthenticated"):
            resolve_api_auth_token("0.0.0.0")

    def test_loopback_without_token_allowed(self, monkeypatch):
        from goldenmatch.api.server import resolve_api_auth_token

        monkeypatch.delenv("GOLDENMATCH_API_TOKEN", raising=False)
        assert resolve_api_auth_token("127.0.0.1") is None


class TestRestAuthGate:
    """_authorized only reads self.headers + the module token, so a tiny stub
    exercises it without constructing a real socket-backed handler."""

    def _gate(self, headers):
        from goldenmatch.api.server import APIHandler

        stub = type("S", (), {"headers": headers})()
        return APIHandler._authorized(stub)

    def test_no_token_is_open(self, monkeypatch):
        import goldenmatch.api.server as srv

        monkeypatch.setattr(srv, "_auth_token", None)
        assert self._gate({}) is True

    def test_token_requires_matching_bearer(self, monkeypatch):
        import goldenmatch.api.server as srv

        monkeypatch.setattr(srv, "_auth_token", "secret")
        assert self._gate({}) is False
        assert self._gate({"Authorization": "Bearer wrong"}) is False
        assert self._gate({"Authorization": "Bearer secret"}) is True


# ── 1.3 / 1.4 A2A ─────────────────────────────────────────────────────────────
class TestA2aHealthAndStreaming:
    def test_card_streaming_is_false(self):
        pytest.importorskip("aiohttp")
        from goldenmatch.a2a.server import build_agent_card

        card = build_agent_card("http://localhost:8080")
        assert card["capabilities"]["streaming"] is False

    def test_health_route_registered(self, monkeypatch):
        pytest.importorskip("aiohttp")
        from goldenmatch.a2a.server import create_app

        monkeypatch.delenv("GOLDENMATCH_AGENT_TOKEN", raising=False)
        app = create_app(host="127.0.0.1")
        paths = {r.resource.canonical for r in app.router.routes() if r.resource}
        assert "/health" in paths
