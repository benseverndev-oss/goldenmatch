# Fellegi-Sunter Per-Rule EM Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace GoldenMatch's single global EM (which corrupts non-blocking fields' `m`-estimates and collapses precision) with Splink's per-blocking-rule EM: estimate each field's `m` from the runs where it is free to vary, average across runs, keep `u` from random sampling.

**Architecture:** `train_em` runs one EM pass per blocking rule (holding that rule's columns fixed, estimating the rest), then averages each field's per-run `m`. `u` stays from random sampling; the neutral-`u` override for blocking fields is dropped. `EMResult` is unchanged, so the already-shipped sigmoid normalization, TF adjustments, and fast path are untouched. Default-on with `GOLDENMATCH_FS_PER_RULE_EM=0` restoring the current single-run path.

**Tech Stack:** Python 3.11+, NumPy, Polars, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-fs-per-rule-em-design.md`

---

## Conventions for the implementing engineer

- Run tests (Windows): `$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest <path> -v`. Do NOT run the full suite (OOMs this box). Run only touched files.
- Branch `feat/probabilistic-splink-parity` (continue on it). Commit code+tests only; NEVER `git add docs/`. ASCII-only commit messages. Do NOT push.
- This builds on already-shipped work on this branch: sigmoid normalization (`_fs_sigmoid_enabled`), TF (`_tf_adjusted_weight`, `EMResult.tf_tables`), union-aware exclusion (`_em_excluded_fields`), multi-pass blocking. Do not regress them.

## Background: current `train_em` shape (READ before starting)

`core/probabilistic.py::train_em(df, mk, n_sample_pairs, max_iterations, convergence, seed, blocks=None, blocking_fields=None)`:
1. Builds `row_lookup`; estimates `u_probs` from `_sample_pairs` random pairs (~line 313-326).
2. **Overrides `u` to neutral for `blocking_fields`** (~line 328-334) — THIS OVERRIDE IS DROPPED under per-rule EM.
3. Samples within-block pairs via `_sample_blocked_pairs(blocks, ...)` (~line 338-347).
4. Builds `comp_matrix`; inits `m_probs` with an exponential prior (~line 349-361).
5. **EM loop** (~line 363-399+): vectorized E-step (per-field log-prob tables), M-step that **skips `blocking_fields`** (`if f.field in blocking_fields: continue`, ~line 397).
6. Computes `match_weights` = fixed `[-3..3]` for `blocking_fields`, else `log2(m/u)`; builds `tf_tables`; returns `EMResult`.

The M-step already skips a field-set — per-rule EM is this loop run once per pass with `excluded = that pass's fields`.

## File Structure

- **Modify** `core/probabilistic.py`: extract the EM loop into `_estimate_m_one_pass(...)`; add `_fs_per_rule_em_enabled()`; add a `passes` param + per-rule branch to `train_em`; the per-rule branch drops the neutral-`u` override and does per-field averaging + fallback.
- **Modify** `core/pipeline.py`: build per-pass blocks (`_build_blocks_per_pass`) and pass them to `train_em(passes=...)` at both call sites (dedupe ~1417, match ~2388). Keep the union `blocks` for scoring. The single-run kill-switch path keeps using `blocks` + `_em_excluded_fields`.
- **New** `tests/test_probabilistic_per_rule_em.py`.

---

## Task 1: Kill-switch helper + extract the per-pass EM loop (pure refactor)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py`
- Test: `packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py`

- [ ] **Step 1: Write the failing test** (kill-switch parsing + extracted helper reproduces current EM)

```python
import os
import numpy as np
import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    _fs_per_rule_em_enabled, _estimate_m_one_pass, _build_comparison_matrix,
)

def test_per_rule_default_on_and_killswitch():
    assert _fs_per_rule_em_enabled() is True
    for v in ("0", "false", "DISABLED", "no", "  0 "):
        os.environ["GOLDENMATCH_FS_PER_RULE_EM"] = v
        try:
            assert _fs_per_rule_em_enabled() is False
        finally:
            os.environ.pop("GOLDENMATCH_FS_PER_RULE_EM", None)

def test_estimate_m_one_pass_skips_excluded_and_estimates_rest():
    # 6 rows: two clear dup clusters agreeing on name+city, distinct otherwise
    df = pl.DataFrame({
        "__row_id__": [0,1,2,3,4,5],
        "name": ["ann","ann","bob","bob","cara","dee"],
        "city": ["x","x","y","y","z","w"],
    })
    mk = MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="exact", levels=2, partial_threshold=0.9),
        MatchkeyField(field="city", scorer="exact", levels=2, partial_threshold=0.9),
    ])
    cols = ["name","city"]
    row_lookup = {r["__row_id__"]: r for r in df.select(["__row_id__"]+cols).to_dicts()}
    pairs = [(0,1),(2,3),(0,2),(1,4)]
    comp = _build_comparison_matrix(pairs, row_lookup, mk)
    u_probs = {"name":[0.5,0.5], "city":[0.5,0.5]}
    # exclude "name" (as if blocked on name): only "city" m should move off the prior
    m, p_match, converged, iterations = _estimate_m_one_pass(
        comp, mk, u_probs, excluded={"name"}, max_iterations=20, convergence=1e-3)
    assert "city" in m and "name" in m
    assert 0.0 < p_match <= 1.0 and isinstance(converged, bool) and iterations >= 1
    # city was estimated (not the bare exponential prior [1/3, 2/3] for 2 levels)
    assert m["city"] != [1/3, 2/3]
    # name was EXCLUDED -> left at the exponential prior
    assert m["name"] == [1/3, 2/3]
```

- [ ] **Step 2: Run, verify fail** (`ImportError: cannot import name '_estimate_m_one_pass'`).
Run: `$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py -v`

- [ ] **Step 3: Implement.**
Add the kill-switch helper near `_fs_sigmoid_enabled`:
```python
def _fs_per_rule_em_enabled() -> bool:
    """Per-rule EM (estimate each field's m from runs where it is free) -- default ON.
    GOLDENMATCH_FS_PER_RULE_EM=0/false/disabled/no restores the single-run EM."""
    return os.environ.get("GOLDENMATCH_FS_PER_RULE_EM", "1").strip().lower() not in (
        "0", "false", "disabled", "no")
```
Extract the EM loop (current ~line 353-401, the `m_probs` init + the `for iteration` loop + the M-step that skips a field-set) into a module-level helper. It takes a prebuilt `comp_matrix` + `u_probs` + the `excluded` field-set and returns the estimated `m_probs` dict:
```python
def _estimate_m_one_pass(comp_matrix, mk, u_probs, excluded, max_iterations, convergence):
    """One EM run: estimate m for fields NOT in `excluded` (the run's blocking fields,
    held constant in this block). Returns (m_probs, p_match, converged, iterations).
    m_probs covers ALL fields (excluded ones keep the exponential prior; callers ignore
    those). Vectorized E-step identical to the inline loop it replaces. The extra return
    values (p_match/converged/iterations) preserve what train_em's EMResult needs so the
    single-run refactor is behavior-preserving (existing tests assert converged/iterations)."""
    n_pairs = comp_matrix.shape[0]
    p_match = 0.02
    m_probs = {}
    for f in mk.fields:
        raw = [2 ** k for k in range(f.levels)]
        m_probs[f.field] = [r / sum(raw) for r in raw]
    converged = False
    iteration = 0
    for iteration in range(max_iterations):
        old_m = {k: list(v) for k, v in m_probs.items()}
        log_m = np.zeros(n_pairs); log_u = np.zeros(n_pairs)
        for j, f in enumerate(mk.fields):
            levels_j = comp_matrix[:, j]
            m_table = np.log(np.maximum(np.asarray(m_probs[f.field], dtype=np.float64), 1e-10))
            u_table = np.log(np.maximum(np.asarray(u_probs[f.field], dtype=np.float64), 1e-10))
            log_m += m_table[levels_j]; log_u += u_table[levels_j]
        log_match = math.log(max(p_match, 1e-10)) + log_m
        log_nonmatch = math.log(max(1 - p_match, 1e-10)) + log_u
        max_log = np.maximum(log_match, log_nonmatch)
        e_match = np.exp(log_match - max_log); e_nonmatch = np.exp(log_nonmatch - max_log)
        posteriors = e_match / (e_match + e_nonmatch)
        total_match = posteriors.sum()
        p_match = max(total_match / n_pairs, 1e-6)
        for j, f in enumerate(mk.fields):
            if f.field in excluded:
                continue
            new_m = [0.0] * f.levels
            for level in range(f.levels):
                mask = comp_matrix[:, j] == level
                new_m[level] = (posteriors[mask].sum() + 1e-6) / (total_match + f.levels * 1e-6)
            m_probs[f.field] = new_m
        max_delta = max((abs(m_probs[f.field][k] - old_m[f.field][k])
                         for f in mk.fields if f.field not in excluded
                         for k in range(f.levels)), default=0.0)
        if max_delta < convergence:
            converged = True
            break
    iterations = iteration + 1
    return m_probs, p_match, converged, iterations
```
**Return contract is `(m_probs, p_match, converged, iterations)` everywhere** — the Task 1 test, the single-run refactor, and Task 2 all unpack the 4-tuple. This is required because existing `test_probabilistic.py` asserts `result.converged`/`result.iterations` on the single-run path (lines ~193, 427, 476, 538); the refactor must thread all four into `EMResult`, not just `m_probs`.

Then **refactor the existing single-run `train_em` body to call**
`_estimate_m_one_pass(comp_matrix, mk, u_probs, set(blocking_fields), max_iterations, convergence)`
in place of its inline `m_probs` init + `for iteration` loop, unpacking
`m_probs, p_match, converged, iterations = _estimate_m_one_pass(...)` and threading
`converged`/`iterations`/`proportion_matched=p_match` into the returned `EMResult` exactly
as the inline loop did. The single-run match-weights computation (fixed `-3..3` for
`blocking_fields`, else `log2(m/u)`) and the TF-table build stay where they are (the TF
build is factored out in Task 2). This keeps the kill-switch path byte-equivalent.

- [ ] **Step 4: Run the new test + the existing probabilistic suite — all green.**
`.venv\Scripts\python.exe -m pytest packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py packages/python/goldenmatch/tests/test_probabilistic.py packages/python/goldenmatch/tests/test_probabilistic_sigmoid.py packages/python/goldenmatch/tests/test_probabilistic_tf.py -q`
Expected: PASS (the refactor is behavior-preserving for the single-run path).

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py
git commit -m "refactor(probabilistic): extract _estimate_m_one_pass + add per-rule-EM kill-switch (behavior-preserving)"
```

---

## Task 2: Per-rule EM path in `train_em` (average across passes, drop neutral-u override, fallback)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/probabilistic.py` (`train_em`)
- Test: `packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py`

- [ ] **Step 1: Write the failing tests** (the behavioral guards from the spec)

```python
from goldenmatch.core.probabilistic import train_em

def _df_person():
    # matches agree on name AND postcode; non-matches in a name block disagree on postcode
    return pl.DataFrame({
        "__row_id__": list(range(8)),
        "name":     ["smith","smith","smith","smith","jones","jones","lee","ng"],
        "postcode": ["AA1","AA1","BB2","CC3","DD4","DD4","EE5","FF6"],
    })

def _mk():
    return MatchkeyConfig(name="p", type="probabilistic", fields=[
        MatchkeyField(field="name", scorer="exact", levels=2, partial_threshold=0.9),
        MatchkeyField(field="postcode", scorer="exact", levels=2, partial_threshold=0.9),
    ])

def _passes_blocks(df, fieldsets):
    # build one BlockResult-like per pass via the real blocker
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
    from goldenmatch.core.blocker import build_blocks
    out = []
    for fs in fieldsets:
        cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=fs)])
        out.append((fs, build_blocks(df.lazy(), cfg)))
    return out

def test_per_rule_postcode_disagree_penalty_is_strong():
    df, mk = _df_person(), _mk()
    # passes: block on name (postcode free) AND block on postcode (name free)
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em = train_em(df, mk, passes=passes)
    # postcode estimated in the name-blocked run where it varies -> disagree weight clearly negative
    assert em.match_weights["postcode"][0] <= -1.0
    # name estimated in the postcode-blocked run
    assert "name" in (em.tf_tables or {}) or em.match_weights["name"][1] > 0

def test_per_rule_beats_single_run_on_disagree_penalty():
    df, mk = _df_person(), _mk()
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em_perrule = train_em(df, mk, passes=passes)
    # single-run kill-switch path: block on name only, postcode m corrupted
    os.environ["GOLDENMATCH_FS_PER_RULE_EM"] = "0"
    try:
        blocks = passes[0][1]
        em_single = train_em(df, mk, blocks=blocks, blocking_fields=["name"])
    finally:
        os.environ.pop("GOLDENMATCH_FS_PER_RULE_EM", None)
    # per-rule gives a stronger (more negative) postcode-disagree weight than single-run
    assert em_perrule.match_weights["postcode"][0] < em_single.match_weights["postcode"][0]

def test_per_rule_u_not_overridden_for_sometimes_blocked_field():
    df, mk = _df_person(), _mk()
    passes = _passes_blocks(df, [["name"], ["postcode"]])
    em = train_em(df, mk, passes=passes)
    # name is a block key in pass 1 but u must be the random-pair estimate, NOT neutral 0.5
    assert em.u_probs["name"] != [0.5, 0.5]

def test_per_rule_fallback_for_always_blocked_field():
    df, mk = _df_person(), _mk()
    # both passes block on name -> name free in NO pass -> fixed prior fallback
    passes = _passes_blocks(df, [["name"], ["name"]])
    em = train_em(df, mk, passes=passes)
    assert em.match_weights["name"] == [-3.0, 3.0]  # fixed 2-level prior
```

- [ ] **Step 2: Run, verify fail** (train_em has no `passes` param yet / weights wrong).

- [ ] **Step 3: Implement the per-rule branch in `train_em`.**
Add `passes: list | None = None` to the signature (list of `(field_set, blocks)` tuples). Near the top, branch:
```python
    if passes is not None and _fs_per_rule_em_enabled():
        return _train_em_per_rule(df, mk, passes, n_sample_pairs, max_iterations,
                                  convergence, seed)
```
Implement `_train_em_per_rule`:
```python
def _train_em_per_rule(df, mk, passes, n_sample_pairs, max_iterations, convergence, seed):
    cols = [f.field for f in mk.fields if f.field != "__record__"]
    row_lookup = {r["__row_id__"]: r for r in df.select(["__row_id__"]+cols).to_dicts()}
    # u from random pairs -- shared, NO neutral override (spec: random u is unbiased for all)
    random_pairs = _sample_pairs(df, min(n_sample_pairs, 5000), seed)
    if len(random_pairs) < 10:
        return _fallback_result(mk)
    random_matrix = _build_comparison_matrix(random_pairs, row_lookup, mk)
    u_probs = {}
    for j, f in enumerate(mk.fields):
        counts = [float((random_matrix[:, j] == lv).sum()) for lv in range(f.levels)]
        total = sum(counts) + f.levels * 1e-6
        u_probs[f.field] = [(c + 1e-6) / total for c in counts]
    # per-pass EM, collect m estimates for fields free in that pass
    m_runs = {f.field: [] for f in mk.fields}
    p_matches = []
    converged_any = False
    iters_max = 0
    for field_set, blocks in passes:
        pair_list = _sample_blocked_pairs(blocks, n_sample_pairs, seed)
        if len(pair_list) < 10:
            logger.info("per-rule EM: pass %s skipped (only %d pairs)", field_set, len(pair_list))
            continue
        comp = _build_comparison_matrix(pair_list, row_lookup, mk)
        excluded = set(field_set)
        m_pass, p_match, conv, iters = _estimate_m_one_pass(
            comp, mk, u_probs, excluded, max_iterations, convergence)
        p_matches.append(p_match)
        converged_any = converged_any or conv
        iters_max = max(iters_max, iters)
        for f in mk.fields:
            if f.field not in excluded:
                m_runs[f.field].append(m_pass[f.field])
    if all(len(v) == 0 for v in m_runs.values()):
        return _fallback_result(mk)  # every pass thin
    # combine: average across runs where the field was free; fixed prior if free in none
    m_probs, match_weights = {}, {}
    for f in mk.fields:
        runs = m_runs[f.field]
        if runs:
            m_probs[f.field] = [float(np.mean([r[lv] for r in runs])) for lv in range(f.levels)]
            match_weights[f.field] = [
                math.log2(max(m_probs[f.field][lv], 1e-10) / max(u_probs[f.field][lv], 1e-10))
                for lv in range(f.levels)]
        else:
            # free in NO pass -> fixed neutral prior (same shape as the single-run blocking-field path)
            n = f.levels
            m_probs[f.field] = [r / sum(2**k for k in range(n)) for r in (2**k for k in range(n))]
            match_weights[f.field] = [(-3.0 + 6.0*k/(n-1)) if n > 1 else 3.0 for k in range(n)]
    # TF tables -- reuse the existing builder logic (factor it out if inline today)
    tf_tables = _build_tf_tables(df, mk)   # see TF-factoring step below
    return EMResult(m_probs=m_probs, u_probs=u_probs, match_weights=match_weights,
                    converged=converged_any, iterations=iters_max,
                    proportion_matched=(float(np.mean(p_matches)) if p_matches else 0.05),
                    tf_tables=(tf_tables or None))
```
Notes:
- `_estimate_m_one_pass` returns `(m_probs, p_match, converged, iterations)` (Task 1) — unpack all four; aggregate `converged` via OR and `iterations` via max across passes (shown above).
- The fixed-weight fallback for free-in-no-pass fields must match the single-run path's blocking-field weights (linear `-3..3`) so single-static-key equivalence holds.

**Step 3a (do FIRST, before writing `_train_em_per_rule`): factor out `_build_tf_tables`.**
The TF-table construction currently lives INLINE in `train_em` (the loop that builds
`tf_tables` from `tf_adjust` fields via `apply_transforms(str(value), f.transforms)` +
per-value frequency — real source ~lines 444-467). Extract it verbatim into a module-level
`def _build_tf_tables(df, mk) -> dict[str, dict[str, float]]:` and replace the inline block
in the single-run `train_em` with a call to it. Run the existing TF tests
(`test_probabilistic_tf.py`) to confirm the extraction is behavior-preserving BEFORE
building the per-rule path (which also calls `_build_tf_tables`). This keeps one TF
definition (DRY) and identical tables on both paths.

- [ ] **Step 4: Run the Task-2 tests + the full probabilistic suite — all green.** Adjust ONLY if a test's expected number is wrong for a defensible reason (note it); never weaken a behavioral assertion (the disagree-penalty and u-not-overridden tests are load-bearing).

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/probabilistic.py packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py
git commit -m "feat(probabilistic): per-rule EM -- estimate each field's m where it is free, average across passes, drop neutral-u override"
```

---

## Task 3: Pipeline wiring (build per-pass blocks; call train_em(passes=...))

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (both probabilistic branches)
- Test: `packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py`

- [ ] **Step 1: Write the failing test** (end-to-end dedupe runs via per-rule EM, multi-pass config)

```python
def test_pipeline_per_rule_em_runs_end_to_end():
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df
    df = pl.DataFrame({
        "first_name": ["ann","ann","bob","bobby","cara","cara","dan","eve"],
        "surname":    ["lee","lee","kim","kim","ng","ng","ono","poe"],
        "dob":        ["1990","1990","1985","1985","1972","1972","1965","1959"],
    })
    cfg = auto_configure_probabilistic_df(df)
    res = dedupe_df(df, config=cfg)
    assert res is not None  # per-rule EM path executes, no crash
```

- [ ] **Step 2: Make the test assert the per-rule path is taken, and verify it FAILS pre-wiring.** Add a spy on `_train_em_per_rule` and assert it fired during `dedupe_df`. Patch it on the `probabilistic` MODULE (the pipeline imports `train_em` from there lazily; `_train_em_per_rule` is called *inside* `train_em`, so patching the module binding works):
```python
def test_pipeline_per_rule_em_runs_end_to_end(monkeypatch):
    import goldenmatch.core.probabilistic as P
    calls = {"n": 0}
    orig = P._train_em_per_rule
    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)
    monkeypatch.setattr(P, "_train_em_per_rule", spy)
    # ... build df + cfg + dedupe_df(df, config=cfg) as above ...
    assert res is not None
    assert calls["n"] >= 1, "per-rule EM path was not taken (passes not wired into the pipeline)"
