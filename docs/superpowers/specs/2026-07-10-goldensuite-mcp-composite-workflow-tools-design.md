# goldensuite-mcp composite workflow tools — design

**Date:** 2026-07-10
**Status:** Approved (brainstorm) → spec
**Package:** `packages/python/goldensuite-mcp`
**Depends on:** curated tool listing (PR #1639) + `suite_find_tools` (PR #1640)

## Problem

The unified `goldensuite-mcp` endpoint exposes ~105 tools. PR #1639 curates the
default `list_tools` down to ~25 headline verbs and PR #1640 adds
`suite_find_tools` for discovery. Those solve *tool count*. They do not solve
*round-trips*: the common happy paths (dedupe a file, match two sources, assess a
file, clean-then-dedupe) each take an agent 3–4 sequential tool calls, and a
non-agent caller has to know the exact sequence.

Composite workflow tools encode those happy paths as **one MCP call each** that
orchestrates the underlying tools, so an agent does the common thing in one hop
and a human gets "CSV in → result out" without chaining.

## Goals

- Four composite tools on `goldensuite-mcp`: `dedupe_file`, `match_sources`,
  `assess_file`, `clean_and_dedupe`.
- One call runs the multi-step path; returns a **merged** result (human `summary`
  + structured per-step state + outputs) so both agents and humans are served.
- Thin orchestration: composites call the **already-aggregated dispatchers**
  (`name_to_dispatch`), i.e. the exact code path a user chaining the tools would
  hit — guaranteeing behavior parity with the granular tools.
- Composites are discoverable (`suite_find_tools`) and listed by default
  (`CURATED_TOOLS`).

## Non-goals

- Not replacing the granular tools. Composites are additive; the granular tools
  stay for recovery and non-standard flows.
- Not `action`-style god-tools. Each composite has a real, specific input schema.
- Not available on the standalone `goldenmatch-mcp`. Per decision **PD2**
  (consolidate on the unified endpoint), `dedupe_file`/`match_sources` live only
  in `goldensuite-mcp` even though they are goldenmatch-only. Accepted tradeoff.
- No new persistence. Composites rely on the tools' existing server-side session
  state (see "Why the dispatcher seam").

## Why the dispatcher seam (not the Python APIs)

Two facts about the current tools make calling the dispatchers the correct seam:

1. **Inline-or-path inputs.** Every goldenmatch input tool accepts inline file
   bytes (`file_content` + `filename`) *or* a server `file_path` via the shared
   `_ingest` resolver (PR #1613). So a composite can upload once and thread the
   returned path to later steps. Cross-package steps (goldencheck `scan`) are *not*
   in the `_ingest` resolver — they take a bare `file_path` and read it directly;
   threading the server path still works because the path is readable, the
   composite just doesn't rely on the resolver for those steps.
2. **Shared server-side session state.** `agent_deduplicate` / `agent_match_sources`
   populate the server session; `export_results(output_path)` exports the *last
   run*. Calling the dispatchers in order threads this state implicitly — exactly
   what a human calling the tools in sequence gets.

Calling the underlying Python APIs directly would re-implement this orchestration
and could drift from what the MCP tools actually do. The dispatcher seam keeps
composites thin and parity-guaranteed.

## Architecture

### Module

New `goldensuite_mcp/composites.py`, isolated from `server.py`:

- A **composite spec** per workflow: `name`, `description`, `inputSchema`, and an
  `orchestrate(dispatch, args) -> dict` function. `dispatch` is the aggregated
  `name_to_dispatch` table (a `dict[str, Callable[[str, dict], dict]]`).
- `build_composites(name_to_dispatch) -> tuple[list[Tool], dict[str, Callable]]`
  returns the composite `Tool` objects plus a dispatch entry per composite (each
  entry closes over `name_to_dispatch`).

`server.py` calls `build_composites` inside `_aggregate`, **after** the
sub-package adapters (so the dispatch table is complete) and **before** the
`suite_find_tools` catalog snapshot (so composites appear in discovery). Composite
names are added to `CURATED_TOOLS`.

Boundary check: `composites.py` depends only on a dispatch-table interface
(`str, dict -> dict`), so each `orchestrate` fn is unit-testable with a fake
dispatch table and no sub-packages imported.

### Step helper

A small internal helper runs one step and normalizes success/failure:

```
run_step(dispatch, tool_name, args) -> (ok: bool, result: dict)
```

- Calls `dispatch[tool_name](tool_name, args)`.
- Treats a raised exception **or** a returned `{"error": ...}` as failure.
- Missing tool in the table (optional dep not installed) → failure with a clear
  message.

Each `orchestrate` fn builds a `steps` list, short-circuiting on the first failure.

## The four composites

All accept the same file input as the underlying tools: inline `file_content` +
`filename`, or an existing server `file_path`. Upload happens **once** per input;
the returned path is threaded to subsequent steps.

### `dedupe_file`

Single-source dedupe. Chain:

1. `upload_dataset(file_content, filename)` → `path`
2. `auto_configure(file_path=path)` → `config` (surfaced for transparency/reuse)
3. `agent_deduplicate(file_path=path, config=config)` → clusters + confidence gating
4. `export_results(output_path=<gen>)` → `golden_path`

`output_path` is generated under the uploads dir (e.g. `<stem>.golden.csv`) and
returned. `exclude_columns` passes through to steps 2–3.

### `match_sources`

Cross-source linkage. Chain:

1. `upload_dataset` × 2 → `path_a`, `path_b`
2. `agent_match_sources(file_a=path_a, file_b=path_b, config?)` → matched pairs
3. `export_results(output_path=<gen>)` → `matches_path`

Input mirrors `agent_match_sources`: `file_a_content`/`file_a_name` +
`file_b_content`/`file_b_name`, or `file_a`/`file_b` paths.

### `assess_file`

Read-only readiness report. Chain:

1. `upload_dataset` → `path`
2. `analyze_data(file_path=path)` → profile (goldenmatch)
3. `scan(path)` → data-quality findings (goldencheck)

No export, no mutation. `summary` combines "N rows, K columns, dedupe-ready?" with
the quality headline. If `scan` is unavailable (goldencheck `[mcp]` extra not
installed), step 3 records `ok:false` with a clear note and the composite still
returns the profile (`ok:true` overall, degraded).

### `clean_and_dedupe`

Standardize then dedupe. Chain:

1. `upload_dataset` → `path`
2. `run_transforms(file_path=path, output_path=<gen cleaned>)` → `cleaned_path`
3. `agent_deduplicate(file_path=cleaned_path)` → clusters
4. `export_results(output_path=<gen>)` → `golden_path`

Uses goldenmatch's **`run_transforms`** tool, *not* goldenflow's raw `transform`.
`run_transforms` is a goldenmatch tool (inline-or-path via `_ingest`) that runs
goldenflow's normalization under the hood, **writes the cleaned CSV to a caller-
supplied `output_path`, and returns it** — so there is a concrete cleaned path to
thread into step 3. (Goldenflow's own `transform` MCP tool takes a YAML config
*path* and returns a manifest with no output file, so it is the wrong seam here.)

`run_transforms` applies goldenmatch's built-in normalization set
(`TransformConfig(mode="silent")`: phone → E.164, dates → ISO, categorical
spelling, Unicode). No caller-supplied recipe in the first cut — the default set
is the goldenmatch-blessed normalization and avoids changing match semantics
unexpectedly. A configurable recipe can be a later addition if needed.

Note: because `run_transforms` wraps goldenflow internally, `clean_and_dedupe` is
effectively a **goldenmatch-only** chain (only `assess_file`'s `scan` step is
genuinely cross-package), though it still lives in `goldensuite-mcp` per the
placement decision.

## Return contract (all four)

```jsonc
{
  "workflow": "dedupe_file",
  "ok": true,
  "summary": "288 records -> 172 entities; 116 merged, 14 to review. Facility mode on.",
  "steps": [
    { "step": "upload",        "ok": true, "path": "/uploads/derm.csv", "rows": 288 },
    { "step": "auto_configure","ok": true, "matchkeys": ["exact(npi)", "weighted(full_name,phone)"] },
    { "step": "deduplicate",   "ok": true, "n_clusters": 172, "auto_merge": 98, "review": 14, "reject": 4 },
    { "step": "export",        "ok": true, "golden_path": "/uploads/derm.golden.csv" }
  ],
  "config": { /* config used, where relevant */ },
  "outputs": { "golden_path": "/uploads/derm.golden.csv", "clusters_preview": [ /* first N */ ] }
}
```

- `summary`: one human-readable line.
- `steps`: ordered, one entry per attempted step, each with `step`, `ok`, and that
  step's key outputs. Ends at the first failure.
- `config` / `outputs`: workflow-relevant structured results (absent for
  `assess_file`, which has no config/export).

### Error handling

- A step failure (raised exception or `{"error"}`) short-circuits: `ok:false`, the
  failed step recorded with its error, subsequent steps omitted, `summary` states
  where it died. No partial-success ambiguity.
- `assess_file` is the one degraded-mode exception: a missing optional sub-tool
  (e.g. goldencheck not installed) records that step `ok:false` but the composite
  still returns the steps that succeeded (`ok:true`, degraded) — it is a read-only
  report, not a transaction.

## Curation & discovery

- All four names added to `CURATED_TOOLS` → listed by default.
- Registered before the `suite_find_tools` snapshot → returned by discovery with
  full `inputSchema`.

## Testing

Fixtures-first with tiny CSVs (reuse existing suite-mcp test fixtures / small
synthetic frames):

1. **Shape** — each composite returns the merged contract (keys present, `steps`
   ordered, `ok` correct).
2. **Threading** — the path from `upload_dataset` reaches later steps; generated
   `output_path` is returned in `outputs`.
3. **Parity** — a composite's result matches calling the same tools by hand in
   sequence through the aggregator (same clusters / same export path).
4. **Failure injection** — a fake dispatch table whose step-K returns `{"error"}`
   (or raises) → composite short-circuits, `ok:false`, `steps` ends at K.
5. **Degraded assess_file** — `scan` missing → step `ok:false`, composite
   `ok:true` with the profile still present.
6. **Discovery + curation** — each composite is in the default listing and in
   `suite_find_tools` output.

Unit tests use a fake dispatch table (no sub-packages imported); one end-to-end
test per composite runs through the real aggregated dispatch on a fixture file.

## Build order

`dedupe_file` first as the reference implementation (module scaffold + step
helper + return contract + its tests). The other three reuse the identical spec
shape and helper. Lands as `feat/goldensuite-mcp-composites` off `main` once
#1639/#1640 merge (rebase onto the merged main so the `_aggregate` registration
point and `CURATED_TOOLS` are present).

## Risks / open items (pinned at implementation)

- **Exact step outputs.** `steps[*]` field names (e.g. what `agent_deduplicate`
  returns for cluster counts, what `auto_configure` exposes as `matchkeys`) are
  read from the real tool returns during implementation; the spec fixes the shape,
  not every leaf field.
- **`clean_and_dedupe` normalization.** Fixed to `run_transforms`' built-in set
  (`TransformConfig(mode="silent")`); no caller recipe in the first cut. A fixture
  test asserts the cleaned file is written to the generated `output_path` and
  threaded into dedupe.
- **`output_path` location.** Generated under the existing uploads/allowed-root
  dir so it passes `safe_path`; never a caller-controlled absolute path unless it
  already resolves under the allowed root.
