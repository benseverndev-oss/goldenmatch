"""FastAPI REST API for GoldenPipe."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.engine.resolver import Resolver, WiringError
from goldenpipe.models.config import PipelineConfig


class RunRequest(BaseModel):
    pipeline: str
    source: str | None = None
    stages: list[Any] = []
    decisions: list[str] = []


def create_app() -> FastAPI:
    app = FastAPI(title="GoldenPipe", version="1.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/stages")
    def list_stages():
        reg = StageRegistry()
        reg.discover()
        return {
            name: {"produces": info.produces, "consumes": info.consumes}
            for name, info in reg.list_all().items()
        }

    @app.post("/validate")
    def validate(req: RunRequest):
        try:
            config = PipelineConfig(
                pipeline=req.pipeline, stages=req.stages, decisions=req.decisions,
            )
            reg = StageRegistry()
            reg.discover()
            plan = Resolver.resolve(config, reg)
            return {"valid": True, "stages": [s.name for s in plan.stages]}
        except (WiringError, KeyError) as e:
            return {"valid": False, "error": str(e)}

    @app.post("/run")
    def run_pipeline(req: RunRequest):
        from goldenpipe._api import run
        result = run(req.source or "", config=None)
        return {
            "status": result.status.value,
            "source": result.source,
            "input_rows": result.input_rows,
            "errors": result.errors,
            "skipped": result.skipped,
            "timing": result.timing,
        }

    return app
