# #1207 PR1 — Per-identifier blocking-union (weighted auto-config) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On null-sparse multi-source person data, make `build_blocking` emit a per-identifier blocking UNION (`[npi] | [email] | [phone] | [first_name,last_name] | [last_name,zip]`) instead of a single high-null compound that caps recall — lifting candidate-generation recall without touching precision.

**Architecture:** Add a `_build_strong_identifier_union(...)` builder in `core/autoconfig.py` and invoke it inside `build_blocking` at the point where the single-exact-key path has fallen through (no single key passed the strict 0.20 `NULL_RATE_CEILING`) and before the compound fallback. The union reuses the existing `BlockingConfig(strategy="multi_pass", passes=[...])` machinery and the `_gate_passes` / `_is_scale_safe` #715 size guards. Each standalone single-id pass is gated on scale-safety + a minimal non-null population floor (NOT a null ceiling — coverage is restored by the OR across passes); the union is emitted only if its passes' OR-coverage ≥ ~95% of rows.

**Tech Stack:** Python 3.11+, Polars, pytest. No new deps.

**Scope:** PR1 of the #1207 staged rollout (blocking-union only). PR2 (data-driven TF name scorer + precision-anchor controller rule) gets its own plan after PR1 lands green. Spec: `docs/superpowers/specs/2026-06-28-1207-weighted-autoconfig-blocking-tf-anchor-design.md`.

**Default posture:** default-on, test-gated (no opt-in flag). Purely widens candidate generation → recall can only rise, precision is decided downstream by scoring.

---

## Background the engineer needs

- `build_blocking(profiles, df, llm_provider=None, *, n_rows_full=None)` lives in `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (def at `:2317`). It returns a `BlockingConfig` (Pydantic model in `goldenmatch/config/schemas.py`).
- `BlockingConfig` fields used here: `keys: list[BlockingKeyConfig] | None`, `strategy: str` (use `"multi_pass"`), `passes: list[BlockingKeyConfig] | None`, `max_block_size`, `skip_oversized`. A `BlockingKeyConfig` has `fields: list[str]` and `transforms: list[str]`.
- The single-exact-key path is the `if exact_cols:` block (`:2561–2615`). `exact_cols` (`:2404–2410`) is already filtered at `_null_rate ≤ NULL_RATE_CEILING (= 0.20)`. So a 39%-null `npi` / 25%-null `email` are EXCLUDED from this path — it returns only when a *low-null* exact key is both present and `_is_scale_safe`. When no such key exists, `exact_cols` is empty (or `safe_exact` is empty) and the function falls through.
- The fall-through reaches: the text-corpus check (`_is_text_corpus`, `:2627`), then the `_all_single_oversized` compound block (`:2661`, calls `_build_compound_blocking`), then the name multi-pass fallback (`:2701`). `_build_compound_blocking` (`:1254`) admits high-null components at the relaxed `_component_null_ceiling = max(max_null_rate, 0.6)` (`:1318`) — this is what produces the recall-capping `[last_name, npi]`.
- **Insertion point for the union:** immediately AFTER the text-corpus check returns (after `:2635`) and BEFORE the `_all_single_oversized` compound block (`:2661`). At that point `name_cols`, `geo_cols`/`_null_rate`, `_classify_by_name`, `_gate_passes`, `_is_scale_safe`, `_projected_block`, `max_safe_block`, `effective_n_full` are all in scope (they are used by the code immediately below).
- Helpers to reuse (all module-local in `autoconfig.py` unless noted): `_null_rate(col_name)` (`:2340`), `_classify_by_name(name)`, `_gate_passes(primary, passes)` (projects passes to full-N, drops oversized — used at `:2682`), `_is_scale_safe([field])` (`:2582`), `_projected_block([fields])`.
- Person fixtures live in `packages/python/goldenmatch/tests/test_autoconfig_regressions.py` (`_person_df(n)`) and `packages/python/goldenmatch/tests/fixtures/realistic_person.py` (`realistic_person_df(n)`, which loads ~10k census surnames and guarantees soundex spread). **Synthetic surnames MUST distribute across soundex codes** or blocking+scoring hangs (project memory). Reuse `goldenmatch.refdata.surnames` for surname spread.

## Environment / how to run tests

- **DO NOT run the full pytest suite locally** (OOM-prone on this box; the full suite runs in CI). Run only the targeted new test file/nodes.
- Run a single test node from the package dir:
  `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py -v`
  (On this Windows box, prefer the repo `.venv` interpreter directly; `uv run pytest` can miss workspace members.)
- If a polars import hangs, set `POLARS_SKIP_CPU_CHECK=1`. If a stale native wheel errors ("expected N got M"), set `GOLDENMATCH_NATIVE=0`. Set `PYTHONIOENCODING=utf-8` for non-ASCII console output.
- Lint the touched files: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m ruff check goldenmatch/core/autoconfig.py tests/test_autoconfig_blocking_union_1207.py`

