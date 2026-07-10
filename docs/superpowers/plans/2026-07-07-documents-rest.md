# Document Ingest REST Endpoint Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /api/v1/documents/suggest-schema` and `POST /api/v1/documents/ingest` to the `[web]` FastAPI app — a thin HTTP surface over the shipped `goldenmatch.documents` functions.

**Architecture:** One new router (`web/routers/documents.py`, `APIRouter(prefix="/api/v1")`) wired into `create_app`, inheriting the app's bearer-auth middleware. Multipart uploads are written to a temp dir (extension preserved), then the shipped `suggest_schema_from_file` / `ingest_documents` run in a `ThreadPoolExecutor` off the event loop. The router has zero extraction logic.

**Tech Stack:** FastAPI, `python-multipart` (uploads), Polars, pytest + `fastapi.testclient`.

**Spec:** `docs/superpowers/specs/2026-07-07-documents-rest-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docs-rest` (branch `feat/documents-rest`). Do NOT push, do NOT touch `main`.
- **Test env** (from `packages/python/goldenmatch`):
  ```bash
  cd D:/show_case/gm-docs-rest/packages/python/goldenmatch
  PY="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="D:/show_case/gm-docs-rest/packages/python/goldenmatch;D:/show_case/gm-docs-rest/packages/python/goldenflow"
  export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  (`GOLDENMATCH_NATIVE=0` forces the pure-Python documents path — this worktree has no built `_native`, and the HTTP tests don't exercise the kernel.)
- **Prereq:** `"$PY" -c "import fastapi, multipart, PIL, fitz" 2>/dev/null || "$PY" -m pip install fastapi python-multipart pymupdf Pillow` (the shared venv already has fastapi/PIL/fitz from other work; `python-multipart` may be missing).
- **Lint before each commit:** `"$PY" -m ruff check goldenmatch/web/routers/documents.py tests/web`.
- **Commit trailers:** copy from `git log -1 --format=%B`. `git -c commit.gpgsign=false commit`.

---

## File structure (locked)

| path | responsibility |
|---|---|
| `goldenmatch/web/routers/documents.py` | the two endpoints + the temp-file upload adapter |
| `goldenmatch/web/app.py` (modify) | import + `include_router(documents_router.router)` |
| `pyproject.toml` (modify) | add `python-multipart` to the `[web]` extra |
| `tests/web/test_documents_router.py` | offline TestClient tests (FakeExtractor / stubbed suggest) |

---

## Task 1: Dependency + empty router wired into the app

**Files:** Modify `pyproject.toml`, `goldenmatch/web/app.py`; Create `goldenmatch/web/routers/documents.py`; Test `tests/web/test_documents_router.py`

- [ ] **Step 1: Failing test** — the app registers the two document routes:
  ```python
  from pathlib import Path
  from fastapi.testclient import TestClient
  from goldenmatch.web.app import create_app
  from goldenmatch.web.state import AppState


  def _client(tmp_path):
      state = AppState(project_root=tmp_path, config_path=None,
                       labels_path=tmp_path / "labels.jsonl")
      return TestClient(create_app(state))


  def test_document_routes_registered(tmp_path):
      client = _client(tmp_path)
      paths = {r.path for r in client.app.routes}
      assert "/api/v1/documents/suggest-schema" in paths
      assert "/api/v1/documents/ingest" in paths
  ```
- [ ] **Step 2: Run → fail.** `"$PY" -m pytest tests/web/test_documents_router.py -q` (routes missing).
- [ ] **Step 3: Implement.**
  - `pyproject.toml`: in the `[project.optional-dependencies]` `web = [...]` list, add `"python-multipart>=0.0.9"`.
  - Create `goldenmatch/web/routers/documents.py`:
    ```python
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
    ```
  - `goldenmatch/web/app.py`: with the other `from goldenmatch.web.routers import X as X_router` lines, add `from goldenmatch.web.routers import documents as documents_router`; with the other `app.include_router(...)` calls, add `app.include_router(documents_router.router)`.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(web): document-ingest router scaffold + wiring + python-multipart`.

