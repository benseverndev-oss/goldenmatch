# Dip Valley-Targeting Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `ScoreDiagnostics::dip()` in the `suggest-core` kernel so it targets the valley just **below the high-score (true-match) mass** instead of the global lowest-count interior bin, which on right-skewed ER distributions collapses the threshold (proposes 0.90 -> 0.04 and destroys precision).

**Architecture:** Localized to `suggest-core/src/diagnostics.rs::dip()` (one function). The current heuristic returns the deepest interior valley with any higher bin on each side; on a distribution with a dominant low-score non-match spike it locks onto a left-tail sliver. Replace it with a **right-anchored, prominence-based** finder: locate the rightmost prominent high-score peak (the true-match mode, detectable as a local maximum standing well above the trough to its left even when it carries little total mass), then return the minimum-count bin in the gap below it. Because the kernel's `threshold_rule` already raises/lowers *toward* `dip()`, fixing the valley location fixes BOTH the lower-magnitude bug (this finding) and the symmetric raise case in one change. Rules, contract, Python adapter, and the `GOLDENMATCH_SUGGEST_FULL_DIST` flag are UNCHANGED.

**Tech Stack:** Rust (`suggest-core` crate, pyo3-free; `cargo test`), the in-tree native build (`scripts/build_native.py`), and the `scripts/suggest_quality` gym/oracle for end-to-end validation.

**Spec / context:** `docs/superpowers/specs/2026-06-25-pre-threshold-scores-design.md` (the full-dist work that exposed this; its `## Findings` section records the destructive-lowering symptom this plan fixes). This plan is the follow-on the findings point to.

---

## Conventions
- Work from `D:\show_case\goldenmatch\.worktrees\suggest-gym` (branch `feat/suggest-gym`). Local commits only -- NO push, NO PR.
- Rust build/test (set the toolchain env per CLAUDE.md):
  `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" CARGO_HOME="C:/Users/bsevern/.cargo" RUSTUP_HOME="C:/Users/bsevern/.rustup"` then `cargo test -p goldenmatch-suggest-core` from the worktree root (the crate is at `packages/rust/extensions/suggest-core`). **Do NOT run `cargo build`/`test` on crates that link `ort`/`onnxruntime` -- suggest-core does not, so `-p goldenmatch-suggest-core` is safe.**
- Native rebuild so the gym picks up the Rust change (the in-tree `goldenmatch._native` is first in the loader order): `D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/build_native.py` from the worktree root. Confirm the symbol moved by re-running the gym (a stale wheel/in-tree skew is a known footgun -- see root CLAUDE.md `goldenmatch-native`).
- Python/gym runs: `cd D:/show_case/goldenmatch/.worktrees/suggest-gym && POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_AUTOCONFIG_MEMORY=0 PYTHONPATH="D:/show_case/goldenmatch/.worktrees/suggest-gym/packages/python/goldenmatch;D:/show_case/goldenmatch/.worktrees/suggest-gym" D:/show_case/goldenmatch/.venv/Scripts/python.exe ...`. Never run the full pytest suite (OOMs the box).
- Commit trailers each time:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01Wz94wngiSXtkxzBPKzqyUy`.

## Grounded facts (verified 2026-06-25)
- `ScoreDiagnostics` struct: `diagnostics.rs:133-140` -- fields `histogram: Vec<(f64, i64)>` (24 bins over [0,1], left-edge + count), `mass_above`, `mass_just_below`, `n_pairs`.
- `dip()` to replace: `diagnostics.rs:182-205`. Current logic: global lowest-count interior bin `i` (1..len-1) with `left_max > counts[i] && right_max > counts[i]`, filtered to `count < 0.25 * peak`, returns `histogram[i].0`.
- Consumer: `rules.rs:26-57` (the `thr:dip:<matchkey>` branch). It fires when `dip()` is `Some` and `(dip - current).abs() > DIP_MIN_GAP` (`DIP_MIN_GAP` const), emitting `RaiseThreshold` if `dip > current` else `LowerThreshold`, with `proposed_value`/patch `value = round2(dip)`. **Do not change this branch** -- only change what `dip()` returns.
- The recall-risk branch (`rules.rs:85-112`, `thr:lower:*`, fixed `current - RECALL_STEP_DOWN`) is NOT the culprit and is out of scope -- instrumentation confirmed the destructive suggestion is `thr:dip:fuzzy_match`, not `thr:lower:*`.
- Rust dip tests live in `diagnostics.rs` `#[cfg(test)]` (≈ lines 334-362, incl. `dip_detection_bimodal`); rule tests in `rules.rs` (≈ 226-417) with the `sd(mass_above, mass_just_below, dip_opt)` fixture helper and `moves_to_dip_when_threshold_off_valley` (≈ 261-271). Re-run BOTH files; update any test only with a written justification.

