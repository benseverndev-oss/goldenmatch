# Hoist matchkey transforms — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate ~7000 redundant Polars `frame.select(...)` calls during dedupe scoring by precomputing each unique `(field, transforms)` pair once on the parent DataFrame; per-block scoring becomes an O(1) column lookup.

**Architecture:** New helper `precompute_matchkey_transforms` in `core/matchkey.py` augments the working DataFrame with `__xform_<sig>__` columns (one per unique signature). Wired in once each into `_run_dedupe_pipeline` and `_run_match_pipeline`, immediately after `compute_matchkeys` so it runs before all `build_blocks` calls. `scorer._get_transformed_values` gets a fast-path lookup with the legacy path preserved as fallback for callers that bypass the pipeline.

**Tech Stack:** Python 3.11+, Polars (lazy + eager APIs), pytest, hashlib (blake2b for stable signatures).

**Spec:** `docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md`

**Pre-existing state confirmed (2026-05-04):**
- `_try_native_chain` lives at `packages/python/goldenmatch/goldenmatch/core/matchkey.py:13`
- `_get_transformed_values` lives at `packages/python/goldenmatch/goldenmatch/core/scorer.py:113`
- `_run_dedupe_pipeline` calls `compute_matchkeys` at `pipeline.py:371` and `build_blocks` at lines **407 AND 423** (weighted + probabilistic phases — single precompute covers both).
- `_run_match_pipeline` calls `compute_matchkeys` at `pipeline.py:803` and `build_blocks` at lines **836 AND 850** (same pattern).
- `combined_lf` is a `pl.LazyFrame` at the insertion points — the helper signature must accept LazyFrame OR materialize at call site. Plan adopts the latter (one explicit `.collect()` per pipeline) to keep the helper's API DataFrame-in/out as specified.
- Test file already exists: `packages/python/goldenmatch/tests/test_matchkey.py` and `tests/test_scorer.py`.
- Profile script already exists: `.profile_tmp/profile_dedupe.py` (`.profile_tmp/` is gitignored per package CLAUDE.md — do NOT commit).
- Convention from `goldenmatch/CLAUDE.md`: TDD, test files at `tests/test_{module}.py`, internal columns use `__` prefix, conventional commits (`feat:`, `fix:`, `test:`, `chore:`).
- GH auth: must `gh auth switch --user benzsevern` before push.

**Branch:** `perf/hoist-matchkey-transforms`

---

## Task 1: Branch + write failing tests for the new helpers

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_matchkey.py` (append)

**Why:** TDD per CLAUDE.md. The new helper has 5 distinct test cases per the spec — write them all up front so the implementation has a fixed target.

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull && git checkout -b perf/hoist-matchkey-transforms
```

- [ ] **Step 2: Append the 7 new tests to `tests/test_matchkey.py`**

