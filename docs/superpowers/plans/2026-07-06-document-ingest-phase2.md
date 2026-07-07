# Document Ingest Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose `goldenmatch.documents` on the MCP + CLI surfaces and add AI-assisted schema generation (a VLM proposes a reviewable JSON schema file from a sample doc).

**Architecture:** A JSON schema-file round-trip (`schema_io`), a `suggest_schema` VLM call that reuses the extracted OpenAI transport/image helpers, two MCP tools (`documents_suggest_schema`, `documents_ingest`) wired like `ROUTING_TOOLS`, and a `goldenmatch ingest-docs` typer sub-app (`suggest-schema`, `run`). Everything offline-testable via the injectable transport / `FakeExtractor`.

**Tech Stack:** Python 3.11+, Polars, typer, the `mcp` package (`mcp.types.Tool`/`TextContent`), stdlib `urllib`. TDD with pytest.

**Spec:** `docs/superpowers/specs/2026-07-06-document-ingest-phase2-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docingest-p2` (branch `feat/document-ingest-phase2`, stacked on `feat/document-image-ingest`). Package root: `packages/python/goldenmatch`. Do NOT push, do NOT touch `main`.
- **Test env** (run from `packages/python/goldenmatch`; note BOTH PYTHONPATH entries point at THIS worktree so `import goldenflow` uses the clean copy — see the phase-1 plan for why):
  ```bash
  cd D:/show_case/gm-docingest-p2/packages/python/goldenmatch
  PY="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="D:/show_case/gm-docingest-p2/packages/python/goldenmatch;D:/show_case/gm-docingest-p2/packages/python/goldenflow"
  export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
- **Prereq:** ensure the `mcp` package is importable (Task 4+): `"$PY" -c "import mcp" || "$PY" -m pip install "mcp>=1.0"`.
- **Lint before each commit:** `"$PY" -m ruff check goldenmatch/documents goldenmatch/mcp/document_tools.py goldenmatch/cli/ingest_docs.py tests/documents`.
- **Commit trailers:** copy the two trailer lines from an existing commit (`git log -1 --format=%B`). Use `git -c commit.gpgsign=false commit`.
- **Regression guard:** after Tasks 2, always re-run `tests/documents/test_vlm_backend.py` — Task 2 refactors shared helpers OUT of `vlm_backend.py` and must not change its behavior.

---

## File structure (locked)

| path | responsibility |
|---|---|
| `goldenmatch/documents/schema_io.py` | `TargetSchema` ⇄ dict/JSON file: `schema_to_dict`, `schema_from_dict`, `save_schema`, `load_schema` |
| `goldenmatch/documents/_openai.py` | shared VLM plumbing extracted from `vlm_backend.py`: `Transport`, `urllib_transport(api_key)`, `image_blocks(pages)` |
| `goldenmatch/documents/vlm_backend.py` (modify) | import the extracted helpers instead of defining them inline |
| `goldenmatch/documents/config.py` (modify) | expose `resolve_api_key() -> str` (factored out of `resolve_extractor`) |
| `goldenmatch/documents/suggest.py` | `suggest_schema(pages, *, transport, model)` + `suggest_schema_from_file(path, *, backend, model)` |
| `goldenmatch/mcp/document_tools.py` | `DOCUMENT_TOOLS`, `DOCUMENT_TOOL_NAMES`, `handle_document_tool(name, args) -> dict` |
| `goldenmatch/mcp/server.py` (modify) | register in 4 spots (import+frozenset, `TOOLS`, `dispatch()`, `list_tools()`, `call_tool()`) |
| `goldenmatch/cli/ingest_docs.py` | `ingest_docs_app` typer sub-app: `suggest-schema`, `run` |
| `goldenmatch/cli/main.py` (modify) | `app.add_typer(ingest_docs_app, name="ingest-docs")` |
| `tests/documents/test_schema_io.py`, `test_suggest.py`, `test_document_tools.py`, `test_cli_ingest_docs.py` | offline tests |

---

## Task 1: Schema JSON round-trip

**Files:** Create `goldenmatch/documents/schema_io.py`; Test `tests/documents/test_schema_io.py`

- [ ] **Step 1: Failing test.**
  ```python
  import json
  import pytest
  from goldenmatch.documents.schema_io import (
      load_schema, save_schema, schema_from_dict, schema_to_dict,
  )
  from goldenmatch.documents.types import Field, TargetSchema

  SCHEMA = TargetSchema([Field("full_name"), Field("email", kind="email", hint="work email")])


  def test_round_trip_dict():
      d = schema_to_dict(SCHEMA)
      assert d == {"fields": [
          {"name": "full_name", "kind": "text", "hint": None},
          {"name": "email", "kind": "email", "hint": "work email"}]}
      assert schema_from_dict(d) == SCHEMA


  def test_round_trip_file(tmp_path):
      p = tmp_path / "schema.json"
      save_schema(SCHEMA, p)
      assert json.loads(p.read_text())["fields"][0]["name"] == "full_name"
      assert load_schema(p) == SCHEMA


  def test_load_rejects_malformed(tmp_path):
      p = tmp_path / "bad.json"
      p.write_text('{"nope": 1}')
      with pytest.raises(ValueError, match="fields"):
          load_schema(p)
  ```
- [ ] **Step 2: Run → fail.** `"$PY" -m pytest tests/documents/test_schema_io.py -q`
- [ ] **Step 3: Implement.**
  ```python
  """TargetSchema <-> JSON. Pure stdlib, offline. The serializable schema form used by the
  MCP tools and the CLI."""
  from __future__ import annotations

  import json
  from pathlib import Path

  from goldenmatch.documents.types import Field, TargetSchema


  def schema_to_dict(schema: TargetSchema) -> dict:
      return {"fields": [{"name": f.name, "kind": f.kind, "hint": f.hint}
                         for f in schema.fields]}


  def schema_from_dict(d: dict) -> TargetSchema:
      if not isinstance(d, dict) or "fields" not in d or not isinstance(d["fields"], list):
          raise ValueError("schema must be an object with a 'fields' list")
      fields = []
      for item in d["fields"]:
          if "name" not in item:
              raise ValueError(f"schema field missing 'name': {item!r}")
          fields.append(Field(name=item["name"], kind=item.get("kind", "text"),
                              hint=item.get("hint")))
      if not fields:
          raise ValueError("schema has no fields")
      return TargetSchema(fields)


  def save_schema(schema: TargetSchema, path) -> None:
      Path(path).write_text(json.dumps(schema_to_dict(schema), indent=2), encoding="utf-8")


  def load_schema(path) -> TargetSchema:
      return schema_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
  ```
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(documents): schema JSON round-trip (schema_io)`.