## File Structure

- **Modify:** `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` — add `_build_strong_identifier_union(...)` (module-level helper) + a `_union_coverage(...)` helper, and one invocation block inside `build_blocking`.
- **Create:** `packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py` — the fixture + red/green tests.
- No schema change (`BlockingConfig` already supports `multi_pass`/`passes`).

---

### Task 1: Null-sparse multi-source fixture + characterization of today's emission

**Files:**
- Test: `packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py`

- [ ] **Step 1: Write the fixture + a characterization test of CURRENT behavior**

Create the file with a fixture shaped to the issue and a test that asserts *what `build_blocking` does today* (so we can see the fix flip it). Derive the current emission by calling `build_blocking` — do NOT hardcode `[last_name, npi]`; assert the weaker property that today's config is NOT a coverage-complete union.

```python
"""#1207 PR1: per-identifier blocking-union on null-sparse multi-source person data."""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.core.autoconfig import build_blocking
from goldenmatch.core.profile import profile_columns  # profiling entry point
from goldenmatch.refdata import surnames


def _null_sparse_person_df(n: int = 6000, seed: int = 1207) -> pl.DataFrame:
    """Null-sparse multi-source person/provider shape from #1207.

    - npi: highest-cardinality strong id, ~39% null
    - email ~25% null, phone ~71% null, zip ~69% null (none 1:1)
    - common-name collisions: many records share first+last across different npi
    Surnames drawn from the census refdata pool so soundex codes spread
    (else blocking hangs — project invariant).
    """
    import random

    rng = random.Random(seed)
    surnames._load()
    last_pool = [s.title() for s in list(surnames._state.ranks.keys())[:400]]
    first_pool = ["John", "Jane", "Robert", "Mary", "Michael", "Linda",
                  "James", "Patricia", "David", "Jennifer", "William", "Susan"]
    cities = ["Springfield", "Riverton", "Fairview", "Greenville", "Madison"]

    rows = []
    for i in range(n):
        # ~1/3 of records reuse a small (first,last) space to force collisions
        first = rng.choice(first_pool)
        last = rng.choice(last_pool[:30]) if i % 3 == 0 else rng.choice(last_pool)
        npi = None if rng.random() < 0.39 else f"npi{1000000 + i}"
        email = None if rng.random() < 0.25 else f"user{i}@example.com"
        phone = None if rng.random() < 0.71 else f"555{rng.randint(1000000, 9999999)}"
        zipc = None if rng.random() < 0.69 else f"{rng.randint(10000, 99999)}"
        rows.append({
            "first_name": first, "last_name": last, "npi": npi,
            "email": email, "phone": phone, "zip": zipc,
            "city": rng.choice(cities),
        })
    return pl.DataFrame(rows)


def _has_union_over_identifiers(cfg) -> bool:
    """True if cfg is a multi_pass union with >=2 distinct single-id/name passes."""
    if cfg.strategy != "multi_pass" or not cfg.passes:
        return False
    pass_fieldsets = {tuple(p.fields) for p in cfg.passes}
    id_singletons = {("npi",), ("email",), ("phone",)}
    return len(pass_fieldsets & id_singletons) >= 2


def test_characterize_current_emission_is_not_a_union():
    """RED baseline: today build_blocking does NOT emit a per-identifier union
    on this shape (it returns a single-id compound or a name fallback)."""
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    # Document what it actually is, for the record:
    print("CURRENT strategy=", cfg.strategy, "keys=",
          [k.fields for k in (cfg.keys or [])],
          "passes=", [p.fields for p in (cfg.passes or [])])
    assert not _has_union_over_identifiers(cfg)
```

