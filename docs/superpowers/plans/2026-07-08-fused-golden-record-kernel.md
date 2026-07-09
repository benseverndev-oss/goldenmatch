# Fused Arrow-native Golden-Record Kernel — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone Arrow-native `golden_fused` Rust kernel (+ Python `run_golden_fused_arrow`) that turns a cluster map into golden records byte-identically to `build_golden_records_batch`, at ~2x lower peak RSS, covering every Rust-portable survivorship feature.

**Architecture:** The kernel returns per-(cluster, column) **source-row indices** + confidences, never values. Python precomputes comparable keys (str form + integer factorization codes) so Rust never sees raw values, and materializes the output with one `take()` per column on the original typed data. The wide `multi_df` never exists. Declines validator/plugin/LLM configs loudly to the classic path.

**Tech Stack:** Rust (PyO3/`goldenmatch-native`, arrow-rs `PyArrowType`), Python (polars, pyarrow), pytest. Reference-mode discipline: byte-identical to `core/golden.py`.

**Spec:** `docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md`

**Ground-truth reference (read before starting):**
- `packages/python/goldenmatch/goldenmatch/core/golden.py` — `merge_field:62` (returns `(value, confidence, source_index)`, `source_index` = positional index into the values list), the 8 `_strategy` helpers (`:125`–`:305`), `build_golden_records_batch:784`, `_multi_df_from_frames:1275`.
- `packages/python/goldenmatch/goldenmatch/core/survivorship/resolve.py` — `resolve_cluster` (the oracle for covered configs; walks `resolution_order`, one confidence per unit, `__golden_confidence__ = sum(confidences)/len`).
- `packages/python/goldenmatch/goldenmatch/core/survivorship/winner.py` (`group_winner`/`_ranking`/`GroupResult` at `:50/:21/:8`), `groups.py` (group *detection* only — `build_field_groups`), `conditions.py` (`build_resolution_order`, `select_conditional_strategy`, `eval_predicate`).
- `packages/python/goldenmatch/goldenmatch/core/fused_match.py` — the sibling pattern (gate + `run_*_arrow` + `_native` dispatch + decline-to-None).
- `packages/rust/extensions/native/src/fused.rs`, `block.rs`, `lib.rs` — the sibling kernel + registration.

---

## Conventions used throughout

- **Build the kernel:** `python scripts/build_native.py` (in-tree build → `goldenmatch/_native.pyd`). Rust unit tests: `cargo test --lib` from `packages/rust/extensions/native/`.
- **Run Python tests (Windows):** `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_golden_fused.py -v` from repo root. Set `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`.
- **Parity oracle:** every parity test builds a cluster+frame, sorts within-cluster by `__row_id__` ascending (spec §4.3), runs `run_golden_fused_arrow` AND `build_golden_records_batch` on the identical input, and asserts frame + provenance equality.
- **Oracle is the EXACT survivorship path, NOT the fast columnar path.** `build_golden_records_batch` has three internal paths with different confidence semantics; the polars-native fast path *approximates* `most_complete` confidence (0.7 for any multi-distinct cluster, not the exact `_most_complete` unique-longest→1.0). The fused path **declines** fast-path-eligible configs (§2/§7 of the spec) — reuse `golden.py::_polars_native_eligible` in `run_golden_fused_arrow` to return `None` when the config+`quality_scores` would route to that path. So every parity test must build a config that routes the reference to the EXACT path: use an explicit `field_rules` entry, a `field_group`, `cluster_overrides`, or pass a non-None `quality_scores` — a bare `default_strategy="most_complete"` with no other feature routes to the approximating fast path and is out of scope.
- **`run_golden_fused_arrow` filters singletons/oversized itself** (`size > 1 & ~oversized`), matching `_multi_df_from_frames`, so it accepts an unfiltered cluster frame and emits one golden row per multi-member cluster. **Harness asymmetry (important):** the oracle `build_golden_records_batch` does NOT self-filter — it expects a pre-filtered `multi_df` and will emit a 1-row golden record for a singleton if handed one. So the singleton/oversized parity test feeds `run_golden_fused_arrow` the RAW frame but `build_golden_records_batch` the PRE-FILTERED frame (singletons/oversized removed), then asserts equal output. Do NOT feed the identical unfiltered frame to both, or the test goes spuriously red and reads as a kernel bug.
- **Commit after every green step.** Branch: `feat/golden-fused-kernel` (already created off fresh `origin/main`).
- **Strategy id enum** (Rust `u8`, shared with Python `_GOLDEN_STRATEGY_IDS`):
  `most_complete=0, majority_vote=1, source_priority=2, most_recent=3, first_non_null=4, longest_value=5, unanimous_or_null=6, confidence_majority=7`.

