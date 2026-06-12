# Rust Coverage Gaps (P1/P2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three highest-value Rust test-coverage gaps found in the 2026-06-08 audit: (A) wire the standalone pure-Rust crates' (`graph-core`, `score-core`) tests into CI, (B) add Rust-level unit tests to the `native` kernel and run them in CI, (C) make the `postgres` pgrx `#[pg_test]` suite actually execute via `cargo pgrx test`.

**Architecture:** Three independent tasks, each shippable as its own PR/branch. None share state. Task A is a pure CI-wiring change (the tests already exist). Task B adds in-crate `#[cfg(test)]` tests to pure-Rust kernel helpers plus a Cargo feature-gate so the pyo3 `extension-module` crate's test binary links. Task C wires `cargo pgrx test` into the existing `rust_pgrx` matrix lane and adds a few pyo3-free `#[pg_test]`s.

**Tech Stack:** Rust 1.94 (`cargo test`, `cargo clippy`), pyo3 0.23/0.24 (`extension-module` / `abi3`), pgrx 0.12.9 (`cargo pgrx test`), GitHub Actions (`.github/workflows/ci.yml`, `dorny/paths-filter`), pytest (native parity lane).

**Context for the executor — read before starting:**
- Local Rust on Windows needs this bash preamble before any `cargo`/`maturin` command (from `packages/rust/extensions/CLAUDE.md`):
  `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`
- **Task A (`graph-core`, `score-core`)** crates are pyo3-free standalone workspaces — `cargo test` runs cleanly locally on Windows with the preamble.
- **Task B (`native`)** is a pyo3 `extension-module` cdylib (no libpython linkage). Its test binary will NOT link by default — Step B1 fixes that. Local `cargo test` for it is best-effort; **CI's `native` lane is the authoritative verifier.**
- **Task C (`postgres`)** builds + tests ONLY on Linux CI (needs `libclang` + PG dev headers). Do NOT expect to run `cargo pgrx test` locally on Windows — verify via a CI run on the branch.
- Per `feedback_avoid_full_suite_oom`: never run the full pytest suite locally (xdist OOMs the box); run targeted files only, and lean on CI for the suite.
- Per `feedback_verify_perf_not_just_ship` and the repo's parity discipline: a test that doesn't actually go red on a regression is worthless. Each parity test below includes a "prove it catches a regression" sanity step.

---

## File Structure

| File | Task | Responsibility / change |
|---|---|---|
| `.github/workflows/ci.yml` | A, B, C | Add `cargo test` steps for sibling crates (`rust` job); add native unit-test step + seq-parity pytest (`native` lane); add `cargo pgrx test` step (`rust_pgrx` lane). |
| `packages/rust/extensions/graph-core/src/lib.rs` | A | (no change — tests already present; verifying they pass.) |
| `packages/rust/extensions/score-core/src/lib.rs` | A | Add a `#[cfg(test)] mod tests` with anchored scorer vectors. |
| `packages/rust/extensions/native/Cargo.toml` | B | Gate `pyo3/extension-module` behind a `default` feature so `cargo test --no-default-features` links libpython. |
| `packages/rust/extensions/native/src/featurize.rs` | B | Add `#[cfg(test)] mod tests` for `prepare`/`hash_gram`/`featurize_one`/`project_one` invariants. |
| `packages/rust/extensions/native/src/score.rs` | B | Add `#[cfg(test)] mod tests` for `soundex`/`soundex_code`/`compute_pairwise` mirror. |
| `packages/rust/extensions/native/src/pairs.rs` | B | Add `#[cfg(test)] mod tests` for `canonicalize_pairs`/`candidate_pair_count`/`block_histogram`. |
| `packages/rust/extensions/native/src/hash.rs` | B | Add `#[cfg(test)] mod tests` for the `gm_record_fingerprint` C ABI. |
| `packages/rust/extensions/postgres/src/kernels.rs` | C | Add a few pyo3-free `#[pg_test]`s (graph + fingerprint edge cases). |

---

## Task A: Wire `graph-core` + `score-core` tests into CI (P2)