- [ ] **Step 2: Run it to confirm the fixture builds and the baseline holds**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py::test_characterize_current_emission_is_not_a_union -v -s`
Expected: PASS (today emits no union), and the `-s` print shows the actual current strategy/keys (note it — it informs Task 3's recall comparison). If it FAILS because a union already appears, STOP and reconcile with the spec before continuing.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py
git commit -m "test(autoconfig): #1207 null-sparse fixture + characterize current non-union blocking"
```

---

### Task 2: `_build_strong_identifier_union` + `_union_coverage` helpers (TDD)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (add two module-level helpers near `_build_compound_blocking`, ~`:1254`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py`

- [ ] **Step 1: Write the failing unit test for the builder**

Append to the test file:

```python
from goldenmatch.core.autoconfig import (  # noqa: E402
    _build_strong_identifier_union,
    _union_coverage,
)


def test_union_coverage_is_or_over_passes():
    df = _null_sparse_person_df()
    # npi alone ~61%, but npi OR email OR (first,last always present) -> ~100%
    cov = _union_coverage(df, [["npi"], ["email"], ["first_name", "last_name"]])
    assert cov >= 0.95


def test_build_union_includes_high_null_id_passes():
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = _build_strong_identifier_union(profiles, df, n_rows_full=df.height)
    assert cfg is not None
    assert cfg.strategy == "multi_pass"
    fieldsets = {tuple(p.fields) for p in cfg.passes}
    # phone (71% null) and zip (69% null) MUST survive — they're recall-bearing
    # in the union even though each is individually high-null.
    assert ("phone",) in fieldsets
    assert ("npi",) in fieldsets
    assert ("email",) in fieldsets
    # a name+geo pass present for rows missing every strong id
    assert any(p.fields == ["first_name", "last_name"] for p in cfg.passes)


def test_build_union_returns_none_when_no_coverage():
    # A frame with only one sparse id and no name+geo can't reach 95% -> None
    df = pl.DataFrame({"npi": [None, None, "x", None], "note": ["a", "b", "c", "d"]})
    profiles = profile_columns(df)
    assert _build_strong_identifier_union(profiles, df, n_rows_full=df.height) is None
```

- [ ] **Step 2: Run to verify ImportError / failure**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py -k "union" -v`
Expected: FAIL — `ImportError: cannot import name '_build_strong_identifier_union'`.

- [ ] **Step 3: Implement the helpers**

In `autoconfig.py`, add near `_build_compound_blocking` (module level). The builder gates each standalone single-id pass on `_is_scale_safe` + a minimal non-null *population* floor (NOT the 0.6 null ceiling), and emits only if the union's OR-coverage clears the target. Adjust helper names to whatever is actually in scope (`_is_scale_safe`, `_classify_by_name`); these are referenced from inside `build_blocking` today so they exist.

```python
# Strong-identifier blocking-union (#1207). Coverage is restored by the OR
# across passes, so a per-id pass is admitted on scale-safety + a minimal
# non-null population floor (NOT a null ceiling) — that keeps high-null
# phone/zip passes that each block only the rows that *have* that id.
_BLOCKING_UNION_COVERAGE_TARGET = 0.95
_UNION_PASS_MIN_NONNULL = 0.02  # a pass must block more than a trivial handful

_STRONG_ID_TYPES = ("identifier", "email", "phone")


def _union_coverage(df: pl.DataFrame, pass_field_lists: list[list[str]]) -> float:
    """Fraction of rows non-null on at least one pass's fields (OR across passes).
    A multi-field pass requires ALL its fields non-null (it can't block a row
    missing any component)."""
    if df.height == 0:
        return 0.0
    covered = pl.repeat(False, df.height, eager=True)
    for fields in pass_field_lists:
        present = pl.repeat(True, df.height, eager=True)
        for f in fields:
            if f not in df.columns:
                present = pl.repeat(False, df.height, eager=True)
                break
            present = present & df[f].is_not_null()
        covered = covered | present
    return float(covered.sum()) / df.height