---

## Stage 0 — Scaffolding + decline path + gate

Goal: `run_golden_fused_arrow` exists, `golden_fused_ready` gates correctly, and a minimal kernel round-trips a single `most_complete` column. Everything else declines to None.

### Task 0.1: Python module skeleton + gate

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/golden_fused.py`
- Test: `packages/python/goldenmatch/tests/test_golden_fused.py`

- [ ] **Step 1: Write the failing gate test**

```python
# tests/test_golden_fused.py
import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenRulesConfig, GoldenFieldRule, GoldenGroupRule
from goldenmatch.core.golden_fused import golden_fused_ready

def test_gate_accepts_simple_default_strategy():
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert golden_fused_ready(rules) is True

def test_gate_accepts_covered_field_rule():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="majority_vote")},
    )
    assert golden_fused_ready(rules) is True

def test_gate_declines_validator():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="most_complete", validate_with="phone")},
    )
    assert golden_fused_ready(rules) is False

def test_gate_declines_custom_plugin():
    rules = GoldenRulesConfig(default_strategy="custom:my_plugin")
    assert golden_fused_ready(rules) is False

def test_gate_declines_llm():
    rules = GoldenRulesConfig(default_strategy="most_complete", use_llm_for_ambiguous=True)
    assert golden_fused_ready(rules) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_golden_fused.py -k gate -v`
Expected: FAIL — `ModuleNotFoundError: goldenmatch.core.golden_fused`.

- [ ] **Step 3: Implement the module skeleton + gate**

```python
# core/golden_fused.py
"""Fused Arrow-native golden-record production.

Turns a cluster map into golden records in one FFI call, holding intermediates
as Rust Vecs — no wide multi_df, no per-cluster Python dicts. Byte-identical to
core/golden.build_golden_records_batch for the covered config surface; declines
loudly (returns None) for validator/plugin/LLM configs. Design:
docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md
"""
from __future__ import annotations

import polars as pl

from goldenmatch.config.schemas import GoldenRulesConfig, GoldenFieldRule, GoldenGroupRule

_GOLDEN_STRATEGY_IDS = {
    "most_complete": 0, "majority_vote": 1, "source_priority": 2, "most_recent": 3,
    "first_non_null": 4, "longest_value": 5, "unanimous_or_null": 6, "confidence_majority": 7,
}
_COVERED_STRATEGIES = frozenset(_GOLDEN_STRATEGY_IDS)


def _rule_covered(rule: GoldenFieldRule) -> bool:
    if rule.strategy not in _COVERED_STRATEGIES:
        return False  # custom:* and any unknown strategy
    if getattr(rule, "validate_with", None):
        return False
    # conditional predicate lowerability is checked in golden_fused_ready
    return True


