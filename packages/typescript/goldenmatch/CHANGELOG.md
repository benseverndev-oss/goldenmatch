# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

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
