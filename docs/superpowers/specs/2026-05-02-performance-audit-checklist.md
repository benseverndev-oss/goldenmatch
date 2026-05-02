# Golden Suite Performance Audit — Checklist

**Date:** 2026-05-02
**Scope:** `D:\show_case\goldenmatch` monorepo (packages/{python, rust, typescript, dbt, actions})
**Source:** Explore-agent audit of runtime, dev-loop, and tooling bottlenecks.

Items are roughly ordered by ROI within each section. Check off as we go; we will brainstorm a focused spec before implementing each non-trivial item.

---

## Runtime — Engine speed

- [ ] **Vectorize `pattern_consistency` profiler**
  - File: `packages/python/goldencheck/golenmatch/profilers/pattern_consistency.py:38,54-66`
  - Problem: `_generalize()` runs as a Polars `map_elements()` Python UDF — one boundary crossing per row (~100k for a 100k sample).
  - Fix: replace with a single vectorized regex pass / native Polars expressions.

- [ ] **Parallelize column profilers**
  - File: `packages/python/goldencheck/goldencheck/engine/scanner.py:35-53`
  - Problem: 10 column profilers × N columns × serial execution. 100 cols ≈ 1000 sequential ops.
  - Fix: dispatch profilers per column batch via `concurrent.futures` (or `polars` concurrency where applicable).

- [ ] **Batch / async the LLM provider calls**
  - File: `packages/python/goldencheck/goldencheck/llm/providers.py:35-64`
  - Problem: `call_llm()` is one synchronous `messages.create()` per finding. 50 findings = 50 round-trips.
  - Fix: async parallelism with `asyncio.gather`, or use Anthropic's batch API for offline runs.

- [ ] **Verify the Rust core is actually on the hot path**
  - Confirm PyO3 bindings are invoked for blocking/scoring/dedup rather than a Python fallback.
  - Add a smoke benchmark to detect regressions if the fallback path silently re-engages.

---

## Developer feedback loop — CI + local

- [ ] **Re-enable pytest in CI** *(correctness, but unblocks everything else)*
  - File: `.github/workflows/ci.yml:23-25` — currently lint-only with a comment that pytest is intentionally skipped.
  - Fix: add `pytest -x` (or `pytest -n auto -x` once xdist is in).

- [x] **Add dependency caches to CI**
  - File: `.github/workflows/ci.yml`
  - Done: setup-uv `enable-cache`, Swatinem/rust-cache, setup-python `cache: pip`, and pnpm/turbo caches in the typescript job (latter via the pnpm+turbo migration below).

- [x] **Enable parallel pytest with `pytest-xdist`**
  - File: root `pyproject.toml`
  - Done: `pytest-xdist` + `pytest-asyncio` in dev deps; `-n auto` passed from CI step rather than global `addopts` (because `goldensuite-mcp`'s own `[dev]` extras don't include xdist).

- [x] **Enable incremental TypeScript builds**
  - Subsumed by the pnpm+turbo migration below — turbo's task cache replaces the bash for-loop and gives per-task hashing across the workspace.

- [ ] **Rust build hygiene**
  - Confirm sccache or `CARGO_INCREMENTAL=1` in CI.
  - Make sure tests aren't being built in `--release` unnecessarily.

---

## Monorepo tooling overhead

- [x] **Reassess npm workspaces / adopt Turborepo** — adopted; CI cache verification deferred.
  - Done via `docs/superpowers/specs/2026-05-02-pnpm-turbo-migration.md` and corresponding plan. Adopted pnpm workspaces + Turborepo. Single root `pnpm-lock.yaml`, single CI typescript job replacing the 4-entry matrix, `.turbo/` cache configured.
  - [ ] Verify pnpm-store + `.turbo` cache hits on the N+1 CI run after the migration PR merges.

- [ ] **Audit duplicated dependencies across packages**
  - After workspace decision is made, look for redundant installs (typescript, eslint, ruff, etc.) that could be hoisted.

- [ ] **Editable installs for fast Python iteration**
  - Confirm each Python package is installed in editable mode within the uv workspace so cross-package edits don't require reinstall.

---

## Suggested execution order

1. CI dependency caches  *(15 min, instant payoff every CI run)*
2. `pytest-xdist` + re-enable pytest in CI  *(unblocks correctness signal)*
3. Vectorize `pattern_consistency` profiler  *(biggest single runtime win)*
4. Parallelize column profilers
5. Async/batch LLM provider
6. Rust core verification + smoke benchmark
7. TS incremental builds
8. Workspace / Turborepo decision (bigger architectural call — own brainstorm)

---

## Working agreement

- Each non-trivial item gets its own brainstorm → spec → plan → implement cycle.
- Quick wins (caching, xdist, config flags) can be bundled into a single PR.
- Before claiming any "speed up" win, capture a before/after measurement on a representative dataset.