def golden_fused_ready(rules: GoldenRulesConfig) -> bool:
    """True iff every effective strategy is covered, no validator/plugin/LLM,
    and every conditional predicate lowers to the kernel IR."""
    if getattr(rules, "use_llm_for_ambiguous", False):
        return False
    if rules.default_strategy not in _COVERED_STRATEGIES:
        return False
    # field_rules: each entry is a GoldenFieldRule or a list of them (conditional)
    for entry in rules.field_rules.values():
        clauses = entry if isinstance(entry, list) else [entry]
        for clause in clauses:
            if not _rule_covered(clause):
                return False
            # predicate lowerability wired in Stage 6; until then decline list-form.
            if getattr(clause, "when", None) is not None:
                from goldenmatch.core.golden_fused_predicate import predicate_lowerable
                if not predicate_lowerable(clause.when):
                    return False
    for group in rules.field_groups:
        if group.strategy not in _COVERED_STRATEGIES and group.strategy not in {"anchor"}:
            return False
    if rules.cluster_overrides:
        for overrides in rules.cluster_overrides.values():
            for rule in overrides.values():
                if not _rule_covered(rule):
                    return False
    return True
```

Note: Stage 6 adds `golden_fused_predicate.py`. Until then, guard the import so list-form conditionals decline. For Stage 0, add a stub `predicate_lowerable` that returns `False` (so any `when:` declines) — see Task 6.1.

The **fast-path decline** is NOT in `golden_fused_ready(rules)` (which lacks `quality_scores`); it lives in `run_golden_fused_arrow` (Task 0.3), which reuses `golden.py::_polars_native_eligible(...)` with the resolved config + `quality_scores` in scope and returns `None` when eligible. Add a test for it there:

```python
def test_run_declines_fast_path_eligible_simple_config():
    df = _cluster_frame()  # from Task 0.3
    # simple most_complete default, no field_rules/groups/overrides, no quality_scores
    rules = GoldenRulesConfig(default_strategy="most_complete")
    assert run_golden_fused_arrow(df, rules) is None  # routes to fast columnar path
```

- [ ] **Step 4: Run to verify pass** (add a temporary `golden_fused_predicate.py` with `def predicate_lowerable(_): return False`)

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_golden_fused.py -k gate -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/golden_fused.py packages/python/goldenmatch/goldenmatch/core/golden_fused_predicate.py packages/python/goldenmatch/tests/test_golden_fused.py
git commit -m "feat(goldenmatch): golden_fused gate + module skeleton"
```

### Task 0.2: Rust kernel skeleton + registration

**Files:**
- Create: `packages/rust/extensions/native/src/golden.rs`
- Modify: `packages/rust/extensions/native/src/lib.rs` (register `golden::golden_fused`)

- [ ] **Step 1: Write the Rust signature returning empty indices**

Kernel input (all Arrow via `PyArrowType<ArrayData>` / `Vec<...>`), returns `(winner_idx, field_conf)` as `Vec<Vec<i64>>` and `Vec<Vec<f64>>` (outer = column, inner = cluster) plus the per-cluster id list. Define the pyfunction `golden_fused` mirroring `fused.rs::match_fused`'s arg-reading + `py.detach` structure. For Stage 0, accept: `row_ids: Int64Array`, `cluster_ids: Int64Array`, `n_output_cols: usize`, `strategy_ids: Vec<u8>`, `text_cols: Vec<PyArrowType<ArrayData>>` (Utf8), `code_cols: Vec<PyArrowType<ArrayData>>` (Int64). Group rows into contiguous per-cluster spans (reuse the `group_block_positions` sort-into-spans idea from `block.rs`, but grouping by `cluster_id` and pre-sorted by `(cluster_id, row_id)` on the Python side). Return the winner-index / confidence matrices + the ordered cluster-id list.

Follow `fused.rs` exactly for: `PyArrowType` reading, `Int64Array::from(data)`, `StrCol::from_data`, `py.detach(|| ...)`.

- [ ] **Step 2: `cargo test --lib` to confirm it compiles** (no test yet; just build)

Run: `cd packages/rust/extensions/native && cargo build --lib 2>&1 | grep -E "^error" || echo OK`
Expected: `OK`.

- [ ] **Step 3: Register in lib.rs**