```python
# --- Tests for precompute_matchkey_transforms (perf/hoist-matchkey-transforms) ---
import polars as pl
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.matchkey import (
    _xform_sig,
    precompute_matchkey_transforms,
)


def _mk(name: str, fields: list[MatchkeyField], threshold: float = 0.7) -> MatchkeyConfig:
    return MatchkeyConfig(name=name, type="weighted", threshold=threshold, fields=fields)


def _field(field: str, transforms: list[str], scorer: str = "jaro_winkler",
           weight: float = 1.0) -> MatchkeyField:
    return MatchkeyField(field=field, transforms=transforms, scorer=scorer, weight=weight)


def test_xform_sig_is_deterministic_across_processes():
    # blake2b output is stable across processes (unlike Python's salted hash()).
    f1 = _field("name", ["lowercase", "strip"])
    f2 = _field("name", ["lowercase", "strip"])
    assert _xform_sig(f1) == _xform_sig(f2)
    # Hex digest: stable shape.
    sig = _xform_sig(f1)
    assert sig.startswith("__xform_name_") and sig.endswith("__")
    assert len(sig) > len("__xform_name___")  # has digest body


def test_precompute_matchkey_transforms_dedups_signatures():
    # Same field+transforms across two matchkeys → ONE column, not two.
    df = pl.DataFrame({"name": ["Alice", "BOB"]})
    mk_a = _mk("a", [_field("name", ["lowercase"])])
    mk_b = _mk("b", [_field("name", ["lowercase"])])
    out = precompute_matchkey_transforms(df, [mk_a, mk_b])
    xform_cols = [c for c in out.columns if c.startswith("__xform_")]
    assert len(xform_cols) == 1


def test_precompute_matchkey_transforms_distinct_transforms_same_field():
    # Same field with two different transform chains → two distinct columns.
    df = pl.DataFrame({"name": ["Alice"]})
    mk = _mk("m", [
        _field("name", ["lowercase"]),
        _field("name", ["uppercase"]),
    ])
    out = precompute_matchkey_transforms(df, [mk])
    xform_cols = sorted(c for c in out.columns if c.startswith("__xform_"))
    assert len(xform_cols) == 2
    assert out[xform_cols[0]].to_list() != out[xform_cols[1]].to_list()


def test_precompute_matchkey_transforms_native_chain_path():
    # lowercase + strip is in _NATIVE_TRANSFORMS — fast path runs.
    df = pl.DataFrame({"name": ["  Alice  ", "BOB"]})
    mk = _mk("m", [_field("name", ["lowercase", "strip"])])
    out = precompute_matchkey_transforms(df, [mk])
    sig = _xform_sig(_field("name", ["lowercase", "strip"]))
    assert out[sig].to_list() == ["alice", "bob"]


def test_precompute_matchkey_transforms_python_fallback_path():
    # soundex is NOT in the native chain (per matchkey.py _try_native_transform);
    # falls through to apply_transforms per-row.
    df = pl.DataFrame({"name": ["Smith", "Smyth"]})
    mk = _mk("m", [_field("name", ["soundex"])])
    out = precompute_matchkey_transforms(df, [mk])
    sig = _xform_sig(_field("name", ["soundex"]))
    vals = out[sig].to_list()
    # Smith and Smyth share a Soundex code (S530).
    assert vals[0] == vals[1]


def test_precompute_matchkey_transforms_skips_record_embedding():
    # record_embedding fields use field.columns (plural), not field.field.
    # Including them would crash on df["__record__"]. Helper must skip them.
    df = pl.DataFrame({"name": ["a"], "desc": ["b"]})
    mk = MatchkeyConfig(name="m", type="weighted", threshold=0.5, fields=[
        MatchkeyField(field="__record__", transforms=[], scorer="record_embedding",
                      weight=1.0, columns=["name", "desc"]),
        _field("name", ["lowercase"]),  # this one should still be precomputed
    ])
    out = precompute_matchkey_transforms(df, [mk])
    assert "__record__" not in out.columns
    sig_name = _xform_sig(_field("name", ["lowercase"]))
    assert sig_name in out.columns


def test_precompute_matchkey_transforms_skips_empty_transforms():
    # Empty transforms list → no precompute (legacy path is already a no-op).
    df = pl.DataFrame({"name": ["Alice"]})
    mk = _mk("m", [_field("name", [])])
    out = precompute_matchkey_transforms(df, [mk])
    xform_cols = [c for c in out.columns if c.startswith("__xform_")]
    assert xform_cols == []
```

- [ ] **Step 3: Run tests — verify they all FAIL with `ImportError`**

```bash
cd /d/show_case/goldenmatch && uv run pytest packages/python/goldenmatch/tests/test_matchkey.py -v -k "xform_sig or precompute_matchkey"
```

Expected: 7 failures, all `ImportError: cannot import name '_xform_sig' / 'precompute_matchkey_transforms' from 'goldenmatch.core.matchkey'`

- [ ] **Step 4: Commit failing tests**

```bash
git add packages/python/goldenmatch/tests/test_matchkey.py
git commit -m "test: add failing tests for precompute_matchkey_transforms helper"
```

---

## Task 2: Implement `_xform_sig` + `precompute_matchkey_transforms`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/matchkey.py`

**Why:** Make Task 1's tests pass. Implementation is verbatim from the spec.

- [ ] **Step 1: Add the helper at the end of `matchkey.py`**

Append after the existing `compute_matchkeys` function:

```python
import hashlib  # add at top of file with other stdlib imports if not present


def _xform_sig(field: MatchkeyField) -> str:
    """Stable, process-independent signature for a (field, transforms) pair.

    Uses blake2b rather than Python's salted hash() so the resulting column
    name is deterministic across processes — makes debugging dumps diffable
    and avoids spooky cross-run differences in error messages.
    """
    digest = hashlib.blake2b(
        repr(field.transforms).encode(), digest_size=8
    ).hexdigest()
    return f"__xform_{field.field}_{digest}__"


def precompute_matchkey_transforms(
    df: pl.DataFrame, matchkeys: list[MatchkeyConfig]
) -> pl.DataFrame:
    """Add one __xform_<sig>__ column per unique (field, transforms) signature.

    Same field+transforms across multiple matchkeys reuses one column — dedup
    is automatic via the signature. Native chains use _try_native_chain (Rust);
    non-native chains fall back to Python per-row apply_transforms once.

    Skips fields whose scorer is `record_embedding` (uses multi-column
    `field.columns`, has its own scoring path that doesn't call
    `_get_transformed_values`).

    Skips fields with empty `transforms` list — nothing to precompute, and
    `_get_transformed_values`' legacy path is already a single `to_list()`.

    Returns the augmented DataFrame. Original columns are untouched.
    """
    seen: set[str] = set()
    new_cols: list[pl.Series] = []
    for mk in matchkeys:
        for field in mk.fields:
            if field.scorer == "record_embedding":
                continue
            if not field.transforms:
                continue
            sig = _xform_sig(field)
            if sig in seen or sig in df.columns:
                continue
            seen.add(sig)

            native_expr = _try_native_chain(field.field, field.transforms)
            if native_expr is not None:
                col = df.select(native_expr.alias(sig))[sig]
            else:
                values = df[field.field].to_list()
                col = pl.Series(
                    sig,
                    [apply_transforms(v, field.transforms) if v is not None else None
                     for v in values],
                )
            new_cols.append(col)

    if not new_cols:
        return df
    return df.with_columns(new_cols)
```

- [ ] **Step 2: Verify the new tests pass**

```bash
uv run pytest packages/python/goldenmatch/tests/test_matchkey.py -v -k "xform_sig or precompute_matchkey"
```

Expected: 7 passed.

- [ ] **Step 3: Verify the rest of `test_matchkey.py` still passes (no regression)**

```bash
uv run pytest packages/python/goldenmatch/tests/test_matchkey.py -v
```

Expected: all green.

- [ ] **Step 4: Commit the helper**

```bash
git add packages/python/goldenmatch/goldenmatch/core/matchkey.py
git commit -m "feat(matchkey): add precompute_matchkey_transforms helper

Hoists each unique (field, transforms) signature to its own __xform_<sig>__
column on the parent DataFrame. Setup for eliminating per-block redundant
Polars .select() calls in scorer._get_transformed_values."
```

---

## Task 3: Wire `_get_transformed_values` to use the precomputed columns

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py:113-127`
- Modify: `packages/python/goldenmatch/tests/test_scorer.py` (append 2 tests)

**Why:** The hot path. Spec section 3.

- [ ] **Step 1: Append 2 new tests to `tests/test_scorer.py`**

```python
# --- Tests for _get_transformed_values fast-path (perf/hoist-matchkey-transforms) ---
import polars as pl
from goldenmatch.config.schemas import MatchkeyField
from goldenmatch.core.matchkey import _xform_sig, precompute_matchkey_transforms
from goldenmatch.core.scorer import _get_transformed_values


def test_get_transformed_values_uses_precomputed_column_when_present():
    # If a __xform_*__ column matches the field signature, the fast path
    # reads it directly — no Polars .select() round-trip.
    field = MatchkeyField(field="name", transforms=["lowercase"],
                          scorer="jaro_winkler", weight=1.0)
    sig = _xform_sig(field)
    block_df = pl.DataFrame({
        "name": ["Alice", "BOB"],
        sig: ["PRECOMPUTED_A", "PRECOMPUTED_B"],  # sentinel — proves we read this, not the raw column
    })
    assert _get_transformed_values(block_df, field) == ["PRECOMPUTED_A", "PRECOMPUTED_B"]


def test_get_transformed_values_falls_back_when_column_absent():
    # Without the precomputed column, behavior must be identical to the
    # legacy path — regression pin for callers that bypass the pipeline
    # (DataFrame entry points, tests calling find_fuzzy_matches directly).
    field = MatchkeyField(field="name", transforms=["lowercase"],
                          scorer="jaro_winkler", weight=1.0)
    block_df = pl.DataFrame({"name": ["Alice", "BOB"]})
    assert _get_transformed_values(block_df, field) == ["alice", "bob"]