---

## Task 2: Extract shared OpenAI helpers + `resolve_api_key`

Refactor only — no behavior change. Move the transport factory and image-encoding out of
`vlm_backend.py` into `_openai.py`, and factor the key lookup out of `config.resolve_extractor`.

**Files:** Create `goldenmatch/documents/_openai.py`; Modify `vlm_backend.py`, `config.py`; Test `tests/documents/test_openai_helpers.py`

- [ ] **Step 1: Failing test.**
  ```python
  from goldenmatch.documents._openai import image_blocks, urllib_transport
  from goldenmatch.documents.types import PageImage


  def test_image_blocks_emit_data_uris():
      pages = [PageImage(b"\x89PNG\r\n\x1a\n0", 1, 1, 0), PageImage(b"abc", 1, 1, 1)]
      blocks = image_blocks(pages)
      assert len(blocks) == 2
      assert blocks[0]["type"] == "image_url"
      assert blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


  def test_urllib_transport_is_callable():
      assert callable(urllib_transport("k"))
  ```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `_openai.py`** (move the exact logic out of `vlm_backend.py`):
  ```python
  """Shared OpenAI-vision plumbing for the documents backends (extractor + schema suggest).
  Kept dependency-free (stdlib urllib) and transport-injectable so callers test offline."""
  from __future__ import annotations

  import base64
  import json
  from collections.abc import Callable

  from goldenmatch.documents.types import PageImage

  ENDPOINT = "https://api.openai.com/v1/chat/completions"
  Transport = Callable[[dict], dict]


  def urllib_transport(api_key: str) -> Transport:
      import urllib.request

      def send(payload: dict) -> dict:
          body = json.dumps(payload).encode()
          req = urllib.request.Request(
              ENDPOINT, data=body,
              headers={"Authorization": f"Bearer {api_key}",
                       "Content-Type": "application/json"})
          with urllib.request.urlopen(req, timeout=120) as r:
              return json.loads(r.read())

      return send


  def image_blocks(pages: list[PageImage]) -> list[dict]:
      out = []
      for pg in pages:
          b64 = base64.b64encode(pg.png_bytes).decode()
          out.append({"type": "image_url",
                      "image_url": {"url": f"data:image/png;base64,{b64}"}})
      return out
  ```
  Then edit `vlm_backend.py`: delete its inline `_urllib_transport` and the base64 loop in
  `_payload`; import `from goldenmatch.documents._openai import Transport, image_blocks, urllib_transport`;
  use `urllib_transport(api_key)` where `_urllib_transport(api_key)` was, and build `content` as
  `[{"type": "text", "text": _instruction(schema)}] + image_blocks(pages)`.
  Then edit `config.py`: add
  ```python
  def resolve_api_key() -> str:
      key = next((os.environ[e] for e in _KEY_ENV_ORDER if os.environ.get(e)), None)
      if not key:
          raise ValueError("no OpenAI API key found; set OPENAI_API_KEY_PERSONAL "
                           "(or OPENAI_API_KEY)")
      return key
  ```
  and have `resolve_extractor` call it (`VLMExtractor(api_key=resolve_api_key(), model=model)`).