Add `mod golden;` and `m.add_function(wrap_pyfunction!(golden::golden_fused, m)?)?;`. NOTE: register with a `::`-qualified path (the `check_native_symbols` gate misses bare forms — see root CLAUDE.md).

- [ ] **Step 4: Build the wheel + confirm the symbol**

Run: `python scripts/build_native.py && python -c "from goldenmatch.core._native_loader import native_module; print(hasattr(native_module(), 'golden_fused'))"`
Expected: `True`.

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/native/src/golden.rs packages/rust/extensions/native/src/lib.rs
git commit -m "feat(goldenmatch-native): golden_fused kernel skeleton + registration"
```

### Task 0.3: `run_golden_fused_arrow` end-to-end for `most_complete`

**Files:** Modify `core/golden_fused.py`, `core/golden.rs`, `tests/test_golden_fused.py`.

- [ ] **Step 1: Write the failing parity test (single covered column, default `most_complete`)**

```python
from goldenmatch.core.golden import build_golden_records_batch
from goldenmatch.core.golden_fused import run_golden_fused_arrow

def _cluster_frame():
    # two clusters, within-cluster __row_id__ ascending (spec 4.3)
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 10, 11],
        "__cluster_id__": [1, 1, 1, 2, 2],
        "name": ["Bob", "Robert", "Bob", "Sue", "Suzanne"],
    })

def test_most_complete_matches_reference():
    df = _cluster_frame()
    # EXPLICIT field_rule forces the reference off the approximating fast columnar
    # path onto the exact merge_field path (see the oracle note in Conventions).
    # A bare default_strategy="most_complete" would route to the fast path and is
    # DECLINED by run_golden_fused_arrow (returns None).
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="most_complete")},
    )
    ref = build_golden_records_batch(df, rules)               # list[dict]
    got = run_golden_fused_arrow(df, rules)                   # pl.DataFrame
    # compare on (cluster_id, name, confidence)
    ref_map = {r["__cluster_id__"]: r for r in ref}
    for row in got.iter_rows(named=True):
        cid = row["__cluster_id__"]
        assert row["name"] == ref_map[cid]["name"]["value"]
        assert abs(row["__golden_confidence__"] - ref_map[cid]["__golden_confidence__"]) < 1e-12
```

(Confirm the exact `build_golden_records_batch` return shape while writing — adapt the assertion to the real dict layout. The point is byte-identical value + confidence per cluster.)

- [ ] **Step 2: Run to verify failure** — `run_golden_fused_arrow` not implemented.

- [ ] **Step 3: Implement `run_golden_fused_arrow` (most_complete path)**

Python responsibilities:
1. Return `None` if `not golden_fused_ready(rules)`.
2. Determine output user columns (exclude `__`-internal). Determine per-column effective strategy (default or field_rule; Stage 5/6/7 extend).
3. Sort the frame by `["__cluster_id__", "__row_id__"]` (spec §4.3).
4. Per column, build `text` (Utf8 `str(v)`) via `pl.col(c).cast(pl.Utf8)`; `most_complete` needs only `text`.
5. Call `native_module().golden_fused(...)` with row_ids, cluster_ids, strategy_ids, text_cols, code_cols (empty for now).
6. Materialize: for each column, `orig[c].gather(winner_idx[col])` with `-1`→null; add `__cluster_id__`, `__golden_confidence__`.

Kernel (`golden.rs`) `most_complete` per cluster per column: replicate `_most_complete` (`golden.py:125`) — `str_vals` lengths, max_len, unique-longest→conf 1.0 else first-in-order→conf 0.7 (quality-weight tie-break is Stage 3). Universal short-circuit first (`golden.py:82`): all non-null identical → that value, conf 1.0. Emit `winner_idx` = positional index within the cluster's sorted member span mapped to the GLOBAL row position (so Python's `gather` indexes the sorted frame). Null column value → the strategy sees fewer non-null; all-null → `-1`.

- [ ] **Step 4: Build + run to verify pass**

Run: `python scripts/build_native.py && .venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_golden_fused.py -k most_complete -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(goldenmatch): run_golden_fused_arrow end-to-end for most_complete"
```

---

## Stage 1 — Remaining pure-scalar strategies (no extra columns)

Add `majority_vote`, `first_non_null`, `longest_value`, `unanimous_or_null`. These need only `code` (factorization) and/or `text`. Provenance (`source_index`) parity is required now — return the positional index the reference returns.

### Task 1.1: Python factorization helper

**Files:** Modify `core/golden_fused.py`; Test: `tests/test_golden_fused.py`.

- [ ] **Step 1: Failing unit test for `_factorize_codes`**

```python
from goldenmatch.core.golden_fused import _factorize_codes

