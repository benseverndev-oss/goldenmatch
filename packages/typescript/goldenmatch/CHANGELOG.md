# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

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