```

- [ ] **Step 2: Run them — verify the first FAILS, second PASSES (legacy path already works)**

```bash
uv run pytest packages/python/goldenmatch/tests/test_scorer.py -v -k "get_transformed_values"
```

Expected: 1 fail (`assert ['alice', 'bob'] == ['PRECOMPUTED_A', 'PRECOMPUTED_B']`), 1 pass.

- [ ] **Step 3: Update `_get_transformed_values` in `scorer.py:113`**

Replace the function body (keep signature, keep docstring intent):

```python
def _get_transformed_values(block_df: pl.DataFrame, field: MatchkeyField) -> list:
    """Get transformed values for a field as a list.

    Fast path: read the precomputed __xform_*__ column populated by
    precompute_matchkey_transforms (called once per pipeline run, eagerly,
    before blocking). Avoids ~7000 redundant Polars .select() calls per
    dedupe.

    Fallback path: legacy per-block .select(_try_native_chain(...)) for
    callers that bypass the pipeline (DataFrame entry points, tests calling
    find_fuzzy_matches directly).
    """
    from goldenmatch.core.matchkey import _xform_sig, _try_native_chain

    sig = _xform_sig(field)
    if sig in block_df.columns:
        return block_df[sig].to_list()

    # Legacy path — preserved verbatim from before the perf change.
    col = field.field
    native_expr = _try_native_chain(col, field.transforms)
    if native_expr is not None:
        result_df = block_df.select(native_expr.alias("__tmp__"))
        return result_df["__tmp__"].to_list()
    values = block_df[col].to_list()
    return [apply_transforms(v, field.transforms) if v is not None else None for v in values]
```

(The `apply_transforms` import was already present at the top of `scorer.py` — verify it's still imported. If not, add `from goldenmatch.utils.transforms import apply_transforms`.)

- [ ] **Step 4: Verify both new scorer tests pass**

```bash
uv run pytest packages/python/goldenmatch/tests/test_scorer.py -v -k "get_transformed_values"
```

Expected: 2 passed.

- [ ] **Step 5: Verify the rest of `test_scorer.py` still passes (regression check)**

```bash
uv run pytest packages/python/goldenmatch/tests/test_scorer.py -v
```

Expected: all green (modulo any pre-existing failures on this branch — record the baseline pass/fail count from `git stash` + run on main if any failures look surprising).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_scorer.py
git commit -m "perf(scorer): fast-path _get_transformed_values via precomputed column

Reads the __xform_<sig>__ column written by precompute_matchkey_transforms
when present; falls back to the legacy Polars .select() path otherwise.
No behavior change for callers that bypass the pipeline."
```

---

## Task 4: Add an end-to-end equivalence test

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_scorer.py` (append)

**Why:** Pin that scoring with vs without precompute returns identical results on a small synthetic block. Catches any future drift in the fast-path lookup.

- [ ] **Step 1: Append the equivalence test**

```python
def test_find_fuzzy_matches_identical_results_with_and_without_precompute():
    # Score the same block twice — once with the precomputed __xform_*__ column,
    # once without. Pair lists must match exactly.
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    from goldenmatch.core.scorer import find_fuzzy_matches
    from goldenmatch.core.matchkey import precompute_matchkey_transforms

    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["Alice Smith", "Alice Smyth", "Bob Jones", "Robert Jones"],
        "zip": ["10001", "10001", "20002", "20002"],
    })
    mk = MatchkeyConfig(
        name="m", type="weighted", threshold=0.6,
        fields=[
            MatchkeyField(field="name", transforms=["lowercase", "strip"],
                          scorer="jaro_winkler", weight=0.7),
            MatchkeyField(field="zip", transforms=["strip"],
                          scorer="exact", weight=0.3),
        ],
    )

    pairs_legacy = sorted(find_fuzzy_matches(df, mk))
    pairs_precomputed = sorted(find_fuzzy_matches(
        precompute_matchkey_transforms(df, [mk]), mk
    ))
    assert pairs_legacy == pairs_precomputed
```

- [ ] **Step 2: Run it**

```bash
uv run pytest packages/python/goldenmatch/tests/test_scorer.py::test_find_fuzzy_matches_identical_results_with_and_without_precompute -v
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_scorer.py
git commit -m "test(scorer): pin end-to-end equivalence of precompute vs legacy path"
```

---

## Task 5: Wire precompute into both pipelines

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`

**Why:** Spec section 2. Inserts the actual perf win.