def test_factorize_respects_python_equality_and_order():
    # int 1 and float 1.0 are == in Python -> same code; None -> -1; first-occurrence order
    vals = [1, 1.0, None, "x", 1]
    codes = _factorize_codes(vals)
    assert codes == [0, 0, -1, 1, 0]
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement `_factorize_codes(values: list) -> list[int]`** using a dict keyed by the raw value (Python `==`/hash), assigning codes in first-occurrence order, `None`→`-1`. This is the byte-identical grouping key for `majority_vote`/`unanimous_or_null`/`confidence_majority` (matches `Counter`/`set` grouping in `golden.py:153,246,285`).

- [ ] **Step 4: Run → pass. Step 5: Commit.**

### Task 1.2: `majority_vote` + `unanimous_or_null` + `first_non_null` + `longest_value` in the kernel

**Files:** Modify `golden.rs`, `core/golden_fused.py`, `tests/test_golden_fused.py`.

- [ ] **Step 1: Write parity tests, one per strategy**, each building a frame where the strategy's tie-break is exercised (a count-majority tie, a unanimous-disagree emitting null, a first-non-null with a leading null, a longest-value length tie). Assert value + confidence + (Stage 8) provenance vs `build_golden_records_batch`.

```python
@pytest.mark.parametrize("strategy,expected_conf_rule", [
    ("majority_vote", None), ("first_non_null", None),
    ("longest_value", None), ("unanimous_or_null", None),
])
def test_scalar_strategy_matches_reference(strategy, expected_conf_rule):
    df = pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "__cluster_id__": [1, 1, 1, 1],
        "v": ["a", "a", "bb", None],
    })
    rules = GoldenRulesConfig(default_strategy=strategy)
    ref = {r["__cluster_id__"]: r for r in build_golden_records_batch(df, rules)}
    got = run_golden_fused_arrow(df, rules)
    for row in got.iter_rows(named=True):
        r = ref[row["__cluster_id__"]]
        assert row["v"] == r["v"]["value"]
        assert abs(row["__golden_confidence__"] - r["__golden_confidence__"]) < 1e-12
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Port each strategy into `golden.rs`**, mirroring the reference line-for-line:
  - `majority_vote` (`golden.py:153`): count codes, winner = highest count, tie → first `most_common` insertion (i.e. lowest first-occurrence code); `conf = count/total`; `source_index` = first occurrence of the winning code.
  - `unanimous_or_null` (`golden.py:237`): distinct non-null codes; if 1 → that value conf 1.0 else `-1` conf 0.0.
  - `first_non_null` (`golden.py:198`): first non-null in order, conf 0.6.
  - `longest_value` (`golden.py:209`): max `text` length; unique → 1.0 else first-in-order → 0.5 (weight tie-break Stage 3).
  Wire Python to pass `code`/`text` cols per the strategy's needs.
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 2 — `source_priority` + `most_recent` (extra gathered columns)

### Task 2.1: `source_priority`

**Files:** Modify `golden.rs`, `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity test** with a `__source__` column and `GoldenFieldRule(strategy="source_priority", source_priority=[...])`. Reference is `build_golden_records_batch` with `__source__` present.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python passes `source_code` (factorized `__source__`) + the `source_priority` list mapped to source-codes (per column/rule). Kernel mirrors `_source_priority` (`golden.py:161`): first-occurrence value per source; walk priority list, first source with a non-null first-occurrence wins; `conf = max(0.1, 1.0 - idx*0.1)`; no match → `-1` conf 0.0.
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