- [ ] **Step 4: Run → pass**, then the regression guard: `"$PY" -m pytest tests/documents/test_vlm_backend.py tests/documents/test_ingest.py -q` (all still green).
- [ ] **Step 5: Commit** `refactor(documents): extract shared OpenAI transport/image helpers + resolve_api_key`.

---

## Task 3: `suggest_schema`

**Files:** Create `goldenmatch/documents/suggest.py`; Test `tests/documents/test_suggest.py`

- [ ] **Step 1: Failing test.**
  ```python
  import json
  import pytest
  from goldenmatch.documents.suggest import suggest_schema
  from goldenmatch.documents.types import PageImage

  PAGES = [PageImage(b"\x89PNG\r\n\x1a\n0", 10, 10, 0)]


  def _resp(fields):
      return {"choices": [{"message": {"content": json.dumps({"fields": fields})}}]}


  def test_suggests_fields_from_a_sample():
      fields = [{"name": "full_name", "kind": "text", "hint": "the person's name"},
                {"name": "email", "kind": "email"}]
      out = suggest_schema(PAGES, transport=lambda p: _resp(fields), model="gpt-4o")
      assert out.column_names() == ["full_name", "email"]
      assert out.fields[1].kind == "email"


  def test_empty_fields_is_an_error():
      with pytest.raises(ValueError):
          suggest_schema(PAGES, transport=lambda p: _resp([]), model="gpt-4o")


  def test_malformed_json_is_an_error():
      bad = {"choices": [{"message": {"content": "not json"}}]}
      with pytest.raises(ValueError):
          suggest_schema(PAGES, transport=lambda p: bad, model="gpt-4o")


  def test_truncation_is_an_error():
      trunc = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
      with pytest.raises(ValueError, match="truncat"):
          suggest_schema(PAGES, transport=lambda p: trunc, model="gpt-4o")
  ```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.**
  ```python
  """VLM-assisted target-schema suggestion: look at a sample document and PROPOSE fields.
  Distinct prompt from vlm_backend (which extracts against a KNOWN schema); shares only the
  transport + image encoding."""
  from __future__ import annotations

  import json

  from goldenmatch.documents._openai import Transport, image_blocks, urllib_transport
  from goldenmatch.documents.config import resolve_api_key
  from goldenmatch.documents.loader import load_pages
  from goldenmatch.documents.schema_io import schema_from_dict
  from goldenmatch.documents.types import PageImage, TargetSchema

  _PROMPT = (
      "You are shown a sample document. Propose a compact extraction schema: the fields a "
      "person would want pulled from documents like this for record matching (names, "
      "emails, addresses, phones, ids, dates...). Return ONLY JSON:\n"
      '{"fields": [{"name": "<snake_case>", "kind": "text|email|phone|address|date|number", '
      '"hint": "<short guidance>"}, ...]}\n'
      "Prefer 3-12 stable, matchable fields. No prose."
  )


  def suggest_schema(pages: list[PageImage], *, transport: Transport,
                     model: str = "gpt-4o", max_attempts: int = 3) -> TargetSchema:
      payload = {"model": model, "temperature": 0, "max_tokens": 1500,
                 "messages": [{"role": "user",
                               "content": [{"type": "text", "text": _PROMPT}] + image_blocks(pages)}]}
      last = "no response"
      for _ in range(max(1, max_attempts)):
          try:
              resp = transport(payload)
              break
          except Exception as e:  # transport/network: retry
              last = f"{type(e).__name__}: {e}"
      else:
          raise ValueError(f"schema suggestion failed: {last}")
      choice = resp["choices"][0]
      if choice.get("finish_reason") == "length":
          raise ValueError("schema suggestion truncated (finish_reason=length); raise max_tokens")
      text = choice["message"]["content"].strip()
      if text.startswith("```"):
          text = text.split("\n", 1)[1].rsplit("```", 1)[0] if "\n" in text else text
      return schema_from_dict(json.loads(text))  # raises ValueError on empty/malformed


  def suggest_schema_from_file(path, *, backend: str = "vlm", model: str = "gpt-4o") -> TargetSchema:
      if backend != "vlm":
          raise ValueError(f"unknown backend: {backend!r} (Phase 2 supports 'vlm')")
      return suggest_schema(load_pages(path), transport=urllib_transport(resolve_api_key()),
                            model=model)
  ```
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(documents): VLM-assisted suggest_schema`.

