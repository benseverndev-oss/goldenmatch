# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [1.7.0] - 2026-07-23

### Added
- **`identity_claim` + `identity_resolve_conflict` MCP tools** (TS↔Python parity, mirroring `goldenmatch/mcp/identity_tools.py`). These complete the identity mutation set (`identity_merge`/`identity_split` already shipped), flipping from `python_only` to `shared` in `parity/goldenmatch.yaml` and taking the TS MCP server to **63 tools** (was 61). Both are class-B net-new core ports — the backing TS core functions did not exist and were ported here.
  - `claimRecord` (`src/core/identity/query.ts` ← Python `claim_record`) reassigns a record to a target entity and emits a `claimed` event on BOTH the gaining and losing entities. Idempotent: claiming a record already in the target is a no-op (`moved: false`, no events). Added `"claimed"` to the `EventKind` union.
  - `mediateConflict` + `openConflicts` (`src/core/identity/mediation.ts` ← Python `mediation.py`) adjudicate a `conflicts_with` pair: `same` keeps the entity intact, `distinct` splits `record_b` out via `manualSplit`, `defer` only logs. Every verdict is a durable `mediation_verdict` evidence edge + a `conflict_mediated` event. Added `"mediation_verdict"` to `EdgeKind` and `"conflict_mediated"` to `EventKind`.
  - New `IdentityStore.edgesByKind(kind, dataset?)` store method (the generic counterpart to `findConflicts`, ports Python `store.edges_by_kind`) added to BOTH the `InMemoryIdentityStore` and `SqliteIdentityStore` backends; `openConflicts` depends on it. Newest-first with a `recordedAt DESC, edgeId DESC` tiebreak for deterministic latest-wins verdict lookups.
  - **Idempotency + re-mediation:** `claimRecord`/`mediateConflict` preserve the identity-event guarding — a claim replay is a no-op, and each mediation mints a unique `mediation:<iso>:<seq>` run_name so re-adjudicating the SAME pair appends a new verdict edge instead of silently no-op'ing against the edge `UNIQUE(entity, a, b, kind, run_name)` constraint. All edge lookups canonicalize via `canonRecordPair`.
  - These mutate the durable identity graph via `SqliteIdentityStore` (`.goldenmatch/identity.db`) — the node-only, stateful, durable-SQLite-backed surface. The new core functions stay edge-safe; only the persistence backend is node-only. They do NOT touch the ephemeral `RUN_STORE`.
  - Tests: `tests/unit/identity-mediation.test.ts` (edge-safe core on `InMemoryIdentityStore`: claim reassign/no-op-replay/both-entity events, `same`/`distinct`/`defer`, `distinct` splits `record_b` via `manualSplit`, re-mediation is NOT a silent no-op, `edgesByKind` filtering), `tests/node/identity/sqlite-store.test.ts` (durable backend `edgesByKind`/`openConflicts`/claim/re-mediation), `tests/unit/mcp-identity-tools.test.ts` (the two tools' dispatch + snake_case wire format).

## [1.6.0] - 2026-07-23

### Added
- **`unmerge_record` + `shatter_cluster` MCP run-surgery tools** (TS↔Python parity, mirroring `goldenmatch/mcp/server.py`'s `_tool_unmerge_record`/`_tool_shatter_cluster`, which delegate to `MatchEngine.unmerge_record`/`unmerge_cluster`). These flip from `python_only` to `shared` in `parity/goldenmatch.yaml`, bringing the TS MCP server to **61 tools** (was 59). Both live in the node-only MCP server (`src/node/mcp/surgery-tools.ts` — `SURGERY_TOOLS`/`SURGERY_TOOL_NAMES`/`handleSurgeryTool`), NOT the edge-safe core.
  - `unmerge_record` removes a record from its cluster in the current run and re-clusters the remaining members from the stored `pairScores` (no re-scoring); the removed record becomes a singleton. `shatter_cluster` breaks a whole cluster into singletons (pair scores discarded).
  - **In-place mutation design:** both tools mutate the CURRENT run (the last `dedupe` in this stdio session) via a new `RunStore.update(runId, newResult)` method, which replaces the stored run's `result` while preserving its `runId` / `createdAt` / `rowsById` / `sourcePath` / insertion order / current pointer. Surgery edits an existing run, so it must NOT `put` a new run id — a subsequent `get_stats`/`get_cluster` reads the mutated run under the same id. The stats on the rebuilt result are recomputed the same way the dedupe pipeline computes them.
  - **Memory-write parity:** the surgery kernels (`unmergeRecord`/`unmergeCluster` in `core/cluster.ts`) accept an OPTIONAL `memoryStore` that auto-writes `reject` corrections; Python's MCP path passes none, so the TS wiring omits it too (no auto-emitted corrections).
  - Tests: `tests/unit/mcp-surgery-tools.test.ts` (record pulled out + remainder re-clustered via stored pair scores, members → singletons, run id preserved across the in-place mutation, and no-run / unknown-cluster-id / missing-record-id error paths).

## [1.5.0] - 2026-07-23

### Added
- **`list_runs` + `rollback` MCP tools** (TS↔Python parity, mirroring `goldenmatch/core/rollback.py`). These flip from `python_only` to `shared` in `parity/goldenmatch.yaml`, bringing the TS MCP server to **59 tools** (was 57 after the Tier-1 run store). Both live in the node-only MCP server (`src/node/mcp/`), NOT the edge-safe core.
  - New node-only run-log module `src/node/mcp/run-log.ts` — a faithful port of Python's `rollback.py`: `saveRunSnapshot` / `listRuns` / `rollbackRun` over an on-disk `.goldenmatch_runs.json` log (append, keep last 50, mark-and-rewrite). This is a **separate, durable** state layer from the ephemeral in-memory `RUN_STORE` (added for the run-query tools) — rollback needs a persistent record of which output *files* a run wrote.
  - `list_runs` returns the parsed run log (empty list when the log is absent or corrupt). `rollback` deletes a run's output files (each path-jailed via the shared `sanitizePath` cwd guard, mirroring Python's `safe_path` — a jailed or missing file is reported under `not_found`, never aborting the call), refuses an unknown (`{ error, available_runs }`) or already-rolled-back run, then marks it `rolled_back` + `rolled_back_at` and rewrites the log.
  - **Writer parity note:** like Python, `saveRunSnapshot` is a callable that is NOT auto-wired into the dedupe pipeline (Python's own pipeline never calls `save_run_snapshot` either — only tests do). Wiring a snapshot writer into `dedupe()`/`export_results` is a deliberate follow-up on both surfaces.

## [1.3.0] - 2026-07-14

### Added
- **Fellegi-Sunter negative evidence** (full mirror of Python #1764): the FS API surface now honors `negative_evidence` on probabilistic matchkeys end-to-end (`trainEM` -> `scoreProbabilistic` -> validation -> fallback). `trainEM` learns each penalty-bits-free NE field as a constrained 2-state dimension (separate NE matrix, u from the same random-pair sample, full likelihood inside the EM loop, `[wFired, 0.0]` clamp applied at STORAGE only under `__ne__<field>`); scoring (`scoreProbabilistic` / `scoreProbabilisticPair`) adds the fired weight (or `-abs(penalty_bits)` for the fixed override) and normalizes against the NE-aware `fsWeightRange` envelope; `validateEmResultFor` requires a 2-entry `__ne__<field>` model entry per EM-learned NE field; the tiny-dataset fallback ships Python's exact NE entries (`[-3.0, 0.0]`). New exports: `neFired`, `fsWeightRange`, `NegativeEvidenceUnsupportedError`.
- Loader/validation matrix for `negative_evidence`: the config loader now PARSES `negative_evidence` for all three matchkey types (previously it was silently dropped for every type — weighted/exact NE via YAML/JSON configs was lost too, a pre-existing bug fixed here) and enforces the per-type knob matrix: weighted/exact require `penalty` and reject `penalty_bits`; probabilistic rejects `penalty` and accepts `penalty_bits` or neither (the EM-learned shape).
- Cross-surface parity: a committed Python-trained fixture (`tests/parity/fs-negative-evidence-fixtures.json`) is re-scored in TS at FULL float equality (EM-learned and penalty-bits variants), plus a TS homonym E2E mirroring the Python #1764 success bar.

### Changed
- **Loud decline instead of silent mis-scoring.** This port opened by making every FS entry point throw `NegativeEvidenceUnsupportedError` on NE-bearing probabilistic matchkeys (before it, a Python-authored NE config consumed in TS scored WITHOUT the veto and without warning), then lifted the throws as the capability landed. Two declines are PERMANENT, matching Python: the continuous (Winkler) path (`trainEMContinuous` / `scoreProbabilisticContinuous`) rejects NE, and `derive_from` negative evidence is rejected at the loader (goldenmatch-js has no derived-column materialization — materialize the column in your data, or use the Python runtime). A third decline is TS-scope-specific: the pipeline (`dedupe` / `matchRecords`) throws `NegativeEvidenceUnsupportedError` on a probabilistic+NE matchkey, because its probabilistic scoring is a simplified weighted-style average (a pre-existing TS scope gap) that cannot apply the NE veto — use the FS API (`trainEM` + `scoreProbabilistic`) directly, or remove `negative_evidence`. Exact and weighted NE run in the pipeline unchanged.
- WASM: documented no-op — no FS scoring path exists in WASM, so negative evidence changes nothing there (the PR #1755 precedent).

## [1.2.0] - 2026-07-13

### Added
- Splink -> GoldenMatch config converter, ported from Python: `fromSplink()` (edge-safe core, `src/core/config/from-splink.ts`), the `import-splink` CLI command, and the `convert_splink_config` MCP tool. Converts Splink comparison levels, blocking rules, and trained m/u probabilities (via a Python-schema-compatible `EMResult` JSON round-trip) into a GoldenMatch config, with a findings report for lossy mappings and an opt-in `--strict` mode. `mcp_tools`/`cli_commands` parity manifest entries moved from `python_only` to `shared`.
- `levelThresholds` N-level custom banding on `MatchkeyField`, and `modelPath` on probabilistic matchkeys (persisted EM model path, Splink-style train-once -> reuse).

## [1.1.0] - 2026-06-27

### Added
- **Config-suggestion healer on WASM + TypeScript.** The `suggest-core` Rust
  kernel is compiled to `suggest-wasm` and wired into `dedupe(rows, { suggest,
  heal })` at full default-pipeline parity: a free trigger off the run's
  postflight surfaces raw candidate `suggestions` (no extra pipeline run),
  `suggest: true` runs the verified gate, and `heal: true` runs the bounded
  apply-and-re-run loop (returning `healTrail` + the healed config). The kernel
  is reached through the opt-in subpath `goldenmatch/core/suggest-wasm`
  (`enableSuggestWasm()` / `disableSuggestWasm()`) — the `[native]` analog;
  without it every surface degrades to graceful-empty and never throws.
  `buildColumnSignals` (`suggestColumnSignals.ts`) builds the kernel's column
  signals TS-side with a Python-parity fixture. Surfaces: CLI `--suggest` /
  `--heal`, MCP `review_config` tool (MCP 44 → 45), A2A `review_config` skill
  (15 agent skills). Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE`. Cross-surface
  golden-vector parity (TS == Rust == Python on shared fixtures). Design:
  `context-network/decisions/0027-healer-wasm-ts.md`.
- **MinHash/LSH sketch kernel (#1081).** `src/core/sketch.ts` is a pure-TS,
  edge-safe port of the Python/Rust sketch kernel (shingling → MinHash → banded
  LSH) using `BigInt` for u64/u128 math; it reproduces the shared golden vectors
  byte-for-byte. `src/core/lshBlocker.ts` adds `MinHashLSHBlocker` (over
  `string[]`) for near-duplicate candidate generation: `candidatePairs(texts)`
  returns deduplicated `(min,max)` pairs. WASM acceleration is deferred (pure-TS
  is the default + fallback). Part of the training-data dedup tier (#1080).
- **SimHash sketch kernel (#1082).** `src/core/simhash.ts` is a pure-TS,
  edge-safe port of the Python/Rust SimHash kernel (random ±1 hyperplane
  projection → sign-bit signature → banded LSH) for *semantic* near-duplicate
  detection over dense embeddings. Exports `simhashSignature` and
  `simhashBandHashes`; reuses the `sketch.ts` `baseHash`/`splitmix64` bitstream,
  so the projection matrix and band hashes reproduce the shared golden vectors
  (`tests/fixtures/sketch_simhash_golden.json`) byte-for-byte. The semantic
  blocker stays Python-primary (the TS port has no real embedder); this is the
  cross-language kernel parity surface. Builds on the MinHash sketch above;
  part of the dedup epic (#1080).

## [1.0.0] — 2026-06-15

**First stable release.** The API is now stable: breaking changes only at the next
major (2.0.0). This is a one-time jump from the `0.x` wave line (`0.13.0 → 1.0.0`) —
a deliberate "this is stable" signal, not a `2.0.0` product-alignment bump (see
`docs/versioning-policy.md`; npm and PyPI keep independent semver, not lockstep).

- **AgentSession agent surface ported** (the last undeclared parity gap, 2026-06-15):
  the edge-safe `AgentSession` decision core + shared `AGENT_SKILLS` registry, 14
  agent-level MCP tools (MCP **30 → 44**), the A2A skill-union agent card + fail-closed
  bearer auth + unified dispatch (`/tasks/send`, `/tasks/{id}/cancel`), and node
  file-loaders (`analyzeFile` / `deduplicateFile` / `matchSourcesFile`). Behavior-fixture
  parity vs Python (`selectStrategy` decision table). The 3 agent tools with no TS core
  (`sensitivity` / `incremental` / `certify_recall`) are declared Python-only.
- **Opt-in WASM scorer backend.** `enableWasm()` / `disableWasm()` register a
  WebAssembly scorer (the Rust `score-core` kernel via the new `score-wasm`
  crate) behind the sync `scoreMatrix` for `jaro_winkler`/`levenshtein`/`exact`.
  Because the WASM kernel IS rapidfuzz, it matches the Python source of truth
  exactly; it is CI-gated (`wasm_score` lane) against canonical rapidfuzz
  goldens. Pure-TS remains the default and the fallback; runs in Node, browsers,
  and Workers. Note: the hand-rolled pure-TS Jaro-Winkler has small known
  divergences from rapidfuzz (the 0.7 boost threshold; transposition counting on
  repeated-character words), so enabling WASM can shift borderline scores toward
  the Python values without changing dedup decisions. Aligning the pure-TS
  scorers with rapidfuzz, token_sort WASM coverage, and non-BMP codepoint parity
  are tracked follow-ups.

### Added — refdata-aware name scorers (parity with Python `refdata`)

- `given_name_aliased_jw` scorer: Jaro-Winkler with an alias-aware exact bonus
  (William <-> Bill -> 1.0), backed by a bundled given-name alias table.
- `name_freq_weighted_jw` scorer: Jaro-Winkler modulated by US Census 2010
  surname IDF in the borderline zone, backed by a bundled top-10k surname table.
- Auto-config now refines first-name columns to `given_name_aliased_jw` and
  last-name columns to `name_freq_weighted_jw` (port of `refine_matchkey_field`,
  last-name checked before first-name; `multi_name` left unrefined for parity).
- Both refdata tables are generated from the Python source of truth via
  `scripts/sync_ts_refdata.mjs` and drift-guarded by `tests/unit/refdata-sync.test.ts`.

### Changed — auto-config blocking-selection parity

- `buildBlocking` now matches Python's `build_blocking`: exact-blocking candidates
  are gated at `cardinality_ratio <= 0.5` (was 0.95), with the null and cardinality
  gates applied only to the exact pool (name columns are ungated), and the name
  multi-pass adds secondary-name-column passes when two name columns are present.

Closes the controller-stoppoint parity drift (#857, from the #856 audit). Numeric
scorer parity is locked by Python-computed ground truth in
`tests/parity/scorer-ground-truth.test.ts`.

### Fixed — weighted-matchkey null-gate parity (#860)

- `buildWeightedMatchkey` no longer drops `nullRate > 0.5` columns. Python's
  `build_matchkeys` applies no null gate to fuzzy fields — high-null name columns
  are kept and demoted via the downstream avg-null threshold adjustment, not
  dropped. Surfaced by the #857 whole-branch review: TS emitted an empty weighted
  matchkey on heavily-null person data (`sparse_people`, ~75% null `first`/`last`)
  where Python emits a `given_name_aliased_jw` + `name_freq_weighted_jw` weighted
  matchkey at threshold 0.75. `sparse_people` is now byte-equal in the
  controller-stoppoint parity suite.

### Fixed — scorer rapidfuzz parity
- `jaro` / `jaroWinkler` / `levenshtein` / `indel` now match rapidfuzz to 4
  decimals on non-BMP (codepoint iteration via `Array.from` instead of UTF-16
  code units), sub-0.7-prefix (Winkler prefix boost gated on `jaro > 0.7`), and
  repeated-char (floored transposition count `t // 2`) inputs. Existing canonical
  anchors (MARTHA, DIXON, JELLYFISH, …) are unchanged. New parity gate:
  `tests/parity/scorer-rapidfuzz.test.ts`, goldens from `rapidfuzz` 3.14.5 via
  `packages/python/goldenmatch/scripts/emit_scorer_parity_fixtures.py`. Closes
  two of the three pure-TS-vs-rapidfuzz divergences the WASM slice flagged (the
  0.7 boost threshold and non-BMP codepoints) plus the repeated-char
  transposition count; token_sort WASM coverage remains the open follow-up.

## [2.0.0] - 2026-05-22

Major version: v1.18.2 plugin parity for the TS port (#208).

### Added — predefined golden-strategy plugins

All 22 v1.18.2 builtins from Python's `goldenmatch.plugins.builtin`
are now byte-identically ported. Validated by 304 vitest parity
tests that load JSON fixtures emitted from the Python sibling.

**Numeric (6):** `numeric_max`, `numeric_min`, `numeric_mean`,
`numeric_median`, `numeric_sum`, `numeric_weighted_average`.

**Format (7):** `shortest_value`, `concat_unique`, `email_normalize`,
`phone_digits_only`, `url_canonical`, `whitespace_normalize`,
`boolean_normalize`.

**Business (6):** `system_of_record`, `lifecycle_stage`,
`freshness_with_max_age`, `enum_canonical`, `regex_validated`,
`weighted_by_recency`. Date-based strategies accept
`ruleKwargs.now_iso` to pin the reference instant for deterministic
testing.

**Aggregation (3):** `count_distinct`, `count_non_null`,
`agreement_rate`.

### Added — PluginRegistry singleton

- `PluginRegistry.instance()` / `reset()` / `discover()` /
  `registerGoldenStrategy()` / `getGoldenStrategy()` /
  `hasGoldenStrategy()` / `listPlugins()`
- `BUILTIN_PLUGINS` const array of all 22 builtins
- User-registered plugins override builtins on the same name
  (matches Python's last-write-wins)

### Why v2.0.0 (not v0.11.0)

- Adds a new top-level public-API surface (22 plugins + registry)
- Goldenmatch-js had been on a 0.x track. The plugin port is the
  v2.0 milestone called out in
  `docs/superpowers/specs/2026-05-22-phase-5-typescript-port-design.md`.
- No breaking changes to existing exports -- all v0.10.0 surfaces
  still work unchanged.

### Migration from v0.10.0

No code changes required for existing callers. To consume the new
plugins:

```ts
import { PluginRegistry, NumericMaxStrategy } from "goldenmatch";

const registry = PluginRegistry.instance();
registry.discover();

const [value, conf, idx] = new NumericMaxStrategy().merge([10, 50, 25]);
// -> [50, 1.0, 1]

// Or via registry:
const strategy = registry.getGoldenStrategy("numeric_max")!;
const result = strategy.merge([10, 50, 25]);
```

User plugins:

```ts
registry.registerGoldenStrategy("my_custom", {
  name: "my_custom",
  merge: (values) => [values[0], 1.0, 0] as const,
});
```

### Out of scope (v2.1+)

- TS port of scorer / transform / connector plugin types
- Entry-point-style discovery (npm has no Python-style entry-point
  system; user plugins always register manually)

## [0.10.0] - 2026-05-19

Identity Graph on the CLI and REST API surfaces. The v0.9.0 persistent backend lit up the storage layer; v0.10.0 lights up two of the four user-facing surfaces Python ships (`web` UI is Python-only, TUI has no identity tab in Python either).

### Added — CLI

- New `goldenmatch identity` subgroup with 6 subcommands, mirroring `packages/python/goldenmatch/goldenmatch/cli/identity.py`:
  - `goldenmatch identity list [--dataset] [--status] [--limit] [--offset] [--json]`
  - `goldenmatch identity show <entity-id> [--json]`
  - `goldenmatch identity history <entity-id> [--limit] [--json]`
  - `goldenmatch identity conflicts [--dataset] [--json]`
  - `goldenmatch identity merge <source-id> <target-id>` (target stays, source absorbed)
  - `goldenmatch identity split <entity-id> <record-ids...>`
- All commands accept `--path <path>` (default `.goldenmatch/identity.db`). The store is opened lazily per command via the v0.9.0 `SqliteIdentityStore`.

### Added — REST API

- `setServerIdentityStore(store)` binder mirrors `setServerMemoryStore`. When set, the following routes are live; otherwise 503 with a hint.
  - `GET /identities?dataset&status&limit&offset` → list
  - `GET /identities/:id` → node + records + edges + events (full `IdentityView`)
  - `GET /identities/:id/history?limit` → event log
  - `GET /identities/conflicts?dataset` → conflict edges
  - `POST /identities/merge` body=`{keep, absorb}` → manualMerge
  - `POST /identities/split` body=`{entity_id, record_ids[]}` → manualSplit
- 9 new integration tests under `tests/unit/api-identity.test.ts` exercising every route plus the 503-when-unbound path.

### Not ported (Python deltas with no TS analogue)

- **Web UI Identities tab.** TS port doesn't ship a React workbench — the Python web UI at `packages/python/goldenmatch/web/frontend/` is the only one. Out of scope.
- **TUI Identities tab.** Python TUI has no identity tab either (controller tab landed v1.14, no identity tab on the roadmap).
- **`resolve` CLI subcommand.** Python ships it because the pipeline writes identity events post-cluster. The TS pipeline doesn't yet wire `resolveClusters`; deferred to a future wave.
- **MCP identity tools.** Six tools (`identity_list/show/resolve/history/conflicts/merge/split`) on the Python MCP server. TS port can ship these in a follow-up now that the API surface is stable; not in this PR to keep scope tight.

### Test counts
877 → 886 (+9 API identity).

## [0.9.0] - 2026-05-19

Persistent Identity Graph backend (Python `goldenmatch.identity.IdentityStore(backend="sqlite")` parity).

### Added

- **`SqliteIdentityStore`** in `src/node/identity/sqlite-store.ts` — full Node-only persistent backend for the Identity Graph. Implements every method on the `IdentityStore` interface (19 methods covering nodes, source records, evidence edges, events, aliases). Schema is byte-identical to Python's `goldenmatch/identity/store.py`, so an `identity.db` produced by either toolkit is readable by the other.
  - `better-sqlite3` is an optional peer dep (same pattern as `SqliteMemoryStore`).
  - WAL journal mode + 5s busy timeout + `foreign_keys=ON` for multi-process safety.
  - Schema versioning via `PRAGMA user_version` (currently v2). Migration body from Python v1 → v2 (evidence_edges unique key) preserved verbatim so a TS-opened Python v1 DB upgrades in place.
  - Record pairs canonicalized to `(min, max)` on insert (mirrors `canon_record_pair`).
- **23 new TS unit tests** under `tests/node/identity/sqlite-store.test.ts` covering every method, the close/reopen round trip, and edge canonicalization.
- **Public API:** `SqliteIdentityStore`, `SqliteIdentityStoreOptions` re-exported from `goldenmatch` (Node entry).

### Not yet shipped (deferred to v0.10.0)

- **Pipeline-driven population** — the Python `resolve_clusters(...)` hook runs after dedupe clustering and writes identity events. Wiring this into the TS pipeline is the v0.10 wave.
- **MCP identity tools** — Python ships 6 `identity_*` MCP tools backed by the persistent store. Will follow in v0.10 alongside the resolveClusters hook.

## [0.8.0] - 2026-05-12

Identity Graph edge-safe core (Python `goldenmatch` v1.15 partial parity).

### Added

- **`goldenmatch.identity`** edge-safe surface mirroring the Python `goldenmatch.identity.*` module. All exports are safe to import from Vercel Edge, Cloudflare Workers, and other Web-Standards runtimes — no `node:*` imports.
  - **Types:** `IdentityNode`, `SourceRecord`, `EvidenceEdge`, `IdentityEvent`, `IdentityAlias`, `IdentityStatus`, `EventKind`, `EdgeKind`, `IdentityView`, `IdentityStore` interface.
  - **`newEntityId(prefix?)`** — UUIDv7 via Web Crypto; deterministic when seeded for tests.
  - **`InMemoryIdentityStore`** — process-local store satisfying the full `IdentityStore` interface. Suitable for tests, edge-runtime scratch state, and code paths that don't require cross-call durability.
  - **`findByRecord(store, record)` / `getEntity(store, id)` / `listEntities(store)`** — read paths.
  - **`manualMerge(store, sourceId, targetId, ...)` / `manualSplit(store, entityId, recordIds, ...)`** — steward operations.
- **TS parity tests** (13 cases) under `tests/identity/` covering the cluster-resolve absorb/merge/create branches, `findByRecord` semantics, manual merge/split idempotency, and `IdentityView` shape parity vs the Python `IdentityView` dataclass.

### Not yet shipped (deferred to a future wave)

- **Persistent SQLite-backed `IdentityStore`** in `src/node/identity/` — the Python `IdentityStore(backend="sqlite")` writes to `.goldenmatch/identity.db`. The TS port keeps the edge-safe interface but the Node-only persistent implementation is a future wave's work. Today, `InMemoryIdentityStore` resets on process restart.
- **Pipeline-driven population** — the Python `resolve_clusters(...)` hook runs after dedupe clustering and writes identity events; the TS pipeline doesn't yet wire this hook.
- **MCP identity tools** — Python ships 6 `identity_*` MCP tools backed by the persistent store. TS will follow after the persistent backend lands.

### Python deltas NOT relevant to this wave

- **Python v1.13 (release plumbing, typed accessors).** TypeScript's strict mode already enforces equivalent invariants without runtime accessor properties; the TS surface didn't drift.
- **Python v1.14 (AutoConfigController surface-parity arc).** The arc threaded telemetry through TUI / CLI / Postgres / DuckDB surfaces that TS doesn't expose. TS already surfaces telemetry on its MCP server (added v0.5.0); the shared `serialize_telemetry` JSON shape is preserved.
- **Python v1.16 (`backend="bucket"` 5M-on-one-node)**. Polars-only path; TS port runs edge-safe in Web Crypto and doesn't ship Polars. The `backend="bucket"` Python recommendation has no TS analogue and is intentionally not ported. The TS port's scale envelope is unchanged from v0.7.0 — single-node workloads, no out-of-core backend.

## [0.7.0] - 2026-05-10

Negative-evidence parity with Python `goldenmatch` v1.11 + v1.12 (Path Y).
Python v1.12 lifted DQbench T3 from 53.8% F1 to 85.5% (+31.7 pp) by applying
NE as a post-filter on exact matchkeys directly; this release ports that
machinery to the TS runtime.

### Added

- `NegativeEvidenceField` interface and `makeNegativeEvidenceField` factory
  in `src/core/types.ts` (defaults: `threshold=0.5`, `penalty=0.5`).
  `MatchkeyConfig` variants (`ExactMatchkey`, `WeightedMatchkey`,
  `ProbabilisticMatchkey`) now accept optional `negativeEvidence`.
  `ExactMatchkey` also gains optional `threshold` so Path Y can stamp the
  default 0.5 cutoff when NE is added without a user-set threshold.
- `src/core/autoconfigNegativeEvidence.ts`:
  - `applyNegativeEvidence(mk, rowA, rowB)` — per-pair penalty sum.
  - `applyNegativeEvidenceToExactPairs(pairs, mk, allRows)` — v1.12 Path Y
    post-filter for `findExactMatches` output.
  - `promoteNegativeEvidence(config, rows, columnPriors)` — eager rule
    that walks both weighted AND exact matchkeys (v1.12 change). The
    `_is_exact_matchkey_field` anchor gate is skipped on the exact branch.
  - `pickScorerForColumn(colName, colType?)` — name-keyed scorer dispatch
    matching Python `_pick_scorer_for_column` (`email→token_sort`,
    `phone→exact+digits_only`, `address→token_sort`, otherwise
    `ensemble`).

### Changed

- `findFuzzyMatches` (`src/core/scorer.ts`) — applies NE penalty after
  weighted-sum aggregation, before the threshold compare. No-op when the
  matchkey has no `negativeEvidence`.
- `pipeline.ts` — after `findExactMatches`, calls
  `applyNegativeEvidenceToExactPairs` when the exact matchkey has NE set.
  Mirrors Python v1.12 post-filter design; `findExactMatches`'s signature
  is unchanged.
- `AutoConfigController.run()` — eager `promoteNegativeEvidence` pass runs
  once on the full row set (not the sample) before the iteration loop,
  matching Python's `auto_configure_df` pre-iteration pass.

### Tested

- 19 new unit tests across `types.negativeEvidence`, `autoconfigNegativeEvidence`,
  `scorer.negativeEvidence`, `scorer.pathY`, and `autoconfigRules.negativeEvidence`.
- 6 new Python-parity fixtures in
  `tests/parity/negative-evidence-fixtures.json` covering
  clustered-email-different-surname, clustered-phone-different-name,
  dense-population promotion, sparse no-op, blocking-field skip, and
  idempotency. All 6 green vs Python `promote_negative_evidence`.

## [0.6.0] - 2026-05-10

Indicator-aware refit parity with Python `goldenmatch` v1.9 + v1.10.

### Added

- `IndicatorContext` memoization layer (`src/core/indicators.ts`) and 5 pure
  complexity indicators ported from Python `core/indicators.py`:
  `computeColumnPriors`, `estimateSparseMatchSignal`,
  `computeCorruptionScore`, `estimateFullPopHits`,
  `computeCrossBlockingOverlap`, plus `computeIdentityCollisionSignal`
  used by the collision-aware refit rule.
- 7 new indicator-aware refit rules in `autoconfigRules.ts`:
  `ruleUniformHeavyBlocking`, `ruleBlockingFieldNullHeavy`,
  `ruleRecallGapSuspected`, `ruleCollisionSignalTooHigh`,
  `ruleSparseMatchExpand`, `ruleCrossBlockingDisagreement`,
  `ruleCorruptionNormalize`.
- `DEFAULT_RULES_V1_10` — 14-rule list mirroring Python's `DEFAULT_RULES`
  order. The legacy `DEFAULT_RULES_V1_7_V1_8` 7-rule list is still exported
  for callers that opt into base-only behavior.
- `RuleContext.indicators` optional field carries the per-iteration
  `IndicatorContext`; rules that need indicator signals are silent no-ops
  when callers run the legacy v1.7/v1.8 rule list.
- `RefitPolicy.propose(profile, current, history, indicators?)` — fourth
  positional argument (back-compat: defaults to `null`).

### Changed

- `autoConfigureRows` rewrite: matchkey naming now matches Python
  (`fuzzy_match` for weighted, `exact_<col>` for exact). Scorer selection
  follows Python's `_SCORER_MAP` (e.g. `name → ensemble`,
  `email → exact`). Adaptive threshold uses Python's formula plus the
  post-build data-quality adjustment (avg_null > 0.15 → −0.05;
  avg_len < 5 → +0.05).
- `buildBlocking` aligned with Python: prefers high-cardinality
  exact-eligible columns (email/phone/zip/identifier/year) for static
  blocking, falls back to multi-pass name blocking
  (`soundex` + `substring:0:5` + `token_sort + substring:0:8`).
- Controller provisions a fresh `IndicatorContext` per iteration and
  threads it into `policy.propose()` for v1.10 rule consumption.

### Parity status

- Controller stoppoint parity: 6/6 datasets pass shape-level assertions,
  2/6 (`dirty_people`, `mixed_blocking`) byte-equal on the normalized
  committed config. The remaining 4 diverge because Python's iteration
  path hits a `ModuleNotFoundError` on subsequent iterations and falls
  back to a virtual v0 entry (out-of-scope to replicate in TS).
- Indicators parity: 8/8 fixture datasets pass at 4-decimal tolerance
  on the 5 indicators. Identity-collision signal is unit-tested only —
  the TS pure-JS token-sort approximation diverges numerically from
  Python's `rapidfuzz.token_sort_ratio` at sub-rule precision, but the
  rule-firing boundary (rate > 0.75) is preserved.

## [0.5.0] - 2026-05-10

Auto-config controller parity with Python `goldenmatch` v1.7 + v1.8.

### Added

- `AutoConfigController` (async `.run()`) — iterative auto-config with
  pathological-input gates, deterministic sampling, policy-driven refit loop,
  and best-effort commit via `RunHistory.pickCommitted`.
- `ComplexityProfile` + sub-profiles (`DataProfile`, `DomainProfile`,
  `MatchkeyProfile`, `BlockingProfile`, `ScoringProfile`, `ClusterProfile`,
  `ProfileMeta`, `IndicatorsProfile`) with `HealthVerdict` rollup.
- `RunHistory` audit trail with `PolicyDecision` / `ErrorRecord` / `HistoryEntry`
  and `pickCommitted(precisionCollapseFloor)` lexicographic commit selection.
- `HeuristicRefitPolicy` rule dispatcher + 7 base v1.7/v1.8 rules:
  `ruleBlockingSingletonTrap`, `ruleBlockingTooCoarse`, `ruleBlockingKeySwap`,
  `ruleLowReductionRatio`, `ruleLowTransitivity`, `ruleNoMatches`,
  `ruleUnimodalScoring`.
- `StopReason` telemetry (8 variants matching Python).
- `autoConfigureRowsIterate(rows)` async iterative entry point.
- `AutoconfigOptions.iterate` field (default `false`; preserves pre-0.5.0
  behavior).
- `getLastControllerRun()` debug accessor mirroring Python's
  `_LAST_CONTROLLER_RUN` ContextVar.
- Parity test suite: 6 dataset fixtures generated from the Python sibling
  via `packages/python/goldenmatch/scripts/emit_ts_parity_fixtures.py`.

### Deferred to v0.6.0 (Wave 2)

- 5 complexity indicators + `IndicatorContext` memoization.
- Indicator-aware refit rules (`ruleCorruptionNormalize`,
  `ruleCrossBlockingDisagreement`, `ruleSparseMatchExpand`).
- Indicator-aware extensions to `ruleBlockingKeySwap` and `ruleNoMatches`.

## [0.4.0] - 2026-05-05

### BREAKING

- `Correction.verdict` renamed to `Correction.decision` (`"approve" | "reject"`)
- `Correction.feature` renamed to `Correction.matchkeyName`
- `MemoryStore` interface methods are now async (return `Promise<...>`)
- `runDedupePipeline` and `runMatchPipeline` are now async
- `dedupe`, `match`, `dedupeFile`, `matchFile` API functions are now async
- Hash algorithm changed from FNV-1a to SHA-256 (cross-language storage parity with Python goldenmatch v1.6.0)
- `MemoryConfig.backend` enum: `"sqlite" | "postgres"` -> `"memory" | "sqlite"`
- `MemoryConfig.trust`: `number` -> `{ human: number; agent: number }` (matches Python)

### Added

- Pipeline integration for Learning Memory (`config.memory.enabled = true`)
- Five MCP tools: `list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`
- CLI subgroup: `goldenmatch-js memory <stats|learn|export|import|show>`
- Python API mirror: `getMemory`, `addCorrection`, `learn`, `memoryStats`
- `SqliteMemoryStore` (Node only; requires `better-sqlite3` peer dep)
- Cross-language parity tests (JSON, SQLite, apply-outcome) -- Python and TS both run against the same fixtures
- Postflight rendering: `Memory: N applied, M stale, K stale-ambiguous, J unanchorable`
- Re-anchoring: corrections survive row reordering across runs (collision-safe; `record_hash` excludes `__row_id__`)
- `CorrectionStats.staleAmbiguous` and `staleUnanchorable` counters
- Explainer integration: `ReviewItem` carries a one-sentence `why`, deterministic by default with LLM upgrade when API key is set
