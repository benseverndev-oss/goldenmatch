from __future__ import annotations

import io
import tempfile
from pathlib import Path

import polars as pl
from fastapi import FastAPI, File, UploadFile

import goldenflow
from goldenflow.engine.transformer import TransformEngine
from goldenflow.transforms import list_transforms


def create_app() -> FastAPI:
    app = FastAPI(title="GoldenFlow", version=goldenflow.__version__)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": goldenflow.__version__}

    @app.get("/transforms")
    def transforms():
        return [
            {
                "name": t.name,
                "input_types": t.input_types,
                "auto_apply": t.auto_apply,
                "priority": t.priority,
                "mode": t.mode,
            }
            for t in list_transforms()
        ]

    @app.post("/transform")
    async def transform(file: UploadFile = File(...)):
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        engine = TransformEngine()
        result = engine.transform_file(tmp_path)
        tmp_path.unlink(missing_ok=True)

        csv_buffer = io.StringIO()
        result.df.write_csv(csv_buffer)

        return {
            "data": csv_buffer.getvalue(),
            "manifest": result.manifest.to_dict(),
        }

    @app.post("/map")
    async def map_schemas(
        source: UploadFile = File(...),
        target: UploadFile = File(...),
    ):
        from goldenflow.mapping.schema_mapper import SchemaMapper

        s_content = await source.read()
        t_content = await target.read()

        s_df = pl.read_csv(io.BytesIO(s_content))
        t_df = pl.read_csv(io.BytesIO(t_content))

        mapper = SchemaMapper()
        mappings = mapper.map(s_df, t_df)

        return [
            {
                "source": m.source,
                "target": m.target,
                "confidence": m.confidence,
                "transform": m.transform,
            }
            for m in mappings
        ]

    return app
