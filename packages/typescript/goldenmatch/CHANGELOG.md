# Changelog

All notable changes to goldenmatch-js are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

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
