# N-level Native Scoring (Rust/arrow-native port) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port custom `level_thresholds` banding into the native Rust FS kernel so N-level probabilistic matchkeys score natively (byte-identical to the Python path) instead of falling back to pure Python; merge to main.

**Architecture:** Thesis phase 2 for the Splink-converter feature (PR #1749, in merge queue). `fs_level_from_sim` in `packages/rust/extensions/native/src/score.rs` gains a custom-thresholds branch (level = count of satisfied descending thresholds — the exact `_levels_from_similarity` semantics). `score_block_pairs_fs` gains an OPTIONAL trailing `level_thresholds` kwarg (pyo3 signature default `None`) plus a module const `FS_SUPPORTS_LEVEL_THRESHOLDS = True` for wheel-skew-safe capability detection. Python's `_fs_native_eligible` flips from "always decline level_thresholds" to "eligible iff the loaded kernel advertises support" — old wheels keep the pure-Python fallback with zero behavior change. The dormant fused kernel (`match_fused_fs`) passes `None` and its `match_fused_fs_ready` guard stays (YAGNI: dormant path).

**Tech Stack:** Rust (pyo3 abi3, rayon), maturin in-tree build via `scripts/build_native.py`, pytest parity tests.

**Working branch:** `feat/nlevel-native-scoring` (stacked on `feat/splink-config-converter` @ 5c4ff66cc in worktree `..\goldenmatch-wt-splink-converter`; rebase onto origin/main after #1749 squash-merges: `git rebase --onto origin/main 5c4ff66cc`).

**Wheel-skew contract (from CLAUDE.md):** new kernel capability must be detectable by the caller; callers must never pass the new kwarg to an old wheel. The `FS_SUPPORTS_LEVEL_THRESHOLDS` const + eligibility gate is that detection. Wheel republish (tag `goldenmatch-native-v0.1.14`) is a post-merge release step flagged to the user — in-tree builds and CI pick the change up immediately.

**Env notes:** cargo/rustup per memory (`D:\.rustup\toolchains\1.94.0\bin` on PATH, `CARGO_HOME=D:\.cargo`). Native-ext CI runs `clippy -D warnings`; run it locally pre-push. rustfmt TOUCHED files by name (not `cargo fmt`). Verify builds explicitly (`grep ^error`). ort/onnxruntime don't link locally — use `cargo check`/`cargo clippy` for validation and the maturin build for the importable module.

---

### Task N1: Rust kernel — custom banding + optional kwarg + capability const

**Files:**
- Modify: `packages/rust/extensions/native/src/score.rs` (`fs_level_from_sim`, `score_block_pairs_fs`), `packages/rust/extensions/native/src/fused.rs` (pass `None`), `packages/rust/extensions/native/src/lib.rs` (export const)
- Modify (version bumps, lockstep): `packages/rust/extensions/native/Cargo.toml`, `packages/rust/extensions/native/pyproject.toml`, `packages/rust/extensions/native/python/goldenmatch_native/__init__.py` — all `0.1.13` → `0.1.14`

- [ ] **Step 1:** `fs_level_from_sim(sim, n_levels, partial_threshold, level_thresholds: Option<&[f64]>)`: when `Some(ts)`, return `ts.iter().filter(|&&t| sim >= t).count()` (order-independent count, identical to Python `_levels_from_similarity` custom branch, `>=` inclusive); when `None`, existing 2/3/N-even branches unchanged. Update the doc comment. Rust unit tests in the same file's test module (or wherever kernel unit tests live — check for `#[cfg(test)]`): custom `[1.0, 0.92, 0.88]` over sims `[1.0, 0.95, 0.90, 0.5, 0.88]` → `[3,2,1,0,1]` (mirror the Python test), legacy branches unchanged.
- [ ] **Step 2:** `score_block_pairs_fs`: add `#[pyo3(signature = (row_ids, block_sizes, field_values, scorer_ids, levels, partial_thresholds, match_weights, calibrated, prior_w, min_weight, weight_range, threshold, exclude, level_thresholds=None))]` with `level_thresholds: Option<Vec<Option<Vec<f64>>>>` (one entry per field). Inside the pair loop pass `level_thresholds.as_ref().and_then(|lt| lt[f].as_deref())`. Validate length == n_fields when Some (PyValueError like fused does). Update doc comment (incl. that match_weights[f] length must equal the field's level count — unchanged invariant).
- [ ] **Step 3:** `fused.rs` `match_fused_fs`: pass `None` at the `fs_level_from_sim` call site + one-line comment (fused stays declined for level_thresholds via `match_fused_fs_ready`; port when the fused path goes live).
- [ ] **Step 4:** Export capability const: in the pymodule registration (find `#[pymodule]` in lib.rs / score.rs), `m.add("FS_SUPPORTS_LEVEL_THRESHOLDS", true)?;`.
- [ ] **Step 5:** Bump the 3 version files 0.1.13 → 0.1.14 (lockstep — pyproject drives the wheel republish, Cargo drives the crate, `__init__.py` is the fallback `__version__`).
- [ ] **Step 6:** Validate: `cargo check` + `cargo clippy --all-targets -- -D warnings` + `cargo test` in the crate dir (capture output, `grep -E "^error"` must be empty); rustfmt the touched .rs files BY NAME. Fix everything.
- [ ] **Step 7:** In-tree build so Python tests can load it: `python scripts/build_native.py` (repo root; check its --help/source for invocation), confirm `python -c "from goldenmatch._native import ...; import goldenmatch"` sees `FS_SUPPORTS_LEVEL_THRESHOLDS` — check the loader order in `goldenmatch/core/_native_loader.py` (in-tree `goldenmatch._native` wins).
- [ ] **Step 8:** Commit.

### Task N2: Python routing + test updates

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`_fs_native_eligible`, `score_probabilistic_native`), `packages/python/goldenmatch/tests/test_nlevel_banding.py` (eligibility/router tests), from_splink docstring/comment touch-ups
- Test: extend `tests/test_nlevel_banding.py` + new real-kernel parity test in `tests/test_nlevel_em.py` or `tests/test_probabilistic.py` (follow `TestNativeFSParity`'s home)

- [ ] **Step 1 (TDD):** rewrite `test_level_thresholds_not_native_eligible_synthetic` → two tests: (a) mock native WITHOUT the const → not eligible (old-wheel behavior pinned); (b) mock native WITH `FS_SUPPORTS_LEVEL_THRESHOLDS=True` → ELIGIBLE. Update the real-kernel test to assert eligible (the in-tree build now has the const). Update `test_level_thresholds_router_selects_non_native_scorer` → with supporting native mock the router selects `_native`; keep a variant pinning the non-supporting fallback.
- [ ] **Step 2:** `_fs_native_eligible`: replace the unconditional decline with `if any(f.level_thresholds is not None for f in mk.fields) and not getattr(mod, "FS_SUPPORTS_LEVEL_THRESHOLDS", False): return False` (careful: `mod` = `native_module()` — check how the function currently obtains it; keep the existing lazy-import pattern). Update docstring.
- [ ] **Step 3:** `score_probabilistic_native`: build `level_thresholds = [list(f.level_thresholds) if f.level_thresholds else None for f in mk.fields]`; pass the kwarg ONLY when any entry is non-None (never sends the kwarg to an old wheel even if eligibility drifted).
- [ ] **Step 4:** Real-kernel parity test (skipif on native availability AND the const): a 4-level `level_thresholds` matchkey scored through `probabilistic_block_scorer` with native forced vs numpy forced → identical pair sets and scores (rounding tolerance per existing parity tests — copy `TestNativeFSParity`'s comparison idiom).
- [ ] **Step 5:** Doc touch-ups: `_fs_native_eligible` docstring; the comment block in `tests/test_nlevel_banding.py`; `docs-site/goldenmatch/scoring.mdx` ("native/fused fall back" → native scores level_thresholds from goldenmatch-native >= 0.1.14, fused falls back); CHANGELOG Unreleased entry. `match_fused_fs_ready` docstring stays (fused still declines).
- [ ] **Step 6:** Run `tests/test_nlevel_banding.py tests/test_nlevel_em.py tests/test_probabilistic.py tests/test_probabilistic_vectorized.py tests/test_fused_match.py` with the fresh in-tree build, NO `GOLDENMATCH_NATIVE=0` → all pass incl. the new parity test actually RUNNING (not skipping). Also run once WITH `GOLDENMATCH_NATIVE=0` → pure-Python still green.
- [ ] **Step 7:** Commit.

### Task N3: land it on main

- [ ] **Step 1:** Wait for #1749 to merge (poll `gh pr view 1749 --json state` at ≥3-min intervals — this is external state, not CI-babysitting of our own PR).
- [ ] **Step 2:** `git fetch origin main`; rebase: `git rebase --onto origin/main 5c4ff66cc feat/nlevel-native-scoring` (squash-merge rebase per memory). Resolve conflicts (expect none — the stack only ADDS on top of #1749's files).
- [ ] **Step 3:** Re-run the Task N2 test set post-rebase (in-tree native rebuild if the rebase touched the crate).
- [ ] **Step 4:** Push (benzsevern token-URL dance), `gh pr create` (base main) with the parity evidence in the body, enqueue `gh pr merge --auto`, STOP (no CI polling). Flag in the final message: `goldenmatch-native-v0.1.14` tag/republish is a user decision (PyPI wheel users stay on pure-Python fallback until republished — safe, not broken).