### The real distribution this must fix (measured: NCVR-synthetic, `threshold_too_high` perturbation, FULL_DIST=1 diagnostic run, 875,128 pairs, 24 bins)
| idx | left_edge | count | | idx | left_edge | count |
|----|-----------|-------|-|----|-----------|-------|
| 0 | 0.0000 | 441008 | | 12 | 0.5000 | 66375 |
| 1 | 0.0417 | 48 | | 13 | 0.5417 | 40352 |
| 2 | 0.0833 | 1002 | | 14 | 0.5833 | 19660 |
| 3 | 0.1250 | 5376 | | 15 | 0.6250 | 7669 |
| 4 | 0.1667 | 6263 | | 16 | 0.6667 | 2296 |
| 5 | 0.2083 | 10586 | | 17 | 0.7083 | 536 |
| 6 | 0.2500 | 16894 | | 18 | 0.7500 | 152 |
| 7 | 0.2917 | 39651 | | 19 | 0.7917 | 154 |
| 8 | 0.3333 | 52055 | | 20 | 0.8333 | 181 |
| 9 | 0.3750 | 40747 | | 21 | 0.8750 | 97 |
| 10 | 0.4167 | 49015 | | 22 | 0.9167 | 583 |
| 11 | 0.4583 | 72972 | | 23 | 0.9583 | 1456 |

- `dip()` currently returns **idx 1 (0.0417)** -- the sliver between the bin-0 spike (441008) and the central hump.
- Correct target: **idx 21 (0.8750)** -- the trough between the central non-match hump (tapers out by idx 17) and the true-match mode (idx 22-23, 583+1456). bin 23 (1456) is ~15x bin 21 (97): the match mode is a clear local peak despite being ~0.2% of total mass.

## File structure
- Modify only: `packages/rust/extensions/suggest-core/src/diagnostics.rs` (rewrite `dip()` + add tests in its `#[cfg(test)]` module).
- Task 3 appends a findings note to `docs/superpowers/specs/2026-06-25-pre-threshold-scores-design.md` (no code).

---

## Task 1: Failing Rust tests pinning the right-anchored valley behavior

**Files:** `packages/rust/extensions/suggest-core/src/diagnostics.rs` (tests only).

- [ ] **Step 1: Read** `diagnostics.rs` -- the `ScoreDiagnostics` struct, the current `dip()`, and the existing dip tests. Note the test-module style (how a `ScoreDiagnostics` is constructed directly in tests; mirror it).

- [ ] **Step 2: Add three failing tests** in the `#[cfg(test)]` module. Construct `ScoreDiagnostics` directly (histogram + zeroed masses + `n_pairs`). Helper:

```rust
fn diag(hist: Vec<(f64, i64)>) -> ScoreDiagnostics {
    let n: i64 = hist.iter().map(|(_, c)| *c).sum();
    ScoreDiagnostics { histogram: hist, mass_above: 0.0, mass_just_below: 0.0, n_pairs: n as usize }
}

// (1) The real NCVR-synthetic shape: dip MUST land in the high band (the
//     trough below the true-match mode), NOT the 0.04 left-tail sliver.
#[test]
fn dip_targets_valley_below_match_mode_on_right_skewed() {
    let hist = vec![
        (0.0000, 441008), (0.0417, 48), (0.0833, 1002), (0.1250, 5376),
        (0.1667, 6263), (0.2083, 10586), (0.2500, 16894), (0.2917, 39651),
        (0.3333, 52055), (0.3750, 40747), (0.4167, 49015), (0.4583, 72972),
        (0.5000, 66375), (0.5417, 40352), (0.5833, 19660), (0.6250, 7669),
        (0.6667, 2296), (0.7083, 536), (0.7500, 152), (0.7917, 154),
        (0.8333, 181), (0.8750, 97), (0.9167, 583), (0.9583, 1456),
    ];
    let d = diag(hist).dip().expect("should find a high-side valley");
    assert!(d >= 0.75, "expected valley below the match mode (~0.875), got {d}");
    assert!(d >= 0.10, "must not return the left-tail sliver 0.04, got {d}");
}

// (2) Clean bimodal (preserves existing behavior): valley between the two modes.
#[test]
fn dip_clean_bimodal_returns_mid_valley() {
    let d = diag(vec![(0.0, 100), (0.5, 2), (0.9, 100)]).dip();
    assert_eq!(d, Some(0.5));
}

// (3) Single mode / no prominent high-score peak: return None (no suggestion
//     beats a destructive one). Monotonic decay, no second hump.
#[test]
fn dip_single_mode_returns_none() {
    let d = diag(vec![(0.0, 500), (0.1, 200), (0.2, 80), (0.3, 30),
                      (0.4, 12), (0.5, 5), (0.6, 2), (0.7, 1)]).dip();
    assert_eq!(d, None);
}
```