**Why:** `graph-core` has 9 unit tests and `score-core` has 0; neither crate is a member of the `extensions` cargo workspace (each is its own standalone `[workspace]`), so the `rust` job's `cargo test --workspace` (which only builds `bridge`) never touches them. The `rust` job already TRIGGERS on `packages/rust/**`, so the only missing piece is the test invocation itself.

**Files:**
- Modify: `.github/workflows/ci.yml` (the `rust:` job, currently ends at the two `cargo` steps ~line 734-735)
- Create test module in: `packages/rust/extensions/score-core/src/lib.rs`
- Verify-only: `packages/rust/extensions/graph-core/src/lib.rs`

- [ ] **Step A1: Confirm graph-core's existing tests pass locally**

Run (with the Windows cargo preamble):
```bash
cargo test --manifest-path packages/rust/extensions/graph-core/Cargo.toml
```
Expected: PASS, 7 tests run — 6 in `src/lib.rs` (`dedup_keeps_max_and_canonicalizes`, `cc_groups_transitive_and_includes_singletons`, `dedup_arrow_int64_canonicalizes_and_keeps_max`, `dedup_arrow_utf8_maps_back_to_strings`, `cc_arrow_int64_returns_sorted_list`, `cc_arrow_utf8_returns_sorted_list`) + 1 in `src/dict.rs` (`first_seen_order_is_deterministic`). This proves the tests are real and currently green — they just never run in CI. (Take the count `cargo test` actually prints as authoritative.)

- [ ] **Step A2: Add score-core unit tests (write the failing tests)**

Append to `packages/rust/extensions/score-core/src/lib.rs` (use the crate's actual public fn names — confirm via `grep -n "pub fn" packages/rust/extensions/score-core/src/lib.rs` first; the names below match the shims in `native/src/score.rs`):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jaro_winkler_identity_and_disjoint() {
        assert_eq!(jaro_winkler_similarity("abc", "abc"), 1.0);
        assert_eq!(jaro_winkler_similarity("abc", "xyz"), 0.0);
    }

    #[test]
    fn levenshtein_identity_and_disjoint() {
        assert_eq!(levenshtein_similarity("abc", "abc"), 1.0);
        // one substitution out of three chars -> 1 - 1/3
        let s = levenshtein_similarity("abc", "abx");
        assert!((s - (2.0 / 3.0)).abs() < 1e-9, "got {s}");
    }

    #[test]
    fn token_sort_is_order_invariant_on_0_100_scale() {
        // token_sort_ratio is the 0-100 scale (score_field divides by 100).
        assert_eq!(token_sort_ratio("a b", "b a"), 100.0);
    }

    #[test]
    fn score_one_dispatches_by_id() {
        // ids: 0=jaro_winkler, 1=levenshtein, 2=token_sort (0-1 here per
        // score.rs docs), 3=exact. Confirm the dispatch table is wired.
        assert_eq!(score_one(3, "abc", "abc"), 1.0); // exact match
        assert_eq!(score_one(3, "abc", "abd"), 0.0); // exact mismatch
    }
}
```

- [ ] **Step A3: Run score-core tests to verify they pass**

Run:
```bash
cargo test --manifest-path packages/rust/extensions/score-core/Cargo.toml
```
Expected: PASS, 4 tests. If a name/scale assertion fails, FIX THE TEST to match the crate's real contract (do not change the crate) — these are characterization tests over known-correct code. Note the exact `score_one` token-sort scale: `score.rs:509-511` says ids 0-3 return `[0,1]` from `score_one`, while the `token_sort_ratio` *shim* returns 0-100. Keep the two assertions consistent with that.

- [ ] **Step A4: Prove the tests catch a regression (sanity)**

Temporarily change `assert_eq!(jaro_winkler_similarity("abc", "abc"), 1.0);` to `0.9`, run Step A3, confirm it FAILS, then revert. This proves the assertion is load-bearing.

- [ ] **Step A5: Wire both crates into the `rust` job in CI**

In `.github/workflows/ci.yml`, the `rust:` job has `working-directory: packages/rust/extensions` and currently ends with:
```yaml
      - run: cargo test --workspace
      - run: cargo clippy --workspace -- -D warnings
