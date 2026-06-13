# Scale-Invariant Blocking Selection (#876) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make auto-config's blocking selection scale-invariant — candidate-pair count linear in N — by adding a total-candidate-pairs budget, a surrogate-key exclusion, and a bounded-compound / capped-multi-pass fallback with a budget-driven selector, so the same config is correct from 1K to 200M.

**Architecture:** All changes are in `core/autoconfig.py::build_blocking` (a large, multi-path function — #408/#410/#491/#715 history) plus a new public `n_rows_full` kwarg on `auto_configure_df`, plus the QIS harness `build_frozen_config`. The fix is **behavior-test-driven**: each task pins a desired `build_blocking` behavior with a failing test, then implements the minimal change at the identified insertion point. The existing `max_safe_block` (per-block OOM guard) is left untouched; the NEW gate is on total projected pairs.

**Tech Stack:** Python 3.12, polars, goldenmatch auto-config; pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-scale-invariant-blocking-876-design.md` (read it first).

---

## Orientation (do this before Task 2)

Read `build_blocking` end-to-end (`autoconfig.py` ~1582–1960) and the spec. Key landmarks:
- `effective_n_full = n_rows_full or df.height` (~1630) — the scale the projection uses.
- `max_safe_block = max(1000, min(10000, df.height // 200))` (~1739) — per-block OOM guard, **unchanged**.
- `project_max_block_size` import (~1749), `_projected_block(fields)` (~1751), `_pass_is_bounded` (~1758), `_gate_passes` (~1761).
- **Exact-cols path** (~1799–1817): sorts candidates by `n_unique` desc, keeps those with `_max_block_size <= max_safe_block`, returns the highest-`n_unique` one as a sole key. This is where `id` (unique surrogate) or a bounded key gets returned without any total-pairs check.
- Compound path (~1857–1893): `_build_compound_blocking` then `_gate_passes`.
- `_build_compound_blocking` (~1060).

## Constants

`K` = candidate-pairs-per-row budget (constant; the scale-invariance knob).
Default **50** (projected avg block ≤ ~101). Env override
`GOLDENMATCH_BLOCKING_PAIRS_PER_ROW`. Rationale (verify in Task 7): QIS
`zip`-alone at 200M → block ~2000 → ~1000 pairs/row ≫ 50 → rejected;
`zip+first-syllable` → block ~83 → ~41 pairs/row < 50 → kept; benchmark name+DOB
blocks (≤ ~10 rows) → ≪ 50 → kept.

## Test conventions

Local single-file pytest only (no full suite — OOMs the box). Pattern:
```bash
cd D:/show_case/gm-510/packages/python/goldenmatch && \
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 \
PYTHONPATH="D:/show_case/gm-510/packages/python/goldenmatch" \
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_autoconfig.py -q -p no:cacheprovider
```
The auto-config matchkey/blocking unit tests are FAST (no dedupe). The #510 ladder
re-run (Task 7) uses the GCP box. Commits: focused, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: `_project_pairs` + scale-safety helpers (pure, TDD)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig.py`

- [ ] **Step 1: Write the failing test**

Add to `test_autoconfig.py` (a new `TestScaleInvariantBlocking` class):
```python
class TestScaleInvariantBlocking:
    def test_project_pairs_linear_for_constant_block(self):
        from goldenmatch.core.autoconfig import _project_pairs_per_row
        # block size 5 (constant) -> ~2 pairs/row regardless of scale
        assert _project_pairs_per_row(proj_block=5) == 2  # (5-1)//2
        assert _project_pairs_per_row(proj_block=101) == 50

    def test_blocking_pairs_budget_default(self):
        from goldenmatch.core.autoconfig import _blocking_pairs_per_row_budget
        assert _blocking_pairs_per_row_budget() == 50  # default K
```

- [ ] **Step 2: Run — expect failure (ImportError).**

- [ ] **Step 3: Implement the helpers** near `_projected_block` (~1751, but at module
scope so they're importable):
```python
def _blocking_pairs_per_row_budget() -> int:
    """K: max candidate pairs per row a blocking option may project at full N.
    Constant (does NOT scale with N) -> keeps the total pair count linear. The
    scale-invariance knob; separate from max_safe_block (per-block OOM)."""
    import os
    try:
        return max(1, int(os.environ.get("GOLDENMATCH_BLOCKING_PAIRS_PER_ROW", "50")))
    except ValueError:
        return 50


def _project_pairs_per_row(proj_block: int) -> int:
    """Pairs/row a (near-)uniform key contributes: a block of B rows makes
    C(B,2) pairs over B rows -> (B-1)/2 per row. Uses the projected MAX block
    (conservative on skew)."""
    return max(0, (int(proj_block) - 1) // 2)
```

- [ ] **Step 4: Run — expect pass. Step 5: Commit.**

---

## Task 2: Surrogate-key exclusion (TDD)

Drop blocking candidates whose `n_unique` ≈ `n_rows` (block size ~1, zero pairs) so the exact-cols path can never return `id`.

**Files:** `autoconfig.py` (exact-cols path ~1799–1811), `test_autoconfig.py`

- [ ] **Step 1: Failing test**
```python
    def test_unique_surrogate_never_blocking_key(self):
        from goldenmatch.core.autoconfig import build_blocking, ColumnProfile
        import polars as pl
        n = 500
        df = pl.DataFrame({
            "id": [f"r{i}" for i in range(n)],                 # unique surrogate
            "first_name": [f"n{i//5}" for i in range(n)],      # 5-row clusters
            "last_name": [f"s{i//5}" for i in range(n)],
            "zip": [f"{(i//5)%50:05d}" for i in range(n)],     # 50 distinct
        })
        profiles = [
            ColumnProfile("id", "Utf8", "identifier", 0.9, cardinality_ratio=1.0),
            ColumnProfile("first_name", "Utf8", "name", 0.9, cardinality_ratio=0.2),
            ColumnProfile("last_name", "Utf8", "name", 0.9, cardinality_ratio=0.2),
            ColumnProfile("zip", "Utf8", "zip", 0.9, cardinality_ratio=0.1),
        ]
        b = build_blocking(profiles, df, n_rows_full=100_000_000)
        keys = [tuple(k.fields) for k in (b.keys or [])]
        assert ("id",) not in keys, f"surrogate id chosen as blocking key: {keys}"
```

- [ ] **Step 2: Run — expect FAIL** (id currently returned at large n_rows_full).

- [ ] **Step 3: Implement.** In the exact-cols candidate filter (~1802–1805), exclude
near-unique columns BEFORE picking `best`. A column is a surrogate if its
projected full-N cardinality ≈ full_n (projected block ≤ 1) OR
`cardinality_ratio >= 1.0` (mirrors the exact-matchkey guard at ~813):
```python
        exact_cols_sorted = sorted(exact_cols, key=lambda p: df[p.name].n_unique(), reverse=True)
        # #876: a unique-per-row column blocks into singletons -> 0 candidate
        # pairs (useless). Exclude surrogates so the fallback can't degenerate
        # to `id`. (cardinality_ratio>=1.0 mirrors the exact-matchkey surrogate
        # guard; projected block<=1 catches it at scale too.)
        exact_cols_sorted = [
            p for p in exact_cols_sorted
            if (p.cardinality_ratio or 0.0) < 1.0 and _projected_block([p.name]) >= 2
        ]
        candidates = exact_cols_sorted[:5]
```

- [ ] **Step 4: Run — expect PASS. Step 5: Commit.**

**Sequencing note:** after Task 2, the surrogate filter excludes `id`, but the
downstream `safe_exact` check (line ~1805) still uses the *unscaled*
`_max_block_size` — so `zip` (sample block ~10 ≤ `max_safe_block` 1000) is still
accepted and returned as the sole key. That is EXPECTED: Task 2's test only
asserts `id` is never chosen (it now returns `zip`, not `id`). Task 3 then
replaces that check with the scale-aware `_scale_safe` and catches `zip`. Don't
"fix" `safe_exact` in Task 2.

---

## Task 3: Total-pairs gate on the exact-cols path (TDD)

A bounded-cardinality sole key (zip) whose projected pairs/row exceeds K must be rejected even when its per-block size is "safe".

**Files:** `autoconfig.py` (~1805, the `safe_exact` filter), `test_autoconfig.py`

- [ ] **Step 1: Failing test**
```python
    def test_bounded_cardinality_key_not_sole_at_scale(self):
        from goldenmatch.core.autoconfig import build_blocking, ColumnProfile
        import polars as pl
        n = 1000
        # zip wraps to 100 distinct -> at 100M, block ~ 100M/100 huge -> >K pairs/row
        df = pl.DataFrame({
            "first_name": [f"fn{i//5:06d}" for i in range(n)],
            "last_name": [f"ln{i//5:06d}" for i in range(n)],
            "zip": [f"{(i//5)%100:05d}" for i in range(n)],
        })
        profiles = [
            ColumnProfile("first_name", "Utf8", "name", 0.9, cardinality_ratio=0.2),
            ColumnProfile("last_name", "Utf8", "name", 0.9, cardinality_ratio=0.2),
            ColumnProfile("zip", "Utf8", "zip", 0.9, cardinality_ratio=0.1),
        ]
        b = build_blocking(profiles, df, n_rows_full=100_000_000)
        keys = [tuple(k.fields) for k in (b.keys or [])]
        # zip must NOT be the SOLE single-field blocking key at 100M, and the
        # config must not be degenerate-empty (something must block).
        assert keys, f"degenerate empty blocking at 100M: {b}"
        assert not (len(keys) == 1 and keys[0] == ("zip",)), f"sole zip at 100M: {keys}"
```
(Note: the single broken/ambiguous assertion form `assert ["zip"] != ... if keys
else True` was intentionally NOT used — the two clear asserts above are the real
guards.)

- [ ] **Step 2: Run — expect FAIL** (sole zip returned).

- [ ] **Step 3: Implement.** Change the exact-cols `safe_exact` filter (~1805) to ALSO
require the total-pairs budget, and add the same to `_pass_is_bounded` (~1758)
so passes are gated identically:
```python
        K = _blocking_pairs_per_row_budget()
        def _scale_safe(fields: list[str]) -> bool:
            pb = _projected_block(fields)
            return pb <= max_safe_block and _project_pairs_per_row(pb) <= K
        safe_exact = [p for p in candidates if _scale_safe([p.name])]
```
And in `_pass_is_bounded` (~1758):
```python
    def _pass_is_bounded(key: BlockingKeyConfig) -> bool:
        pb = _projected_block(key.fields)
        return pb <= max_safe_block and _project_pairs_per_row(pb) <= _blocking_pairs_per_row_budget()
```

- [ ] **Step 4: Run — expect PASS** (zip rejected → falls through to the compound/name
path → returns a compound or name key, NOT sole-zip). **Step 5: Commit.**

---

## Task 4: Bounded compound is reached + selected (TDD)

When no single key is scale-safe, a `zip + name-token` compound must be built and chosen (it already exists; ensure the gated path reaches it and it survives the total-pairs gate).

**Files:** `autoconfig.py` (compound path ~1857–1893; `_build_compound_blocking` ~1060), `test_autoconfig.py`

- [ ] **Step 1: Failing test** — extend the Task 3 fixture: assert the returned
blocking is a COMPOUND (a 2-field key, or a multi_pass with a compound primary),
not sole-zip and not name-only:
```python
    def test_scale_unsafe_single_keys_yield_bounded_compound(self):
        # same fixture as test_bounded_cardinality_key_not_sole_at_scale
        ... (build df/profiles) ...
        b = build_blocking(profiles, df, n_rows_full=100_000_000)
        all_keys = [k.fields for k in (b.keys or [])] + [p.fields for p in (b.passes or [])]
        assert any(len(fields) >= 2 for fields in all_keys), \
            f"expected a bounded compound (>=2 fields) at scale, got {all_keys}"
        # and the compound's projected pairs/row is within budget
        from goldenmatch.core.autoconfig import _projected_block, _project_pairs_per_row, _blocking_pairs_per_row_budget
        # (recompute via the same df in-test or assert b.max_total_comparisons set)
```

- [ ] **Step 2: Run — expect FAIL** if the compound path isn't reached / survives.

- [ ] **Step 3: Implement.** Two sub-changes:
  (a) The compound gate at ~1878 uses `_gate_passes`, which now (Task 3) enforces
      the total-pairs budget — so a compound that's still too coarse is dropped.
      Verify `_build_compound_blocking` produces a `zip + <name-token>` candidate;
      if its refinement set lacks a name-prefix/syllable token, add one (a
      first-K-chars transform on a name column) so a bounded compound exists.
  (b) When the exact path falls through (Task 3), control reaches the
      `_all_single_oversized` compound branch (~1857). Ensure that branch triggers
      when the exact path rejected its keys on the **pairs budget** (not only when
      single name cols are oversized) — broaden the `_all_single_oversized`
      condition to "no single key (exact or name) is scale-safe".
  Set `max_total_comparisons = K * effective_n_full` on the emitted BlockingConfig
  so the runtime also enforces the budget.

- [ ] **Step 4: Run — expect PASS. Step 5: Commit.**

---

## Task 5: Capped multi-pass union + budget selector (TDD)

**Files:** `autoconfig.py`, `test_autoconfig.py`

- [ ] **Step 1: Failing test** — concrete fixture: a shape where the compound
builder cannot refine the coarse key (no name column to AND with the bounded
`zip`), but TWO independent bounded passes exist. Build a df with `zip` (bounded,
~100 distinct), a `soundex_name` column (bounded, ~few-K distinct at scale, so a
bounded pass), and NO high-cardinality name column for the compound to use:
```python
    def test_multipass_union_when_no_single_or_compound_covers(self):
        from goldenmatch.core.autoconfig import build_blocking, ColumnProfile
        import polars as pl
        n = 1000
        df = pl.DataFrame({
            "zip": [f"{(i//5)%100:05d}" for i in range(n)],          # bounded ~100
            "phon": [f"S{(i//5)%300:03d}" for i in range(n)],        # bounded ~300 (soundex-like)
        })
        profiles = [
            ColumnProfile("zip", "Utf8", "zip", 0.9, cardinality_ratio=0.1),
            ColumnProfile("phon", "Utf8", "string", 0.9, cardinality_ratio=0.3),
        ]
        b = build_blocking(profiles, df, n_rows_full=100_000_000)
        # Neither single key nor a compound (no refining name col) is scale-safe
        # alone -> expect a capped multi-pass union of the two bounded passes,
        # whose summed projected pairs stay within K*n_rows_full.
        assert b.strategy == "multi_pass"
        assert len(b.passes or []) >= 2
        assert b.max_total_comparisons is not None and b.max_total_comparisons <= 50 * 100_000_000
```
(If the selector instead legitimately emits the degenerate/refuse config for this
shape, adjust the fixture so at least one bounded multi-pass union fits the
budget — the point is to exercise the multi-pass branch, not the refuse branch.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** the selector: assemble candidate options (scale-safe
single keys, the bounded compound, a capped multi-pass union of single-key passes
each sub-blocked to its budget share), and choose the one maximizing structural
recall coverage (non-surrogate; larger projected block / more passes = more
coverage) subject to total projected pairs ≤ `K * effective_n_full`. Prefer a
single scale-safe key, else the compound, else the multi-pass union; emit the
degenerate/empty config (controller refuses) if nothing fits — never a surrogate.
Keep this as one cohesive selector function the existing return points delegate to.

- [ ] **Step 4: Run — expect PASS. Step 5: Commit.**

---

## Task 6: `n_rows_full` public + harness plumbing (TDD)

**Files:** `autoconfig.py` (`auto_configure_df` ~2372 + controller call), `scripts/quality_invariant_scale.py` (`build_frozen_config`), `test_autoconfig.py`

- [ ] **Step 1: Failing test**
```python
    def test_auto_configure_df_threads_n_rows_full_to_blocking(self):
        import polars as pl, goldenmatch
        n = 1000
        df = pl.DataFrame({
            "first_name": [f"fn{i//5:06d}" for i in range(n)],
            "last_name": [f"ln{i//5:06d}" for i in range(n)],
            "zip": [f"{(i//5)%100:05d}" for i in range(n)],
        })
        cfg = goldenmatch.auto_configure_df(df, confidence_required=False,
                                            allow_red_config=True, _skip_finalize=True,
                                            n_rows_full=100_000_000)
        bk = (cfg.blocking.keys or []) if cfg.blocking else []
        assert not (len(bk) == 1 and bk[0].fields == ["zip"]), "sole-zip at n_rows_full=100M"
```

- [ ] **Step 2: Run — expect FAIL** (TypeError: unexpected kwarg `n_rows_full`).

- [ ] **Step 3: Implement.** Add `n_rows_full: int | None = None` to
`auto_configure_df` (keyword-only, ~2384). **Route (use this one):**
`controller.run` does NOT accept `n_rows_full` — it computes `n_rows = df.height`
internally. The clean path is to inject the caller's value into the **`v0_kwargs`**
dict that `auto_configure_df` already passes to the controller, e.g.
`v0_kw["n_rows_full"] = n_rows_full` (only when not None). `_initial_config`
already forwards `v0_kwargs["n_rows_full"]` to `_legacy_auto_configure_v0` →
`build_blocking` via the existing `kw["n_rows_full"]` path
(autoconfig_controller.py:~1352). Do NOT add `n_rows_full` to `controller.run`'s
signature (bigger change, unnecessary). Then in
`scripts/quality_invariant_scale.py::build_frozen_config`, add a `n_rows_full:
int = 200_000_000` param and pass it to `auto_configure_df`; document why (the
frozen config must be built FOR the scale it's applied at).

- [ ] **Step 4: Run — expect PASS. Step 5: Commit.**

---

## Task 7: Regression + rebuild frozen config + validate scale

**Files:** none new (runs + the committed frozen config + report)

- [ ] **Step 1: Auto-config regression suites green** (no benchmark regression):
```bash
cd .../goldenmatch && ... pytest tests/test_autoconfig.py tests/test_autoconfig_491_levers.py tests/test_autoconfig_regressions.py tests/test_refdata_autoconfig.py -q -p no:cacheprovider
```
Expected: all pass (run with `GOLDENMATCH_NATIVE=0` — the local native wheel is
stale; the 2 `build_clusters_arrow` failures are pre-existing and pass native-off).
If a `#491`/`#715` test regresses, that's a real signal — re-tune `K` or narrow
the gate; do NOT weaken a benchmark assertion to pass.

- [ ] **Step 2: Rebuild the #510 frozen config** with the target scale:
```bash
cd D:/show_case/gm-510 && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 GOLDENMATCH_AUTOCONFIG_MEMORY=0 \
  PYTHONPATH=... python scripts/quality_invariant_scale.py --rebuild-frozen-config
```
Then assert the rebuilt `scripts/qis_realistic_frozen_config.json` blocking is a
bounded compound / capped multi-pass (NOT sole-`zip`, NOT `id`). Commit the
rebuilt config. Re-run the 1K band + determinism tests (frozen) — still green /
in band.

- [ ] **Step 3: Re-run the ladder 1K→10M** (CI `bench-quality-invariant-scale.yml`,
frozen, on `large-new-64GB`) and confirm: (i) wall scales ~LINEARLY (not the old
super-linear 1M=74s→10M=67min), and (ii) precision is FLAT through 10M (the #876
drift gone). Update `docs/quality-invariant-scale.md` with the new curve.

- [ ] **Step 4: Cluster tier** — on the GCP box (`qis-ladder-510`, restart it):
run 25M, 50M, 100M (and 200M if it fits a sane window) with the rebuilt frozen
config. With bounded blocks the pair count is now linear, so these should
complete in practical time. Add the rows to the report; tear the box down after
(produce the delete command for the user).

- [ ] **Step 5: Final commit + PR update** — update `docs/quality-invariant-scale.md`
(full curve, #876 resolved), reference #876 fixed in the PR #877 body, push.

---

## Risks (carried from spec)
- **Benchmark recall regression** → the auto-config regression + benchmark suites
  are the gate (Task 7 Step 1). The gate only ADDS a total-pairs constraint +
  compound/multi-pass fallback; small-block benchmark keys pass trivially.
- **`K` mis-tuned** → tune against benchmark recall + the #510 ladder; it's an
  env knob (`GOLDENMATCH_BLOCKING_PAIRS_PER_ROW`).
- **build_blocking is large/intricate** → behavior-test-first (Tasks 2-5 each pin
  a contract); read the function + spec fully before editing.
- **200M feasibility** → size from 50M; drop 200M loudly if it doesn't fit.
