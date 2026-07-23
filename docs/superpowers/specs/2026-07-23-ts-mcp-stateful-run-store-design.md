# Adding server-held state to the TypeScript MCP surface — design note

**Status:** Proposal — awaiting a build/no-build decision (2026-07-23)
**Author:** cross-surface parity work (follows the api-surface boundary contract, PRs #2055 / #2057 / #2058)
**Scope:** the `goldenmatch` npm package's MCP server (`src/node/mcp/`) only. Not the core, not the other suite packages.

## The question

The api-surface capability matrix shows GoldenMatch exposing **82 Python MCP tools vs 51 TypeScript**. The parity manifest (`parity/goldenmatch.yaml`) attributes most of the ~39-tool Python-only remainder to a single architectural fact:

> the TS MCP is stateless — its `dedupe()` returns stats + clusters inline (`DedupeResult`), so there is no server-held dataset to query.

This note asks whether we *should* add server-held state to the TS MCP surface, what it would take, and what it would cost — so the decision is made deliberately, not by drift.

## Framing correction: TS is not stateless; the MCP *run surface* is

"TS is stateless" is imprecise. The package already holds durable state in Node:

- **`SqliteIdentityStore`** (`src/node/identity/`) — the full 19-method identity graph, schema byte-identical with Python.
- **`SqliteMemoryStore`** (`src/node/memory/`) — Learning Memory corrections + learned thresholds.

Edge-safety is a **`src/core/**` rule** (no `node:*`), enforced by build separation. `src/node/**` — where the MCP server already lives (it uses `node:fs`/`node:readline` and is explicitly "NOT edge-safe") — is free to be stateful. So:

> **Adding a run store does not touch the edge story.** `dedupe()` in core still returns everything inline and stays pure; the state layer is a server-side wrapper in `src/node/mcp/`.

The thing that is genuinely stateless is only the MCP *dedupe → query-the-result* loop. That is what this note addresses.

## Python already built the template

Python solved the exact problem on 2026-07-12 ("session-backed stateful tools"). Its shape is the reference to port:

- **`mcp/_session_store.py`** — a bounded store (`GOLDENMATCH_MCP_SESSION_MAX` / `_TTL`) mapping a session id → the last run's state.
- **`mcp/_session_ctx.py`** — a `ContextVar` session id set per request from the MCP session, so concurrent sessions don't see each other's runs.
- **`mcp/server.py::_resolve_run_state()`** — resolves state as: module globals (standalone `--file` server) → the current session's last run → a clean "no run loaded" error.

The TS port is the same three pieces in `src/node/mcp/`: a bounded `Map<sessionId, RunState>`, a per-request session id, and a `resolveRunState()` helper.

## What state each Python-only tool actually needs

The Python-only MCP tools split by **what backing they require**, not by difficulty alone:

### Tier 1 — Run cache (read-only). Near-free; no core ports.

`DedupeResult` already carries `clusters`, `stats`, `goldenRecords`, `scoredPairs`, `config`, and `postflightReport` **inline**. So "state" for these is just *keep the last `DedupeResult` keyed by run id*, and each tool is a thin reader:

| Tool | Reads |
|---|---|
| `get_stats` | `run.stats` |
| `list_clusters` / `get_cluster` | `run.clusters` |
| `get_golden_record` | `run.goldenRecords` |
| `export_results` | writes `run.*` to disk |
| `list_runs` | the store's keys (needs a multi-run registry, not just last-run) |
| `upload_dataset` | the "open a run" side — loads a dataset + runs `dedupe()` into the store |

Effort: port Python's session store + wire ~6 readers. No `src/core` change. This is the high-value slice.

### Tier 2 — Run mutation. Needs core ports first (portable-with-port).

| Tool | Blocker |
|---|---|
| `shatter_cluster` | no `shatter`/`recluster` primitive exists in `src/core` (grep: none) |
| `unmerge_record` | no `unmerge` primitive in `src/core` |
| `rollback` | needs run history / snapshots the store doesn't keep |

Each needs the core surgery op ported (the `compare_clusters` pattern from #2057: port the pure op, then wire the tool), *then* the store to mutate.

### Tier 3 — Separate subsystems (not the run cache).

| Tool(s) | Backing |
|---|---|
| `memory_import` | `SqliteMemoryStore` already exists — mostly a wire |
| `identity_audit*` / `identity_claim` / `identity_profile` / `identity_stats` / `identity_worklist` / `identity_show` / `identity_resolve_conflict` | `SqliteIdentityStore` exists but needs the **audit-table extension** |
| `incremental` | needs a persisted base dataset + `match_one` (partial core support) |
| `lineage` | `scoredPairs` is in `DedupeResult`, so it's cache-tier *if* the lineage core is ported |

### Out of scope for the state question

`schema_match`, `analyze_blocking`, `config_weaknesses`, `certify_recall`, `sensitivity`, `retrieve_similar` and the `pprl_*` / `create_domain` / routing tools are **not** blocked on state — they're stateless-compute or separate subsystems (PPRL crypto, domain packs, routing planner, embedding index). They belong to the Track-2 "port the portable compute" axis or stay legitimately Python-only, and are tracked by the existing boundary doc — not this one.

## The two costs to weigh

1. **It partially reverses the boundary we just documented.** Tracks 1/3 (PRs #2055/#2058) positioned TS as the *stateless edge/compute* surface, and the `reference/versioning` policy leans on that. Adding a run-server is un-choosing part of that positioning — a product decision to make explicitly, not a silent slide. (It does **not** contradict the parity gate: a stateful tool that exists on both surfaces simply flips `python_only → shared` in the manifest, same as `compare_clusters`.)

2. **Edge-deploy caveat.** In-process state works for a **long-running Node MCP server** (which is exactly what `goldenmatch-mcp` is). It does **not** survive a serverless/Workers deploy — isolates don't share memory across invocations — which would need external state (the SQLite stores, KV, or Durable Objects). Since the MCP server is already node-only, this is a non-issue *for the MCP server*, but it means "stateful TS MCP" is the Node-server story, never the edge story. Worth stating so nobody expects the run cache to work on Workers.

## Recommendation

If the goal is to shrink the gap, do **Tier 1 only, as its own PR**: port Python's session store into `src/node/mcp/` and wire the ~6 read tools (`get_stats`, `get_cluster`, `list_clusters`, `get_golden_record`, `export_results`, `list_runs`) + `upload_dataset` as the "open a run" entry. It:

- closes ~7 of the ~39 Python-only MCP tools,
- needs **zero** `src/core` changes (edge-safety untouched),
- mirrors an already-shipped Python design (low novelty risk),
- and flips those tools to `shared` in the manifest (the `api_parity` gate keeps it honest).

Defer Tiers 2–3: the mutation tools need core-surgery ports, and the subsystem tools (identity-audit, incremental, lineage) are each a separate, larger commitment. Sequence them only if the run cache proves the demand.

## Decision needed

**Do we cross the stateless-run boundary for the TS MCP server at all?** If yes, Tier 1 is the slice to start with and this note is the plan. If no, the honest close is to *document* the run-query tools as intentionally Python-only in the manifest header (like the distributed engine / web UI already are), so the gap stays declared rather than looking like neglect.

## Non-goals

- No change to `src/core/**` or the edge/WASM story in Tier 1.
- No stateful *browser/Workers* deployment (in-process state is Node-server only).
- No re-opening the `sensitivity`/`certify_recall` "deliberately Python-only" decision.
- No version-lockstep or parity-gate changes beyond the normal `python_only → shared` manifest flips per landed tool.
