# Changelog

All notable changes to `dbt-goldensuite`. This package ships inside the Golden
Suite monorepo and is consumed from there (not published to PyPI).

## [Unreleased]

### Changed
- Relocated from `packages/python/goldenmatch/dbt-goldensuite/` to the top-level
  `packages/dbt/goldensuite/` per the documented monorepo layout. Update the
  `subdirectory:` in your `packages.yml` to `packages/dbt/goldensuite`.
- Synced `dbt_project.yml` version to `0.5.0` (was stale at `0.1.0`) and bumped
  the `goldenmatch` floor to `>=2.0.0`.

### Fixed
- README install instructions: the package is not on PyPI, so `pip install
  dbt-goldensuite` was replaced with the `packages.yml` git-subdirectory install
  (macros) and a `pip install "git+...#subdirectory=..."` for the Python helper.

### Added
- Restored the CI lane removed in #464: `dbt parse` smoke (in-memory DuckDB
  profile) plus the package's pytest suite now run on changes under `packages/dbt/**`.
- Standalone `profiles.yml` + `profile:` key so the project parses on its own.

## [0.5.0]
- GoldenFlow transform macros, Snowflake Cortex macros, and `infermap_apply`.

## [0.4.0]
- `goldenmatch_dedupe` custom materialization (golden / clusters / pairs output).

## [0.3.0]
- Identity Graph read macros (`identity_resolve`, `identity_list`, `identity_view`,
  `identity_history`, `identity_conflicts`).

## [0.2.0]
- Folded `dbt-goldenmatch` + the standalone `packages/dbt/goldencheck` into a single
  `dbt-goldensuite` package; added GoldenCheck quality-gate macros (#464).
