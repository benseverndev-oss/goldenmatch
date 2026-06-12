# 0013 — GoldenCheck TS port: finish the module parity + harden the golden harness (and the temporal bug it caught)

**Status:** accepted (2026-06-12; PRs #873 + #874, CI green). Closes the #855 surface-gap item from the 2026-06-11 TS↔Python audit.

## Context
The 2026-06-11 parity audit found the goldencheck TypeScript port ~77% module-complete vs the Python source of truth: missing 2 profilers (`freshness`, `fuzzy_values`), 4 of 10 relations (`approx_duplicate`, `approx_fd`, `composite_key`, `functional_dependency`), and the `validate` MCP tool. Worse, the gaps were **invisible to CI**: `tests/parity/parity.test.ts` asserted finding *identity* only (column + check + severity), **skipped silently** when goldens were absent, and never exercised the missing modules. A profiler could diverge on confidence or affected-row counts and stay green.

Two constraints shaped the execution:
- **The native kernels are Python-only by design** (#855 "out of scope"). The TS port mirrors the Python **fallback** path; the goldencheck CLAUDE.md guarantees kernel output is byte-identical to the fallback, so a golden generated with the kernel present still matches the TS fallback.
- **The local dev box cannot run Polars** without OOMing (15.7 GB box, often <1 GB free; a local `vitest` run was OOM-killed). The golden generator (`scripts/gen_parity_goldens_js.py`) is a Polars program. So goldens could not be regenerated locally.

## Decision
1. **Port the 6 modules + `validate` onto the edge-safe core, mirroring the Python fallback.** Each ships with a TS unit test mirroring its Python sibling. `composite_key` / `functional_dependency` get a shared `TabularData.nUniqueTuple` helper (distinct-tuple count = Polars `df.select(cols).n_unique()`). `validate` wraps the existing `validateData` + `validateConfig`; `node:fs` is a top import and the optional `yaml` peer is resolved via `createRequire(import.meta.url)` (the package is ESM — there is no ambient `require`). Registries: `COLUMN_PROFILERS` 10→12, `RELATION_PROFILERS` 5→9, MCP tools 17→18 (8 core + 10 agent).

2. **Harden the golden harness to assert confidence (4 dp) + affected_rows, and FAIL (not skip) on a missing manifest/golden.** This is the real cross-language check: it binds corroboration-boosted confidence and per-finding row counts to Python's output, so a port can't silently regress.

3. **Regenerate goldens on a CI runner, returned as an artifact — not locally, not committed-back.** A `workflow_dispatch` + branch-`push` workflow (`regen-855-parity-goldens.yml`) does `uv sync --all-packages` and runs the generators on `ubuntu-latest`, uploading `parity_cases.json` + `_goldens_js/` as an artifact. The author downloads and commits, so the *author's* push (real token) triggers the parity lane — a commit-back via `GITHUB_TOKEN` would not re-trigger CI. The generators run from the TS package dir so `tests/fixtures/...` resolves to the goldens the TS test reads; `gen_parity_goldens_js.py` now also emits `affected_rows`.

4. **`freshness` stays unit-test-only.** The harness round-trips each case through a temp CSV, and `pl.read_csv` defaults to `try_parse_dates=False`, so date columns arrive as `Utf8` and Python's date-dtype-gated `FreshnessProfiler` never fires — while the TS `dtype()` reports `"date"` for ISO strings. A freshness golden would mismatch by construction. Covered by `tests/unit/profilers/freshness.test.ts` instead.

## Consequences
- **The hardening immediately earned its keep — it caught a pre-existing TS bug the loose test could not.** Three of six cases (`approx_fd`, `functional_dependency`, `composite_key`) failed not on the new modules but on `TemporalOrderProfiler`: its `tryParseDate` used `new Date(s)`, and JS parses bare integer strings (`"7"`) as valid dates, so the temporal fallback fired `temporal_order` on integer column pairs (`zip,amt`, `line_no,qty`) that Python never flags (Python only casts strings via `str.to_date(format="%Y-%m-%d")`). Fixed by gating TS `tryParseDate` on the ISO `YYYY-MM-DD` shape; existing temporal tests all use that format and were unaffected. A regression unit test asserts temporal doesn't fire on integer columns. After the fix all 6 cases match Python byte-for-byte.
- **Fixtures exercise collateral profilers, not just their target** (e.g. the approx-fd fixture's sequential `city_N` values fuzzy-cluster at 297 rows), so the harness is a broad cross-language check — that breadth is why it surfaced the temporal gap.
- The runner-regen workflow is a reusable tool on `main` (`workflow_dispatch`) for future goldencheck-TS golden regeneration; it sidesteps the local-Polars-OOM constraint.
- **Out of scope, unchanged:** the native deep-profiling kernels (Python-only) and the `install_domain` MCP tool (community-pack download, no TS infra).

## Related
- [../planning/surface-hardening.md](../planning/surface-hardening.md) — the surface-gap arc this closes (#855).
- [../architecture/goldencheck-native-kernel.md](../architecture/goldencheck-native-kernel.md) — the Python native kernels the TS port deliberately does NOT have.
- [0008-fellegi-sunter-splink-parity.md](0008-fellegi-sunter-splink-parity.md), [0009-rust-test-coverage.md](0009-rust-test-coverage.md) — sibling "make the parity/coverage check actually bind" decisions.