```
Add four steps after them:
```yaml
      # The standalone pyo3-free sibling crates (graph-core, score-core) are
      # each their own [workspace], so `cargo test --workspace` above (which
      # only builds the `bridge` member) never touches them. Their logic IS
      # exercised indirectly (native parity + the datafusion FFI tests), but
      # their own unit tests + clippy were dead in CI. Run them explicitly.
      - run: cargo test --manifest-path graph-core/Cargo.toml
      - run: cargo clippy --manifest-path graph-core/Cargo.toml -- -D warnings
      - run: cargo test --manifest-path score-core/Cargo.toml
      - run: cargo clippy --manifest-path score-core/Cargo.toml -- -D warnings
```

- [ ] **Step A6: Validate the workflow edit**

Run:
```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
```
Expected: `ci.yml parses`. (Editing `ci.yml` also force-triggers every CI job per the `ci_workflow` filter, so the new steps run on the very first push.)

- [ ] **Step A7: Commit**

```bash
git add .github/workflows/ci.yml packages/rust/extensions/score-core/src/lib.rs
git commit -m "test(rust): run graph-core + score-core unit tests in CI

The two crates are standalone [workspace]s, so the rust job's
cargo test --workspace (bridge-only) never ran them. graph-core had
9 dead-in-CI tests; score-core had none. Wire both + add score-core
scorer-vector tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task B: Native kernel Rust unit tests (P1)

**Why:** `native` is 1,859 lines of perf-critical kernels with ZERO Rust-level tests — covered only by Python parity, which only exercises the inputs the pytest suite happens to feed. This task adds in-crate `#[cfg(test)]` tests for the pure-Rust helpers (featurize/soundex/pairs-math/C-ABI), runs them in CI, AND wires the orphaned `test_native_block_seq_parity.py` (the rayon-vs-sequential `#688` branch) into the native lane.

