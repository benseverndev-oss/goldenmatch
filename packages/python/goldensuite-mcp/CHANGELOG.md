# Changelog

## 0.4.0 (2026-07-10)

### Added

- **Curated tool listing (`GOLDENSUITE_MCP_TOOLS`)** — `list_tools` now returns a
  curated headline set (~25 tools) **by default** instead of the full ~105, so LLM
  tool-selection isn't swamped by the flat namespace. `GOLDENSUITE_MCP_TOOLS=full`
  restores the complete listing; a comma-separated value lists exactly those names.
  Filtering is **list-only** — every hidden tool stays callable by exact name via
  `dispatch`. The set lives in `CURATED_TOOLS` in `server.py`. (README: "Curated
  tool listing".)

## 0.3.0 (2026-06-24)

### Added

- **goldenanalysis tool surface** — the aggregator now surfaces a sixth sub-package, `goldenanalysis`. Its MCP tools (`list_analyzers`, `analyze_frame`, `get_trend`, `detect_regressions`) flow through transitively via `goldenanalysis.mcp.server.TOOLS`/`HANDLERS`, registered last in `_SUITE_ORDER` so existing tools keep first-wins precedence on name collisions. (#817)

### Changed

- **Security/hardening** — bumped the `starlette` pin and closed CodeQL findings as part of the suite-wide workflow hardening. (#738)

## 0.2.0 (2026-05-13)

First real release. The aggregator package was scaffolded as `0.1.0` but
never published. This release wires up the publish workflow, adds smoke
tests, and ships the v1.15 Identity Graph tool surface.

### Added

- **`.github/workflows/publish-goldensuite-mcp.yml`** -- tag-driven PyPI
  publish workflow. Fires on `goldensuite-mcp-v*` release tags. Mirrors
  the `publish-goldenmatch.yml` pattern.
- **`tests/test_aggregator_smoke.py`** -- 6 smoke tests covering: bare
  import, per-adapter load, Identity Graph tool surfacing via the
  goldenmatch adapter, end-to-end `create_server` composition, collision
  logging without crash, and dispatch routing for known sub-package tools.
- **MCP Registry tag routing** -- `publish-mcp.yml` now explicitly noops
  on `goldensuite-mcp-v*` tags. The aggregator isn't registered as a
  separate listing on the MCP Registry; its sub-packages each have their
  own listing, and the aggregator's value is the unified endpoint, not a
  registry presence.

### Verified

- v1.15 Identity Graph tools (`identity_resolve`, `identity_list`,
  `identity_history`, `identity_conflicts`, `identity_merge`,
  `identity_split`) flow through transitively via
  `goldenmatch.mcp.server.TOOLS`. The aggregator picks them up
  automatically without per-tool wiring -- as designed.

### Surfaced from upstream

This release picks up everything published in the suite between
2026-03 and 2026-05-13:

- **goldenmatch 1.15.0** -- Identity Graph v2.0, 6 new identity tools
- **goldenmatch 1.14.0** -- AutoConfigController surface, `controller_telemetry` tool
- **goldenmatch 1.6.0** -- Learning Memory, 5 memory tools
- **goldencheck 1.2.0** -- baseline + drift detection tools
- **goldenflow 1.1.6** -- 14 CLI commands surface area
- **goldenpipe 1.1.0** -- pipeline orchestration tools
- **infermap 0.4.0** -- schema mapping tools

## 0.1.0 (initial scaffold; never published)

Initial aggregator skeleton. Not published to PyPI.