- [ ] **Step 1: Add the import near the top of `pipeline.py`**

Find the existing `from goldenmatch.core.matchkey import compute_matchkeys` import (around line 16). Replace with:

```python
from goldenmatch.core.matchkey import compute_matchkeys, precompute_matchkey_transforms
```

- [ ] **Step 2: Modify the existing `.collect()` in `_run_dedupe_pipeline`**

The dedupe pipeline already collects the LazyFrame right after `compute_matchkeys` (currently line 374: `collected_df = combined_lf.collect()`). Augment that collect with the precompute and rebuild `combined_lf` so downstream `build_blocks` (lines 407, 423) sees the new columns AND `collected_df` (used for `_run_auto_suggest`, source_lookup) is kept in sync.

Find this block in `_run_dedupe_pipeline`:

```python
    combined_lf = compute_matchkeys(combined_lf, matchkeys)

    # ── Step 2.5: AUTO-SUGGEST blocking keys ──
    collected_df = combined_lf.collect()
```

Replace with:

```python
    combined_lf = compute_matchkeys(combined_lf, matchkeys)

    # ── Step 2.5: AUTO-SUGGEST blocking keys ──
    # Hoist matchkey transforms onto the materialized df once — eliminates
    # ~7000 redundant per-block .select() calls during scoring (folds into the
    # existing collect; no extra materialization). See spec
    # docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md.
    collected_df = precompute_matchkey_transforms(combined_lf.collect(), matchkeys)
    combined_lf = collected_df.lazy()
```

- [ ] **Step 3: Modify the existing `.collect()` in `_run_match_pipeline`**

Same shape — the match pipeline also already collects right after `compute_matchkeys` (currently line 804: `combined_df = combined_lf.collect()`).

Find this block in `_run_match_pipeline`:

```python
    # ── Step 3: Compute matchkeys ──
    combined_lf = compute_matchkeys(combined_lf, matchkeys)
    combined_df = combined_lf.collect()
```

Replace with:

```python
    # ── Step 3: Compute matchkeys ──
    combined_lf = compute_matchkeys(combined_lf, matchkeys)
    # Hoist matchkey transforms — see spec
    # docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md.
    combined_df = precompute_matchkey_transforms(combined_lf.collect(), matchkeys)
    combined_lf = combined_df.lazy()
```

This keeps `combined_df` (used at line 811 for source_lookup) populated with the new `__xform_*__` columns and lets `build_blocks` at lines 836, 850 see them via `combined_lf`.

- [ ] **Step 4: Run a small dedupe smoke test to verify pipeline integration**

```bash
uv run python -c "
import goldenmatch as gm
import csv, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp()) / 't.csv'
with open(tmp, 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['id','name','email','zip'])
    w.writerow([1,'Alice Smith','alice@x.com','10001'])
    w.writerow([2,'Alice Smyth','alice@x.com','10001'])
    w.writerow([3,'Bob Jones','bob@y.com','20002'])
r = gm.dedupe(str(tmp), exact=['email'], fuzzy={'name': 0.8}, blocking=['zip'])
print(f'OK: clusters={r.total_clusters} pairs={len(r.scored_pairs)}')
"
```

Expected: `OK: clusters=...` (some non-zero number, no exception).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/pipeline.py
git commit -m "perf(pipeline): hoist matchkey transforms before block scoring

Calls precompute_matchkey_transforms once per pipeline (after compute_matchkeys,
before build_blocks) so the per-block scorer reads precomputed columns instead
of re-running native chains 7000+ times per dedupe.

Wired into both _run_dedupe_pipeline and _run_match_pipeline."
```

---

## Task 6: Run the full goldenmatch test suite

**Why:** Drop-in compatibility check per spec acceptance. The 1319-test suite (per CLAUDE.md) must stay green modulo the pre-fold ignore list.

- [ ] **Step 1: Run the suite with the ignore list from `goldencheck` package CLAUDE.md**

```bash
uv run pytest packages/python/goldenmatch -n auto \
  --ignore=packages/python/goldenmatch/tests/test_db.py \
  --ignore=packages/python/goldenmatch/tests/test_reconcile.py \
  --ignore=packages/python/goldenmatch/tests/test_mcp_and_watch.py \
  --ignore=packages/python/goldenmatch/tests/test_embedder.py \
  --ignore=packages/python/goldenmatch/tests/test_llm_boost.py \
  2>&1 | tail -20
