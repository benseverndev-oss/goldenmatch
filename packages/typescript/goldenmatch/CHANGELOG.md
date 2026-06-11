# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [Unreleased]

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
