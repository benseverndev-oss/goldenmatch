# Document Ingest Phase 2 — MCP tool + CLI + AI-assisted schema

**Goal.** Expose the Phase 1 `goldenmatch.documents` capability on the MCP and CLI surfaces,
and add **AI-assisted schema generation** so users don't hand-author the target schema — a VLM
proposes it from a sample document into a reviewable JSON file.

**Builds on:** Phase 1 (`ingest_documents`, `TargetSchema`, `vlm_backend`), currently in PR #1508.
This spec's branch is stacked on `feat/document-image-ingest`.

## Decisions (settled in brainstorming)

- **Two-step explicit flow:** `suggest-schema` (VLM proposes a schema file) → user reviews/edits →
  `run` (ingest the pile against that file). Inline auto-infer (`run` with no schema) is deferred —
  the point of a *file* is reviewing the AI's guess before a batch, and a fixed schema keeps the
  batch deterministic.
- **Schema as a JSON file** (the serializable form of `TargetSchema`, needed by both MCP and CLI):
  ```json
  {"fields": [
    {"name": "full_name", "kind": "text", "hint": "person's full name"},
    {"name": "email", "kind": "email"}
  ]}
  ```
- **Names:** CLI `goldenmatch ingest-docs {suggest-schema, run}`; MCP `documents_suggest_schema`,
  `documents_ingest`.

## Components / files

| path | responsibility | notes |
|---|---|---|
| `goldenmatch/documents/schema_io.py` | `TargetSchema` ⇄ JSON: `schema_to_dict`, `schema_from_dict`, `load_schema(path)`, `save_schema(schema, path)` | pure, offline |
| `goldenmatch/documents/suggest.py` | `suggest_schema(pages, *, extractor_or_transport, model) -> TargetSchema`: VLM proposes fields from a sample doc | reuses the injectable-transport pattern from `vlm_backend.py`; offline-testable |
| `goldenmatch/mcp/document_tools.py` | `DOCUMENT_TOOLS = [Tool(documents_suggest_schema…), Tool(documents_ingest…)]` + `handle_document_tool(name, args)` | mirrors `agent_tools.py` (`AGENT_TOOLS` + `handle_agent_tool`) |
| `goldenmatch/cli/ingest_docs.py` | `ingest_docs_app` (typer sub-app) with `suggest-schema` + `run` commands | mirrors `identity_app`/`memory_app` sub-app style |
| wire-in: `goldenmatch/mcp/server.py` | register `DOCUMENT_TOOLS` + `handle_document_tool` in **all four** touch points (see below) | mirrors each existing `*_TOOL_NAMES` frozenset |
| wire-in: `goldenmatch/cli/main.py` | `app.add_typer(ingest_docs_app, name="ingest-docs")` | mirrors `identity_app` registration |

**MCP wiring is four places, not one** (each existing `*_TOOLS` pair is registered in all of them;
missing one — e.g. the aggregator `dispatch()` — silently breaks that access path): (1) the
module-level `TOOLS` union list, (2) `create_server()`'s inline `list_tools()` return, (3)
`call_tool()`'s dispatch chain, (4) the standalone `dispatch()` used by the goldensuite-mcp
aggregator. The plan must enumerate all four.

`suggest.py` reuses only the **mechanical** halves of `vlm_backend.py`: `loader.load_pages` for the
sample, and the base64-data-URI image encoding + injectable `transport` (factor the shared
`_urllib_transport` / image-payload helper out of `vlm_backend.py` so both files import it rather
than duplicating). The **prompt is written fresh** — `vlm_backend._instruction()` walks a known
`TargetSchema` to extract; suggestion has no schema yet and needs a different prompt asking the VLM
to *propose* fields (name/kind/hint). Do NOT try to reuse `_instruction()`.

## Data flow

- **suggest-schema:** `load_pages(sample)` → `suggest_schema(pages, …)` (VLM returns a fields list)
  → `TargetSchema` → `save_schema(schema, out_path)`. The user edits the file.
- **run:** `load_schema(schema_path)` → `ingest_documents(paths, schema)` → DataFrame → write
  `--out` (CSV or parquet by extension) + print the `IngestReport` to stderr.
- **MCP `documents_ingest`:** a DataFrame can't cross MCP, so return
  `{"records": [...], "report": {n_files, n_rows, errors}}` as JSON; an optional `out_path` also
  writes a file and returns its path.
- **MCP `documents_suggest_schema`:** returns the proposed schema as JSON (the client saves it).

## Error handling

- `suggest_schema`: malformed VLM JSON, `finish_reason == "length"`, or an empty/invalid fields
  list → raise a clear error; **do not write a garbage schema file**.
- `run`: missing schema file or invalid schema JSON → fail fast with a clear message before
  processing. Per-document batch errors are already captured by `ingest_documents`'s `IngestReport`.
- MCP handlers wrap exceptions as tool-error responses (match the existing `handle_*_tool`
  convention). CLI commands raise `typer.Exit(code=…)` on bad input (match `anomalies.py`).

## Testing (offline-first, matches repo culture)

- `schema_io`: round-trip (`save_schema` → `load_schema` yields an equal `TargetSchema`); malformed
  file → clear error. Pure.
- `suggest`: fake transport returns a proposed-fields JSON → `suggest_schema` yields the expected
  `TargetSchema`; malformed/empty → error. Offline.
- MCP `document_tools`: call `handle_document_tool` with a `FakeExtractor`/fake transport injected;
  assert the returned JSON shape for both tools. No network.
- CLI `ingest-docs`: use typer's `CliRunner`; inject the fake backend by monkeypatching
  `resolve_extractor` **at the reference the CLI module imported** (`goldenmatch.cli.ingest_docs
  .resolve_extractor`, not `documents.config.resolve_extractor`) — patching the wrong reference is a
  known foot-gun here. Assert `suggest-schema` writes a valid file and `run` writes the records file
  + non-zero rows.
- The two MCP tools' `inputSchema` arg names (`paths`, `schema_path`/`schema`, `out_path`,
  `backend`, `model`) are spelled out in the plan, not here.
- One optional gated live smoke (`OPENAI_API_KEY_PERSONAL`), excluded from CI.

## Scope (YAGNI)

**In:** schema JSON I/O, `suggest_schema`, the two MCP tools, the `ingest-docs` CLI sub-app, wiring.
**Out (deferred):** inline auto-infer (`run` with no schema); local OCR backend (Phase 3);
review-queue integration (Phase 3); a one-shot ingest→dedupe command (compose via existing
`dedupe`/`find_duplicates` instead).

## Open risks

- **Suggest quality:** the VLM's proposed field names/kinds are a *starting point the user edits*,
  so imperfect suggestions are acceptable — but measure on a real document before claiming it's good.
- **CLI test injection:** the fake backend needs a clean seam. Prefer monkeypatching
  `resolve_extractor` over adding a production-only injection param; the plan will specify.