```

Expected: all pass (or same baseline pass/fail count as `main`). Capture the final `N passed, M failed` line.

- [ ] **Step 2: If new failures appear (failures NOT in the same set as `main`), STOP and triage**

For each new failure: read the error, decide whether it's caused by the precompute change. The fast-path's signature-based lookup is the most likely suspect. If a test exercises a custom matchkey transform not covered by `_try_native_chain`, the fallback path should engage automatically — verify by adding a print of `sig in block_df.columns` to the failing test temporarily.

If all new failures are unrelated (e.g., flaky integration tests), proceed.

- [ ] **Step 3: No commit needed (verification only)**

---

## Task 7: Capture before/after performance numbers

**Why:** Acceptance criterion from spec — `_get_transformed_values` cumulative time drops from ~8.97s to <1s on the 10k synthetic, end-to-end ≥2x.

- [ ] **Step 1: Run the existing profile script on the branch (after-numbers)**

```bash
uv run python .profile_tmp/profile_dedupe.py 2>&1 | tail -50 > .profile_tmp/profile_after.txt
cat .profile_tmp/profile_after.txt
```

Capture from the output:
- `_get_transformed_values` cumtime
- Total wall-clock (the `Total wall: X ms` line)
- `frame.select` cumtime

- [ ] **Step 2: Stash, switch to main, run again for the before-numbers**

```bash
git stash
git checkout main
uv run python .profile_tmp/profile_dedupe.py 2>&1 | tail -50 > .profile_tmp/profile_before.txt
git checkout perf/hoist-matchkey-transforms
git stash pop
```

(`.profile_tmp/` is gitignored, so the `before`/`after` text files are not committed — they only feed the PR description.)

- [ ] **Step 3: Verify acceptance**

The synthetic must show: `_get_transformed_values` cumtime < 1s AND total wall < 5.5s on the branch.

If `_get_transformed_values` doesn't drop substantially: the fast path isn't being hit. Check that `precompute_matchkey_transforms` actually added columns (print `[c for c in combined_lf.collect_schema().names() if c.startswith('__xform_')]` from a debug branch).

If `_get_transformed_values` drops to near-zero but total wall doesn't improve ≥2x: the bottleneck has shifted; report the new top-3 hot functions in the PR for the next item to look at.

- [ ] **Step 4: No commit needed (numbers go in PR description)**

---

## Task 8: Update parent checklist with measured result

**Files:**
- Modify: `docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`

**Why:** Mirror the pattern_consistency entry — hypothesis → measured reality → decision. Keeps future work honest.

- [ ] **Step 1: Replace the placeholder for the now-implemented item**

Find the section under `## Runtime — Engine speed` near the entry currently noting the unmeasured items. Replace whatever bullet currently corresponds to "biggest single runtime win" / scoring path with:

```markdown
- [x] **Hoist matchkey transforms out of per-block scoring** — measured, shipped
  - File: `packages/python/goldenmatch/goldenmatch/core/scorer.py:113` + `core/matchkey.py` + `core/pipeline.py`
  - Hypothesis (cProfile, 2026-05-04): `_get_transformed_values` was 8.97s / 78% of an 11.4s 10k-row dedupe — 7028 redundant Polars `.select()` calls.
  - Reality (measured 2026-05-04 on branch): `_get_transformed_values` <BEFORE>ms → <AFTER>ms (<MULT>x). End-to-end <BEFORE>ms → <AFTER>ms (<MULT>x). See PR #<N>.
  - Decision: shipped. Per-spec `docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md`.
```

(Replace `<BEFORE>`/`<AFTER>`/`<MULT>`/`<N>` with the Task 7 numbers and the eventual PR number — easiest to do this AFTER the PR is open and update the file as a follow-up commit.)

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-05-02-performance-audit-checklist.md
git commit -m "docs(perf-audit): mark hoist-matchkey-transforms shipped with measured numbers"
```

---

## Task 9: Push, open PR, attach numbers

- [ ] **Step 1: Switch gh auth to personal account (per CLAUDE.md)**

```bash
gh auth switch --user benzsevern
gh auth status 2>&1 | grep "Active account"
```

Expected: shows `benzsevern` active.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin perf/hoist-matchkey-transforms
```

- [ ] **Step 3: Open the PR using the captured numbers**

