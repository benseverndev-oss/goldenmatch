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

    from goldenmatch.web.routers import project as project_router
    from goldenmatch.web.routers import rules as rules_router
    from goldenmatch.web.routers import runs as runs_router
    app.include_router(project_router.router)
    app.include_router(rules_router.router)
    app.include_router(runs_router.router)

    if STATIC_DIR.exists() and any(STATIC_DIR.iterdir()):
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
