# Golden Suite Performance Audit — Checklist

**Date:** 2026-05-02
**Scope:** `D:\show_case\goldenmatch` monorepo (packages/{python, rust, typescript, dbt, actions})
**Source:** Explore-agent audit of runtime, dev-loop, and tooling bottlenecks.

Items are roughly ordered by ROI within each section. Check off as we go; we will brainstorm a focused spec before implementing each non-trivial item.

---

## Runtime — Engine speed

> **Lesson from the first attempt (2026-05-04):** the audit framed the runtime items as ROI-ranked, but framing was based on *static counts of "Python boundary crossings"*, not measured time. When we actually benchmarked the first item below, the speedup was 1.1x–1.4x, not the 10x+ the framing implied. **For every item in this section: measure before designing.** If the measured speedup is <2x, ship is optional, not assumed.

- [~] **Vectorize `pattern_consistency` profiler** — measured, deferred
  - File: `packages/python/goldencheck/goldencheck/profilers/pattern_consistency.py:38`
  - Hypothesis: `_generalize()` Python UDF in `map_elements` is the bottleneck.
  - Reality (measured 2026-05-04): pure generalization step is **1.1x faster** at 100k rows (89ms → 82ms) and **1.4x faster** at 500k high-cardinality (549ms → 393ms). End-to-end profiler call on 100k rows is essentially flat (101ms → 100ms). Polars' `map_elements` releases the GIL and per-call overhead is small — the audit's "100k boundary crossings" framing overstated the cost.
  - Decision: not worth the churn for a 10-40% speedup on a profiler that's already sub-100ms per column. Reconsider if a column ever measures >1s.

- [ ] **Parallelize column profilers**
  - File: `packages/python/goldencheck/goldencheck/engine/scanner.py:35-53`
  - Problem: 10 column profilers × N columns × serial execution. 100 cols ≈ 1000 sequential ops.
  - Fix: dispatch profilers per column batch via `concurrent.futures` (or `polars` concurrency where applicable).

- [ ] **Batch / async the LLM provider calls**
  - File: `packages/python/goldencheck/goldencheck/llm/providers.py:35-64`
  - Problem: `call_llm()` is one synchronous `messages.create()` per finding. 50 findings = 50 round-trips.
  - Fix: async parallelism with `asyncio.gather`, or use Anthropic's batch API for offline runs.

- [x] **Verify the Rust core is actually on the hot path** — diagnosed, N/A
  - Diagnosis (2026-05-04): no Rust core exists for goldenmatch's Python path. `packages/rust/extensions/` is the *Postgres/DuckDB SQL surface* — Rust calls Python via PyO3, not the other way around. Audit's framing was a misread of the architecture.
  - Decision: nothing to do. Item closed.

- [x] **Hoist matchkey transforms out of per-block scoring** — measured, shipped
  - File: `packages/python/goldenmatch/goldenmatch/core/scorer.py:113` + `core/matchkey.py` + `core/pipeline.py`
  - Hypothesis (cProfile, 2026-05-04): `_get_transformed_values` was 8.97s / 78% of an 11.4s 10k-row dedupe — 7028 redundant Polars `.select()` calls.
  - Reality (measured 2026-05-04 without cProfile overhead): 5-run median dedupe wall on 10k synthetic dropped from **3127ms → 2567ms (1.22x)**, best-case **2939ms → 2243ms (1.31x)**. cProfile's per-call overhead inflated the function's apparent share of wall — the underlying `select()` calls are individually fast enough that eliminating them yields a real but modest improvement.
  - Decision: shipped per spec acceptance ("ship if structural cleanup is good even when speedup is smaller than expected"). Spec: `docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md`.

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