---

## Task 4: MCP tools (`document_tools.py`)

**Files:** Create `goldenmatch/mcp/document_tools.py`; Test `tests/documents/test_document_tools.py`

Handler returns a plain `dict` (mirrors `handle_routing_tool`), so it serves both the stdio
`call_tool` and the aggregator `dispatch` paths.

- [ ] **Step 1: Failing test** (inject fakes by monkeypatching the names the module imported):
  ```python
  import json
  import goldenmatch.mcp.document_tools as dt
  from goldenmatch.documents.extractor import FakeExtractor
  from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema

  SCHEMA_JSON = {"fields": [{"name": "full_name"}, {"name": "email", "kind": "email"}]}


  def test_tool_names_and_list():
      names = {t.name for t in dt.DOCUMENT_TOOLS}
      assert names == {"documents_suggest_schema", "documents_ingest"}
      assert dt.DOCUMENT_TOOL_NAMES == names


  def test_suggest_schema_tool(monkeypatch, tmp_path):
      from PIL import Image
      p = tmp_path / "s.png"; Image.new("RGB", (10, 10), "white").save(p)
      schema = TargetSchema([Field("full_name"), Field("email", kind="email")])
      monkeypatch.setattr(dt, "suggest_schema_from_file", lambda path, **k: schema)
      out = dt.handle_document_tool("documents_suggest_schema", {"sample_path": str(p)})
      assert out["schema"] == SCHEMA_JSON


  def test_ingest_tool_returns_records_and_report(monkeypatch, tmp_path):
      from PIL import Image
      a = tmp_path / "a.png"; Image.new("RGB", (10, 10), "white").save(a)
      schema = TargetSchema([Field("full_name"), Field("email")])
      row = ExtractedRow.from_partial({"full_name": "Ada", "email": "a@x.io"}, {}, schema,
                                      source_file="", source_page=0)
      monkeypatch.setattr(dt, "resolve_extractor", lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
      out = dt.handle_document_tool("documents_ingest",
                                    {"paths": [str(a)], "schema": SCHEMA_JSON})
      assert out["report"]["n_rows"] == 1
      assert out["records"][0]["full_name"] == "Ada"
      assert out["records"][0]["email"] == "a@x.io"
  ```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.**
  ```python
  """MCP tools for document/image ingest. Handlers return JSON-serializable dicts, matching
  handle_routing_tool so both the stdio call_tool and aggregator dispatch paths work."""
  from __future__ import annotations

  from mcp.types import Tool

  from goldenmatch.documents import ingest_documents
  from goldenmatch.documents.config import resolve_extractor  # noqa: F401 (monkeypatch seam)
  from goldenmatch.documents.schema_io import schema_from_dict, schema_to_dict
  from goldenmatch.documents.suggest import suggest_schema_from_file  # noqa: F401 (seam)

  DOCUMENT_TOOLS = [
      Tool(
          name="documents_suggest_schema",
          description="Propose a target extraction schema (JSON) from a sample document image/PDF.",
          inputSchema={
              "type": "object",
              "properties": {
                  "sample_path": {"type": "string"},
                  "backend": {"type": "string", "default": "vlm"},
                  "model": {"type": "string", "default": "gpt-4o"},
              },
              "required": ["sample_path"],
          },
      ),
      Tool(
          name="documents_ingest",
          description=("Extract records from documents (PDF/image) against a target schema into "
                       "rows ready for dedupe_df. Returns records + an ingest report."),
          inputSchema={
              "type": "object",
              "properties": {
                  "paths": {"type": "array", "items": {"type": "string"}},
                  "schema": {"type": "object", "description": "schema JSON: {'fields':[...]}"},
                  "backend": {"type": "string", "default": "vlm"},
                  "model": {"type": "string", "default": "gpt-4o"},
                  "drop_empty": {"type": "boolean", "default": True},
                  "out_path": {"type": "string", "description": "optional CSV/parquet to also write"},
              },
              "required": ["paths", "schema"],
          },
      ),
  ]
  DOCUMENT_TOOL_NAMES = frozenset(t.name for t in DOCUMENT_TOOLS)


  def handle_document_tool(name: str, arguments: dict) -> dict:
      if name == "documents_suggest_schema":
          schema = suggest_schema_from_file(
              arguments["sample_path"],
              backend=arguments.get("backend", "vlm"),
              model=arguments.get("model", "gpt-4o"))
          return {"schema": schema_to_dict(schema)}

      if name == "documents_ingest":
          schema = schema_from_dict(arguments["schema"])
          extractor = resolve_extractor(arguments.get("backend", "vlm"),
                                        arguments.get("model", "gpt-4o"))
          df, report = ingest_documents(
              arguments["paths"], schema, extractor=extractor,
              drop_empty=arguments.get("drop_empty", True), return_report=True)
          out_path = arguments.get("out_path")
          if out_path:
              (df.write_parquet if str(out_path).endswith(".parquet") else df.write_csv)(out_path)
          return {
              "records": df.to_dicts(),
              "report": {"n_files": report.n_files, "n_rows": report.n_rows,
                         "errors": [{"file": f, "error": e} for (f, e) in report.errors]},
              **({"out_path": str(out_path)} if out_path else {}),
          }
      raise ValueError(f"unknown document tool: {name}")
  ```
  Note the two `# noqa` imports of `resolve_extractor` / `suggest_schema_from_file` at module scope
  are deliberate: they are the monkeypatch seams the tests patch on `dt.<name>`. Reference them
  inside the handler via the module globals (call `resolve_extractor(...)` / `suggest_schema_from_file(...)`
  as bare names) so the patched versions take effect.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** `feat(mcp): documents_suggest_schema + documents_ingest tools`.