### Task 2.2: `most_recent`

- [ ] **Step 1: Failing parity test** with a date column + `GoldenFieldRule(strategy="most_recent", date_column="dt")`. Include a date-tie fixture (conf 0.5) and a null-date row (dropped).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python passes `date: i64` + a `date_null_mask` per most_recent column (spec §4.2 — explicit mask, not a sentinel). Parse dates via the reference path (whatever `resolve_cluster._dates_for` yields; usually `pl` date → physical i64). Kernel mirrors `_most_recent` (`golden.py:185`): drop rows where value-null OR date-null, stable-sort by date desc, top; tie on top date → conf 0.5 else 1.0; none eligible → `-1` conf 0.0. **Stable sort direction is load-bearing** — Python's `sort(key=date, reverse=True)` is stable, so among rows tied on the top date the FIRST-occurring (lowest original index) wins. In Rust, sort by `(date DESC, original_index ASC)` — a naive "reverse the whole comparator" would flip the tie to the LAST occurrence and pick the wrong representative index (breaking provenance).
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 3 — Quality-weight tie-breaks

### Task 3.1: Thread `qweight` into the kernel tie-breaks

**Files:** Modify `golden.rs`, `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity test.** Build a frame + a `quality_scores: dict[(row_id, col), float]`; pass it to both `build_golden_records_batch(df, rules, quality_scores=...)` and `run_golden_fused_arrow(df, rules, quality_scores=...)`. Exercise a `most_complete` length-tie broken by weight (conf `min(1.0, 0.7*w)`) and a weighted `majority_vote` (`conf = winner_weight/total`).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python builds a per-column `qweight: list[float]` aligned to the sorted frame (`quality_scores.get((row_id, col), 1.0)`), passes it when present. Kernel branches exactly as `merge_field` does when `quality_weights is not None` for `_most_complete` (`:132`), `_majority_vote` (`:140`), `_first_non_null` (`:199`), `_longest_value` (`:228`). Missing-index fallback `1.0` matches `x[0] < len(quality_weights)`.
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 4 — `confidence_majority` (pair scores)

### Task 4.1: Flatten pair scores + kernel edge-sum

**Files:** Modify `golden.rs`, `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity test.** Build a 3-member cluster with `cluster_pair_scores={cid: {(row_a,row_b): score}}` where a 2-member strong-edge minority beats a 3-member weak-edge majority. Pass to both reference (`build_golden_records_batch(..., cluster_pair_scores=...)`) and fused.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python remaps pair scores from row-id keys to positional indices within the sorted cluster (exactly `resolve.py:38-46`), flattens to per-cluster edge arrays `(a_pos, b_pos, score)` **in `pair_scores.items()` iteration order** (spec §6.4 — the representative index depends on it). Kernel mirrors `_confidence_majority` (`golden.py:252`): for each edge where both endpoints share a code, add score to that code's weight, set representative index on the FIRST such edge; winner = max weight; `conf = winner/total`; empty → fall back to `majority_vote`.
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 5 — `field_groups` (correlated survivorship)

### Task 5.1: Group ranking + lock-step winner

