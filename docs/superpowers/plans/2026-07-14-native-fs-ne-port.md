# Native FS_SUPPORTS_NE Port Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port FS negative-evidence scoring into the native Rust kernels (`score_block_pairs_fs`
AND `match_fused_fs`), close fused's `level_thresholds` gap, and widen the Python gates so
NE-bearing matchkeys use the fast paths — shipped as goldenmatch-native 0.1.15.

**Spec:** `docs/superpowers/specs/2026-07-14-native-fs-ne-port-design.md` — READ IT FIRST. It pins
the FFI shape (flat optional kwargs), the `_ne_fired` semantics (both present post-transform +
non-empty + STRICT `<`), the two capability consts, the fused derive_from decline, and the
tolerance discipline.

**Architecture:** Python precomputes everything semantic (transforms via
`_field_values_for_block`/`_field_values_from_list`, `w_fired` from `__ne__` entries or
`penalty_bits`); the kernels add one additive check per NE field per pair. Old wheels never see
the new kwargs (send-only-when-present + capability-const gates).

**Tech Stack:** Rust (pyo3 abi3, rayon, arrow), Python 3.12, pytest. In-tree kernel builds via
`scripts/build_native.py`.

---

## Environment / repo mechanics (read before Task R0)

- Work in a NEW worktree `D:\show_case\gm-native-ne`, branch `feat/native-fs-ne` off
  freshly-fetched `origin/main`. **NEVER `git stash`.** (This feature has NO file overlap with
  the in-flight fan-out lever PR #1771 — it touches probabilistic.py's scoring tail,
  fused_match.py, and the Rust crate; #1771 touched splink_upgrade*/loader only.)
- Python tests via the MAIN checkout's venv + worktree PYTHONPATH (Git Bash):
  `cd /d/show_case/gm-native-ne/packages/python/goldenmatch && PYTHONPATH="D:/show_case/gm-native-ne/packages/python/goldenmatch" POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <tests> -q`
  NOTE: do NOT set `GOLDENMATCH_NATIVE=0` for the parity/native tests in this plan — they need
  the kernel. Tests that don't need native are harmless either way.
- **Rust toolchain on this box** (memory `reference_rustup_proxy_exfat_direct_binary` + root
  CLAUDE.md): call cargo via the direct toolchain path with an explicit CARGO_HOME —
  `export PATH="/d/.rustup/toolchains/1.94.0/bin:$PATH" CARGO_HOME="D:/.cargo"` (cargo defaults
  CARGO_HOME to the drive root on D: otherwise). Verify with `cargo --version` before building.
- **In-tree kernel build — WINDOWS GOTCHA (verified against the script):**
  `scripts/build_native.py` only looks for `target/release/lib_native.{so,dylib}` and copies to
  `_native.abi3.so` — on Windows the cdylib builds as `target\release\_native.dll` and CPython
  imports `_native.pyd`, so the script exits 1 AFTER a successful cargo build. Task R0 patches
  the script (drive-by fix that lands in the PR): add a win32 branch to the artifact lookup —
  `target/release/_native.dll` → copy to `packages/python/goldenmatch/goldenmatch/_native.pyd`.
  (The main checkout's `_native.pyd` proves prior sessions did this copy manually.) Build
  command from the worktree root:
  `PATH="/d/.rustup/toolchains/1.94.0/bin:$PATH" CARGO_HOME="D:/.cargo" D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/build_native.py`
  The loader prefers `goldenmatch._native` (in-tree) over the wheel, and PYTHONPATH points at
  the worktree, so tests see the fresh kernel. Rebuild after EVERY Rust change.
  Verify each build explicitly (memory `feedback_verify_rust_builds_explicitly`): check exit
  code AND probe, e.g.
  `python -c "from goldenmatch.core._native_loader import native_module; print(native_module().__file__)"`
  (and from R1 on, print the new const) — piped tails mask failures.
- **Lint:** ruff on Python; `cargo clippy --all-targets -- -D warnings` from the crate dir
  (CI runs `-D warnings`; local default doesn't — memory `feedback_ci_clippy_dwarnings_native_ext`);
  `rustfmt <touched .rs files by name>` (NOT `cargo fmt` — repo-wide fmt is poisoned, memory
  `reference_rust_fmt_box_and_nonrequired_gate`).
- `docs/superpowers/` is gitignored: `git add -f` the spec + plan.
- Push/PR auth dance: `unset GH_TOKEN`; push via
  `git push "https://x-access-token:$(gh auth token --user benzsevern)@github.com/benseverndev-oss/goldenmatch.git" <branch>`;
  PR via `GH_TOKEN=$(gh auth token --user benzsevern) gh pr create ...`; arm
  `gh pr merge --auto <N>` and STOP (never poll CI).
- Commit trailers on every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01R8MSaGwsjdxzf6Z7Bt3BXs`

**Key existing code (all paths worktree-relative):**
- `packages/rust/extensions/native/src/score.rs` — `score_block_pairs_fs` (~273; signature ~268,
  level_thresholds validation ~293-312, hoisted `field_thresholds` ~316, inner loop ~330-360),
  `fs_level_from_sim` (~208), `fs_normalize`, `score_one` re-import (~15), `StrCol` (~380).
- `packages/rust/extensions/native/src/fused.rs` — `match_fused_fs` (~219; signature ~214-233,
  `read_ids_and_fields`/`read_key_cols`, `fused_gather` span loop ~253-285 with the
  `fs_level_from_sim(..., None)` call ~269 and its "port the kwarg when the fused path goes
  live" comment).
- `packages/rust/extensions/native/src/lib.rs` — `m.add("FS_SUPPORTS_LEVEL_THRESHOLDS", true)`
  (line 30), function registrations.
- `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` — `_ne_fired` (466),
  `_fs_native_eligible` (2264: the `if mk.negative_evidence: return False` at 2287),
  `score_probabilistic_native` (2311: NE comment ~2338-2343, `fs_weight_range` call 2344,
  `_field_values_for_block` usage 2352, kwarg-send discipline 2363-2377),
  `_NATIVE_FS_SCORER_IDS`, `fs_weight_range` (1468), `_field_values_for_block` (1710).
- `packages/python/goldenmatch/goldenmatch/core/fused_match.py` — `match_fused_fs_ready` (255;
  NE decline 283, level_thresholds condition 290), `run_match_fused_fs_arrow` (hand-rolled
  min/max 341-342, `src_cols` 332, score_arrs prep loop ~355-360, kernel call ~362).
- `packages/python/goldenmatch/tests/test_native_parity.py` — module-level
  `pytest.mark.skipif` when the kernel is absent (line 16); FS parity test conventions.
- `packages/python/goldenmatch/tests/test_fs_ne_e2e.py` — the homonym fixture (reuse its
  builders for the native success bar).
- Version files (bump ALL THREE in R5): `packages/rust/extensions/native/Cargo.toml`,
  `packages/rust/extensions/native/pyproject.toml`,
  `packages/rust/extensions/native/python/goldenmatch_native/__init__.py`.
- `scripts/check_native_symbols.py` — `_MADD` regex already parses `m.add` consts.

## File structure

- Modify: `packages/rust/extensions/native/src/score.rs` (NE kwargs + validation + loop)
- Modify: `packages/rust/extensions/native/src/fused.rs` (NE + level_thresholds kwargs)
- Modify: `packages/rust/extensions/native/src/lib.rs` (two consts)
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (gate + caller)
- Modify: `packages/python/goldenmatch/goldenmatch/core/fused_match.py` (gate + caller)
- Create: `packages/python/goldenmatch/tests/test_native_fs_ne.py` (kernel-direct + gate +
  parity + native success bar; module-level skipif-no-kernel like test_native_parity.py)
- Modify: `packages/python/goldenmatch/tests/test_fused_match.py` (fused NE/level_thresholds
  gate + parity additions — find the existing fused test file first: `ls tests/ | grep fused`;
  if the fused tests live elsewhere, extend THAT file and note it)
- Modify (R5): the three version files.

---

### Task R0: Worktree + baseline build + commit spec/plan

- [ ] **Step 1:** From `D:\show_case\goldenmatch`: `git fetch origin main -q && git worktree add /d/show_case/gm-native-ne -b feat/native-fs-ne origin/main`
- [ ] **Step 2:** Copy spec + this plan into the worktree under `docs/superpowers/{specs,plans}/`;
  `git add -f` both; commit `docs: spec + plan for the native FS_SUPPORTS_NE port` (+ trailers).
- [ ] **Step 3:** Patch `scripts/build_native.py` per the environment-block gotcha (win32
  artifact `target/release/_native.dll` → dest `goldenmatch/_native.pyd`; keep the .so/.dylib
  branches for Linux/mac), then run the baseline in-tree build → exit 0, artifact at
  `packages/python/goldenmatch/goldenmatch/_native.pyd` in the WORKTREE, loader probe shows the
  worktree path. Commit the script patch separately:
  `fix(scripts): build_native.py produces the in-tree kernel on Windows` (+ trailers).
- [ ] **Step 4:** Baseline sanity: run `pytest tests/test_native_parity.py -q` (worktree
  PYTHONPATH, NO GOLDENMATCH_NATIVE=0) → all pass/skip-free (the kernel is present). Also
  `pytest tests/test_fs_ne_e2e.py tests/test_fs_ne_scoring.py tests/test_fs_ne_guards.py -q` →
  green. If ANY red, STOP and investigate before changing code.

### Task R1: Rust — `score_block_pairs_fs` NE kwargs + `FS_SUPPORTS_NE`

**Files:** Modify `packages/rust/extensions/native/src/score.rs`, `src/lib.rs`.
**Test:** Create `packages/python/goldenmatch/tests/test_native_fs_ne.py` (kernel-direct tests).

- [ ] **Step 1: Failing tests** (new file; module-level skipif copied from
  test_native_parity.py:16). Call the kernel DIRECTLY via
  `goldenmatch.core._native_loader.native_module()`:
  - `test_kernel_exports_fs_supports_ne`: `getattr(mod, "FS_SUPPORTS_NE", False) is True`.
  - `test_kernel_ne_fires_strictly_below_threshold`: one 3-row block, one regular exact field
    (identical values → full agreement weight), one NE field with `ne_scorer_ids=[3]` (exact),
    `ne_thresholds=[0.5]`, `ne_weights=[-4.0]`: rows with DIFFERENT ne values (sim 0.0 < 0.5 →
    fires, total drops by 4.0) vs IDENTICAL ne values (sim 1.0 → no fire). Assert both pairs'
    normalized scores against hand-computed expectations (pick min_weight/weight_range including
    the NE contribution the way `fs_weight_range` would: min includes -4.0). Include a pair where
    sim == threshold is NOT constructible with exact (sim ∈ {0,1}) — strictness is pinned in the
    parity task with token_sort instead.
  - `test_kernel_ne_null_and_empty_never_fire`: NE values `None` on one side, and `""` on one
    side → no contribution (score equals the no-NE-field score).
  - `test_kernel_ne_validation_errors`: mismatched lengths (ne_values vs ne_scorer_ids), partial
    kwarg group (ne_values without ne_weights), ne_values row count != row_ids length → each
    raises `ValueError` with a message naming `score_block_pairs_fs`.
- [ ] **Step 2:** Run → FAIL (`FS_SUPPORTS_NE` missing / unexpected keyword).
- [ ] **Step 3: Implement** in `score.rs`:
  - Extend the `#[pyo3(signature = (...))]` with
    `ne_values=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None` after
    `level_thresholds=None`; parameters:
    ```rust
    ne_values: Option<Vec<Vec<Option<String>>>>,
    ne_scorer_ids: Option<Vec<u8>>,
    ne_thresholds: Option<Vec<f64>>,
    ne_weights: Option<Vec<f64>>,
    ```
  - Upfront validation (before the spans loop, style-matching the level_thresholds block):
    all four `Some`-or-`None` together (else PyValueError "ne_* kwargs must be passed
    together"); equal lengths across the four; every `ne_values[k].len() == row_ids.len()`.
    Bind a hoisted `ne: Vec<(&[Option<String>], u8, f64, f64)>` (or parallel slices) for the
    hot loop — mirror the `field_thresholds` hoisting comment.
  - Inner loop, after the regular-field sum, before `fs_normalize` (spec-pinned snippet):
    ```rust
    for k in 0..n_ne {
        if let (Some(a), Some(b)) = (&ne_vals[k][i], &ne_vals[k][j]) {
            if !a.is_empty() && !b.is_empty()
                && score_one(ne_scorer_ids_v[k], a, b) < ne_thresholds_v[k] {
                total_weight += ne_weights_v[k];
            }
        }
    }
    ```
    with a doc-comment citing `_ne_fired` (core/probabilistic.py:466) and the empty-string =
    inconclusive rule. Update the function's doc comment (NE paragraph + old-wheel note).
  - `lib.rs`: `m.add("FS_SUPPORTS_NE", true)?;` next to `FS_SUPPORTS_LEVEL_THRESHOLDS`.
- [ ] **Step 4:** Rebuild in-tree (verify explicitly); run the new tests → PASS; run
  `pytest tests/test_native_parity.py -q` → still green (no regression on the extended
  signature).
- [ ] **Step 5:** `cargo clippy --all-targets -- -D warnings` (crate dir) → clean;
  `rustfmt src/score.rs src/lib.rs`; ruff the test file. Commit
  `feat(native): NE kwargs on score_block_pairs_fs + FS_SUPPORTS_NE` (+ trailers).

### Task R2: Python — gate widening + native caller + parity + native success bar

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`.
**Test:** Extend `tests/test_native_fs_ne.py`.

- [ ] **Step 1: Failing tests:**
  - `test_fs_native_eligible_ne_supported`: NE-bearing mk (exact-scorer NE) → True with the
    real kernel; `test_fs_native_eligible_ne_old_wheel_declines`: monkeypatch
    `native_module` to a stub lacking `FS_SUPPORTS_NE` (but having `score_block_pairs_fs` +
    `FS_SUPPORTS_LEVEL_THRESHOLDS`) → False; `test_fs_native_eligible_ensemble_ne_declines`:
    NE field with scorer `ensemble` → False.
  - `test_native_kwargs_not_sent_without_ne`: spy-wrap `score_block_pairs_fs` (monkeypatch the
    module object) on a no-NE matchkey → call kwargs contain no `ne_values`.
  - `test_native_numpy_parity_ne`: same blocks/mk/em scored by `score_probabilistic_native` and
    `score_probabilistic_vectorized` → identical pair sets + scores (round 4) across: EM-learned
    NE (`__ne__` entries), `penalty_bits` NE, a null-valued row, an empty-string-after-transform
    row (e.g. value `"-"` with `digits_only`), NE combined with `level_thresholds` on a regular
    field, and TWO NE fields. Fixture similarities away from thresholds (tolerance discipline;
    token_sort strictness case uses a sim well below and well above, not at, the threshold).
  - `test_native_success_bar_homonym`: build the homonym fixture via the builders in
    `tests/test_fs_ne_e2e.py` (import its helpers), assert `_fs_native_eligible(mk)` is True
    (so the test cannot silently run numpy), run the same dedupe as the E2E, assert the same
    bar: traps separate, true dups merge, AND clustering identical to a
    `GOLDENMATCH_FS_NATIVE=0` run of the same fixture (byte-identical membership).
- [ ] **Step 2:** Run → FAIL (gate still declines NE).
- [ ] **Step 3: Implement** in probabilistic.py:
  - `_fs_native_eligible`: replace the unconditional NE decline with:
    ```python
    ne_fields = mk.negative_evidence or []
    for ne in ne_fields:
        if ne.scorer not in _NATIVE_FS_SCORER_IDS:
            return False
    ```
    and inside the existing `try` block after the level_thresholds check:
    `if ne_fields and not getattr(mod, "FS_SUPPORTS_NE", False): return False`.
    Update the docstring (delete the "NE never crosses the FFI" paragraph; document the new
    conditions + old-wheel decline).
  - `score_probabilistic_native`: after the existing kwarg prep, when `mk.negative_evidence`:
    ```python
    ne_fields = mk.negative_evidence or []
    if ne_fields:
        ne_values = [_field_values_for_block(block_df, ne, n) for ne in ne_fields]
        ne_scorer_ids = [_NATIVE_FS_SCORER_IDS[ne.scorer] for ne in ne_fields]
        ne_thresholds = [float(ne.threshold) for ne in ne_fields]
        ne_weights = [
            -abs(float(ne.penalty_bits)) if ne.penalty_bits is not None
            else float(em_result.match_weights[f"__ne__{ne.field}"][0])
            for ne in ne_fields
        ]
    ```
    and thread the four kwargs into the kernel call ONLY when `ne_fields` (extend the existing
    two-branch call or build a kwargs dict — keep the "old wheel must NEVER see it" comment
    pattern; the level_thresholds branch shows the shape). Update the stale NE comment above
    `fs_weight_range` (it now sees NE fields for real on this path).
- [ ] **Step 4:** Run new tests + `pytest tests/test_native_parity.py tests/test_fs_ne_scoring.py tests/test_fs_ne_guards.py tests/test_fs_ne_e2e.py -q` → PASS.
- [ ] **Step 5:** Ruff; commit `feat(goldenmatch): native FS kernel scores negative evidence (FS_SUPPORTS_NE)` (+ trailers).

### Task R3: Rust — `match_fused_fs` NE + level_thresholds

**Files:** Modify `packages/rust/extensions/native/src/fused.rs`, `src/lib.rs`.
**Test:** Extend `tests/test_native_fs_ne.py` (kernel-direct fused tests).

- [ ] **Step 1: Failing tests** (kernel-direct, arrow inputs via `pyarrow`):
  - `test_fused_exports_level_thresholds_const`:
    `getattr(mod, "FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", False) is True`.
  - `test_fused_fs_ne_fires`: tiny single-key-block dataset where an NE disagreement flips a
    pair below threshold → cluster membership differs from the no-NE call on the same data.
  - `test_fused_fs_level_thresholds_bands`: a field with custom `level_thresholds` +
    3-entry weights produces the same clusters as `score_block_pairs_fs`-based classic scoring
    on the same data (or hand-computed membership on a 4-row fixture).
  - `test_fused_fs_ne_validation_errors`: partial NE kwarg group / length mismatches /
    level_thresholds length != field count / weights-vs-thresholds arity → ValueError naming
    `match_fused_fs`.
- [ ] **Step 2:** Run → FAIL (unexpected keyword).
- [ ] **Step 3: Implement** in fused.rs:
  - Signature grows `level_thresholds=None, ne_fields=None, ne_scorer_ids=None, ne_thresholds=None, ne_weights=None`:
    `level_thresholds: Option<Vec<Option<Vec<f64>>>>`,
    `ne_fields: Option<Vec<PyArrowType<ArrayData>>>`, the other three as in R1.
  - Validation: reuse score.rs's level_thresholds checks verbatim (length == n_fields;
    `match_weights[f].len() == ts.len() + 1` per Some field). NE group: all-or-none, equal
    lengths; each `ne_fields[k]` read via `StrCol::from_data` and length == n_rows.
  - IMPORTANT gather note: `match_fused_fs` sorts rows by block key (`fused_gather` produces
    `rid_sorted` + reordered `vals`). NE columns must be gathered THE SAME WAY — read how
    `fused_gather` reorders `score_cols` and apply the identical reordering to the NE columns —
    extend `fused_gather` (or pass the NE `StrCol`s through the same gather alongside
    `score_cols`); it returns `(rid_sorted, vals, spans)` with NO separate permutation vector,
    so do NOT index NE columns by the unsorted row index. This is the one real trap in this task — verify with a fixture whose
    block-key sort differs from input order (the R3 tests above must include out-of-order keys).
  - Per-pair loop: `fs_level_from_sim(sim, levels[f], partial_thresholds[f], field_thresholds[f])`
    (hoist like score.rs; delete the "port the kwarg when the fused path goes live" comment),
    then the NE additive check (same both-present + non-empty + strict-`<` snippet over the
    gathered NE values).
  - `lib.rs`: `m.add("FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS", true)?;`.
- [ ] **Step 4:** Rebuild; new tests PASS; `pytest tests/test_native_parity.py -q` green.
- [ ] **Step 5:** clippy `-D warnings`; `rustfmt src/fused.rs src/lib.rs`; ruff; commit
  `feat(native): match_fused_fs scores NE + custom level_thresholds` (+ trailers).

### Task R4: Python fused — gate + caller + parity

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/fused_match.py`.
**Test:** Extend the existing fused-FS test file (locate via `grep -rln "match_fused_fs_ready" tests/`) + `tests/test_native_fs_ne.py`.

- [ ] **Step 1: Failing tests:**
  - Gate matrix: NE + `FS_SUPPORTS_NE` present → ready; NE + const absent (monkeypatched stub
    module) → declined; `derive_from`-bearing NE → declined EVEN with the const (and a
    companion assert that `_fs_native_eligible` does NOT decline the same mk — the asymmetry
    from the spec); ensemble-scorer NE → declined; `level_thresholds` +
    `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS` present → ready, absent → declined; per-feature
    independence (NE-supporting stub without the fused-level const: NE config ready,
    level_thresholds config declined).
  - `test_fused_weight_range_uses_fs_weight_range`: monkeypatch `fs_weight_range` where
    fused_match looks it up to a sentinel raise → `run_match_fused_fs_arrow` on a ready config
    hits it (proves the hand-rolled sums are gone).
  - `test_fused_fs_ne_parity`: `run_match_fused_fs_arrow` vs the classic pipeline
    (`probabilistic_block_scorer` + clustering) on a covered-boundary NE config → identical
    cluster membership. Include a `level_thresholds` + NE combined case.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement:**
  - `match_fused_fs_ready`: replace the two unconditional declines with per-feature checks —
    NE: every `ne.scorer in _NATIVE_FS_SCORER_IDS`, no `ne.derive_from`, and (module probe)
    `FS_SUPPORTS_NE`; level_thresholds: `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS`. The gate
    currently does NOT import the module — add a guarded capability probe mirroring
    `_fs_native_eligible`'s try/except (module absent → the existing `_match_fused_fs_symbol()`
    None-check upstream already declines; keep the gate pure on config when both features are
    absent so no-native environments don't pay an import). Rewrite the docstring to the new
    covered boundary (incl. the derive_from rationale from the spec).
  - `run_match_fused_fs_arrow`: `min_w, max_w` → `fs_weight_range(em_result, mk)` (import it);
    extend `src_cols` with NE field names (`dict.fromkeys` dedup keeps order); build
    `ne_arrs`/`ne_scorer_ids`/`ne_thresholds`/`ne_weights` mirroring R2's resolution (values via
    `frame.utf8_values(ne.field) if ne.field in frame.columns else None` +
    `_field_values_from_list(raw, ne, n)` → `pa.array(..., type=pa.large_string())` — absent
    column degrades to all-null per spec); build `level_thresholds` list; pass each kwarg group
    ONLY when non-trivial (NE list non-empty; any level_thresholds not None) — old wheels never
    see them.
- [ ] **Step 4:** Run new tests + the whole fused test file + `tests/test_native_fs_ne.py` →
  PASS.
- [ ] **Step 5:** Ruff; commit `feat(goldenmatch): fused FS path covers NE + level_thresholds` (+ trailers).

### Task R5: Version bump ×3 + symbol gate + full sweep + PR

**Files:** the three version files; no code changes.

- [ ] **Step 1:** Bump 0.1.14 → 0.1.15 in `Cargo.toml` (`[package] version`),
  `pyproject.toml` (`[project] version` — the one maturin reads), and
  `python/goldenmatch_native/__init__.py` (fallback `__version__` string). Grep for any other
  `0.1.14` in the crate.
- [ ] **Step 2:** `python scripts/check_native_symbols.py` (main venv, worktree cwd) → the two
  new consts + kwargs reconcile, exit 0.
- [ ] **Step 3:** Full targeted sweep (worktree PYTHONPATH, native ON):
  `pytest tests/test_native_fs_ne.py tests/test_native_parity.py tests/test_fs_ne_schema.py tests/test_fs_ne_em.py tests/test_fs_ne_scoring.py tests/test_fs_ne_guards.py tests/test_fs_ne_e2e.py <fused test file> tests/test_probabilistic.py -q`
  → all pass. Then the pure-path sweep with `GOLDENMATCH_FS_NATIVE=0` → all pass (numpy/scalar
  FS paths byte-unchanged). Design kernel-requiring tests' skipifs on module PRESENCE (the
  test_native_parity.py:16 pattern), not on env vars — the in-tree kernel exists on this box,
  so env-based skips would silently skip nothing locally and everything on a wheel-less CI lane.
- [ ] **Step 4:** Final clippy `-D warnings` + rustfmt check on touched files; ruff on touched
  Python.
- [ ] **Step 5:** Push (auth dance), PR titled
  `feat(goldenmatch): native FS negative-evidence + fused N-level (goldenmatch-native 0.1.15)`,
  body: spec summary, parity/success-bar results, the wheel-skew note (0.1.15 tag +
  `publish-goldenmatch-native.yml` must ship in this rollout — capability-gated fast paths are
  invisible to `pip install goldenmatch[native]` envs until the wheel publishes; tag is Ben's
  call or delegated post-merge). Arm `gh pr merge --auto` and STOP.
- [ ] **Step 6:** After merge: memory updates (`project_fs_negative_evidence` open-items,
  `project_goldenmatch_native_package`), work-tracker entry, and surface the tag decision to
  Ben.