```
Before Step 3 wires `passes` into the `train_em` call, `calls["n"]` stays 0 → the assert FAILS. That's the red state.

- [ ] **Step 3: Implement.** Add a helper (in `pipeline.py` or `blocker.py`) that returns per-pass blocks from a `BlockingConfig`:
```python
def _build_blocks_per_pass(lf, blocking):
    """Return [(field_set, blocks), ...] -- one entry per blocking pass (or per key when
    no passes). Reuses the static block builder per pass so pass identity is retained
    (build_blocks unions+dedupes and loses it)."""
    from goldenmatch.config.schemas import BlockingConfig
    passes = blocking.passes if blocking.passes else (blocking.keys or [])
    out = []
    for p in passes:
        cfg = BlockingConfig(keys=[p], max_block_size=blocking.max_block_size,
                             skip_oversized=blocking.skip_oversized)
        out.append((list(p.fields), build_blocks(lf, cfg)))
    return out
```
At BOTH probabilistic branches in pipeline.py: keep the existing union `blocks = build_blocks(...)` (used for SCORING), and add `passes = _build_blocks_per_pass(combined_lf, config.blocking)`; call `train_em(collected_df, mk, ..., passes=passes)` (drop the `blocking_fields=` arg on the per-rule call; the single-run kill-switch path inside train_em still uses `blocks`+`blocking_fields` when `passes is None` OR per-rule disabled — so under the kill-switch, pass `blocks=blocks, blocking_fields=_em_excluded_fields(config.blocking)` and `passes=None`). Simplest wiring: always pass BOTH `blocks=blocks, blocking_fields=_em_excluded_fields(config.blocking), passes=passes`; `train_em` chooses per-rule when enabled+passes, else single-run.

- [ ] **Step 4: Run the test + a broad probabilistic regression** (`test_probabilistic*.py`, `test_autoconfig_probabilistic*.py`) — all green.

- [ ] **Step 5: Commit**
```
git add packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_probabilistic_per_rule_em.py
git commit -m "feat(probabilistic): wire per-rule EM into both pipeline branches (per-pass blocks)"
```

---

## Task 4: Measure against the gate (no code; uses the surviving-dump PR-curve method)

- [ ] **Step 1** Re-run the local PR-curve diagnostic (the `.profile_tmp/diag_pr_tf.py` shape, but training via the per-rule path — i.e. pass `passes=_build_blocks_per_pass(...)` into `train_em`, or just run through `auto_configure_probabilistic_df` + the pipeline pair-dump hook and compute pairwise P/R from the dump as in `.profile_tmp/compute_pairwise.py`).
- [ ] **Step 2** Check the spec gate on `historical_50k`:
  - **Mechanism:** postcode-disagree weight ≤ −1.5 (vs −0.06 before).
  - **Headline:** an operating point with F1 materially > 0.655 (target P ≥ ~0.8 @ R ≥ ~0.7).
- [ ] **Step 3** Non-regression: febrl3 + synthetic don't drop (run the panel GM-only on those two, which complete locally).
- [ ] **Step 4: DECISION GATE.** If the mechanism metric improved but precision still collapses on the PR curve → the residual wall is blocking-candidate quality → STOP and open the selective-compound-blocking spec (lever #3) before further EM work. If the gate is met → per-rule EM is validated; record the numbers in the PR body.

---

## Final checks before PR

- [ ] Run all touched test files individually (not the full suite).
- [ ] `ruff check` the changed files.
- [ ] Confirm `GOLDENMATCH_FS_PER_RULE_EM=0` restores the current single-run + `_em_excluded_fields` intersection behavior (the kill-switch equivalence test).
- [ ] Confirm `EMResult` shape unchanged → sigmoid/TF/fast-path tests still green.
- [ ] PR body: the before/after PR-curve numbers + the gate verdict. Do NOT `git add docs/`.

## Follow-ups (NOT in this plan)

- Selective compound blocking (lever #3) — only if the kill criterion fires, or as the next planned lever.
- Sample-weighted m-combination; threshold auto-calibration; `train_em_continuous` parity; TS parity.
- CI/Splink head-to-head panel + branch rebase onto origin/main + merge.