- [ ] **Step 3: Run -> fail.** `cargo test -p goldenmatch-suggest-core dip_` (env per Conventions). Expected: test (1) fails (current `dip()` returns 0.0417), test (3) likely fails (current returns some tail bin), test (2) may pass. Confirm the red.

- [ ] **Step 4: Commit** `test(suggest): pin right-anchored dip valley behavior (failing)`.

---

## Task 2: Rewrite `dip()` as a right-anchored, prominence-based valley finder

**Files:** `packages/rust/extensions/suggest-core/src/diagnostics.rs` (the `dip()` method).

- [ ] **Step 1: Implement.** Replace `dip()` with the algorithm below. It is traced against all three fixtures (see the trace block); `PEAK_PROMINENCE` is the one TDD-tunable constant -- adjust ONLY if a fixture forces it, and document the change.

```rust
/// Locate the threshold valley that separates the true-match mass (a
/// high-score mode) from the non-match bulk. Right-anchored: finds the
/// RIGHTMOST prominent high-score peak (the match mode -- detectable even when
/// it carries little total mass, because it stands well above the trough to its
/// left), then walks LEFT from that peak to the adjacent local minimum and
/// returns that trough's left edge. Returns None when there is no prominent
/// high-score mode (unimodal / pure decay) -- in that case the kernel emits no
/// dip suggestion rather than collapse the threshold into the left tail.
pub fn dip(&self) -> Option<f64> {
    let counts: Vec<i64> = self.histogram.iter().map(|(_, c)| *c).collect();
    let n = counts.len();
    if n < 3 {
        return None;
    }
    const PEAK_PROMINENCE: f64 = 3.0; // match-mode peak must stand >=3x above its left trough
    let global_max_idx = counts.iter().enumerate().max_by_key(|(_, c)| **c).map(|(i, _)| i)?;

    // 1. Find the RIGHTMOST prominent local-maximum bin to the right of the
    //    global max. "Prominent" = it stands >= PEAK_PROMINENCE x above the
    //    local trough immediately to its left (walk left while non-increasing).
    let mut peak_idx: Option<usize> = None;
    for i in (global_max_idx + 1)..n {
        let is_local_max = counts[i] >= counts[i - 1] && (i + 1 == n || counts[i] >= counts[i + 1]);
        if !is_local_max {
            continue;
        }
        // trough immediately left of this peak
        let mut t = i;
        while t > 0 && counts[t - 1] <= counts[t] {
            t -= 1;
        }
        let trough = counts[t];
        if trough == 0 || (counts[i] as f64) >= PEAK_PROMINENCE * (trough as f64) {
            peak_idx = Some(i); // rightmost qualifying peak wins (loop continues)
        }
    }
    let peak = peak_idx?;

    // 2. Valley = the local minimum adjacent to (left of) that peak: walk left
    //    while the left neighbor is no greater (descending into the trough).
    let mut v = peak;
    while v > 0 && counts[v - 1] <= counts[v] {
        v -= 1;
    }
    Some(self.histogram[v].0)
}
```

