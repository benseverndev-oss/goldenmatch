# FS Scale Parity (#1803) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the FS (probabilistic) pipeline the scale optimizations the weighted path already has — exclude-set Arc handle, zero-copy Arrow kernel entry, one shared bucket-routing function with bucket as the default route — plus bounded EM row materialization and data-scaled bucket counts.

**Architecture:** Four independent PRs off `origin/main` (post-#1799). PR-1 is pure-Python (EM memory). PR-2 adds two kernel capabilities behind wheel-skew gate consts with byte-identical Python fallbacks. PR-3 unifies routing and flips FS to bucket-by-default with measured evidence. PR-4 (follow-up, may be deferred) ports oversized auto-split to the bucket lane.

**Tech Stack:** Python (polars/pyarrow), Rust (pyo3/arrow via `packages/rust/extensions/native`), pytest parity tests, bench via `scripts/bench_*` + `bench-fs-distributed.yml` (workflow_dispatch, `large-new-64GB`).

**Conventions that bind every task:** TDD (failing test first); byte-identical parity proven for every route change; new native symbols gated on `FS_SUPPORTS_*` consts with graceful old-wheel fallback AND version bumped in BOTH `packages/rust/extensions/native/pyproject.toml` and `Cargo.toml` (republish is Ben's call — note it in the PR); worktree tests via main `.venv` + `PYTHONPATH` + `GOLDENMATCH_NATIVE=0` for pure-Python lanes, in-tree `scripts/build_native.py` build for kernel lanes; `clippy -D warnings` + `cargo fmt` on touched Rust files pre-push; squash-merge on green (standing authorization).

---

## PR-1: bounded EM row materialization (epic item 4)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`train_em` ~:673, `estimate_m_from_labels` ~:1009, `train_em_continuous` ~:1244)
- Test: `packages/python/goldenmatch/tests/test_probabilistic.py`

**Problem:** all three trainers build `row_lookup` as Python dicts over the ENTIRE df before sampling, while `_sample_pairs` (u) + `_sample_blocked_pairs` (m) only ever touch a few thousand row ids.

**Approach:** sample pair IDS first (both samplers already return id pairs and need only the `__row_id__` column / blocks), then build `row_lookup` from `df.filter(__row_id__ ∈ sampled_ids)` only. Sampling depends only on row ids + blocks, so the sampled pairs — and therefore the trained model — are byte-identical.

- [ ] Failing test: `test_train_em_row_lookup_is_bounded` — monkeypatch/spy the dict-materialization seam (or assert via a 50k-row frame that the lookup row count == unique sampled ids, not df height); plus `test_train_em_bounded_lookup_identical_model` — same seed, old-vs-new `match_weights` equal (freeze expected from pre-change run).
- [ ] Implement in `train_em`; run FS suites (`test_probabilistic*.py`, `test_fs_ne_em.py`, `test_nlevel_em.py`).
- [ ] Same change in `estimate_m_from_labels` + `train_em_continuous`; rerun.
- [ ] Commit, PR, auto-merge.

## PR-2: kernel exclude handle + zero-copy Arrow FS entry (epic items 1+2)

**Files:**
- Modify: `packages/rust/extensions/native/src/score.rs` (FS entry ~:280-465; mirror `score_block_pairs_arrow` ~:515-664 and `ExcludeSet` ~:37-66)
- Modify: `packages/rust/extensions/native/src/lib.rs` (register fn + consts ~:25-38, :65)
- Modify: `packages/rust/extensions/native/pyproject.toml` + `Cargo.toml` (version bump, lockstep)
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`_score_fs_native_frame` ~:2332-2430, `score_probabilistic_bucket_native` ~:2457, `_fs_native_eligible` ~:2278)
- Modify: `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` (FS exclude-handle build at entry, mirroring :772-791; thread handle into `_score_one_bucket` FS lane :1098-1103)
- Modify: `packages/python/goldenmatch/scripts/check_native_symbols.py` corpus if needed (parses `m.add("CONST", ...)`)
- Test: `packages/python/goldenmatch/tests/test_native_fs_ne.py`, `tests/test_fs_bucket_native.py`, new parity cases

**Kernel changes:**
1. `score_block_pairs_fs`: add trailing `exclude_set: Option<PyRef<ExcludeSet>> = None` kwarg; resolve exactly like `score_block_pairs_arrow:563-574` (handle preferred, Vec fallback bit-identical). Backward compatible.
2. New `score_block_pairs_fs_arrow`: FS scoring math verbatim (levels / level_thresholds / NE / fs_normalize) but `row_ids: PyArrowType<ArrayData>` (Int64) + `field_arrays`/`ne_arrays: Vec<PyArrowType<ArrayData>>` via `StrCol` (zero-copy), exclude handle support, and the #688 sequential-vs-rayon gate (`GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS`) which the Vec FS entry currently lacks (it always rayons — same futex-park exposure). NE empty-string semantics: `StrCol.get` returns `Option<&str>`; keep the `!a.is_empty()` check.
3. `lib.rs`: register `score_block_pairs_fs_arrow`, add consts `FS_SUPPORTS_EXCLUDE_SET = true`, `FS_SUPPORTS_ARROW = true`.

**Python changes:**
- `_score_fs_native_frame`: when `getattr(mod, "FS_SUPPORTS_ARROW", False)` → extract `__row_id__` cast Int64 `.to_arrow()` + field/NE columns `.to_arrow()` (both frame lanes: polars `.to_arrow()`, pa.Table `.combine_chunks()`, mirroring `score_buckets.py:876-892`) and call the arrow entry; else legacy `.to_list()` path. `exclude_set=` sent only when a handle is supplied AND the relevant const present.
- `score_buckets`: for the FS bucket-native lane, build the exclude handle ONCE at entry (`build_exclude_set(list(frozen_exclude))`, probe via `hasattr` like :774-776, reuse `_warn_stale_native_wheel_once`), thread through `score_probabilistic_bucket_native(..., exclude_handle=...)`; per-bucket calls stop passing the Vec.
- Wheel-skew matrix (all byte-identical outputs): new-python+old-wheel → Vec path, no handle (status quo); old-python+new-wheel → kwargs never sent.

- [ ] Failing parity tests first (run against in-tree build): arrow entry == Vec entry on the `test_native_fs_ne.py` parametrized corpus (NE, level_thresholds, two-NE, exclude cases); handle-vs-Vec exclude parity; bucket E2E byte-identical clusters with `GOLDENMATCH_FS_BUCKET_NATIVE=1`.
- [ ] Implement kernel; `scripts/build_native.py`; `cargo clippy -D warnings` + `cargo fmt` (touched files only); run kernel-direct tests.
- [ ] Implement Python routing + handle threading; run `test_fs_bucket_native.py`, `test_native_fs_ne.py`, `test_probabilistic.py`, `test_fs_ne_*.py`.
- [ ] Bump native version in pyproject.toml + Cargo.toml (lockstep); note republish in PR body.
- [ ] Commit, PR, auto-merge.

## PR-3: unified `_fs_route()` + bucket default + data-scaled n_buckets (epic items 3+5)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (`_fs_default_bucket` :146-190; the 3 FS sites :2585+, :4099+, :4338+)
- Modify: `packages/python/goldenmatch/goldenmatch/backends/score_buckets.py` (`_default_n_buckets` :211-214)
- Test: `packages/python/goldenmatch/tests/test_bucket_default_routing.py` (routing), `tests/test_probabilistic.py` (route parity)

**Routing:** one `_fs_use_bucket_route(config, mk)` used by ALL THREE sites: bucket unless explicit non-bucket backend / `GOLDENMATCH_FS_DEFAULT_BUCKET=0` / columnar opt-in / active profile emitter / blocking strategy not in {None, static, multi_pass}. DROPS: the native-kernel requirement (non-native bucket scores via the vectorized per-block lane and is frame-memory-bounded; the batched fallback needs eager `build_blocks` which is the #1798 OOM path) and the 750K row cap for FS. `_use_bucket_scorer` (weighted) unchanged in this PR — weighted cap flip is a separate measured decision.

**n_buckets:** `_default_n_buckets(height=None)` → `min(max(cpu*4, height // 50_000), 4096)` when height given, else current formula. Wire height at the `score_buckets` call site.

**Measurement gate (before merge):**
- Local: `bench_1798.py`-style run at 1M, non-native bucket vs batched (`GOLDENMATCH_NATIVE=0`): parity of clusters + peak RSS + wall.
- CI: `gh workflow run bench-fs-distributed.yml` (5M, `large-new-64GB`) on the branch; require wall/RSS/F1 within noise of main.

- [ ] Failing routing tests: FS routes to bucket with native OFF (currently batched); FS routes to bucket above 750K (frame stub); emitter/strategy/backend exclusions still hold; kill switch works.
- [ ] Implement + local 1M measurement; parity assert.
- [ ] Trigger 5M workflow on branch; compare against a main baseline run.
- [ ] Commit, PR (measurement numbers in body), auto-merge.

## PR-4 (follow-up): bucket oversized auto-split + vectorized NxN cap (epic item 6)

Port `_auto_split_block` recovery into `_score_one_bucket`'s oversized keep-mask stage (FS + weighted slow lane), and cap single-block NxN in `score_probabilistic_vectorized` (route blocks above `_vec_max`-style cap to the native/batched lane). Parity: oversized fixtures vs polars-direct auto-split output. This PR is scoped LAST and may be deferred to its own session — it changes recall behavior (skips → splits) and needs its own fixtures.

---

**Execution order:** PR-1 → PR-2 → PR-3 (PR-4 follow-up). Each PR: branch off fresh origin/main, plan doc rides PR-1, `gh pr merge --auto` on green, never poll CI.