def _build_strong_identifier_union(
    profiles: list["ColumnProfile"],
    df: pl.DataFrame,
    *,
    n_rows_full: int | None = None,
) -> "BlockingConfig | None":
    """Emit a multi_pass UNION of one pass per strong id + name+geo, or None.

    Returns None unless >=2 distinct passes survive AND their OR-coverage
    clears _BLOCKING_UNION_COVERAGE_TARGET. Caller (build_blocking) is
    responsible for invoking this only on the fall-through (no single key
    passed the strict 0.20 ceiling)."""
    def _nonnull(col: str) -> float:
        return 1.0 - (df[col].null_count() / df.height) if df.height else 0.0

    candidate_passes: list[list[str]] = []

    # one pass per strong-identifier field, scale-safe + above the population floor
    for p in profiles:
        if p.col_type in _STRONG_ID_TYPES and p.name in df.columns:
            if _nonnull(p.name) < _UNION_PASS_MIN_NONNULL:
                continue
            if not _is_scale_safe([p.name]):
                continue
            candidate_passes.append([p.name])

    # name+geo passes for rows missing every strong id
    name_cols_local = [p for p in profiles if _classify_by_name(p.name) == "name"]
    first = next((p.name for p in name_cols_local if "first" in p.name.lower()), None)
    last = next((p.name for p in name_cols_local if "last" in p.name.lower()
                 or "surname" in p.name.lower()), None)
    geo = next((p.name for p in profiles if p.col_type in ("zip", "geo")), None)
    if first and last:
        candidate_passes.append([first, last])
    if last and geo:
        candidate_passes.append([last, geo])

    if len(candidate_passes) < 2:
        return None
    if _union_coverage(df, candidate_passes) < _BLOCKING_UNION_COVERAGE_TARGET:
        return None

    def _transforms_for(fields: list[str]) -> list[str]:
        # match the email lowercase/strip convention used by the exact path
        prof = next((p for p in profiles if p.name == fields[0]), None)
        return ["lowercase", "strip"] if prof and prof.col_type == "email" else ["strip"]

    passes = [BlockingKeyConfig(fields=f, transforms=_transforms_for(f))
              for f in candidate_passes]
    return BlockingConfig(
        keys=[passes[0]],
        strategy="multi_pass",
        passes=passes,
        skip_oversized=True,
    )