**Files:** Modify `golden.rs`, `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity tests**, one per group strategy (`most_complete`, `source_priority`, `most_recent`, `anchor`) + one `allow_fill=True`. Reference must route through `resolve_cluster` (survivorship path) — build the frame so `build_golden_records_batch` takes that path (any `field_groups` present forces it). Assert every group column's value + the single group confidence contribution.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python passes per-group: column indices, strategy, `source_code`/`date`+mask/anchor-column-index as needed, `allow_fill`. Kernel mirrors `winner.py::group_winner`: rank rows (populated-count from the group columns' null masks / source rank / date / anchor-present), pin one winner index across all group columns; with `allow_fill`, per-column back-fill from the next-best ranked row that has the column. Confidence `base = (winner_populated + n_filled)/len(columns)`, `×0.7` on tie. Emit ONE confidence entry for the group (spec §8; `resolve.py:100`).
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 6 — Conditional `field_rules` (predicate IR)

### Task 6.1: Predicate lowering in Python

**Files:** Create `core/golden_fused_predicate.py`; Test: `tests/test_golden_fused_predicate.py`.

- [ ] **Step 1: Failing tests** for `predicate_lowerable(expr)` (True for `country == "US"`, `state in ["NY","NJ"]`, `a == 1 and b != 2`; False for a function call, attribute access, or an unsupported node) and `lower_predicate(expr, column_index, code_of) -> IR` (returns an RPN/typed structure).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Reuse `conditions.py` parsing (`ast.parse(mode="eval")` + the existing allowlist). Walk the validated AST into a flat IR: opcodes for `AND/OR/NOT`, `EQ/NE` (operand = referenced column index + a literal resolved to that column's code, or an "absent" sentinel), `IN/NOT_IN` (list of codes), and numeric `LT/LE/GT/GE` (operand = column index + numeric literal, evaluated in a numeric lane). `predicate_lowerable` returns False for any node the IR can't represent (so the gate declines). Miss semantics (unknown name / uncomparable → False arm) match `eval_predicate`.
- [ ] **Step 4: Run → pass. Step 5: Commit.**

### Task 6.2: Resolution order + kernel IR evaluation

**Files:** Modify `golden.rs`, `core/golden_fused.py`, `tests/test_golden_fused.py`.

- [ ] **Step 1: Failing parity test** with a list-form conditional field_rule (`when:` referencing another already-resolved column). Reference routes through `resolve_cluster`. Assert the chosen clause's value + confidence.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python calls `conditions.build_resolution_order(field_rules, groups, all_columns)` and passes the unit order + per-conditional-column the lowered IR for each clause + clause strategy ids. Kernel resolves units in order; for a conditional column, evaluate each clause's IR against the already-resolved winner codes (needs the resolved winner's code per referenced column — kernel tracks `resolved_code[col][cluster]` as it goes), pick the first passing clause's strategy, apply it; else the when-less default clause. Update the gate (`golden_fused_ready`) to use the real `predicate_lowerable`.
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 7 — `cluster_overrides`

### Task 7.1: Per-(cluster, col) strategy dispatch

**Files:** Modify `golden.rs`, `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity test** with `cluster_overrides={cid: {col: GoldenFieldRule(strategy=...)}}`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Python passes a per-cluster strategy-code override array (`-1` = use the column default). Kernel dispatches on the effective strategy per (cluster, col).
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

---

## Stage 8 — Provenance output

### Task 8.1: Per-field `source_row_id` provenance

**Files:** Modify `core/golden_fused.py`, tests.

- [ ] **Step 1: Failing parity test** with `provenance=True`. Compare per-field `source_row_id` and per-group winner ids against `build_golden_records_batch(..., provenance=True)` / the `resolve_cluster` provenance objects.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** `run_golden_fused_arrow(..., provenance=True)` maps each column's `winner_idx` (already computed) through the sorted frame's `__row_id__` to build the provenance frame; `-1` → null. For groups, all columns share the group winner id (or the per-column filled id under `allow_fill`). Match whatever return shape the caller of `build_golden_records_batch(provenance=True)` expects (confirm against `build_golden_records_from_frames`).
- [ ] **Step 4: Build + run → pass. Step 5: Commit.**

### Task 8.2: Full parity matrix + mixed-type fixtures