---

## Task 2: `suggest-schema` endpoint

**Files:** Test `tests/web/test_documents_router.py` (add cases). Implementation already in place from Task 1 — these tests drive/verify it.

- [ ] **Step 1: Failing test** — stub `suggest_schema_from_file` at the router module's reference so no live VLM call runs:
  ```python
  import io
  import goldenmatch.web.routers.documents as docrouter
  from goldenmatch.documents.types import Field, TargetSchema
  from PIL import Image


  def _png_bytes():
      buf = io.BytesIO(); Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
      return buf.getvalue()


  def test_suggest_schema_returns_schema(tmp_path, monkeypatch):
      monkeypatch.setattr(docrouter, "suggest_schema_from_file",
                          lambda path, **k: TargetSchema([Field("full_name"), Field("email", kind="email")]))
      client = _client(tmp_path)
      resp = client.post("/api/v1/documents/suggest-schema",
                         files={"file": ("card.png", _png_bytes(), "image/png")})
      assert resp.status_code == 200, resp.text
      assert resp.json()["schema"]["fields"][0]["name"] == "full_name"
  ```
- [ ] **Step 2: Run → verify PASS** (implementation exists from Task 1). If it fails, fix the router. Also confirm the stub is hit (no network).
- [ ] **Step 3: (no new impl expected)** — if a real gap surfaces, fix `documents.py`.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `test(web): suggest-schema endpoint`.

---

## Task 3: `ingest` endpoint

**Files:** Test `tests/web/test_documents_router.py` (add cases).

- [ ] **Step 1: Failing test** — inject a `FakeExtractor` by monkeypatching `resolve_extractor` at the router module's reference (upload a REAL png so `load_pages` succeeds; `FakeExtractor` returns canned rows):
  ```python
  import json
  from goldenmatch.documents.extractor import FakeExtractor
  from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema

  SCHEMA = TargetSchema([Field("full_name"), Field("email")])


  def test_ingest_returns_records_and_report(tmp_path, monkeypatch):
      row = ExtractedRow.from_partial({"full_name": "Ada", "email": "ada@x.io"}, {},
                                      SCHEMA, source_file="", source_page=0)
      monkeypatch.setattr(docrouter, "resolve_extractor",
                          lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
      client = _client(tmp_path)
      resp = client.post(
          "/api/v1/documents/ingest",
          files=[("files", ("a.png", _png_bytes(), "image/png"))],
          data={"schema": json.dumps({"fields": [{"name": "full_name"}, {"name": "email"}]})},
      )
      assert resp.status_code == 200, resp.text
      body = resp.json()
      assert body["report"]["n_rows"] == 1 and body["report"]["n_files"] == 1
      rec = body["records"][0]
      assert rec["full_name"] == "Ada" and rec["email"] == "ada@x.io"
      # sidecar columns present for the dedupe_df exclude_columns handoff
      assert "_source_file" in rec and "_source_page" in rec and "_extract_confidence" in rec
  ```
- [ ] **Step 2: Run → verify PASS** (impl from Task 1). Confirm no live call (FakeExtractor used).
- [ ] **Step 3:** fix `documents.py` only if a real gap appears.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `test(web): ingest endpoint returns records + report`.

---

## Task 4: Error + auth contract

**Files:** Test `tests/web/test_documents_router.py` (add cases).

- [ ] **Step 1: Failing tests:**
  ```python
  def test_ingest_malformed_schema_400(tmp_path):
      client = _client(tmp_path)
      resp = client.post("/api/v1/documents/ingest",
                         files=[("files", ("a.png", _png_bytes(), "image/png"))],
                         data={"schema": "not json"})
      assert resp.status_code == 400
      assert "schema" in resp.json()["detail"].lower()


  def test_ingest_no_files_400(tmp_path):
      client = _client(tmp_path)
      resp = client.post("/api/v1/documents/ingest",
                         data={"schema": '{"fields":[{"name":"x"}]}'})
      assert resp.status_code == 400  # optional files param -> handler's 400, not FastAPI 422


  def test_auth_required_401_when_token_set(tmp_path, monkeypatch):
      monkeypatch.setenv("GOLDENMATCH_WEB_TOKEN", "secret")  # middleware only enforces when set
      client = _client(tmp_path)
      resp = client.post("/api/v1/documents/ingest",
                         files=[("files", ("a.png", _png_bytes(), "image/png"))],
                         data={"schema": '{"fields":[{"name":"x"}]}'})
      assert resp.status_code == 401
  ```
