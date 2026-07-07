# Document Ingest A2A skills — design

**Goal.** Expose document ingest as two agent-to-agent (A2A) skills so other agents can suggest a
schema from and extract records from documents. Thin surface over the shipped MCP handler.

**Builds on:** the MCP `handle_document_tool` (Phase 2) + `goldenmatch.documents` (which runs through
documents-core natively). All merged to main. Frontend-independent; pure Python.

## Decisions (settled in brainstorming)

- **Two skills, same ids as the MCP tools** (no cross-surface naming divergence — the api_parity work
  deliberately reconciled such splits): `documents_suggest_schema`, `documents_ingest`.
- **Path-based** (A2A is stateless HTTP with no file upload): callers pass server-accessible paths,
  exactly like the MCP tools.
- **Delegate to the MCP handler** (`handle_document_tool`) so the JSON contract, config->400 mapping,
  and native-fallback behavior are the single source of truth — no duplicated logic.

## Components / files

| path | change |
|---|---|
| `goldenmatch/a2a/server.py` | add two `_SKILLS` entries (`{id, name, description, inputModes, outputModes}`) |
| `goldenmatch/a2a/skills.py` | add one `dispatch_skill` branch delegating both ids to `handle_document_tool` |
| `parity/goldenmatch.yaml` | add both ids to `a2a_skills.python_only` (sorted) |
| `tests/test_a2a.py` | bump the hard-coded skill-count assertion (38 -> 40); add dispatch tests |

## Skill card entries (`_SKILLS`)

```python
{"id": "documents_suggest_schema", "name": "Suggest Document Schema",
 "description": "Propose an extraction schema from a sample document (PDF/image).",
 "inputModes": ["application/json"], "outputModes": ["application/json"]},
{"id": "documents_ingest", "name": "Ingest Documents",
 "description": "Extract records from documents against a schema; records ready for dedupe.",
 "inputModes": ["application/json"], "outputModes": ["application/json"]},
```

## Dispatch (`skills.py`)

Add near the other branches in `dispatch_skill(skill_id, params, allow_pprl=False)`:
```python
if skill_id in ("documents_ingest", "documents_suggest_schema"):
    from goldenmatch.mcp.document_tools import handle_document_tool
    return handle_document_tool(skill_id, params)
```
(Other skills delegate to `AgentSession`; documents has no session method, so it routes to the MCP
handler — the closest existing single source of truth. `handle_document_tool` returns a plain dict,
which is exactly what `dispatch_skill` returns.)

## Contract

- `documents_suggest_schema {sample_path, backend?, model?}` -> `{"schema": {"fields": [...]}}`.
- `documents_ingest {paths, schema, backend?, model?, drop_empty?}` -> `{"records": [...], "report":
  {"n_files","n_rows","errors":[{"file","error"}]}}`. Records carry the schema columns +
  `_source_file`/`_source_page`/`_extract_confidence`.

## Error handling

`handle_document_tool` raises `ValueError` on bad config (missing key / bad backend / malformed
schema); the A2A server's existing dispatch wraps a raising skill into a failed-task/error response
(mirror the current behavior for the other skills — no new error path needed here).

## Testing (offline)

- `test_dispatch_documents_ingest`: monkeypatch `resolve_extractor` (at the reference
  `document_tools` uses) to a `FakeExtractor`; call `dispatch_skill("documents_ingest", {paths:[...],
  schema:{...}})`; assert `records`/`report` shape. Use a real tiny image temp file so `load_pages`
  succeeds (FakeExtractor returns canned rows).
- `test_dispatch_documents_suggest_schema`: monkeypatch `suggest_schema_from_file` -> a canned
  `TargetSchema`; assert `{"schema": {...}}`.
- `test_agent_card_lists_document_skills`: `build_agent_card()` includes both ids.
- Update the existing `test_agent_card_has_38_skills` (rename/rebump to 40) — and **grep the WHOLE
  REPO for any other hard-coded `38` skill-count literal** (the #1515 count-enumeration lesson). This
  is NOT just the test suite: confirmed doc surfaces also carry "38 skills" and go stale silently
  (they won't red CI): `packages/python/goldenmatch/README.md` (~line 182), `packages/python/
  goldenmatch/llms.txt` (~line 11), `docs/llms.txt` (~line 8). Bump all of them to 40 in the same PR.
- No live calls in CI.

## Parity manifest (`parity/goldenmatch.yaml`)

Add `documents_ingest` and `documents_suggest_schema` to `a2a_skills.python_only` (TS A2A has no
document skills yet), sorted (they land after `configure`). Without this the `api_parity` a2a gate
fails `undeclared_py_only` (the #1515 gotcha). These same ids are already declared under
`mcp_tools.python_only` from Phase 2 — the a2a surface is a separate partition and needs its own
entries.

## Scope (YAGNI)

**In:** 2 skill-card entries + the dispatch branch + the parity declaration + the count-test bump +
dispatch/card tests. **Out:** file upload (path-based by design), streaming, the TS A2A side (part of
the TS surface later), any new `AgentSession` method.

## Open risks

- **Count-assertion drift:** `test_agent_card_has_38_skills` (and any `"38 skills"` docstring) MUST be
  updated to 40, or CI reds on a count mismatch — same failure mode as #1515's `len(TOOLS)==74`.