- [ ] **Step 1:** Add a parametrized parity test sweeping the cross-product of {each strategy, groups, conditionals, confidence_majority, quality-weights, cluster_overrides} × {provenance on/off}, plus **mixed-type-column fixtures** (int `1` vs float `1.0`; numeric-as-string; None mixed with ""), each asserting frame equality (values + dtypes via `assert_frame_equal`) and provenance equality vs `build_golden_records_batch`.
- [ ] **Step 2: Run → iterate** until all green. Fix any divergence at its Stage.
- [ ] **Step 3: Commit.**

---

## Stage 9 — Memcap RSS bench + dogfood

### Task 9.1: Memory-capped RSS bench

**Files:** Create `packages/python/goldenmatch/scripts/bench_golden_fused_memcap.py`, `.github/workflows/bench-golden-fused-memcap.yml`.

- [ ] **Step 1:** Mirror `scripts/bench_match_fused_memcap.py` + `bench-match-fused-memcap.yml`. Generate a large clustered synthetic frame (surnames spread across soundex codes — see `feedback_synthetic_surname_fixtures`), then under a `systemd-run -p MemoryMax=<cap>` cgroup, measure peak RSS of (a) `build_golden_records_batch` and (b) `run_golden_fused_arrow` on the identical clusters. Report both + the ratio. Assert the fused peak RSS is materially lower (the capacity win); wall is expected to be a wash — report it, don't gate on it.
- [ ] **Step 2:** Add the `workflow_dispatch` job (default runner `large-new-64GB`, per `feedback_bench_default_runner`).
- [ ] **Step 3: Commit.**

### Task 9.2: Febrl3 dogfood

- [ ] **Step 1:** Write a scratch script (scratchpad, not committed) that runs a real Febrl3 dedupe to clusters, then produces golden records via BOTH paths and asserts byte-identical output on real data. Record the result in the memory note.
- [ ] **Step 2: Commit** (only if you promote it to `scripts/` as an example — otherwise leave in scratchpad).

---

## Stage 10 — Docs sweep + wheel discipline + PR

### Task 10.1: Rollout docs sweep

- [ ] **Step 1:** Invoke the `rollout-docs-sweep` skill. Change set = ADDED: `run_golden_fused_arrow`, `golden_fused_ready`, native symbol `golden_fused`. Update: the tuning/opt-ins doc if a new env var was added (none planned — decline is automatic); the native-kernel symbol inventory; CHANGELOG. Run the `check_native_symbols` + `api_parity` gates locally (`golden_fused` is Python-internal, not an MCP tool/CLI command, so `api_parity` should be unaffected — confirm).
- [ ] **Step 2:** Bump `goldenmatch-native` `pyproject.toml` + `Cargo.toml` versions in lockstep (per the #688 republish lesson) since a new depended-on symbol shipped. Note in CHANGELOG that the published wheel must be republished for `pip install goldenmatch[native]` users to get the fused-golden path (the host degrades to the classic path without it — not a correctness bug).
- [ ] **Step 3: Commit.**

### Task 10.2: Open the PR

- [ ] **Step 1:** Verify the full Rust test suite (`cargo test --lib`) and the Python parity suite are green. Run `ruff check` on the new Python.
- [ ] **Step 2:** Push (`gh auth switch --user benzsevern` first; `unset GH_TOKEN`), open a PR titled `feat(goldenmatch): fused Arrow-native golden-record kernel (clusters -> golden, ~2x lower RSS)`, arm `gh pr merge --auto --squash`, switch auth back. STOP — do not poll CI (per `feedback_dont_poll_ci_arm_automerge`).
- [ ] **Step 3:** Update the memory note `project_fused_match_kernel` (or a new `project_fused_golden_kernel`) with the measured RSS result and the covered/declined boundary.

---

## Non-goals (explicit — do NOT build)

- `pipeline.py` wiring / controller auto-routing (separate PR).
- validator (`validate_with`) / plugin (`custom:*`) / LLM (`use_llm_for_ambiguous`) arms — they decline to the classic path by design.
- distributed / Sail backends.