```bash
gh pr create --title "perf(scorer): hoist matchkey transforms out of per-block loop" --body "$(cat <<'EOF'
## Summary

Eliminates ~7000 redundant Polars `frame.select(...)` calls per dedupe by precomputing each unique `(field, transforms)` pair once on the parent DataFrame. Per-block scoring becomes an O(1) column lookup.

cProfile of a representative 10k-row dedupe (before this PR) showed `scorer._get_transformed_values` consuming **8.97s of 11.4s wall (78%)** with 7028 calls. Each call invoked `block_df.select(_try_native_chain(...))` even though the same value was being transformed the same way per block.

This PR adds `precompute_matchkey_transforms` in `core/matchkey.py`, wires it into both pipelines (`_run_dedupe_pipeline`, `_run_match_pipeline`) right after `compute_matchkeys` so its output flows through every `build_blocks` call, and updates `scorer._get_transformed_values` to read precomputed `__xform_<sig>__` columns when present (with the legacy path preserved for callers that bypass the pipeline).

## Numbers (10k synthetic dedupe, `.profile_tmp/profile_dedupe.py`)

| Metric | Before | After | Speedup |
|---|---|---|---|
| `_get_transformed_values` cumtime | <BEFORE>ms | <AFTER>ms | <MULT>x |
| `frame.select` cumtime | <BEFORE>ms | <AFTER>ms | <MULT>x |
| End-to-end wall | <BEFORE>ms | <AFTER>ms | <MULT>x |

## Spec + plan

- Spec: `docs/superpowers/specs/2026-05-04-hoist-matchkey-transforms.md`
- Plan: `docs/superpowers/plans/2026-05-04-hoist-matchkey-transforms.md`

## Test plan

- [ ] CI green
- [ ] All 1319 goldenmatch tests pass (modulo the pre-fold ignore list)
- [ ] 9 new tests added covering: signature determinism, dedup, distinct transforms on same field, native + Python paths, record_embedding skip, empty-transforms skip, end-to-end equivalence, fast-path lookup, fallback path
- [ ] Manual smoke: `gm.dedupe(small_csv, exact=['email'], fuzzy={'name': 0.8}, blocking=['zip'])` returns expected clusters

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Update PR body with the actual numbers from Task 7**

If you couldn't substitute the numbers in the heredoc (Bash escaping issues), use:
```bash
gh pr edit <PR#> --body "$(cat new_body.md)"
```

- [ ] **Step 5: Switch gh auth back to work account**

```bash
gh auth switch --user benzsevern-mjh
```

---

## Acceptance verification (run before requesting merge)

- [ ] **A1:** All tasks completed; branch pushed; PR open.
- [ ] **A2:** `_get_transformed_values` cumulative time drops from ~8.97s to **<1s** on the synthetic 10k workload.
- [ ] **A3:** End-to-end wall improves by **≥2x** (≤5.5s vs 11.4s baseline). If <2x, ship anyway per spec — the structural cleanup is worth it independent of magnitude.
- [ ] **A4:** Full goldenmatch suite passes with no regressions vs main (same baseline failure set, no new failures).
- [ ] **A5:** PR body contains real before/after numbers (not the `<BEFORE>` placeholders).
- [ ] **A6:** Parent checklist updated with the measured result.

---

## Risk reminders during execution

- **`_xform_sig` collision** (theoretical): blake2b 64-bit. ~3e-14 collision probability for <1000 signatures. If anyone trips this, increase `digest_size` to 16. Don't worry about it preemptively.
- **Lazy/eager boundary in pipeline** — the spec's helper takes `pl.DataFrame`. The pipeline call site does `combined_lf.collect()` then `.lazy()` to re-enter the lazy chain. This forces one materialization that wouldn't have happened on `main`. Acceptable cost (single-call op) and observable in the profile if it's not.
- **Custom matchkey transforms** (plugin system, per `goldenmatch/CLAUDE.md`) — the Python fallback path handles these. If a plugin transform mutates external state (it shouldn't — transforms are documented as pure), precomputation could surface latent bugs. Existing test surface protects against this.

---

## Out of scope (explicit)

- The 1.79s `_build_static_blocks` opportunity — separate item.
- Cross-process caching / serialized transform output — adds invalidation surface; not worth it for single-call ops.
- Refactoring the standardize step — different concept (global cleanup vs per-matchkey scoring prep).
- Changing what gets transformed or how — pure plumbing change.
