from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from goldenmatch.web.state import AppState

STATIC_DIR = Path(__file__).parent / "static"


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="goldenmatch-ui", version="1")
    app.state.app_state = state

    @app.get("/api/v1/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from goldenmatch.web.routers import autoconfig as autoconfig_router
    from goldenmatch.web.routers import compare as compare_router
    from goldenmatch.web.routers import domains as domains_router
    from goldenmatch.web.routers import evaluation as evaluation_router
    from goldenmatch.web.routers import labels as labels_router
    from goldenmatch.web.routers import match as match_router
    from goldenmatch.web.routers import preview as preview_router
    from goldenmatch.web.routers import project as project_router
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

    if STATIC_DIR.exists() and any(STATIC_DIR.iterdir()):
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