**Hand-trace (the executor should confirm each before trusting the code):**
- Clean bimodal `[(0.0,100),(0.5,2),(0.9,100)]`: global_max=idx0. idx2 (100) is a local max (>= idx1=2, last bin); its left trough walk -> idx1 (2); `100 >= 3*2` -> peak=idx2. Valley walk-left from idx2: idx1 (2 <= 100) -> idx0 (100 <= 2? no) stop -> v=idx1 -> **0.5**. (idx1's count 2 is NOT a local max, so it never becomes a peak.)
- NCVR fixture: global_max=idx0 (441008). The central hump peak idx11 (72972) walks left to trough idx9 (40747); `72972 >= 3*40747`? no -> rejected (a broad hump, not a sharp peak). idx23 (1456, last bin) is a local max; left trough walk -> idx21 (97); `1456 >= 3*97`? yes -> peak=idx23. Valley walk-left from idx23: idx22 (583 <= 1456) -> idx21 (97 <= 583) -> idx20 (181 <= 97? no) stop -> v=idx21 -> **0.875**. Left-tail idx1 (0.04) is never reached.
- Monotonic decay `[(0.0,500),(0.1,200)...(0.7,1)]`: global_max=idx0; every bin right of it is strictly smaller than its left neighbor, so no bin is a local max -> `peak_idx` stays None -> returns **None**.

- [ ] **Step 2: Run the new tests -> pass.** `cargo test -p goldenmatch-suggest-core dip_`.
- [ ] **Step 3: Run the WHOLE crate -> pass.** `cargo test -p goldenmatch-suggest-core`. If `dip_detection_bimodal` or `moves_to_dip_when_threshold_off_valley` regressed, fix the implementation (preferred) or update the test ONLY with a written justification in the commit body explaining why the old expectation was wrong.
- [ ] **Step 4: Commit** `fix(suggest): right-anchored dip valley targets the match-mode boundary`.

---

## Task 3: Rebuild native + gym/oracle re-validation (run/record, with kill criterion)

**Files:** append `## Findings (dip valley-targeting, <date from git log>)` to `docs/superpowers/specs/2026-06-25-pre-threshold-scores-design.md`. No pytest.

- [ ] **Step 1: Rebuild the in-tree native kernel** so the gym uses the new Rust: `D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/build_native.py`. Confirm success (no build error; the loader uses `goldenmatch._native` first).
- [ ] **Step 2: Confirm the unit behavior end-to-end.** Re-run the behavioral test that proved the misfire:
  `... -m pytest packages/python/goldenmatch/tests/test_suggest_full_dist.py -q` (env per Conventions). Still 5 passed (the `lower_threshold`-fires assertion must hold; the proposed value is now the high-side valley, not 0.04 -- if a test asserted a specific value it doesn't, but it only asserts the KIND fires, so it should stay green).
- [ ] **Step 3: FULL_DIST=1 gym across BOTH datasets** -- `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... -m scripts.suggest_quality.cli gym --datasets synthetic,ncvr_synthetic`. Record per-perturbation `rule_fired` + raw/live recovery + the two headlines. CHECK: does `threshold_too_high` now propose a high-side valley (~0.85) and recovery climb out of the -1231% hole toward / above baseline? Does the symmetric `threshold_too_low` still RAISE correctly toward the same valley?
- [ ] **Step 4: Oracle no-harm** -- `GOLDENMATCH_SUGGEST_FULL_DIST=1 ... report --datasets synthetic,ncvr_synthetic`. Record `suggester_prec` per dataset (must hold ~1.0).
- [ ] **Step 5: Record findings** -- append a table to the spec: one row per dataset/perturbation of interest, columns `rule_fired`, `proposed_value`, `raw recovery`, `live recovery`, `suggester_prec`. Compare against the prior `## Findings (full-dist, ...)` table (which had threshold_too_high at -1231% raw / precision 0.00). State plainly whether the destructive lowering is fixed and whether recovery now climbs.
- [ ] **Step 6: State the verdict against the KILL CRITERION** (in the findings): the fix EARNS continued investment only if, across BOTH datasets, (a) `threshold_too_high` recovery materially improves vs the -1231%/-399.3% baseline AND (b) `suggester_prec` holds ~1.0 (no net-negative). If it only stops the catastrophe without a recovery win (a plausible outcome given the true-match mass is ~0.2% of candidate pairs), say so honestly -- that is still a correctness improvement (no more threshold collapse) but signals the suggestion arc is at diminishing returns and should rest. Do NOT fake a win.
- [ ] **Step 7: Commit** `docs(suggest): dip valley-targeting gym/oracle findings`.

---

## Done criteria
- `dip()` returns the high-side valley (~0.875) on the measured NCVR-synthetic shape, the mid-valley (0.5) on clean bimodal, and `None` on unimodal/no-high-mode -- pinned by Rust unit tests; the whole `suggest-core` crate is green.
- The destructive `thr:dip:fuzzy_match` 0.90 -> 0.04 collapse no longer occurs; under FULL_DIST=1 the proposal is the match-mode boundary.
- Gym/oracle findings recorded honestly: whether recovery climbs and precision holds, judged against the kill criterion (Step 6). Rules/contract/adapter/`FULL_DIST` flag UNCHANGED; default behavior (FULL_DIST=0) unchanged.
- Scope boundary: this plan does NOT flip `FULL_DIST` default-on (separate evidence-backed change) and does NOT touch the recall-risk branch.
