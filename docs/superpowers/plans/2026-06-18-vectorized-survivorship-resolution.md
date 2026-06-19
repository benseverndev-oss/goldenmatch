# Vectorized Survivorship Resolution Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-cluster Python survivorship loop with a vectorized polars path (`_build_golden_records_survivorship_native`, `provenance=False`) that is BYTE-IDENTICAL to the slow path, closing the measured 19-20x gap.

**Architecture:** The slow path stays as the correctness ORACLE and the fallback. The new path vectorizes groups (per-cluster sort by strategy-rank + `first`/`drop_nulls().first()` per group column = lock-step / allow_fill), scalars (extend the existing `_build_golden_records_polars_native` aggregate machinery), and resolves only conditional fields in a tiny per-cluster loop. Every phase is gated by a PARITY TEST comparing the new path against the slow oracle (run on a `__row_id__`-pre-sorted frame for determinism). The new path is gated in only when survivorship-active AND `provenance=False` AND all levers are supported.

**Tech Stack:** Python 3.11+, Polars (group_by/agg, sort, window), pytest. Spec: `docs/superpowers/specs/2026-06-18-vectorized-survivorship-resolution-design.md`.

**Dependency:** Stacks on the merged survivorship feature set (v1 + groups/conditional/allow_fill/anchor + the bench #1057/#1059). Branch off fresh `origin/main`.

---

## Conventions for every task

- **Run tests** (targeted local; the SCALE bench is CI-only): `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <repo>/.venv/Scripts/python.exe -m pytest <path> -v` (set `GOLDENMATCH_NATIVE=0` if a stale wheel interferes).
- **Commit** after each green step. Squash-merge via PR at the end.
- **No em dashes / ASCII only** in committed strings.
- New tests in `packages/python/goldenmatch/tests/survivorship/`.
- **The parity test IS the spec for each strategy** — "make polars match the slow oracle." Implement against it; iterate to byte-identity.

---

## File Structure

**New files**
- `packages/python/goldenmatch/goldenmatch/core/survivorship/native.py` — `survivorship_native_eligible`, `build_survivorship_native` (the vectorized path), the group/scalar/confidence/conditional helpers. (Kept out of `golden.py` to avoid bloating it; mirrors the `survivorship/` package owning the slow path.)
- `packages/python/goldenmatch/tests/survivorship/test_native_parity.py` — the parity harness + per-phase parity tests.

**Modified files**
- `packages/python/goldenmatch/goldenmatch/core/golden.py` — add the gate dispatch before the slow branch (~L884).
- Docs: `docs-site/goldenmatch/tuning.mdx` / configuration (note the vectorized path + provenance=True fallback).

---

## Task 0: Branch setup
- [ ] Branch off fresh `origin/main`: `git fetch origin && git switch -c feat/vectorized-survivorship origin/main`. Sanity: `grep -n "def _build_golden_records_polars_native\|def _survivorship_active\|def build_golden_records_batch" packages/python/goldenmatch/goldenmatch/core/golden.py`; `grep -n "def group_winner\|def _ranking" packages/python/goldenmatch/goldenmatch/core/survivorship/winner.py`.

---

## Phase A — parity harness + gate skeleton (no behavior change)

### Task A1: the parity harness + gate skeleton (eligible=False)

**Files:** Create `core/survivorship/native.py`; Test: `tests/survivorship/test_native_parity.py`

- [ ] **Step 1: Write the parity harness + a no-op gate test**
```python
# tests/survivorship/test_native_parity.py
import polars as pl
from goldenmatch.core.golden import build_golden_records_batch, _is_internal
from goldenmatch.core.survivorship.native import survivorship_native_eligible


def _slow_oracle(multi_df, rules):
    """The slow path on a __row_id__-deterministic frame (the canonical tie-break),
    returning the golden DataFrame (values only; provenance=False)."""
    df = multi_df.sort(["__cluster_id__", "__row_id__"])   # pin within-cluster order (spec Section 2)
    rows = build_golden_records_batch(df, rules, provenance=False)   # slow branch (survivorship)
    golden = []
    for rec in rows:
        row = {"__cluster_id__": rec["__cluster_id__"],
               "__golden_confidence__": rec.get("__golden_confidence__")}
        for col, info in rec.items():
            if col in ("__cluster_id__", "__golden_confidence__", "__survivorship_prov__"):
                continue
            row[col] = info["value"] if isinstance(info, dict) and "value" in info else info
        golden.append(row)
    return pl.DataFrame(golden).sort("__cluster_id__")


def assert_parity(multi_df, rules):
    """Byte-identical golden output: native path == slow oracle (provenance=False)."""
    from goldenmatch.core.survivorship.native import build_survivorship_native
    native = build_survivorship_native(multi_df, rules)   # returns pl.DataFrame (golden)
    oracle = _slow_oracle(multi_df, rules)
    cols = sorted(oracle.columns)
    assert native.sort("__cluster_id__").select(cols).equals(oracle.select(cols)), (
        f"PARITY MISMATCH\nnative:\n{native.sort('__cluster_id__').select(cols)}\noracle:\n{oracle.select(cols)}"
    )


def test_eligible_false_until_implemented():
    rules = ...  # a simple field_groups config
    assert survivorship_native_eligible(rules, provenance=False) is False  # gate off until Phase F
```

- [ ] **Step 2: Run to verify it fails** (module missing).
- [ ] **Step 3: Implement** `native.py` skeleton: `survivorship_native_eligible(rules, provenance) -> bool` returns `False` for now (flipped on in Phase F), and a stub `build_survivorship_native(multi_df, rules)` that, for now, raises `NotImplementedError` (the parity harness `assert_parity` is unused until Phase B). Add NOTHING to `golden.py` yet (gate off).
- [ ] **Step 4: Run to verify pass** (the eligible test).
- [ ] **Step 5: Commit** `feat(survivorship): native path skeleton + parity harness`.

### Task A2: make the slow path deterministic (PARITY ORACLE PREREQUISITE)

**Files:** Modify `core/golden.py` (the survivorship slow branch ~L888); Test: `test_native_parity.py`

> Load-bearing: the slow path's group tie-break is the lowest POSITIONAL index after `multi_df.sort("__cluster_id__")` (single key, no `maintain_order`). Polars' default sort is NOT stable, so the oracle is non-deterministic on tie-heavy clusters -- the parity gate would flake on the ORACLE disagreeing with itself, not the native path. Pre-sorting the harness INPUT does not help (the slow branch re-sorts internally). Fix it at the source (this also repairs a latent #870-class non-determinism in the production slow group path).

- [ ] **Step 1: Write the failing test** — a tie-heavy survivorship frame run through the slow branch TWICE with the rows shuffled between runs must give byte-identical golden output.
```python
def test_slow_path_deterministic_on_ties():
    base = ...  # tie-heavy field_groups config + frame with __row_id__
    a = _slow_oracle(base, rules)
    b = _slow_oracle(base.sample(fraction=1.0, shuffle=True, seed=1), rules)  # shuffled rows
    assert a.equals(b)   # lowest-__row_id__ winner regardless of input order
```
- [ ] **Step 2: Run to verify it fails** (shuffled input -> different tie winner).
- [ ] **Step 3: Implement** — in `build_golden_records_batch`'s survivorship branch, change
  `s_sorted = multi_df.sort("__cluster_id__")` to
  `s_sorted = multi_df.sort(["__cluster_id__", "__row_id__"]) if "__row_id__" in multi_df.columns else multi_df.sort("__cluster_id__")`
  (guard the no-`__row_id__` case, mirroring `_stable_value_expr`'s `has_row_id` branch). This makes the slow path's winner the lowest `__row_id__` on ties -- the canonical tie-break the native path also uses. The non-survivorship paths are untouched.
- [ ] **Step 4: Run to verify pass** + the full existing survivorship suite (this change is byte-identical for any already-deterministic input; only tie-on-non-row_id-order cases change, and they change to the canonical lowest-`__row_id__`).
- [ ] **Step 5: Commit** `fix(golden): deterministic survivorship tie-break by __row_id__ (oracle + #870-class)`.

---

## Phase B — vectorized group resolution (parity-gated per strategy)

> Each task: write `assert_parity` over crafted clusters for the strategy, implement the vectorized group resolution to pass it. The slow oracle defines correctness.

### Task B1: groups via most_complete (strict lock-step)

**Files:** `core/survivorship/native.py`; Test: `test_native_parity.py`

- [ ] **Step 1: Write the failing parity test** — a `field_groups` (most_complete) config, crafted multi_df incl. a "would-Frankenstein" cluster + a tie + an all-null group; `assert_parity(df, rules)`.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** the group resolution in `build_survivorship_native` (for a groups-only most_complete config first):
  - For each `GoldenGroupRule g`: compute the per-cluster strategy rank for most_complete = populated-count of `g.columns` descending; sort the frame by `[__cluster_id__, -populated_count, __row_id__]` (best-first, stable tie -> lowest `__row_id__`, matching the oracle's lowest-index winner). Then `group_by("__cluster_id__", maintain_order=True).agg(pl.col(c).first() for c in g.columns)` -> each group col from the rank-0 winner (lock-step). Build the golden DataFrame (one row per cluster) joining the per-group results on `__cluster_id__`. Confidence is deferred to Phase D (use a placeholder column the parity test ignores until D, OR implement confidence here -- but to keep B focused, have `assert_parity` compare ONLY value columns until Phase D adds `__golden_confidence__` to the comparison; gate this with a `compare_confidence=False` flag on `assert_parity`).
  - Populated-count expr: `sum(pl.col(c).is_not_null().cast(pl.Int32) for c in g.columns)`.
- [ ] **Step 4: Run to verify pass** (values byte-identical; tie -> lowest __row_id__; lock-step; all-null group -> winner's nulls).
- [ ] **Step 5: Commit** `feat(survivorship): vectorized most_complete group resolution`.

### Task B2: groups via source_priority / most_recent / anchor

- [ ] **Step 1: Write parity tests** for each: source_priority (rank by `__source__`), most_recent (rank by `date_column` desc nulls-last), anchor (rank by `(anchor.is_not_null(), populated_count)` desc, fallback to most_complete when no anchor present). Crafted clusters per strategy + ties.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the rank-key expressions per strategy (the sort key differs; the `.first()` per group col is the same). source_priority: map `__source__` to its index in `g.source_priority` (unknown -> large), sort ascending. most_recent: sort `date_column` desc, nulls last, then `__row_id__`. anchor: sort by `[anchor.is_not_null() desc, populated_count desc, __row_id__]`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): vectorized source_priority/most_recent/anchor groups`.

### Task B3: allow_fill

- [ ] **Step 1: Write parity tests** — each strategy with `allow_fill=True`: winner-null cells back-filled per-cell from the strategy-best other row; winner-null with no donor stays null; nothing-to-fill unchanged.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — `allow_fill=True` changes the per-group-col agg from `pl.col(c).first()` to `pl.col(c).drop_nulls().first()` over the SAME strategy-sorted frame (first non-null in rank order = per-cell fill; returns the winner's value when non-null). No other change.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): vectorized allow_fill per-cell back-fill`.

---

## Phase C — vectorized scalar resolution

### Task C1: scalar field strategies via aggregates

**Files:** `core/survivorship/native.py`; Test: `test_native_parity.py`

- [ ] **Step 1: Write parity tests** — configs mixing groups with plain scalar fields under `default_strategy` and per-field `GoldenFieldRule` strategies (most_complete, first_non_null, most_recent, source_priority, longest_value). Read `core/survivorship/winner.py`/`core/golden.py merge_field` for the EXACT per-strategy winner + the all-agree short-circuit.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** scalar resolution reusing the existing `_stable_value_expr` pattern from `_build_golden_records_polars_native` (the #870-stable per-column winner). For non-`most_complete`/`first_non_null` strategies, add the matching value expr (e.g. most_recent = value at max `date_column`; source_priority = value at best source rank; longest_value = longest). Apply `validate:` as a pre-mask (set invalid cells to null per column via the goldenflow validator series BEFORE the agg). Strategies not expressible as an aggregate (`custom:`, `confidence_majority`) -> the config is native-ineligible (Phase F gate). The scalar winners join onto the group results by `__cluster_id__`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): vectorized scalar field resolution + validate mask`.

---

## Phase D — exact confidence

### Task D1: `__golden_confidence__` byte-parity

**Files:** `core/survivorship/native.py`; Test: `test_native_parity.py` (now compare `__golden_confidence__` too)

- [ ] **Step 1: Write parity tests** — flip `assert_parity` to also compare `__golden_confidence__`; cases: group tie (0.7), allow_fill fill-count, all-agree scalar (1.0 short-circuit), mixed-unit clusters. Read `resolve.py` (how each UNIT contributes one confidence + the mean) and `merge_field`/`group_winner` for the exact per-unit formulas.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** exact per-unit confidence (NOT the native path's 0.7 approximation):
  - Group: `(winner_populated + n_filled) / len(columns)` x0.7 on tie. `winner_populated` = rank-0 winner's own non-null count (PRE-fill). `n_filled` = group cells where winner null AND a non-null donor exists (allow_fill only). Tie only for most_complete/anchor (never source_priority/most_recent).
  - Scalar: **all-agree (`nuniq<=1`) -> 1.0 for EVERY strategy** (the `merge_field` short-circuit, before strategy dispatch). Else the strategy's own confidence -- read `merge_field` for the EXACT constants (they differ from the group constants; do NOT carry group values into scalars):
    - `most_complete`: **1.0 when the longest value is UNIQUE among non-nulls**, 0.7 only on a length tie (NOT a flat 0.7 -- that is the existing native path's *approximation*, which we must NOT reuse). A dedicated D1 case: distinct values, unique longest -> 1.0.
    - `most_recent`: 1.0 for a unique top date, **0.5** on a date tie (NOT 0.7).
    - `first_non_null`: **0.6** (when >=2 distinct non-nulls; all-agree already short-circuited to 1.0).
    - `source_priority`: `1.0 - idx*0.1`; `majority_vote`: `count/total`; etc.
  - `__golden_confidence__` = mean over (groups + scalars + conditionals) unit confidences -- match the unit count + ordering the slow path uses.
- [ ] **Step 4: Run to verify pass** (confidence byte-identical across all crafted cases).
- [ ] **Step 5: Commit** `feat(survivorship): exact confidence parity in native path`.

---

## Phase E — conditional fields (the small per-cluster loop)

### Task E1: conditional resolution

**Files:** `core/survivorship/native.py`; Test: `test_native_parity.py`

- [ ] **Step 1: Write parity tests** — list-form `field_rules` with `when:` (state-conditional phone, the bench config shape), mixed with groups + scalars; toposort dependency (a `when:` reading a group member / a resolved scalar); validate in a conditional clause.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the conditional phase: after the vectorized group+scalar units produce a per-cluster resolved frame, resolve conditional fields in `build_resolution_order` toposort order by looping clusters for ONLY the conditional columns: build the per-cluster `resolved` dict from the already-computed vectorized values, **reuse `select_conditional_strategy` + `eval_predicate` verbatim** to pick the clause, then apply that clause's strategy (+ `validate:`) to the cluster's candidate values for that one column. Write the result + its confidence into the golden frame. (Materializes only conditional columns + `when:`-referenced resolved scalars.)
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `feat(survivorship): conditional field resolution in native path`.

---

## Phase F — flip the gate + full parity gate + bench

### Task F1: `survivorship_native_eligible` + dispatch

- [ ] **Step 1: Write the failing test** — `survivorship_native_eligible(rules, provenance=False)` returns True for a supported config (groups + scalars + conditionals + allow_fill, all aggregate-able), False when `provenance=True`, False for a `custom:`/`confidence_majority` scalar strategy, False with `quality_scores`.
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** `survivorship_native_eligible` (the supported-lever check) and wire the dispatch in `golden.py` `build_golden_records_batch` BEFORE the slow branch:
```python
    if _survivorship_active(rules) and provenance is False and quality_scores is None \
       and survivorship_native_eligible(rules, provenance):
        from goldenmatch.core.survivorship.native import build_survivorship_native
        golden_df = build_survivorship_native(multi_df, rules)
        return _golden_df_to_rows(golden_df)   # adapt to the list[dict] return shape callers expect
```
(Return-shape: the adapter MUST emit the SAME nested row dicts the slow branch returns for `provenance=False` -- verified against both consumers (`pipeline.py` reads `rec[col]["value"]`; `golden_records_to_provenance` expects `isinstance(info, dict) and "value" in info`). Exact shape per row: `{col: {"value": v, "confidence": c}, ..., "__golden_confidence__": float, "__cluster_id__": cid}`. Keep the slow branch untouched as the fallback. NOTE/defer: a future optimization could route the native `pl.DataFrame` through the existing flat-golden_df consumer (`pipeline.py` fast-path) to skip the dict round-trip the spec cites as an RSS cost -- NOT for v1; the nested adapter is lower-risk and correct.)
- [ ] **Step 4: Run to verify pass** + the FULL survivorship suite (no regression; non-survivorship + provenance=True paths byte-identical).
- [ ] **Step 5: Commit** `feat(survivorship): enable vectorized native path (provenance=False, supported levers)`.

### Task F2: randomized parity gate

- [ ] **Step 1: Write** a seeded randomized parity test: generate N random supported configs x random clustered frames (varying group strategies, allow_fill, conditionals, ties, nulls); `assert_parity` each. This is the real Frankenstein/tie/confidence catcher (mirrors prior survivorship gates). **Include edge cases:** size-1 clusters (the lone row is the winner; confidence via the all-agree path -- guards a `.first()`-on-singleton surprise), all-agree clusters (every scalar strategy -> 1.0), and all-null group columns.
- [ ] **Step 2: Run to verify pass** (fix any divergence found -- the oracle is right).
- [ ] **Step 3: Commit** `test(survivorship): randomized native-vs-slow parity gate`.

### Task F3: bench validation (execution-time / post-merge)

> Measurement step, not code (like the bench Task 6).
- [ ] **Step 1:** Re-run `bench-survivorship-columnar.yml` at 1M/5M with the native path enabled (the bench's `run_slow` now hits the native gate for its config). Confirm the survivorship wall collapses toward the floor (target: low-single-digit x, not 19-20x), RSS not blown up.
- [ ] **Step 2:** Append the result to the verdict report (the "native" column) -- the measure-first close-out. If it does NOT close the gap, that is a finding (revert the gate, keep slow).

### Task F4: docs
- [ ] Note the vectorized survivorship path + the `provenance=True` slow-path fallback in `docs-site/goldenmatch/configuration.mdx` / `tuning.mdx`. Commit.

---

## Open items carried from the spec (resolve during execution)

- **Oracle determinism (the byte-identity hazard):** the parity harness sorts the oracle frame by `[__cluster_id__, __row_id__]` so the slow path's tie-break is deterministic (the vectorized path's `__row_id__` final sort key is canonical). Without this the gate flakes on the ORACLE, not the native path.
- **Confidence (Phase D) is the trickiest parity surface:** `winner_populated` pre-fill, `n_filled` needs a donor, source_priority/most_recent groups never tie, the scalar all-agree 1.0 short-circuit, the per-unit mean count. Pin each with a dedicated case.
- **Return-shape adaptation (F1):** match `build_golden_records_batch`'s `list[dict]` contract exactly (the pipeline + `golden_records_to_provenance` consume it). Keep the slow branch as the untouched fallback.
- **Scalar strategy coverage:** only aggregate-expressible strategies are native-eligible; `custom:`/`confidence_majority`/`quality_scores` force the slow path. The eligibility check (F1) is the guard.