---

## Task 5: Wire MCP tools into `server.py`

**Files:** Modify `goldenmatch/mcp/server.py`; Test `tests/documents/test_document_tools_wiring.py`

Register in the four places, mirroring `ROUTING_TOOLS`.

- [ ] **Step 1: Failing test.**
  ```python
  import goldenmatch.mcp.server as srv


  def test_document_tools_registered_in_all_surfaces():
      names = {t.name for t in srv.TOOLS}
      assert {"documents_suggest_schema", "documents_ingest"} <= names
      # dispatch routes doc tools without falling through to the base handler
      import goldenmatch.mcp.document_tools as dt
      assert dt.DOCUMENT_TOOL_NAMES <= {t.name for t in srv.TOOLS}
  ```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement — edit `server.py` in these exact spots:**
  1. Import (near the other `*_tools` imports, ~line 35):
     `from goldenmatch.mcp.document_tools import DOCUMENT_TOOLS, DOCUMENT_TOOL_NAMES, handle_document_tool`
  2. `TOOLS` union (~line 621): append ` + DOCUMENT_TOOLS`.
  3. `dispatch()` (~line 642, before the final `return _handle_tool(...)`):
     ```python
     if name in DOCUMENT_TOOL_NAMES:
         return handle_document_tool(name, args)
     ```
  4. `list_tools()` (~line 657): append ` + DOCUMENT_TOOLS` to the returned list.
  5. `call_tool()` try-block (~line 916, alongside the ROUTING branch):
     ```python
     if name in DOCUMENT_TOOL_NAMES:
         result = handle_document_tool(name, arguments)
     elif name in ROUTING_TOOL_NAMES:
         result = handle_routing_tool(name, arguments)
     else:
         result = _handle_tool(name, arguments)
     ```
