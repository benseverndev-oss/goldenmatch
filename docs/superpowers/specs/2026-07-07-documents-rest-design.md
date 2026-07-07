# Document Ingest REST endpoint — design

**Goal.** Expose the shipped `goldenmatch.documents` capability over HTTP in the `[web]` FastAPI
app: upload documents, get back records ready for `dedupe_df`, plus an AI-schema-suggestion
endpoint. This is the foundational surface — the Web UI (next sub-project) and a possible TS client
consume it.

**Builds on:** Phase 1 (`ingest_documents`), Phase 2 (`suggest_schema_from_file`, `schema_io`),
documents-core (native kernels) — all merged to main. The router adds NO extraction logic.

## Decisions (settled in brainstorming)

- **Synchronous v1:** POST files -> wait -> records/report JSON in the response. Mirrors the MCP
  tool. Async jobs + streaming are deferred.
- **Multipart uploads:** clients send file bytes (browser + programmatic); the router writes each to
  a temp file preserving the extension (so `loader.load_pages` routes PDF vs image), then calls the
  shipped Python. Needs `python-multipart` (add to the `[web]` extra).
- **Reuse the app's bearer auth** (the `create_app` middleware wraps all routes) — no per-route auth.

## Module + wiring

- `goldenmatch/web/routers/documents.py` — `router = APIRouter(prefix="/api/v1")`, two endpoints.
- `goldenmatch/web/app.py` (modify) — `from goldenmatch.web.routers import documents as documents_router`
  + `app.include_router(documents_router.router)` (with the other `include_router` calls).
- Blocking work (VLM calls / ingest) runs in a `ThreadPoolExecutor` off the event loop, mirroring
  `autoconfig.py`'s pattern (endpoints are `async def`, delegate the sync body via
  `loop.run_in_executor`).

## Endpoints (under `/api/v1/documents`)

**`POST /documents/suggest-schema`** — `multipart/form-data`:
- `file`: one `UploadFile`.
- `backend` (form, default `"vlm"`), `model` (form, default `"gpt-4o"`).
- -> `200 {"schema": {"fields": [{"name","kind","hint"}, ...]}}` (from `schema_to_dict`).

**`POST /documents/ingest`** — `multipart/form-data`:
- `files`: `list[UploadFile]`.
- `schema`: a JSON string of `{"fields":[...]}` (parsed via `schema_from_dict`).
- `drop_empty` (form bool, default `true`), `backend`, `model`.
- -> `200 {"records": [...], "report": {"n_files", "n_rows", "errors": [{"file","error"}]}}` — the
  same shape as the MCP `documents_ingest` tool (`df.to_dicts()` + the `IngestReport`). Records carry
  the schema columns + `_source_file`/`_source_page`/`_extract_confidence`; the response documents
  that callers pass those three to `dedupe_df(exclude_columns=...)`.

## Data flow

`UploadFile`(s) -> write to a `tempfile.TemporaryDirectory`, each keeping its original suffix ->
`ingest_documents(temp_paths, schema, backend=, model=, drop_empty=, return_report=True)` (or
`suggest_schema_from_file(temp_path, backend=, model=)`) -> serialize -> JSON -> temp dir cleaned up
in a `finally`. The extractor is resolved by the shipped `config.resolve_extractor` (native kernels
under the hood), so REST inherits the fallback thesis for free.

## Error handling

- Missing/invalid `schema` JSON -> **400** with a clear message (catch the `ValueError` from
  `schema_from_dict`).
- No OpenAI key for the `vlm` backend / unknown backend -> **400** (the shipped `resolve_extractor` /
  `suggest_schema_from_file` already raise `ValueError` at config time; map to 400).
- A corrupt/unreadable individual file -> recorded in `report.errors`, response still **200**
  (batch-safe, already handled by `ingest_documents`).
- No files / empty upload -> **400**.
- Follow the existing routers' convention: raise `HTTPException(status_code=..., detail=...)`.

## Testing (offline-first, matches repo)

- FastAPI `TestClient` against `create_app(...)`; monkeypatch `resolve_extractor` (at the reference
  the router module imported) to a `FakeExtractor`, and `suggest_schema_from_file` to a canned
  `TargetSchema`, so NO live VLM call runs in CI.
- Cases: `suggest-schema` returns a schema dict; `ingest` returns records + report with the sidecar
  columns; a malformed `schema` field -> 400; no `file`/`files` -> 400; **auth required** -> 401
  without the bearer token (proves the middleware wraps the new routes).
- One gated live smoke (`OPENAI_API_KEY_PERSONAL`), excluded from CI.
- Tests need the `[web]` extra (fastapi + `python-multipart`) + `[documents]` (Pillow/pymupdf); CI
  already installs `[documents]` (from #1508) — the `[web]` install for this job must include
  `python-multipart`.

## Dependencies

- Add `python-multipart>=0.0.9` to the `[web]` optional-dependency extra (FastAPI `UploadFile`/`Form`
  require it). Verify the web CI/test lane installs `[web]`.

## Scope (YAGNI)

**In:** the two sync endpoints + router + `create_app` wiring + `python-multipart` dep + offline
tests. **Out:** async jobs, streaming/progress, the Web UI frontend (next surface), rate limiting,
persistence of uploads.

## Open risks

- **Sync timeout on big batches:** a large upload (many docs x seconds each) can exceed a proxy/HTTP
  timeout. Accepted for v1 (the async path is the documented next step); the response's `report`
  still lets a client see partial success. Do NOT add a silent doc cap without surfacing it.
- **Temp-file lifecycle:** ensure the `TemporaryDirectory` is cleaned even when `ingest_documents`
  raises — use a `with`/`finally`.
