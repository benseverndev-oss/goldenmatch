# Changelog

## 1.2.1 (2026-06-01)

### Fixed

- The `/validate` API route no longer returns the raw exception/traceback to
  clients: it now logs the traceback server-side and returns only the first
  error line, truncated to 200 chars.

### Changed

- Repository and project URLs rebranded from `benzsevern` to `benseverndev-oss`.

## 1.2.0 (2026-05-13)

**Suite orchestration for Identity Graph.** GoldenPipe gains first-class
orchestration of the GoldenMatch v1.15 Identity Graph -- one CLI / Python
/ Airflow path runs `Check -> Flow -> Match -> Identity` end-to-end and
persists a durable identity store with stable `entity_id`s across runs.

### Added

- **New stage `goldenmatch.identity_resolve`** registered at the
  `goldenpipe.stages` entry-point. Wraps
  `goldenmatch.identity.resolve_clusters`. Stage config dict matches
  `IdentityConfig` shape: `path`, `dataset`, `source_pk_column`,
  `weak_confidence_threshold`, `emit_singletons`, `backend`, `connection`.
  Idempotent: replaying the same `metadata['run_id']` is a no-op.
- **CLI flags on `goldenpipe run`**: `--identity-path`,
  `--identity-dataset`, `--identity-source-pk`,
  `--identity-weak-threshold`. When `--identity-path` is set on a
  zero-config invocation, the identity stage auto-appends with the
  flags as its `stage_config`. YAML config (`--config`) is authoritative
  when supplied -- CLI flags are ignored.
- **`gp.run(source, identity_opts={...})`** Python entry-point exposes
  the same auto-append behaviour.
- **Airflow DAG**: `examples/airflow/golden_suite_identity_graph.py`. Daily
  Check->Flow->Match->Identity chain with S3-synced identity store.
  Surfaces `conflicts_flagged` as XCom for downstream review workers.
- **`DedupeStage` surfaces two new artifacts**: `scored_pairs` (from
  `DedupeResult.scored_pairs`, ~80 B/pair) and `matchkey_used` (first
  matchkey name). Backwards-compatible -- nothing in v1.1 consumed
  either.
- **`decide_identity(ctx)`** helper short-circuits the stage when no
  clusters were produced upstream. Stage-level guard also catches the
  same case for callers who bypass decision logic.
- **Public docs**: `docs/identity-graph.md` covering quickstart, CLI
  flags, YAML equivalent, `PipeResult.artifacts` shape, when to use
  GoldenPipe vs direct GoldenMatch.

### Changed

- `Pipeline.__init__` accepts an optional `identity_opts` dict (only
  used by the auto-config path; YAML wins when supplied).

### Requirements

- Requires `goldenmatch >= 1.15.0` (provides
  `goldenmatch.identity.resolve_clusters`).
- No other-package version bumps required.

### Tests

- 12 new tests: 8 stage-level (`tests/test_identity_stage.py`),
  4 CLI-level (`tests/test_identity_cli.py`). Determinism guarantees
  asserted across two runs.

### Out of scope

Per the spec at
`docs/superpowers/specs/2026-05-13-goldenpipe-v1.2-identity-orchestration-design.md`,
the following were explicitly deferred:

- No new MCP / A2A / REST surface in GoldenPipe v1.2.
- No web UI changes.
- No force-graph visualization.
- No retroactive backfill DAG for pre-v1.15 runs.

## 1.0.1 (2026-03-29)

- Add MCP Registry metadata (server.json, mcp-name verification)
- Add CI test workflow (ruff + pytest)
- Add community files (CODE_OF_CONDUCT.md, SECURITY.md)
- Fix version mismatch in __init__.py
- Clean up tracked internal files

## 1.0.0 (2026-03-29)

First stable release.

### Features
- End-to-end pipeline: GoldenCheck → GoldenFlow → GoldenMatch
- Adaptive logic: skips unnecessary stages, detects PPRL needs
- Pluggable stages via entry points
- 4 MCP tools: list_stages, validate_pipeline, run_pipeline, explain_pipeline
- CLI, REST API, TUI, MCP server, A2A protocol interfaces
- Zero-config default with rich YAML configuration support