- [ ] **Step 4: Run → pass.** Also `"$PY" -c "import goldenmatch.mcp.server"` (no import error).
- [ ] **Step 5: Commit** `feat(mcp): wire document tools into server (list/dispatch/call)`.

---

## Task 6: CLI `ingest-docs` sub-app

**Files:** Create `goldenmatch/cli/ingest_docs.py`; Modify `goldenmatch/cli/main.py`; Test `tests/documents/test_cli_ingest_docs.py`

- [ ] **Step 1: Failing test** (patch `resolve_extractor` at the CLI module's reference):
  ```python
  import json
  from pathlib import Path
  from PIL import Image
  from typer.testing import CliRunner
  import goldenmatch.cli.ingest_docs as ci
  from goldenmatch.documents.extractor import FakeExtractor
  from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema

  runner = CliRunner()
  SCHEMA = TargetSchema([Field("full_name"), Field("email")])


  def _schema_file(tmp_path):
      p = tmp_path / "schema.json"
      p.write_text(json.dumps({"fields": [{"name": "full_name"}, {"name": "email"}]}))
      return p


  def test_run_writes_records_csv(tmp_path, monkeypatch):
      img = tmp_path / "a.png"; Image.new("RGB", (10, 10), "white").save(img)
      row = ExtractedRow.from_partial({"full_name": "Ada", "email": "a@x.io"}, {}, SCHEMA,
                                      source_file="", source_page=0)
      monkeypatch.setattr(ci, "resolve_extractor", lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
      out = tmp_path / "recs.csv"
      r = runner.invoke(ci.ingest_docs_app, ["run", str(img), "--schema", str(_schema_file(tmp_path)),
                                             "--out", str(out)])
      assert r.exit_code == 0, r.output
      assert out.exists() and "Ada" in out.read_text()


  def test_suggest_schema_writes_file(tmp_path, monkeypatch):
      img = tmp_path / "s.png"; Image.new("RGB", (10, 10), "white").save(img)
      monkeypatch.setattr(ci, "suggest_schema_from_file", lambda path, **k: SCHEMA)
      out = tmp_path / "schema.json"
      r = runner.invoke(ci.ingest_docs_app, ["suggest-schema", str(img), "--out", str(out)])
      assert r.exit_code == 0, r.output
      assert json.loads(out.read_text())["fields"][0]["name"] == "full_name"
  ```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `ingest_docs.py`.**
  ```python
  """`goldenmatch ingest-docs` — suggest a schema from a sample, then ingest documents to rows."""
  from __future__ import annotations

  import typer

  from goldenmatch.documents import ingest_documents
  from goldenmatch.documents.config import resolve_extractor
  from goldenmatch.documents.schema_io import load_schema, save_schema
  from goldenmatch.documents.suggest import suggest_schema_from_file

  ingest_docs_app = typer.Typer(help="Ingest documents (PDF/image) into matchable records.")


  @ingest_docs_app.command("suggest-schema")
  def suggest_schema_cmd(
      sample: str = typer.Argument(..., help="A representative document (PDF/image)."),
      out: str = typer.Option(..., "--out", "-o", help="Write the proposed schema JSON here."),
      backend: str = typer.Option("vlm", help="Extraction backend."),
      model: str = typer.Option("gpt-4o", help="Vision model."),
  ):
      try:
          schema = suggest_schema_from_file(sample, backend=backend, model=model)
      except Exception as e:
          typer.echo(f"schema suggestion failed: {e}", err=True)
          raise typer.Exit(code=1) from e
      save_schema(schema, out)
      typer.echo(f"Wrote {len(schema.fields)}-field schema to {out} -- review before running.", err=True)


  @ingest_docs_app.command("run")
  def run_cmd(
      docs: list[str] = typer.Argument(..., help="Document paths (PDF/image)."),
      schema: str = typer.Option(..., "--schema", "-s", help="Target schema JSON file."),
      out: str = typer.Option(..., "--out", "-o", help="Write records here (.csv or .parquet)."),
      backend: str = typer.Option("vlm", help="Extraction backend."),
      model: str = typer.Option("gpt-4o", help="Vision model."),
  ):
      target = load_schema(schema)
      extractor = resolve_extractor(backend, model)
      df, report = ingest_documents(docs, target, extractor=extractor, return_report=True)
      if str(out).endswith(".parquet"):
          df.write_parquet(out)
      else:
          df.write_csv(out)
      typer.echo(f"{report.n_rows} rows from {report.n_files} docs -> {out}", err=True)
      for f, err in report.errors:
          typer.echo(f"  skipped {f}: {err}", err=True)
  ```
  Then edit `main.py`: `from goldenmatch.cli.ingest_docs import ingest_docs_app` (with the other cli
  imports) and `app.add_typer(ingest_docs_app, name="ingest-docs")` (with the other `add_typer` calls).
- [ ] **Step 4: Run → pass.** Also `"$PY" -m goldenmatch --help` shows `ingest-docs` (or
  `"$PY" -c "from goldenmatch.cli.main import app"` imports clean).
- [ ] **Step 5: Commit** `feat(cli): ingest-docs suggest-schema + run`.

---

## Task 7: README + gated live smoke

**Files:** Modify `goldenmatch/documents/README.md`; Create `tests/documents/test_phase2_live_smoke.py`

- [ ] **Step 1: Live smoke (skipped without a key).**
  ```python
  import os
  import pytest
  from PIL import Image, ImageDraw
  from goldenmatch.documents.suggest import suggest_schema_from_file

  pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY_PERSONAL"),
                                  reason="live VLM smoke; set OPENAI_API_KEY_PERSONAL")


  def test_live_suggest_schema(tmp_path):
      p = tmp_path / "card.png"
      img = Image.new("RGB", (400, 200), "white"); d = ImageDraw.Draw(img)
      d.text((20, 40), "Ada Lovelace", fill="black"); d.text((20, 80), "ada@x.io", fill="black")
      img.save(p)
      schema = suggest_schema_from_file(p)
      assert len(schema.fields) >= 1
  ```
- [ ] **Step 2: Run → SKIPPED** (no key).
- [ ] **Step 3: Append a "CLI + MCP" section to `goldenmatch/documents/README.md`:**
  ````markdown
  ## CLI

  ```bash
  # 1. propose a schema from a sample doc (review/edit the file after)
  goldenmatch ingest-docs suggest-schema samples/form.pdf --out schema.json
  # 2. ingest the pile against it
  goldenmatch ingest-docs run inbox/*.pdf --schema schema.json --out records.csv
  ```

  ## MCP

  - `documents_suggest_schema(sample_path)` → proposed schema JSON
  - `documents_ingest(paths, schema, out_path?)` → `{records, report}` (records ready for dedupe)
  ````
- [ ] **Step 4: Full suite + lint.** `"$PY" -m pytest tests/documents -q` (all pass, live smokes
  skipped) and ruff clean on the new/edited files.
- [ ] **Step 5: Commit** `docs(documents): CLI + MCP usage + gated live smoke`.

---

## Done-when

- `tests/documents/` green (2 live smokes skipped without a key), ruff clean.
- `goldenmatch ingest-docs suggest-schema` / `run` work; `documents_suggest_schema` /
  `documents_ingest` registered in all four `server.py` surfaces (`TOOLS`, `dispatch`,
  `list_tools`, `call_tool`).
- `vlm_backend.py` behavior unchanged (its Phase-1 tests still green after the Task 2 refactor).
- Deferred (not here): inline auto-infer, local OCR backend, review-queue integration.
