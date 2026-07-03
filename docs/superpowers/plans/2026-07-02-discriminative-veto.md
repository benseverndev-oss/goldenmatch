# Discriminative-power veto for exact matchkeys (#1351) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `AutoConfigController` from committing a standalone `exact` matchkey on high-density locality columns (e.g. `zip`) by vetoing any proposed exact key whose shared-value records don't co-agree on other identity fields — without regressing genuine identity keys (`npi`, `email`).

**Architecture:** A new, isolated estimator module computes each candidate exact key's *discriminative power* (mean co-agreement of shared-value record pairs across a basket of *other identity-typed* columns). `build_matchkeys` calls a veto helper at the exact-matchkey gate: demote (skip) the exact key when support is sufficient AND co-agreement is below a threshold. Veto-only — never promotes; leaves classification and blocking untouched. Fail-safe = keep on thin evidence, so near-unique identity keys (few shared-value pairs) are auto-protected.

**Tech Stack:** Python, Polars, pytest. Target file `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`; new module `autoconfig_discriminative.py`. Spec: `docs/superpowers/specs/2026-07-02-discriminative-power-veto-design.md`.

**Hard environment constraints:**
- Test runner: `UV=/c/Users/bsevern/AppData/Local/Programs/Python/Python312/Scripts/uv.exe && "$UV" run --package goldenmatch --extra dev python -m pytest <files> -q`
- Local quality gate (Windows needs utf-8): `GOLDENMATCH_AUTOCONFIG_MEMORY=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$UV" run python -m scripts.autoconfig_quality gate` (run from repo root, ~1 min).
- **NEVER** run the full pytest suite, `test_autoconfig_benchmarks.py`, `dedupe_df` on large data, or native builds — they OOM this machine. Only targeted unit tests + the quality gate.
- If `uv.lock` drifts from `uv run`, `git checkout -- uv.lock` (don't commit it).
- Lint: `"$UV" run --package goldenmatch --extra dev ruff check <files>` and `"$UV" run pyright` (autoconfig.py is in the pyright slice — the new module should be too or kept type-clean).

---

## File Structure

- **Create:** `packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py` — the estimator + veto decision. One responsibility: "should this proposed exact key be vetoed?" Pure functions over a Polars frame + the profile list. No imports from `autoconfig.py` (avoid cycles); it may import `ColumnProfile` only if needed via `TYPE_CHECKING`.
- **Modify:** `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` — call the veto helper inside `build_matchkeys`'s exact-matchkey gate loop (~line 1108, alongside the floor/surrogate gates). ~5 lines.
- **Create:** `packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py` — unit tests for the estimator + veto decision.
- **Create:** `packages/python/goldenmatch/tests/test_build_matchkeys_veto_1351.py` — build_matchkeys-level tests (zip vetoed, npi kept, df=None no-op).

Basket taxonomy (from the classifier `col_type` set `{email, name, multi_name, phone, zip, address, geo, identifier, description, numeric, date, string, year}`):
- **Identity basket (co-agree against):** `{name, multi_name, email, phone, identifier}`.
- **Excluded** (locality/attribute/non-discriminative): everything else, notably `{zip, geo, address}`.

---

## Task 1: Estimator module — constants, env knobs, `identity_basket`

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py`

- [ ] **Step 1: Write failing tests for `identity_basket`**

```python
# test_autoconfig_discriminative_1351.py
from dataclasses import dataclass
from goldenmatch.core import autoconfig_discriminative as disc


@dataclass
class _P:  # minimal stand-in for ColumnProfile (only .name/.col_type used)
    name: str
    col_type: str


def test_identity_basket_includes_identity_types_excludes_locality():
    profiles = [
        _P("zip", "zip"), _P("first_name", "name"), _P("last_name", "name"),
        _P("email", "email"), _P("npi", "identifier"), _P("city", "geo"),
        _P("notes", "description"),
    ]
    basket = disc.identity_basket("zip", profiles)
    assert set(basket) == {"first_name", "last_name", "email", "npi"}
    # candidate itself excluded; geo/description excluded
    assert "zip" not in basket and "city" not in basket and "notes" not in basket


def test_identity_basket_excludes_the_candidate_even_if_identity_typed():
    profiles = [_P("npi", "identifier"), _P("email", "email")]
    assert disc.identity_basket("npi", profiles) == ["email"]
```

- [ ] **Step 2: Run to verify fail**

Run: `"$UV" run --package goldenmatch --extra dev python -m pytest packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py -q`
Expected: FAIL (module / function not found).

- [ ] **Step 3: Create the module skeleton + `identity_basket`**

```python
# packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py
"""Discriminative-power veto for exact/identity matchkeys (#1351).

Cardinality cannot separate a shared identity key (``npi``: records sharing a
value are the SAME entity) from a shared locality attribute (``zip``: records
sharing a value are DIFFERENT people in one area) -- both have moderate
cardinality. This module measures, from the data, whether records that SHARE a
candidate value also AGREE on other identity fields. Low co-agreement => the
value is a locality/attribute, not an identity key => veto its standalone exact
matchkey (demote to blocking-only). Veto-only; never promotes.
"""
from __future__ import annotations

import os
from typing import Any

import polars as pl

# col_type values that are identity signals we co-agree AGAINST. Locality /
# attribute / non-discriminative types are excluded -- including zip/geo/address
# would let same-locality pairs spuriously co-agree and defeat the veto.
_IDENTITY_BASKET_TYPES = frozenset({"name", "multi_name", "email", "phone", "identifier"})

_TAU_DEFAULT = 0.5
_MIN_SHARED_PAIRS = 20
_MAX_PAIRS = 200


def veto_enabled() -> bool:
    """Kill-switch: GOLDENMATCH_DISCRIMINATIVE_VETO=0 disables the veto."""
    return os.environ.get("GOLDENMATCH_DISCRIMINATIVE_VETO", "1") != "0"


def tau() -> float:
    """Co-agreement floor below which an exact key is vetoed (env-overridable)."""
    raw = os.environ.get("GOLDENMATCH_DISCRIMINATIVE_TAU")
    if raw is None:
        return _TAU_DEFAULT
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return _TAU_DEFAULT
    return val if 0.0 <= val <= 1.0 else _TAU_DEFAULT


def identity_basket(candidate_col: str, profiles: list[Any]) -> list[str]:
    """Other columns whose col_type is an identity signal (excludes candidate).

    ``profiles`` items need only ``.name`` and ``.col_type`` attributes.
    """
    return [
        p.name
        for p in profiles
        if p.name != candidate_col and getattr(p, "col_type", None) in _IDENTITY_BASKET_TYPES
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: same pytest command as Step 2. Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py
git commit -m "feat(autoconfig): discriminative-veto module skeleton + identity_basket (#1351)"
```

---

## Task 2: `discriminative_power` estimator

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py`

- [ ] **Step 1: Write failing tests**

```python
import polars as pl
from goldenmatch.core import autoconfig_discriminative as disc


def _zip_like_df(n=300):
    # 30 zips x 10 rows each; each row a DIFFERENT person (names all unique)
    zips = [f"{10000 + (i % 30):05d}" for i in range(n)]
    names = [f"person{i}" for i in range(n)]   # unique -> shared-zip pairs disagree
    return pl.DataFrame({"zip": zips, "name": names})


def _npi_like_df(n=300):
    # 30 providers x 10 rows each; rows for the SAME npi share the SAME name
    npis = [f"{1000000000 + (i % 30)}" for i in range(n)]
    names = [f"provider{i % 30}" for i in range(n)]  # shared-npi pairs agree
    return pl.DataFrame({"npi": npis, "name": names})


def test_discriminative_power_low_for_shared_locality():
    df = _zip_like_df()
    power, support = disc.discriminative_power(df, "zip", ["name"])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power < 0.5


def test_discriminative_power_high_for_shared_identity():
    df = _npi_like_df()
    power, support = disc.discriminative_power(df, "npi", ["name"])
    assert support >= disc._MIN_SHARED_PAIRS
    assert power > 0.9


def test_discriminative_power_zero_support_when_all_unique():
    df = pl.DataFrame({"id": [str(i) for i in range(100)], "name": [f"n{i}" for i in range(100)]})
    power, support = disc.discriminative_power(df, "id", ["name"])
    assert support == 0  # no value shared -> no pairs to measure


def test_discriminative_power_empty_basket_zero():
    df = _zip_like_df()
    power, support = disc.discriminative_power(df, "zip", [])
    assert (power, support) == (0.0, 0)
```

- [ ] **Step 2: Run to verify fail**

Run: `"$UV" run --package goldenmatch --extra dev python -m pytest packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py -q`
Expected: FAIL (`discriminative_power` not defined).

- [ ] **Step 3: Implement `discriminative_power`**

```python
def _norm(v: Any) -> str | None:
    """Normalize a cell for equality: str -> stripped lower; blank/None -> None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def discriminative_power(
    df: pl.DataFrame,
    candidate_col: str,
    basket: list[str],
    *,
    max_pairs: int = _MAX_PAIRS,
) -> tuple[float, int]:
    """Mean co-agreement over shared-value pairs, and the support (n pairs measured).

    Groups ``df`` by ``candidate_col``; for value-groups with >=2 rows, forms up
    to ``max_pairs`` record-pairs deterministically (row 0 paired with rows
    1..k-1 within each group, groups visited in sorted-value order). For each
    pair, agreement = (# basket fields where BOTH cells are non-null and
    normalized-equal) / (# basket fields where BOTH are non-null); pairs with no
    jointly-populated basket field are skipped. Returns (mean agreement over
    measured pairs, count of measured pairs). (0.0, 0) if basket empty, candidate
    absent, or no measurable shared-value pair exists.
    """
    if not basket or candidate_col not in df.columns:
        return 0.0, 0
    keep = [candidate_col, *[c for c in basket if c in df.columns]]
    if len(keep) < 2:
        return 0.0, 0
    sub = df.select(keep)
    basket_cols = keep[1:]

    # group rows (as normalized candidate value -> list of row dicts), skipping
    # null/blank candidate values; deterministic sorted-value iteration.
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sub.iter_rows(named=True):
        cv = _norm(row[candidate_col])
        if cv is None:
            continue
        groups.setdefault(cv, []).append(row)

    total = 0.0
    measured = 0
    for cv in sorted(groups):
        rows = groups[cv]
        if len(rows) < 2 or measured >= max_pairs:
            continue
        anchor = rows[0]
        for other in rows[1:]:
            if measured >= max_pairs:
                break
            agree = 0
            comparable = 0
            for c in basket_cols:
                a, b = _norm(anchor[c]), _norm(other[c])
                if a is None or b is None:
                    continue
                comparable += 1
                if a == b:
                    agree += 1
            if comparable == 0:
                continue
            total += agree / comparable
            measured += 1
    if measured == 0:
        return 0.0, 0
    return total / measured, measured
```

- [ ] **Step 4: Run to verify pass**

Run: same pytest command. Expected: all pass (the earlier basket tests too).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py
git commit -m "feat(autoconfig): discriminative_power co-agreement estimator (#1351)"
```

---

## Task 3: `should_veto_exact` decision (kill-switch, support, tau, df=None)

**Files:**
- Modify: `autoconfig_discriminative.py`
- Test: `test_autoconfig_discriminative_1351.py`

- [ ] **Step 1: Write failing tests**

```python
def test_should_veto_zip_true():
    df = _zip_like_df()
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(df, "zip", profiles) is True


def test_should_veto_npi_false():
    df = _npi_like_df()
    profiles = [_P("npi", "identifier"), _P("name", "name")]
    assert disc.should_veto_exact(df, "npi", profiles) is False


def test_should_veto_thin_support_false():
    # all-unique candidate -> no shared pairs -> insufficient support -> keep
    df = pl.DataFrame({"id": [str(i) for i in range(100)], "name": [f"n{i}" for i in range(100)]})
    profiles = [_P("id", "identifier"), _P("name", "name")]
    assert disc.should_veto_exact(df, "id", profiles) is False


def test_should_veto_df_none_false():
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(None, "zip", profiles) is False


def test_should_veto_empty_basket_false():
    df = _zip_like_df()
    profiles = [_P("zip", "zip")]  # no identity columns -> empty basket
    assert disc.should_veto_exact(df, "zip", profiles) is False


def test_should_veto_kill_switch_false(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_DISCRIMINATIVE_VETO", "0")
    df = _zip_like_df()
    profiles = [_P("zip", "zip"), _P("name", "name")]
    assert disc.should_veto_exact(df, "zip", profiles) is False
```

(`_P` is the dataclass from Task 1; ensure `_zip_like_df`/`_npi_like_df` are module-level in the test file.)

- [ ] **Step 2: Run to verify fail**

Expected: FAIL (`should_veto_exact` not defined).

- [ ] **Step 3: Implement `should_veto_exact`**

```python
def should_veto_exact(
    df: pl.DataFrame | None,
    candidate_col: str,
    profiles: list[Any],
    *,
    min_shared_pairs: int = _MIN_SHARED_PAIRS,
    max_pairs: int = _MAX_PAIRS,
) -> bool:
    """True => demote the proposed standalone exact matchkey on ``candidate_col``.

    Fail-safe = keep (return False) on: kill-switch off, ``df is None``, empty
    identity basket, or insufficient shared-value support. Only vetoes a
    high-density column whose shared-value pairs measurably fail to co-agree on
    other identity fields (support >= min_shared_pairs AND power < tau()).
    """
    if not veto_enabled() or df is None:
        return False
    basket = identity_basket(candidate_col, profiles)
    if not basket:
        return False
    power, support = discriminative_power(df, candidate_col, basket, max_pairs=max_pairs)
    if support < min_shared_pairs:
        return False
    return power < tau()
```

- [ ] **Step 4: Run to verify pass**

Expected: all tests in the file pass.

- [ ] **Step 5: ruff + pyright on the new module**

Run: `"$UV" run --package goldenmatch --extra dev ruff check packages/python/goldenmatch/goldenmatch/core/autoconfig_discriminative.py` and `"$UV" run pyright` (add the new module to `pyrightconfig.json` `include` if the slice is enforced and it isn't picked up; keep it type-clean either way).
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(autoconfig): should_veto_exact decision + fail-safes (#1351)"
```

---

## Task 4: Wire the veto into `build_matchkeys`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py` (exact-matchkey gate loop, ~line 1108, near the `_exact_floor` gate and the `>= 1.0` surrogate gate)
- Test: `packages/python/goldenmatch/tests/test_build_matchkeys_veto_1351.py`

**Context:** In `build_matchkeys(profiles, df=None, *, multi_source=False)`, the per-field loop appends columns to `exact_fields` after passing the zip/geo guard (~948, `col_type in ("zip","geo")`), the `_exact_floor` gate (~966), and the `>= 1.0` surrogate gate (~993–1002). **Insert the veto directly after the surrogate gate (~line 1002)** — before the intervening multi_source name-demotion block and the `mf`/`tf_freqs` construction, and before the `if scorer == "exact": exact_fields.append(mf)` record (~1058). Placing it there means the `continue` fires before `mf` is built, and `p`, `scorer`, `profiles`, `df`, `skipped_exact` are all in scope. Call `should_veto_exact(df, p.name, profiles)`; if True, append to `skipped_exact` with a reason and `continue` (mirror the three adjacent skip branches byte-for-byte). Import at top: `from goldenmatch.core.autoconfig_discriminative import should_veto_exact`.

**Note (perf, non-blocking):** the estimator groups rows via `iter_rows` into Python dicts over the whole `df` before capping pairs — O(rows). Fine for the controller's ~1.5k-row sample and the tiny test fixtures; if a future caller passes a large frame, cap/sample rows before grouping.

- [ ] **Step 1: Write failing test at the build_matchkeys level**

```python
# test_build_matchkeys_veto_1351.py
import polars as pl
from goldenmatch.core.autoconfig import build_matchkeys, ColumnProfile


def _exact_fields(mks):
    return {f.field for mk in mks if mk.type == "exact" for f in mk.fields if f.field}


def _mk_profiles(specs):  # specs: list[(name, col_type, cardinality_ratio)]
    return [
        ColumnProfile(name=n, dtype="str", col_type=t, confidence=0.9,
                      sample_values=["x"], null_rate=0.0, cardinality_ratio=c, avg_len=6.0)
        for (n, t, c) in specs
    ]


def test_zip_promoted_to_identifier_is_vetoed_from_exact():
    # zip mis-typed as identifier (the sample-inflation reclassification), high
    # sample cardinality so it would clear the exact floor; df shows shared zips
    # whose sharers DISagree on name -> veto.
    profiles = _mk_profiles([("zip", "identifier", 0.96), ("first_name", "name", 0.9),
                             ("last_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "zip": [f"{10000 + (i % 40):05d}" for i in range(n)],
        "first_name": [f"fn{i}" for i in range(n)],
        "last_name": [f"ln{i}" for i in range(n)],
    })
    assert "zip" not in _exact_fields(build_matchkeys(profiles, df=df))


def test_npi_identifier_is_kept_as_exact():
    profiles = _mk_profiles([("npi", "identifier", 0.9), ("first_name", "name", 0.9)])
    n = 400
    df = pl.DataFrame({
        "npi": [f"{1000000000 + (i % 40)}" for i in range(n)],
        "first_name": [f"fn{i % 40}" for i in range(n)],  # shared-npi sharers agree
    })
    assert "npi" in _exact_fields(build_matchkeys(profiles, df=df))


def test_df_none_is_noop_keep():
    # with no df the veto can't run; behavior identical to pre-#1351
    profiles = _mk_profiles([("email", "identifier", 0.9)])
    mks = build_matchkeys(profiles, df=None)
    assert "email" in _exact_fields(mks)
```

- [ ] **Step 2: Run to verify fail**

Run: `"$UV" run --package goldenmatch --extra dev python -m pytest packages/python/goldenmatch/tests/test_build_matchkeys_veto_1351.py -q`
Expected: `test_zip_...` FAILS (zip currently emitted as exact); the npi/df-none tests may already pass.

- [ ] **Step 3: Add the veto to the exact-field gate loop**

In `autoconfig.py`, add the import near the other `goldenmatch.core.*` imports, then in the per-field loop, immediately AFTER the `>= 1.0` surrogate-key gate and BEFORE the column is recorded as an exact field, insert:

```python
        # #1351: discriminative-power veto. A column that clears the cardinality
        # gates can still be a shared LOCALITY attribute (e.g. a zip mis-promoted
        # to "identifier") rather than an identity key. Veto its exact matchkey
        # when records sharing its value don't co-agree on other identity fields.
        # Fail-safe keep (df is None / thin support / empty basket) is handled
        # inside should_veto_exact, so near-unique identity keys are unaffected.
        if scorer == "exact" and should_veto_exact(df, p.name, profiles):
            reason = "discriminative-power veto: shared-value records do not co-agree on identity fields"
            logger.warning("Skipping exact matchkey for '%s' (%s).", p.name, reason)
            skipped_exact.append((p.name, reason))
            continue
```

(Match the exact variable names in that loop — `p`, `scorer`, `skipped_exact`. If the loop uses a different accumulation than `skipped_exact`, mirror whatever the adjacent floor/surrogate skip branches do.)

- [ ] **Step 4: Run to verify pass**

Run: same as Step 2. Expected: all 3 pass.

- [ ] **Step 5: Confirm no regression on the existing exact-matchkey unit tests**

Run: `"$UV" run --package goldenmatch --extra dev python -m pytest packages/python/goldenmatch/tests/test_exact_matchkey_floor_s3.py packages/python/goldenmatch/tests/test_autoconfig_pincer_715.py packages/python/goldenmatch/tests/test_autoconfig.py -q`
Expected: all pass, none modified. (These build profiles with `df=None` or fully-distinct `_df_with`, so the veto is a no-op keep for them.)

- [ ] **Step 6: ruff + pyright**

Run ruff + pyright on `autoconfig.py`. Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat(autoconfig): wire discriminative-power veto into build_matchkeys (#1351)"
```

---

## Task 5: Validate + tune against the local quality gate

**Files:** none (validation + possible constant tuning in `autoconfig_discriminative.py`)

- [ ] **Step 1: Run the local quality gate**

Run (from repo root `D:\ER\gm-1351`):
```
GOLDENMATCH_AUTOCONFIG_MEMORY=0 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "$UV" run python -m scripts.autoconfig_quality gate 2>&1 | grep -vE "score_buckets|bucket_score|keyed|bucketed|partition|ready for scoring|starting bucket" | tail -50
```
Expected target: `verdict: PASS`. Specifically:
- `anchor_sparse_zip`: `exact_matchkeys` stays `['email', 'npi']`, `classification.npi` / `classification.phone_number` unchanged (still `identifier`), blocking unchanged.
- `ncvr_synthetic` / `historical_50k`: F1 within tolerance of the main baseline (no regression).

- [ ] **Step 2: If any FAIL, diagnose and tune**

- If a genuine identity key is vetoed (e.g. an anchor's `npi`/`email` disappears from `exact_matchkeys`): its shared-value pairs are under-agreeing or support is over-counted. Options in order of preference: raise `_MIN_SHARED_PAIRS` (require more evidence), lower `tau` (require stronger disagreement to veto), or check the basket (are other identity columns being mis-typed, shrinking the basket?). Adjust the module constant, re-run.
- If `zip` is NOT vetoed where expected: lower `_MIN_SHARED_PAIRS` or raise `tau`, or confirm the basket is non-empty for that dataset.
- Re-run the gate after each change. Do NOT loosen a real regression away — the goal is `anchor` identity keys kept AND `zip`-class vetoed.

- [ ] **Step 3: Commit any tuning**

```bash
git checkout -- uv.lock 2>/dev/null; git add -A && git commit -m "chore(autoconfig): tune discriminative-veto thresholds against quality gate (#1351)"
```
(Skip the commit if no tuning was needed.)

---

## Task 6: Regression anchor for the DERM zip shape (stretch — inspect harness first)

**Files:**
- Investigate: `scripts/autoconfig_quality/` (anchor definitions + fixture format)
- Possibly Create/Modify: an anchor fixture + expectation

- [ ] **Step 1: Inspect how anchors are defined**

Read `scripts/autoconfig_quality/` to find where anchors (e.g. `anchor_sparse_zip`) and their expected metrics live. Determine whether a new anchor is a fixture file + an expectations entry, and whether it runs within the gate's fast budget.

- [ ] **Step 2: If cheap to add, add a `anchor_zip_dense` anchor**

A synthetic person dataset (a few thousand rows) with a `zip` column dense enough that the promotion would emit `exact[zip]` absent the veto, plus name/email/npi columns. Expectation: `zip` NOT in `exact_matchkeys`; F1 not degenerate (no mega-cluster). This regression-protects the fix going forward.

- [ ] **Step 3: Run the gate; confirm the new anchor passes and others are unaffected**

Same gate command as Task 5.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "test(autoconfig): add zip-dense regression anchor for the discriminative veto (#1351)"
```

> If the anchor harness is non-trivial to extend within the time box, STOP and note it — the unit tests (Task 4) + the existing gate passing (Task 5) are sufficient to ship; the anchor is protective, not blocking.

---

## Task 7: Final verification + PR

- [ ] **Step 1: Full targeted test sweep (NOT the whole suite)**

Run:
```
"$UV" run --package goldenmatch --extra dev python -m pytest \
  packages/python/goldenmatch/tests/test_autoconfig_discriminative_1351.py \
  packages/python/goldenmatch/tests/test_build_matchkeys_veto_1351.py \
  packages/python/goldenmatch/tests/test_exact_matchkey_floor_s3.py \
  packages/python/goldenmatch/tests/test_autoconfig_pincer_715.py \
  packages/python/goldenmatch/tests/test_autoconfig.py -q
```
Expected: all pass.

- [ ] **Step 2: ruff + pyright clean; quality gate PASS (re-run Task 5 Step 1).**

- [ ] **Step 3: Revert any uv.lock drift, push, open PR**

```bash
git checkout -- uv.lock 2>/dev/null
git push -u origin fix/1351-discriminative-veto
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "fix(autoconfig): discriminative-power veto for exact matchkeys (#1351)" \
  --body "<summary: veto-only co-agreement gate; zip vetoed, npi kept; quality gate PASS; CI accuracy gates are final validation>"
```

- [ ] **Step 4: Watch CI (esp. the accuracy gates / quality_gate + pyright) to green before requesting merge.**

---

## Notes for the implementer
- Reference @superpowers:subagent-driven-development for execution discipline.
- The veto is intentionally narrow: it removes only the standalone `exact` matchkey. It must not touch `col_type`, blocking, or composite matchkeys.
- Determinism: the estimator iterates value-groups in sorted order and pairs deterministically — no RNG — so the quality gate is reproducible.
- Every `uv run` may drift `uv.lock`; always `git checkout -- uv.lock` before committing.
- The DERM real dataset is NOT used in any test (PII + 19k rows OOM risk). All fixtures are tiny synthetic frames.