**The linking constraint (read first):** `native/Cargo.toml` declares `pyo3` with the `extension-module` feature, which tells pyo3 NOT to link libpython (the `.so` is loaded BY CPython). A `cargo test` binary is a standalone executable; with `extension-module` on, the `#[pyfunction]`/`#[pyclass]` symbols compiled into the crate reference undefined `Py*` symbols and the test binary fails to link. The documented pyo3 fix (https://pyo3.rs/main/faq, "I can't run cargo test") is to gate `extension-module` behind a default feature and run `cargo test --no-default-features` (which links libpython). Our pure-Rust test helpers never initialize Python, so no `auto-initialize` is needed — they just need the symbols to resolve at link time.

**Files:**
- Modify: `packages/rust/extensions/native/Cargo.toml`
- Modify: `packages/rust/extensions/native/src/featurize.rs`, `src/score.rs`, `src/pairs.rs`, `src/hash.rs`
- Modify: `.github/workflows/ci.yml` (the `native:` lane, ~line 744-792)

- [ ] **Step B1: Feature-gate `extension-module` in native/Cargo.toml**

In `packages/rust/extensions/native/Cargo.toml`, change the `pyo3` dependency line and add a `[features]` table. From:
```toml
pyo3 = { version = ">=0.23.3, <0.25", features = ["extension-module", "abi3-py311"] }
```
to:
```toml
# extension-module is a DEFAULT feature (not always-on) so `cargo test
# --no-default-features` can link the test binary against libpython. maturin
# (native_wheel lane) and scripts/build_native.py both build with default
# features, so the shipped wheel/.so still gets extension-module. See the
# pyo3 FAQ "I can't run cargo test".
pyo3 = { version = ">=0.23.3, <0.25", features = ["abi3-py311"] }
```
Add (place after the `[dependencies]` block, before `[profile.release]`):
```toml
[features]
default = ["extension-module"]
extension-module = ["pyo3/extension-module"]
```

- [ ] **Step B2: Verify the production build still gets extension-module**

Run (Windows preamble first):
```bash
uv run python scripts/build_native.py
```
Expected: builds `goldenmatch/_native.*.so` (or `.pyd`) successfully — `build_native.py` runs `cargo build --release` with default features, so `extension-module` is still on. Then confirm the ext imports:
```bash
uv run python -c "from goldenmatch.core import _native_loader; m=_native_loader.native_module(); print('native ok', m and m.__name__)"
```
Expected: `native ok goldenmatch._native`. If this regresses, the feature wiring in B1 is wrong — fix before continuing.

- [ ] **Step B3: Verify the test binary now LINKS (the make-or-break step)**

This is the highest-risk step. Run (CI is the real target; locally needs libpython discoverable):
```bash
cargo test --manifest-path packages/rust/extensions/native/Cargo.toml --no-default-features
```
Expected: COMPILES AND LINKS (0 tests run yet, "running 0 tests ... test result: ok. 0 passed"). 

If it FAILS TO LINK with undefined `Py*`/`PyInit` symbols, the runner's Python lib isn't being found. Remedies in order:
1. Set `PYO3_PYTHON` to an interpreter whose shared libpython is present (CI: system `python3` after `apt-get install -y libpython3-dev`; local Windows: the dev Python that has `python3xx.dll`).
2. If `--no-default-features` still won't link on CI's python-build-standalone interpreter, **fall back to the extract-to-core approach**: create a pyo3-free sibling crate `native-kernels-core` (mirroring `score-core`/`graph-core`), MOVE the pure helpers (`prepare`, `hash_gram`, `featurize_one`, `project_one`, `soundex`, `soundex_code`, `compute_pairwise[_precomputed]`, and the `pairs.rs` math) into it, have `native`'s `#[pyfunction]`s delegate to it (matches the existing "thin shim over score-core" pattern), and put the B4-B7 tests in the core crate (which `cargo test`s with zero linking risk). This is more churn but is the architecturally-consistent path the repo already uses. **Decision point:** prefer `--no-default-features` if B3 links on CI; only extract if it doesn't. Surface the choice in the PR description either way.

- [ ] **Step B4: featurize.rs unit tests**

Append to `packages/rust/extensions/native/src/featurize.rs`. These assert Rust-internal invariants (byte-exact cross-language parity stays covered by `tests/test_embeddings.py`):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const SEED: [u8; 8] = 42u64.to_le_bytes();

    #[test]
    fn prepare_lowercases_collapses_and_wraps() {
        assert_eq!(prepare("  John   SMITH ", true, "#"), "#john smith#");
        assert_eq!(prepare("John Smith", false, "#"), "#John Smith#");
    }

    #[test]
    fn prepare_empty_stays_empty_no_boundary() {
        assert_eq!(prepare("   ", true, "#"), "");
        assert_eq!(prepare("", true, "#"), "");
    }

    #[test]
    fn hash_gram_is_deterministic_and_in_range() {
        let (i1, s1) = hash_gram(&SEED, "abc", 64);
        let (i2, s2) = hash_gram(&SEED, "abc", 64);
        assert_eq!((i1, s1), (i2, s2)); // deterministic
        assert!(i1 < 64);
        assert!(s1 == 1.0 || s1 == -1.0);
    }

    #[test]
    fn featurize_one_is_l2_unit_norm_for_nonempty() {
        let row = featurize_one("john smith", 256, 2, 3, true, "#", &SEED);
        let norm: f32 = row.iter().map(|v| v * v).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "expected unit norm, got {norm}");
    }

    #[test]
    fn featurize_one_empty_text_is_zero_vector() {
        let row = featurize_one("   ", 256, 2, 3, true, "#", &SEED);
        assert!(row.iter().all(|&v| v == 0.0));
    }

    #[test]
    fn project_one_is_l2_unit_norm_for_nonempty() {
        // dim=4, n_features=8 row-major weights (all ones -> nonzero acc).
        let dim = 4usize;
        let nf = 8usize;
        let w = vec![1.0f32; nf * dim];
        let out = project_one("john", &w, nf, dim, 2, 3, true, "#", &SEED);
        let norm: f32 = out.iter().map(|v| v * v).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5 || norm == 0.0, "got {norm}");
    }
}
```

- [ ] **Step B5: score.rs unit tests (soundex parity + mirror)**

Append to `packages/rust/extensions/native/src/score.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn soundex_characterizes_implementation_output() {
        // These pin score.rs::soundex()'s ACTUAL output (it targets
        // jellyfish.soundex parity, asserted at the Python level). Robert/
        // Ashcraft match jellyfish exactly; Tymczak is T522 under THIS impl's
        // H/W-collapse handling (some jellyfish builds return T520) — assert
        // the impl's output, see the recovery note below.
        assert_eq!(soundex("Robert"), "R163");
        assert_eq!(soundex("Ashcraft"), "A261");
        assert_eq!(soundex("Tymczak"), "T522");
    }

    #[test]
    fn soundex_pads_short_codes_to_four() {
        assert_eq!(soundex("Lee").len(), 4);
        assert_eq!(soundex("A"), "A000");
    }

    #[test]
    fn soundex_non_alpha_is_empty() {
        assert_eq!(soundex("123"), "");
        assert_eq!(soundex(""), "");
    }

    #[test]
    fn soundex_code_table() {
        assert_eq!(soundex_code('B'), b'1');
        assert_eq!(soundex_code('R'), b'6');
        assert_eq!(soundex_code('A'), b'0');
    }

    #[test]
    fn compute_pairwise_symmetric_mirrors_upper_triangle() {
        let a = vec!["x".to_string(), "y".to_string()];
        let out = compute_pairwise(&a, &a, true, |p, q| if p == q { 1.0 } else { 0.3 });
        // 2x2 row-major: [ (x,x) (x,y) ; (y,x) (y,y) ] = [1, .3, .3, 1]
        assert_eq!(out, vec![1.0, 0.3, 0.3, 1.0]);
    }
}
```
Note: these are CHARACTERIZATION tests — they pin `soundex()`'s actual output. If a vector mismatches at runtime, update the TEST to the value the function actually prints (do NOT change `soundex()`). Do not "correct" a vector to a jellyfish value you computed by hand — the Python-level parity suite owns cross-language parity; this test owns the Rust impl's stability.

- [ ] **Step B6: pairs.rs unit tests (exact-integer kernels)**

Append to `packages/rust/extensions/native/src/pairs.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonicalize_pairs_orders_min_max_preserving_score() {
        let got = canonicalize_pairs(vec![(2, 1, 0.5), (1, 3, 0.9)]);
        assert_eq!(got, vec![(1, 2, 0.5), (1, 3, 0.9)]);
    }

    #[test]
    fn candidate_pair_count_sums_n_choose_2() {
        assert_eq!(candidate_pair_count(vec![3]), 3);
        assert_eq!(candidate_pair_count(vec![4]), 6);
        assert_eq!(candidate_pair_count(vec![3, 4]), 9);
        assert_eq!(candidate_pair_count(vec![1, 0]), 0);
    }

    #[test]
    fn candidate_pair_count_large_block_does_not_overflow_i64_via_i128() {
        // 1e6 choose 2 = 499_999_500_000 (overflows i64 product before /2 only
        // if accumulated in i64; the kernel uses i128 internally).
        assert_eq!(candidate_pair_count(vec![1_000_000]), 499_999_500_000);
    }

    #[test]
    fn block_histogram_nearest_rank_percentiles() {
        // sorted [1,2,3,4]: count=4 total=10 max=4
        // p50 idx=ceil(.5*4)-1=1 -> 2 ; p95 idx=ceil(3.8)-1=3 -> 4 ; p99 -> 4
        assert_eq!(block_histogram(vec![4, 1, 3, 2]), (4, 10, 4, 2, 4, 4));
        assert_eq!(block_histogram(vec![]), (0, 0, 0, 0, 0, 0));
    }
}
```

- [ ] **Step B7: hash.rs C-ABI unit test**

Append to `packages/rust/extensions/native/src/hash.rs` (tests the pyo3-free C boundary + NUL handling; the fingerprint value is the pinned cross-surface vector):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::CString;

    #[test]
    fn gm_record_fingerprint_c_abi_matches_pinned_vector() {
        let json = CString::new(r#"{"a":"x"}"#).unwrap();
        let mut out = [0i8; 65];
        let rc = gm_record_fingerprint(json.as_ptr(), out.as_mut_ptr());
        assert_eq!(rc, 0);
        let bytes: Vec<u8> = out[..64].iter().map(|&c| c as u8).collect();
        assert_eq!(
            String::from_utf8(bytes).unwrap(),
            "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"
        );
        assert_eq!(out[64], 0, "missing NUL terminator");
    }

    #[test]
    fn gm_record_fingerprint_null_ptr_returns_error() {
        let mut out = [0i8; 65];
        assert_eq!(
            gm_record_fingerprint(std::ptr::null(), out.as_mut_ptr()),
            1
        );
    }
}
```
Note: `out_hex` is `*mut c_char`; on platforms where `c_char` is `u8` the `[0i8; 65]` / `as u8` casts may need to become `[0u8;65]` + `as_mut_ptr() as *mut c_char`. Adjust to the crate's `c_char` (it's `std::os::raw::c_char`); use `as *mut c_char` casts to stay portable.

