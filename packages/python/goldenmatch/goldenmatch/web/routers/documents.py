"""POST /api/v1/documents/{suggest-schema,ingest} -- HTTP surface over goldenmatch.documents.

Thin adapter: multipart uploads -> temp files (extension preserved so loader.load_pages
routes PDF vs image) -> the shipped suggest_schema_from_file / ingest_documents (which run
through documents-core natively, pure-Python fallback otherwise). No extraction logic here.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from goldenmatch.documents import ingest_documents
from goldenmatch.documents.config import resolve_extractor
from goldenmatch.documents.schema_io import schema_from_dict, schema_to_dict
from goldenmatch.documents.suggest import suggest_schema_from_file

router = APIRouter(prefix="/api/v1")
_executor = ThreadPoolExecutor(max_workers=2)


def _save_uploads(tmpdir: str, files: list[UploadFile]) -> list[str]:
    paths: list[str] = []
    for f in files:
        suffix = Path(f.filename or "upload").suffix or ".bin"
        p = Path(tmpdir) / f"doc{len(paths)}{suffix}"
        p.write_bytes(f.file.read())
        paths.append(str(p))
    return paths


@router.post("/documents/suggest-schema")
async def suggest_schema_endpoint(
    file: UploadFile = File(...),
    backend: str = Form("vlm"),
    model: str = Form("gpt-4o"),
):
    def work():
        with tempfile.TemporaryDirectory() as td:
            (path,) = _save_uploads(td, [file])
            schema = suggest_schema_from_file(path, backend=backend, model=model)
            return {"schema": schema_to_dict(schema)}
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor, work)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/documents/ingest")
async def ingest_endpoint(
    files: list[UploadFile] | None = File(None),
    schema: str = Form(...),
    drop_empty: bool = Form(True),
    backend: str = Form("vlm"),
    model: str = Form("gpt-4o"),
):
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    try:
        target = schema_from_dict(json.loads(schema))
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"invalid schema: {e}") from e

    def work():
        with tempfile.TemporaryDirectory() as td:
            paths = _save_uploads(td, files)
            extractor = resolve_extractor(backend, model)  # ValueError on bad backend/key
            df, report = ingest_documents(paths, target, extractor=extractor,
                                          drop_empty=drop_empty, return_report=True)
            return {
                "records": df.to_dicts(),
                "report": {
                    "n_files": report.n_files, "n_rows": report.n_rows,
                    "errors": [{"file": f, "error": e} for (f, e) in report.errors],
                },
            }
    try:
        return await asyncio.get_running_loop().run_in_executor(_executor, work)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