```

NOTE for the implementer: if `_is_scale_safe` / `_classify_by_name` are nested INSIDE `build_blocking` (not module-level), either (a) lift the small ones to module level, or (b) define `_build_strong_identifier_union` as a closure inside `build_blocking` at the insertion point. Prefer (b) if lifting would touch unrelated code — keep the diff minimal. Verify by reading the actual definitions before implementing.

- [ ] **Step 4: Run the union unit tests**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py -k "union" -v`
Expected: PASS (all three union tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py
git commit -m "feat(autoconfig): #1207 _build_strong_identifier_union + _union_coverage helpers"
```

---

### Task 3: Wire the union into `build_blocking` + flip the characterization test green

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (`build_blocking`, insert after the text-corpus check at ~`:2635`, before the `_all_single_oversized` compound block at ~`:2661`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py`

- [ ] **Step 1: Write the failing end-to-end test (the fix's headline assertion)**

Append:

```python
def test_build_blocking_emits_union_on_null_sparse_shape():
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(cfg), (
        f"expected a per-identifier union, got strategy={cfg.strategy} "
        f"keys={[k.fields for k in (cfg.keys or [])]} "
        f"passes={[p.fields for p in (cfg.passes or [])]}"
    )
    # union passes cover ~all rows
    cov = _union_coverage(df, [p.fields for p in cfg.passes])
    assert cov >= 0.95


def test_union_does_not_displace_a_good_single_key():
    """Guard: when a low-null high-card exact key exists, the single-key path
    still wins (we only add the union on the fall-through)."""
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(200)],   # 0% null, unique-ish
        "first_name": ["A"] * 200, "last_name": ["B"] * 200,
    })
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert cfg.strategy != "multi_pass" or {tuple(k.fields) for k in (cfg.keys or [])} == {("email",)}
```

The characterization test from Task 1 (`test_characterize_current_emission_is_not_a_union`) will now START FAILING after Step 3 — that is expected; convert it in Step 4.

- [ ] **Step 2: Run to verify the headline test fails**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py::test_build_blocking_emits_union_on_null_sparse_shape -v`
Expected: FAIL (no union emitted yet).

- [ ] **Step 3: Insert the invocation in `build_blocking`**

After the text-corpus `return text_blk` block (~`:2635`) and before the `# ── Check if name-based fallback would also be oversized ──` comment (~`:2637`), add:

```python
    # #1207: per-identifier blocking-union. We reach here only when no single
    # exact key passed the strict NULL_RATE_CEILING (0.20) gate — exactly the
    # null-sparse multi-source shape where the compound fallback would build a
    # single-strong-id compound ([last_name, npi]) that caps recall at the id's
    # population. Prefer a UNION of one pass per strong id + name+geo, whose
    # OR-coverage restores the population the single key drops. Emitted before
    # the compound fallback so it wins; falls through unchanged when it can't
    # reach the coverage target or <2 passes survive.
    union_cfg = _build_strong_identifier_union(profiles, df, n_rows_full=n_rows_full)
    if union_cfg is not None:
        primary = (union_cfg.keys or [None])[0]
        gated_primary, gated_passes = _gate_passes(primary, union_cfg.passes or [])
        if gated_primary is not None and len(gated_passes) >= 2:
            logger.info(
                "Auto-selecting strong-identifier blocking UNION (%d passes) on "
                "null-sparse data: no single exact key cleared the 0.20 null "
                "ceiling; union OR-coverage restores the dropped population. "
                "See #1207.",
                len(gated_passes),
            )
            return BlockingConfig(
                keys=[gated_primary],
                strategy="multi_pass",
                passes=gated_passes,
                max_block_size=max_safe_block,
                skip_oversized=True,
            )
```

(If `_build_strong_identifier_union` was implemented as a closure in Task 2, call it without re-defining. If `_gate_passes` drops the union below 2 passes, we correctly fall through to the existing compound/name fallbacks.)

- [ ] **Step 4: Convert the Task-1 characterization test to assert the new behavior**

Replace `test_characterize_current_emission_is_not_a_union` body's final assertion:

```python
def test_characterize_current_emission_is_not_a_union():
    """Post-fix: the null-sparse shape now yields a per-identifier union."""
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(cfg)
```

- [ ] **Step 5: Run the whole new test file**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py
git commit -m "feat(autoconfig): #1207 emit per-identifier blocking union on null-sparse data"
```

---

### Task 4: Recall-lift assertion (end-to-end dedupe) + regression guard

**Files:**
- Test: `packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py`

- [ ] **Step 1: Write a recall-comparison test (union vs single compound)**

Add a test that builds candidate pairs under (a) today's compound fallback and (b) the union, and asserts the union's candidate set covers strictly more of the true same-`npi` / same-`email` pairs. Use the blocking machinery directly (`build_blocks` from `core/blocker.py`) rather than a full `dedupe_df` (cheaper, no scorer/model bootstrap). Pseudocode shape — adapt to the real `build_blocks` signature (read `core/blocker.py` first):

```python
def test_union_lifts_blocking_recall_vs_single_compound():
    from goldenmatch.core.blocker import build_blocks  # confirm signature first
    df = _null_sparse_person_df()
    profiles = profile_columns(df)

    union_cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(union_cfg)

    # ground-truth same-entity pairs: share a non-null npi OR non-null email
    # (in this synthetic fixture those are the true-duplicate signals)
    def covered_truth_fraction(cfg) -> float:
        blocks = build_blocks(df, cfg)          # -> candidate pairs / block members
        candidate = _pairs_from_blocks(blocks)  # helper: set of (min,max) row pairs
        truth = _truth_pairs(df)                # helper: same non-null npi/email
        return len(candidate & truth) / max(len(truth), 1)

    # Build the single-strong-id compound the old path would emit, for comparison.
    from goldenmatch.core.autoconfig import _build_compound_blocking
    compound = _build_compound_blocking(profiles, df, max_safe_block=1000, max_null_rate=0.20)

    union_recall = covered_truth_fraction(union_cfg)
    assert union_recall >= 0.95
    if compound is not None:
        assert union_recall >= covered_truth_fraction(compound)
```

Implement `_pairs_from_blocks` and `_truth_pairs` as small local helpers in the test file (canonicalize pairs as `(min, max)` per the project invariant). If `build_blocks`' return shape makes pair extraction awkward, fall back to asserting block MEMBERSHIP coverage (every true-pair's two rows share at least one block) — same recall property, simpler extraction.

- [ ] **Step 2: Run it**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig_blocking_union_1207.py::test_union_lifts_blocking_recall_vs_single_compound -v`
Expected: PASS (union recall ≥ 0.95 and ≥ compound recall).

- [ ] **Step 3: Run the existing blocking regression tests (targeted, not full suite)**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_autoconfig.py tests/test_autoconfig_regressions.py tests/test_autoconfig_blocking_cost_715.py tests/test_autoconfig_adaptive_blocking.py -q`
Expected: PASS (no regressions). If any fail, the union is firing in a case it shouldn't — tighten the trigger (it must only fire on the fall-through, i.e. when the single-key path didn't return) and re-run. Pay special attention to #715/#876 scale-safety tests.

- [ ] **Step 4: Lint**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m ruff check goldenmatch/core/autoconfig.py tests/test_autoconfig_blocking_union_1207.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/tests/test_autoconfig_blocking_union_1207.py
git commit -m "test(autoconfig): #1207 union lifts blocking recall vs single compound + regression guard"
```

---

### Task 5: TS-parity check + docs touch + PR

**Files:**
- Check (likely no change): `packages/typescript/goldenmatch/` blocking auto-config
- Modify: `packages/python/goldenmatch/CHANGELOG.md`
- Possibly modify: package `CLAUDE.md` Auto-Config section (one bullet)

- [ ] **Step 1: Decide TS parity scope**

The TS port mirrors Python scorers/algorithms. Check whether the TS port has an `auto_configure`/`build_blocking` equivalent: `grep -ri "build_blocking\|auto_configure" packages/typescript/goldenmatch/src`. If it does NOT implement blocking auto-config (likely — TS port is scorer/edge-focused), record "no TS parity needed for PR1 (blocking auto-config is Python-only)" in the PR description and skip. If it DOES, add a parity case under `packages/typescript/goldenmatch/tests/parity/` and port the union trigger — but only after confirming the Python behavior is final. Do NOT build a TS toolchain locally (CI typechecks).

- [ ] **Step 2: CHANGELOG entry**

Add a dated entry under the unreleased/next section of `packages/python/goldenmatch/CHANGELOG.md` describing the #1207 PR1 blocking-union (default-on; recall lift on null-sparse multi-source data; no precision/behavior change when a low-null single key exists).

- [ ] **Step 3: One-bullet CLAUDE.md note (optional but encouraged)**

Add a bullet to the `## Auto-Config` section of `packages/python/goldenmatch/CLAUDE.md` noting `_build_strong_identifier_union` fires on the no-single-key fall-through, emits a multi_pass union of per-id + name+geo passes gated on union-level 0.95 OR-coverage, default-on, see #1207. (Keep it one line; the full docs sweep is the PR2/rollout-docs-sweep job.)

- [ ] **Step 4: Commit + push + open PR**

```bash
git add packages/python/goldenmatch/CHANGELOG.md packages/python/goldenmatch/CLAUDE.md
git commit -m "docs(autoconfig): changelog + note for #1207 PR1 blocking union"
# auth: this repo uses the benzsevern account (unset GH_TOKEN if it overrides)
git push -u origin feat/1207-autoconfig-blocking-union
gh pr create --fill --title "feat(autoconfig): #1207 PR1 per-identifier blocking union (recall on null-sparse data)" \
  --body "Closes part of #1207 (PR1 of 2). Spec: docs/superpowers/specs/2026-06-28-1207-weighted-autoconfig-blocking-tf-anchor-design.md"
```

- [ ] **Step 5: Arm auto-merge, then STOP**

```bash
gh pr merge --auto --squash
```
Do NOT sit in a CI poll loop (project convention). The merge queue runs the full matrix and merges on green. PR2 (TF scorer + precision anchor) starts after PR1 is green on main.

---

## Done criteria for PR1

- `build_blocking` emits a per-identifier `multi_pass` union on the null-sparse fixture; the single-key path is unchanged when a low-null key exists.
- Union OR-coverage ≥ 95% and blocking recall ≥ the single compound's on the fixture.
- New test file green; targeted blocking regression tests green; ruff clean.
- CHANGELOG + one CLAUDE.md bullet updated; PR opened with auto-merge armed.