- [ ] **Step B8: Run all native unit tests + prove one catches a regression**

Run:
```bash
cargo test --manifest-path packages/rust/extensions/native/Cargo.toml --no-default-features
```
Expected: PASS, ~18 tests across featurize/score/pairs/hash. Then sanity: change the pinned fingerprint in B7 by one hex char, re-run, confirm FAIL, revert. (If running locally on Windows is blocked by the libpython link, defer the authoritative run to CI per Step B10 and note it in the PR.)

- [ ] **Step B9: Wire native unit tests into the `native` CI lane**

In `.github/workflows/ci.yml`, the `native:` lane currently has a "clippy + test (fingerprint-core crate)" step (~line 770-777) followed by "Build native extension into the package". Add a step right AFTER the fingerprint-core step and BEFORE the build step:
```yaml
      - name: Unit-test the native kernels (pure-Rust helpers)
        # extension-module is gated behind a default feature so the test binary
        # can link libpython (--no-default-features turns it off). Covers
        # soundex/featurize/pairs-math/C-ABI logic that the Python parity suite
        # only exercises indirectly. Build below still uses default features.
        working-directory: packages/rust/extensions/native
        env:
          PYO3_PYTHON: /usr/bin/python3
        run: |
          sudo apt-get update && sudo apt-get install -y libpython3-dev
          cargo test --no-default-features
```
(If Step B3's fallback extract-to-core path was taken instead, replace this with `cargo test --manifest-path native-kernels-core/Cargo.toml` and drop the libpython install — the core crate links without it.)

- [ ] **Step B10: Wire the orphaned seq-parity test into the lane**

`packages/python/goldenmatch/tests/test_native_block_seq_parity.py` exists but is referenced in NO workflow — it asserts the rayon path and the sequential path emit byte-identical pairs (the `#688` `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS` branch). The test already sweeps BOTH thresholds internally (it `monkeypatch.setenv`s `GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS` to `"0"` and a huge value per case), so wiring it is a one-line list addition — do NOT add a separate forced-env step (it would be a no-op the monkeypatch overrides). In the `native:` lane's final "Parity + in-house suite" pytest step (~line 784-792), add the file to the list:
```yaml
      - name: Parity + in-house suite (ext present -> native path runs, not skips)
        run: |
          uv run pytest \
            packages/python/goldenmatch/tests/test_native_parity.py \
            packages/python/goldenmatch/tests/test_native_block_seq_parity.py \
            packages/python/goldenmatch/tests/test_record_fingerprint.py \
            packages/python/goldenmatch/tests/test_pairs.py \
            packages/python/goldenmatch/tests/test_embeddings.py \
            packages/python/goldenmatch/tests/test_inhouse_embedder.py \
            -v
```
Before relying on the above, confirm the internal sweep is really there: `grep -n "RAYON_MIN_PAIRS\|monkeypatch\|setenv" packages/python/goldenmatch/tests/test_native_block_seq_parity.py`. If (and only if) it does NOT sweep internally, add a second invocation of just that file under `env: GOLDENMATCH_NATIVE_RAYON_MIN_PAIRS: "0"` to force the rayon path.

- [ ] **Step B11: Validate the workflow + commit**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
```
Then:
```bash
git add packages/rust/extensions/native/Cargo.toml \
  packages/rust/extensions/native/src/featurize.rs \
  packages/rust/extensions/native/src/score.rs \
  packages/rust/extensions/native/src/pairs.rs \
  packages/rust/extensions/native/src/hash.rs \
  .github/workflows/ci.yml
git commit -m "test(native): unit-test the pure-Rust kernels + run the rayon parity guard

native had 0 Rust tests across 1859 lines. Gate extension-module behind
a default feature so cargo test --no-default-features links, then cover
soundex/featurize/pairs-math/C-ABI. Also wires the orphaned
test_native_block_seq_parity.py (the #688 rayon-vs-sequential branch)
into the native lane.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step B12: Push the branch and confirm the `native` lane is green in CI**

This is the authoritative verification (local Windows linking is best-effort). Push, then:
```bash
gh pr checks <N> --watch    # or poll: while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done
gh run view <run-id> --log | grep -E "test result:|passed|failed,"
```
Confirm the "Unit-test the native kernels" step ran (not skipped) and the pytest summary shows the seq-parity test passed under both threshold settings.

---

## Task C: Make the pgrx `#[pg_test]` suite execute (P1)

**Why:** `cargo pgrx test` appears NOWHERE in `.github/`. The 5 `#[pg_test]`s in `postgres/src/kernels.rs` are written but never run; the `rust_pgrx` lane only does `cargo pgrx install` + a `psql` smoke. This wires `cargo pgrx test` into the lane so the pg_test suite actually runs, and adds a few more pyo3-free `#[pg_test]`s. **Bridge-backed pg_tests (quick.rs/pipeline.rs/correction.rs/core_apis.rs, which go through embedded CPython) are deferred to a follow-up** — they need `import goldenmatch` live in the pgrx-test backend and carry more flake surface; the psql smoke already covers several of them end-to-end.

**This task is CI-only.** Do not attempt `cargo pgrx test` locally on Windows (no `libclang`/PG dev headers). Verify on the branch via CI.

**Files:**
- Modify: `packages/rust/extensions/postgres/src/kernels.rs` (extend the existing `#[cfg(any(test, feature = "pg_test"))] mod tests`)
- Modify: `.github/workflows/ci.yml` (the `rust_pgrx:` matrix lane)

- [ ] **Step C1: Add new pyo3-free `#[pg_test]`s to kernels.rs**

In `packages/rust/extensions/postgres/src/kernels.rs`, inside the existing `mod tests` (after `connected_components_str_includes_singleton`, before the closing brace ~line 286), add edge-case tests that exercise the graph-core + fingerprint-core direct paths through the pgrx surface:

```rust
    /// Empty edge list: every id is its own singleton component.
    #[pg_test]
    fn connected_components_all_singletons_when_no_edges() {
        let rows: Vec<(i64, i64)> = crate::kernels::goldenmatch_connected_components(
            vec![],
            vec![],
            vec![],
            vec![10, 20, 30],
        )
        .collect();
        // 3 ids, no edges -> 3 distinct component labels.
        let labels: std::collections::HashSet<i64> = rows.iter().map(|(c, _)| *c).collect();
        assert_eq!(rows.len(), 3);
        assert_eq!(labels.len(), 3);
    }

    /// Fingerprint is stable across key order (canonicalization sorts fields).
    #[pg_test]
    fn record_fingerprint_is_key_order_independent() {
        let a = crate::kernels::goldenmatch_record_fingerprint(r#"{"x":"1","y":"2"}"#.to_string());
        let b = crate::kernels::goldenmatch_record_fingerprint(r#"{"y":"2","x":"1"}"#.to_string());
        assert_eq!(a, b);
    }

    /// dedup keeps the max score across duplicate canonical pairs.
    #[pg_test]
    fn pair_dedup_keeps_max_across_duplicates() {
        let rows: Vec<(i64, i64, f64)> = crate::kernels::goldenmatch_pair_dedup(
            vec![1, 1, 2],
            vec![2, 2, 1],
            vec![0.3, 0.7, 0.5],
        )
        .collect();
        // (1,2) seen thrice -> keep 0.7.
        assert_eq!(rows, vec![(1, 2, 0.7)]);
    }
```
First confirm the exact signatures of `goldenmatch_connected_components` / `goldenmatch_pair_dedup` (arg order, return iterator types) by reading `kernels.rs` — match the existing tests' call style exactly.

- [ ] **Step C2: Add the `cargo pgrx test` step to the `rust_pgrx` lane**

In `.github/workflows/ci.yml`, the `rust_pgrx:` lane builds the extension at "Build + install extension" (~line 1094-1106, `working-directory: packages/rust/extensions/postgres`). Add a step immediately after it, gated to the pg16 leg (the pg_tests are PG-version-independent pure logic; one leg bounds CI cost):
```yaml
      - name: cargo pgrx test (runs the #[pg_test] suite in a managed PG)
        # The pgrx test harness builds a fresh extension into its own initdb'd
        # instance (cargo pgrx init ran above) and runs every #[pg_test]. Without
        # this, the kernels.rs pg_tests never execute. Pinned to the pg16 leg —
        # the tests are pyo3-free pure logic (graph-core/fingerprint-core), so
        # they're PG-major-independent and one leg is sufficient coverage.
        if: matrix.pg == '16'
        working-directory: packages/rust/extensions/postgres
        run: cargo pgrx test --no-default-features --features "pg16 pg_test" pg16
```
Note: confirm the exact pgrx 0.12.9 invocation. The canonical form is `cargo pgrx test pg16`; pgrx auto-enables the `pg_test` feature for the run. If `cargo pgrx test pg16` alone errors on feature resolution, use the explicit `--no-default-features --features "pg16 pg_test"` form shown. Verify against `cargo pgrx test --help` in the lane if it fails.

- [ ] **Step C3: Validate the workflow edit**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
```
Expected: `ci.yml parses`.

- [ ] **Step C4: Commit**

```bash
git add packages/rust/extensions/postgres/src/kernels.rs .github/workflows/ci.yml
git commit -m "test(pg): execute the pgrx #[pg_test] suite via cargo pgrx test

cargo pgrx test was nowhere in CI -> the 5 kernels.rs pg_tests never ran.
Wire it into the rust_pgrx lane (pg16 leg) and add graph/fingerprint
edge-case pg_tests. Bridge-backed (embedded-CPython) pg_tests for
quick/pipeline/correction are a deferred follow-up.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step C5: Push and confirm the `rust_pgrx` lane runs the pg_test step green in CI**

```bash
while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done
gh run view <run-id> --log | grep -E "cargo pgrx test|pg_test|test result:|passed|failed,"
```
Confirm the "cargo pgrx test" step appears on the pg16 leg and reports all `#[pg_test]`s passing. If the managed backend fails to start because the embedded bridge can't `import goldenmatch`, confirm the goldenmatch pip-install step (lane line ~1083) ran before this step; if the bridge imports at `_PG_init` (load time) rather than lazily, move the goldenmatch install ahead of the pgrx test step.

---

## Deferred (documented, NOT in scope)

- **Bridge-backed pgrx pg_tests** (`quick.rs`, `pipeline.rs`, `correction.rs`, `core_apis.rs`, `goldenflow.rs`, `spi.rs`): need `import goldenmatch` live in the pgrx-test backend (embedded CPython); higher flake surface. The psql smoke already exercises several end-to-end. Add incrementally once Task C's harness is proven green.
- **`bridge/api.rs` wrapper tests** (P2 from the audit, 33 pub fns / 6 tests): separate plan — the JSON-marshalling shims for `autoconfig`/`match_tables`/`identity_*`/`evaluate`/`train_em`/etc. are untested at the Rust level. Out of scope here.
- **`cargo-llvm-cov` measured coverage job**: the meta-gap (no quantified line coverage anywhere). A single coverage lane over the buildable crates would turn this structural audit into a tracked number. Separate plan.
- **`datafusion-udf` graph `_str` variants + `goldenembed/main.rs` CLI**: low-priority tail; not in P1/P2.

---

## Done criteria

- [ ] `rust` job runs `graph-core` (7) + `score-core` (4) tests green in CI.
- [ ] `native` lane runs the new Rust unit tests (~18) green AND runs `test_native_block_seq_parity.py` under both rayon thresholds.
- [ ] `rust_pgrx` lane (pg16) runs `cargo pgrx test` with all `#[pg_test]`s green.
- [ ] `scripts/build_native.py` + the `native_wheel` lane still produce a working `extension-module` artifact (feature-gate is transparent to them).
- [ ] No new `continue-on-error` masking; the new steps are hard gates.