- [ ] **Step 2: Run → verify.** The malformed-schema and auth cases should pass with Task 1's code. For `no_files`: confirm the `files: ... | None = File(None)` default yields the handler's 400 (NOT a 422). If FastAPI still returns 422, adjust the param so the missing-field case reaches the handler (e.g. keep `File(None)` and ensure no other required-field 422 fires first).
- [ ] **Step 3:** adjust `documents.py` if the no-files path returns 422 instead of 400.
- [ ] **Step 4: Run → pass** (full file: `"$PY" -m pytest tests/web/test_documents_router.py -q`).
- [ ] **Step 5: Commit** `test(web): document endpoints error + auth contract`.

---

## Task 5: Gated live smoke + README note

**Files:** Test `tests/web/test_documents_router_live.py`; Modify `goldenmatch/documents/README.md`

- [ ] **Step 1: Live smoke (skipped without a key):**
  ```python
  import io, os
  import pytest
  from PIL import Image, ImageDraw
  from fastapi.testclient import TestClient
  from goldenmatch.web.app import create_app
  from goldenmatch.web.state import AppState

  pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY_PERSONAL"),
                                  reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL")


  def test_live_suggest_then_ingest(tmp_path):
      img = Image.new("RGB", (400, 200), "white"); d = ImageDraw.Draw(img)
      d.text((20, 40), "Ada Lovelace", fill="black"); d.text((20, 80), "ada@x.io", fill="black")
      buf = io.BytesIO(); img.save(buf, format="PNG"); png = buf.getvalue()
      client = TestClient(create_app(AppState(project_root=tmp_path, config_path=None,
                                              labels_path=tmp_path / "labels.jsonl")))
      s = client.post("/api/v1/documents/suggest-schema",
                      files={"file": ("card.png", png, "image/png")})
      assert s.status_code == 200 and s.json()["schema"]["fields"]
  ```
- [ ] **Step 2: Run → SKIPPED** (no key).
- [ ] **Step 3: Append a "REST" section to `goldenmatch/documents/README.md`:**
  ````markdown
  ## REST (goldenmatch[web])

  ```bash
  # suggest a schema from a sample
  curl -F file=@form.pdf localhost:8000/api/v1/documents/suggest-schema
  # ingest a pile against a schema -> records + report JSON
  curl -F files=@a.pdf -F files=@b.jpg -F schema='{"fields":[{"name":"full_name"},{"name":"email","kind":"email"}]}' \
       localhost:8000/api/v1/documents/ingest
  ```
  Records carry `_source_file`/`_source_page`/`_extract_confidence`; pass them to
  `dedupe_df(exclude_columns=...)`. Auth: set `GOLDENMATCH_WEB_TOKEN` and send `Authorization: Bearer`.
  ````
- [ ] **Step 4: Full suite + lint.** `"$PY" -m pytest tests/web/test_documents_router.py tests/web/test_documents_router_live.py -q` (live skipped) and ruff clean.
- [ ] **Step 5: Commit** `docs(web): REST usage + gated live smoke`.

---

## Done-when

- `tests/web/test_documents_router.py` green (live smoke skipped without a key), ruff clean.
- `POST /api/v1/documents/suggest-schema` + `/ingest` registered, behind the bearer middleware,
  returning the documented JSON shapes; records feed `dedupe_df(exclude_columns=[...])`.
- `python-multipart` in the `[web]` extra.
- Deferred (not here): async jobs, streaming, the Web UI frontend, rate limiting.
