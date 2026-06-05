from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from goldenmatch.web.state import AppState

STATIC_DIR = Path(__file__).parent / "static"

# Paths that never require a bearer token (page shell + liveness probe).
_PUBLIC_API_PATHS = frozenset({"/api/v1/healthz"})


def resolve_web_auth_token(host: str) -> str | None:
    """Return the web-UI bearer token, enforcing the fail-closed bind rule.

    Raises ``RuntimeError`` when binding to a non-loopback host without
    ``GOLDENMATCH_WEB_TOKEN`` set, so the single-tenant dev tool is never
    exposed unauthenticated by accident. Returns the token (or ``None`` for an
    intentionally-open loopback bind).
    """
    token = os.environ.get("GOLDENMATCH_WEB_TOKEN")
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not token and not is_loopback:
        raise RuntimeError(
            f"Refusing to start an unauthenticated web UI on host {host!r}. "
            "Set GOLDENMATCH_WEB_TOKEN, or bind to 127.0.0.1 for local use."
        )
    return token


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html on 404 so client-side routes
    (``/workbench``, ``/runs/<name>``, ...) survive a hard refresh or shared URL."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="goldenmatch-ui", version="1")
    app.state.app_state = state

    # Optional bearer auth: enforced on /api/v1/* (except healthz) only when
    # GOLDENMATCH_WEB_TOKEN is set. Static assets stay public so the page loads.
    @app.middleware("http")
    async def _bearer_auth(request: Request, call_next):
        token = os.environ.get("GOLDENMATCH_WEB_TOKEN")
        path = request.url.path
        if token and path.startswith("/api/") and path not in _PUBLIC_API_PATHS:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer ") or header[7:] != token:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/v1/healthz")
    def healthz() -> JSONResponse:
        """Liveness + readiness: confirms the project root and data.csv exist."""
        root = state.project_root
        checks = {
            "project_root": root.is_dir(),
            "data_csv": (root / "data.csv").exists(),
        }
        ok = all(checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "checks": checks},
            status_code=200 if ok else 503,
        )

    from goldenmatch.web.routers import autoconfig as autoconfig_router
    from goldenmatch.web.routers import compare as compare_router
    from goldenmatch.web.routers import controller as controller_router
    from goldenmatch.web.routers import domains as domains_router
    from goldenmatch.web.routers import evaluation as evaluation_router
    from goldenmatch.web.routers import identity as identity_router
    from goldenmatch.web.routers import labels as labels_router
    from goldenmatch.web.routers import match as match_router
    from goldenmatch.web.routers import memory as memory_router
    from goldenmatch.web.routers import plugins as plugins_router
    from goldenmatch.web.routers import preview as preview_router
    from goldenmatch.web.routers import project as project_router
    from goldenmatch.web.routers import quality as quality_router
    from goldenmatch.web.routers import rules as rules_router
    from goldenmatch.web.routers import run as run_router
    from goldenmatch.web.routers import runs as runs_router
    from goldenmatch.web.routers import sensitivity as sensitivity_router
    from goldenmatch.web.routers import settings as settings_router
    from goldenmatch.web.routers import unmerge as unmerge_router
    app.include_router(project_router.router)
    app.include_router(rules_router.router)
    app.include_router(runs_router.router)
    app.include_router(preview_router.router)
    app.include_router(labels_router.router)
    app.include_router(autoconfig_router.router)
    app.include_router(run_router.router)
    app.include_router(settings_router.router)
    app.include_router(evaluation_router.router)
    app.include_router(unmerge_router.router)
    app.include_router(compare_router.router)
    app.include_router(sensitivity_router.router)
    app.include_router(domains_router.router)
    app.include_router(match_router.router)
    app.include_router(memory_router.router)
    app.include_router(plugins_router.router)
    app.include_router(quality_router.router)
    app.include_router(controller_router.router)
    app.include_router(identity_router.router)

    if STATIC_DIR.exists() and any(STATIC_DIR.iterdir()):
        app.mount("/", SPAStaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
