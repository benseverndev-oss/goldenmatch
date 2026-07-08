# GoldenCheck Denial-Constraint Discovery (Stage 1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new discovered-rule family to GoldenCheck — single-table, approximate **denial-constraint** discovery — that mines ranked near-DCs (`¬(status=shipped ∧ ship_date<order_date)`) and surfaces their violating rows as `Finding`s, opt-in and off the default scan path.

**Architecture:** A new `goldencheck/denial/` subpackage runs a **sample-then-validate** pipeline: build a bounded predicate space over encoded columns, collect an **evidence set** in two passes (row-level over n rows for single-tuple DCs; pairwise over S² sampled pairs for cross/mixed DCs), derive minimal DCs via a FastDC hitting-set cover search, then validate each candidate's g1 error on the real data. A pure-Python reference is the correctness/parity oracle; a `goldencheck-core` Rust `dc.rs` kernel is the fast path, shipped only if it beats a Polars cross-join baseline (measure-first). Mirrors the existing `relations/approx_fd.py` + `core/kernels.py` native-gated pattern exactly.

**Tech Stack:** Python 3.13 (Polars, pyarrow, pytest), Rust (`goldencheck-core` slice kernels + `goldencheck-native` abi3 PyO3 shim, `arrow=59`), Typer CLI.

**Spec:** `docs/superpowers/specs/2026-07-08-goldencheck-denial-constraint-discovery-design.md`

---

## Conventions (this plan runs in the `gc-denial` worktree)

This work is on branch `feat/goldencheck-denial-constraints`, worktree `D:\show_case\gc-denial`, cut from fresh `origin/main`. The repo-root `.venv` has `goldencheck` editable-installed from the **main** tree, so tests MUST prepend the worktree package to `PYTHONPATH` (per the `reference_py_worktree_test_native_skew` memory):

**Python test preamble** (run from the worktree root `/d/show_case/gc-denial`):
```bash
export PYTHONPATH="D:/show_case/gc-denial/packages/python/goldencheck"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe
# confirm the worktree shadows the installed copy:
$PY -c "import goldencheck, pathlib; print(goldencheck.__file__)"  # must be under gc-denial
```
Run tests: `$PY -m pytest packages/python/goldencheck/tests/denial/<file> -v`. Ruff (100-char lines): `$PY -m ruff check <paths>`.

**Rust preamble** (before any cargo): `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`. Core standalone: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml`. Native build (into repo-root .venv): `cd packages/rust/extensions/goldencheck-native && /d/show_case/goldenmatch/.venv/Scripts/maturin.exe develop --release`. Verify builds explicitly (grep `^error`, per `feedback_verify_rust_builds_explicitly`).

**Commit discipline:** conventional commits, one per task's final step. Do NOT push (execution stays local; a PR to `main` is a separate step after all tasks). All commits on `feat/goldencheck-denial-constraints`.

**Patterns to mirror (read them):** `relations/approx_fd.py` (the discover+violation profiler with native-try/except/Python-fallback + `_intern`/`_discover_python`/`_violation_rows` helpers), `core/kernels.py` (list-shaped native-gated entries), `core/_native_loader.py` (`_COMPONENT_SYMBOLS` tuple probe), `goldencheck-core/src/keys.rs` (slice-based `&[&[u64]]` kernel style), `models/finding.py` (Finding fields: `severity, column, check, message, affected_rows, sample_values, suggestion, confidence, metadata` — `check` is a free-form str).

---

## File structure

New subpackage `packages/python/goldencheck/goldencheck/denial/`:
| File | Responsibility |
|---|---|
| `__init__.py` | re-export `discover_denial_constraints`, `DenialConstraint`, `Predicate` |
| `models.py` | `Op` enum, `Predicate`, `DenialConstraint` dataclasses + human-readable render |
| `predicates.py` | column encoding (categorical first-seen / numeric-temporal rank / null rule), predicate-space enumeration, arity partition, `|P|` budgets + support prefilter |
| `evidence.py` | row-level + pairwise evidence maps (pure-Python reference; delegates to `core.kernels` fast path) |
| `discover.py` | FastDC minimal-cover hitting-set search; approximate ε; minimality; interestingness ranking |
| `validate.py` | g1 validation (single-tuple exact O(n) + rows; cross-tuple sampled + representative pairs) |
| `mine.py` | orchestrator + `discover_denial_constraints(df, ...)` + `DenialConstraintProfiler` (Finding emission) |
| `constants.py` | tunables (`MAX_LITERAL_CARD`, `MIN_SUPPORT`, `MAX_PREDICATES=64`, `DEFAULT_SAMPLE`, `DEFAULT_EPS`, `MAX_CONSTRAINTS`, `VALIDATION_SAMPLE`) |

Rust: `packages/rust/extensions/goldencheck-core/src/dc.rs` (+ `lib.rs` wiring), `goldencheck-native/src/dc.rs` (+ `lib.rs` registration). Python fast-path entry: `goldencheck/core/kernels.py::denial_constraint_evidence`. Loader: `core/_native_loader.py` `_COMPONENT_SYMBOLS`. Surfaces: `goldencheck/__init__.py` (`__all__`), `cli/main.py` (`denial-constraints` command + `--deep` wiring in the scan path). Tests under `packages/python/goldencheck/tests/denial/`.

---

## WAVE A — pure-Python engine (correctness reference, works end-to-end without native)

### Task 1: `models.py` — Predicate + DenialConstraint

**Files:** Create `goldencheck/denial/models.py`, `goldencheck/denial/constants.py`; Test `tests/denial/test_models.py`

- [ ] **Step 1: Failing test.**
```python
# tests/denial/test_models.py
from goldencheck.denial.models import Op, Predicate, DenialConstraint

def test_predicate_render_constant_and_variable():
    p_const = Predicate(kind="const", col_a="country", op=Op.EQ, col_b=None, literal="US")
    p_cmp = Predicate(kind="single", col_a="ship_date", op=Op.LT, col_b="order_date", literal=None)
    assert p_const.render() == "country = 'US'"
    assert p_cmp.render() == "ship_date < order_date"

def test_dc_render_and_g1_bounds():
    dc = DenialConstraint(
        predicates=(
            Predicate(kind="const", col_a="status", op=Op.EQ, col_b=None, literal="shipped"),
            Predicate(kind="single", col_a="ship_date", op=Op.LT, col_b="order_date", literal=None),
        ),
        g1=0.006, support=500, tuple_scope="single", exact=True,
    )
    # A DC is ¬(p1 ∧ p2): renders as "if status='shipped' then NOT ship_date<order_date"
    assert "status = 'shipped'" in dc.render()
    assert dc.columns() == ("status", "ship_date", "order_date")  # ordered, de-duped
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError`). `$PY -m pytest packages/python/goldencheck/tests/denial/test_models.py -v`

- [ ] **Step 3: Implement `constants.py` + `models.py`.**
```python
# constants.py
MAX_LITERAL_CARD = 50      # only mine equality literals on columns with <= this many distinct values
MIN_SUPPORT = 0.01         # a literal/predicate must apply to >= this fraction of rows
MAX_PREDICATES = 64        # per evidence pass; mask fits one u64
DEFAULT_SAMPLE = 2000      # S for the pairwise pass
VALIDATION_SAMPLE = 20000  # bounded sample for cross-tuple g1 validation
DEFAULT_EPS = 0.05         # g1 threshold: keep DCs violated by <= eps of elements
MAX_CONSTRAINTS = 20       # top-N reported
```
```python
# models.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

__all__ = ["Op", "Predicate", "DenialConstraint"]

class Op(Enum):
    EQ = "="; NE = "≠"; LT = "<"; LE = "≤"; GT = ">"; GE = "≥"

@dataclass(frozen=True)
class Predicate:
    kind: str          # "const" (t.A = literal) | "single" (t.A op t.B) | "cross" (tα.A op tβ.B)
    col_a: str
    op: Op
    col_b: str | None  # None for const predicates
    literal: object | None

    def render(self) -> str:
        if self.kind == "const":
            return f"{self.col_a} {self.op.value} {self.literal!r}".replace('"', "'")
        return f"{self.col_a} {self.op.value} {self.col_b}"

@dataclass(frozen=True)
class DenialConstraint:
    predicates: tuple[Predicate, ...]
    g1: float                 # fraction of elements (rows or pairs) that violate
    support: int              # elements the DC's condition applies to
    tuple_scope: str          # "single" | "cross"
    exact: bool               # True = g1 measured exactly on full data; False = sampled estimate

    def columns(self) -> tuple[str, ...]:
        seen: list[str] = []
        for p in self.predicates:
            for c in (p.col_a, p.col_b):
                if c and c not in seen:
                    seen.append(c)
        return tuple(seen)

    def render(self) -> str:
        # ¬(p1 ∧ … ∧ pm). Present the first const/single predicate as the "if", rest as "then not".
        parts = [p.render() for p in self.predicates]
        return "¬(" + " ∧ ".join(parts) + ")"
```
(The friendlier "if…then not…" phrasing lives in the Finding message, Task 7 — `render()` stays canonical.)

- [ ] **Step 4: Run → PASS.** Adjust the `render()` assertion in the test if you choose the canonical `¬(…)` form (update the test to assert on that; keep the "if…then" phrasing for the Finding, not the model).

- [ ] **Step 5: Commit.** `git add goldencheck/denial/ tests/denial/test_models.py && git commit -m "feat(goldencheck): denial-constraint models + constants"`
(Create `tests/denial/__init__.py` and `goldencheck/denial/__init__.py` empty-ish as needed.)

---

### Task 2: `predicates.py` — encoding + bounded predicate space

**Files:** Create `goldencheck/denial/predicates.py`; Test `tests/denial/test_predicates.py`

- [ ] **Step 1: Failing tests.** Cover: (a) categorical columns get first-seen ids; (b) numeric/temporal get **order-preserving rank** ids (id order matches value order); (c) a null operand makes an order predicate unsatisfiable; (d) equality literals only enumerated for low-cardinality columns with support ≥ `MIN_SUPPORT`; (e) predicates partition into `single`/`cross`; (f) Pass-1 budget `s ≤ 64`, Pass-2 budget `2s + c ≤ 64`, with a support prefilter + a reported cap flag when exceeded.
```python
from goldencheck.denial.predicates import encode_columns, build_predicate_space
import polars as pl

def test_numeric_rank_is_order_preserving():
    enc = encode_columns(pl.DataFrame({"x": [30, 10, 20, 10]}))
    ids = enc["x"].ids
    # value order 10<20<30 must be reflected in id order
    assert ids[1] < ids[2] < ids[0] and ids[1] == ids[3]

def test_null_operand_predicate_unsatisfiable():
    enc = encode_columns(pl.DataFrame({"a": [1, None, 3], "b": [2, 2, 2]}))
    # a < b on row 1 (a is null) must be reported unsatisfiable, not "0 < 2"
    ...  # assert the predicate evaluator returns False for the null row

def test_literal_gating_low_card_only():
    df = pl.DataFrame({"country": ["US"]*80 + ["CA"]*20, "id": list(range(100))})
    space = build_predicate_space(df)
    lits = [p for p in space.predicates if p.kind == "const"]
    assert any(p.col_a == "country" and p.literal == "US" for p in lits)   # low-card, high support
    assert not any(p.col_a == "id" for p in lits)                          # id is high-card -> no literals

def test_pass2_budget_counts_singletuple_twice():
    space = build_predicate_space(big_df)  # engineered to exceed 64
    assert space.pass2_effective == 2*space.n_single + space.n_cross
    assert space.capped is True  # reported, not silent
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `encode_columns` + `build_predicate_space`.** Key points:
  - `encode_columns(df) -> dict[str, EncodedColumn]`: per column, classify type (categorical=Utf8/Boolean/low-card-int; numeric=Int/Float; temporal=Date/Datetime). Categorical → first-seen dense ids (null→0, matching `relations/approx_fd._intern`). Numeric/temporal → **dense rank of sorted distinct non-null values** (null→sentinel 0; real values → 1..k in ascending order) so `<`/`>` on ids equals value order. Keep a `nulls: list[bool]` per column.
  - Predicate evaluation must treat any predicate with a null operand as **not satisfied** (checked via the `nulls` mask), never comparing the sentinel id.
  - `build_predicate_space(df) -> PredicateSpace` with `.predicates` (list[Predicate]), `.n_single`, `.n_cross`, `.pass2_effective = 2*n_single + n_cross`, `.capped: bool`. Enumerate: const (`t.A = c` for low-card cols, support ≥ MIN_SUPPORT), single (`t.A op t.B` same-tuple, type-compatible), cross (`tα.A op tβ.B`). Type-gate operators (`{EQ,NE}` categorical; full set numeric/temporal). Apply the support prefilter to respect `MAX_PREDICATES` per pass (Pass-1 counts `n_single`≤64; Pass-2 counts `2*n_single + n_cross`≤64); set `.capped=True` when trimming.

- [ ] **Step 4: Run → PASS.** Ruff clean.

- [ ] **Step 5: Commit.** `feat(goldencheck): denial-constraint predicate space + order-preserving encoding`

---

### Task 3: `evidence.py` — pure-Python evidence maps (reference)

**Files:** Create `goldencheck/denial/evidence.py`; Test `tests/denial/test_evidence.py`

- [ ] **Step 1: Failing test** on a tiny hand-verified table: given a predicate space + encoded columns, `row_evidence(...)` returns `{mask: row_count}` where bit i set ⇔ single-tuple predicate i holds for that row; `pair_evidence(..., sample_idx)` returns `{mask: pair_count}` over sampled ordered pairs with the Pass-2 bit layout (single-tuple preds occupy 2 slots: tα-slot + tβ-slot). Assert a hand-computed mask.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement (pure Python, correctness reference).**
  - `row_evidence(space, enc, n) -> dict[int,int]`: for each row, build a u64 over single-tuple predicates (const + same-tuple), honoring the null rule; count distinct masks.
  - `pair_evidence(space, enc, idx) -> dict[int,int]`: for each ordered pair (α,β) in `idx`, set bits: single-tuple preds evaluated on α → their α-slot; on β → their β-slot; cross preds → their cross-slot. Count distinct masks. This is O(S²·|P|) and intentionally slow — it's the reference; the native/Polars fast path (Task 8/9) replaces it for real S.
  - Document the exact bit layout in a module docstring (α-slots [0..s), β-slots [s..2s), cross-slots [2s..2s+c)) — the plan's Task 8 kernel must match it byte-for-byte.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit.** `feat(goldencheck): pure-Python denial-constraint evidence maps (reference)`

---

### Task 4: `discover.py` — FastDC minimal-cover derivation

**Files:** Create `goldencheck/denial/discover.py`; Test `tests/denial/test_discover.py`

- [ ] **Step 1: Failing tests.** (a) On an evidence map with a planted "never co-satisfied" predicate set, the minimal DC is recovered. (b) **Complement bit-masking:** a candidate must not be reported valid via phantom high bits — verify `complement(mask, p) == (~mask) & ((1<<p)-1)` and that a DC over `p<64` predicates isn't spuriously satisfied. (c) Approximate: with ε>0, a predicate set violated by ≤ε·N elements is returned; >ε·N is not. (d) Minimality: no returned DC is a superset of another. (e) Determinism.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `discover(evidence, n_predicates, total, eps) -> list[frozenset[int]]`.**
  A DC = a predicate set X (bitmask) such that the count of evidence elements whose mask ⊇ X is ≤ eps·total ("approximately never fully satisfied"). **Concrete reference algorithm** (correct + directly codable — do NOT try to reconstruct FastDC's hitting-set recursion):
  ```
  ARITY_BOUND = 4
  def satisfied_count(X, evidence):        # elements where ALL of X's bits are set
      return sum(cnt for mask, cnt in evidence.items() if (mask & X) == X)
  # Generate candidate predicate sets by INCREASING size (1, 2, … up to ARITY_BOUND).
  # A single-predicate X0 is already a DC if satisfied_count(X0) <= eps*total
  #   (that predicate is ~never true -> a trivial always-false predicate; usually filtered).
  # Combine surviving smaller candidates into larger ones (only over predicate bits actually
  #   present, i.e. bits appearing set in some evidence mask -> the "active" predicate set).
  found = []                               # list of DC bitmasks, minimal
  for size in 1..=ARITY_BOUND:
      for X in combinations(active_predicate_bits, size):
          Xmask = OR of the chosen bits
          if any(f for f in found if (f & Xmask) == f):   # superset of an existing DC -> not minimal
              continue
          if satisfied_count(Xmask, evidence) <= eps*total:
              found.append(Xmask)         # minimal by construction (smaller sizes checked first)
  ```
  The `complement = (~m) & ((1<<n_predicates)-1)` low-bit guard (dedicated test) is used when you
  precompute per-element "predicates NOT satisfied" for pruning the `active_predicate_bits` / the
  combination space — the phantom-high-bit mask must be applied there too. `ARITY_BOUND` keeps the
  `combinations` space tractable; note the bound in the output. Return minimal DC bitmasks +
  their satisfied-count (→ g1 later). This brute-force-by-increasing-size reference is what the
  Task-4 tests assert; a smarter FastDC DFS is a Stage-2 optimization, not required here.
  Provide `rank(dcs, ...)` by interestingness (support × succinctness), cap `MAX_CONSTRAINTS`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit.** `feat(goldencheck): FastDC minimal-cover denial-constraint derivation`

---

### Task 5: `validate.py` — g1 validation + violating rows/pairs

**Files:** Create `goldencheck/denial/validate.py`; Test `tests/denial/test_validate.py`

- [ ] **Step 1: Failing tests.** Plant a single-tuple DC + exactly K violating rows in a Polars df → `validate_single_tuple` returns g1=K/n and the exact K row indices. For a cross-tuple DC, `validate_cross_tuple` on a bounded sample returns an estimated g1 within tolerance + up to R representative violating pairs; a candidate exceeding the ε threshold is dropped.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - `validate_single_tuple(dc, df) -> (g1, violating_row_indices)`: translate the DC's single-tuple predicates into a vectorized Polars boolean expression (the DC is violated by a row when ALL its predicates hold); `df.with_row_index().filter(all_preds)` → exact rows; g1 = len/height. O(n).
  - `validate_cross_tuple(dc, df, sample) -> (g1_est, representative_pairs)`: on a bounded validation sample, evaluate the pairwise predicates via the same evidence machinery (or the native kernel once available) counting violating pairs; g1_est = viol/|sample|²; collect ≤R example pairs. Attach a confidence note (sample size).

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit.** `feat(goldencheck): denial-constraint g1 validation (exact single-tuple, sampled cross-tuple)`

---

### Task 6: `mine.py` — orchestrator + public API + FP guard

**Files:** Create `goldencheck/denial/mine.py`; Test `tests/denial/test_mine.py`

- [ ] **Step 1: Failing tests.** (a) End-to-end: a synthetic df with a planted single-tuple DC (`status=shipped ⇒ ship_date≥order_date`) + K exceptions → `discover_denial_constraints(df)` returns that DC with g1≈K/n, `exact=True`, and the K violating rows. (b) **FP guard:** a df of independent random columns yields ≤ a small number of DCs (ideally 0) — the ε + min-support + minimality gates hold. (c) Determinism under a fixed seed.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `discover_denial_constraints(df, *, min_confidence=1-DEFAULT_EPS, sample_size=DEFAULT_SAMPLE, max_constraints=MAX_CONSTRAINTS, seed=0) -> list[DenialConstraint]`.** Pipeline: encode → build predicate space → Pass-1 row_evidence over full n (or scan sample) + Pass-2 pair_evidence over a seeded S-row sample → `discover` each pass → `validate` candidates (single-tuple exact, cross-tuple sampled), drop those over ε → rank → top-N. Guard: `df.height < MIN_ROWS` (reuse 100) → `[]`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Build + test `DenialConstraintProfiler` (Finding mapping).** Add a `DenialConstraintProfiler` class in `mine.py` with `profile(df) -> list[Finding]` mirroring `ApproximateFDProfiler` (Task's read of `relations/approx_fd.py`). Write a unit test asserting the Finding mapping directly (not just via the CLI later): a near-DC → `Severity.WARNING`, `check="denial_constraint"`, `column` = the joined predicate-column string (spec lines 187-189), plain-English message ("if status='shipped' then ship_date≥order_date — holds 99.4%, N rows violate"), `metadata` carrying the structured DC + `exact` bool + g1 + support (spec lines 180-198); a strict DC (g1=0) → `Severity.INFO`. Run → PASS.

- [ ] **Step 6: Commit.** `feat(goldencheck): denial-constraint orchestrator + public API + Finding-emitting profiler`

---

## WAVE B — native kernel (measure-first; ships only if it beats Polars cross-join)

### Task 7: `goldencheck-core/src/dc.rs` — evidence-set bitmask kernels

**Files:** Create `packages/rust/extensions/goldencheck-core/src/dc.rs`; Modify `goldencheck-core/src/lib.rs`; Rust tests in `dc.rs`

- [ ] **Step 1: Failing Rust unit tests** in `dc.rs` `#[cfg(test)]`: `row_evidence(single_preds, cols, n) -> Vec<(u64,u64)>` and `pair_evidence(preds, cols, sample_idx) -> Vec<(u64,u64)>` produce the hand-verified distinct-mask→count maps for a tiny encoded table, using the EXACT bit layout from Task 3 (α-slots [0..s), β-slots [s..2s), cross-slots [2s..2s+c)).

- [ ] **Step 2: Run → FAIL.** `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml dc`

- [ ] **Step 3: Implement `dc.rs`** over slice-based encoded columns (`&[&[u64]]` ids + `&[&[bool]]` null masks + a predicate spec = list of `(kind, col_a, op, col_b_or_literal_id)`). Build `FxHashMap<u64,u64>` of masks. Honor the null rule (null operand ⇒ bit unset). Match the Python reference byte-for-byte. Add `mod dc;` + `pub use dc::{row_evidence, pair_evidence};` to `lib.rs` (and update the lib docstring line that says "no order" — the DC kernel adds ordered comparisons over rank-encoded ids).

- [ ] **Step 4: Run → PASS.** `cargo fmt` + `cargo clippy --manifest-path .../goldencheck-core/Cargo.toml -- -D warnings` clean.

- [ ] **Step 5: Commit.** `feat(goldencheck-core): denial-constraint evidence-set kernels (dc.rs)`

---

### Task 8: `goldencheck-native` shim + registration

**Files:** Create `goldencheck-native/src/dc.rs`; Modify `goldencheck-native/src/lib.rs`

- [ ] **Step 1:** Add `mod dc;` + register `denial_constraint_evidence` in the `#[pymodule]` (mirror the existing `wrap_pyfunction!` lines).
- [ ] **Step 2:** Implement `#[pyfunction] denial_constraint_evidence(...)` — decode the encoded columns (already interned/ranked on the Python side, passed as Arrow int arrays) + null masks + the predicate spec (a list of tuples) + `pass` selector + `sample_idx`, call `goldencheck_core::{row_evidence|pair_evidence}`, return the mask→count map as two parallel lists `(list[int] masks, list[int] counts)` (parallel-lists contract for the SQL surfaces; the mask→count dict is rebuilt on the Python side).
- [ ] **Step 3:** Build: `cd packages/rust/extensions/goldencheck-native && /d/show_case/goldenmatch/.venv/Scripts/maturin.exe develop --release` → `Finished`; `cargo clippy --release -- -D warnings` clean.
- [ ] **Step 4:** Smoke: `$PY -c "import goldencheck_native._native as m; print(hasattr(m,'denial_constraint_evidence'))"` → True.
- [ ] **Step 5: Commit.** `feat(goldencheck-native): denial_constraint_evidence Arrow shim + registration`

---

### Task 9: `core/kernels.py` entry + loader gate + parity

**Files:** Modify `goldencheck/core/kernels.py`, `goldencheck/core/_native_loader.py`; Modify `goldencheck/denial/evidence.py` (call the fast path); Test `tests/core/test_kernels.py` (+ `tests/denial/test_evidence_parity.py`)

- [ ] **Step 1: Failing parity test.** For random encoded tables, `denial_constraint_evidence(...)` (native path forced on) equals the pure-Python `row_evidence`/`pair_evidence` reference (byte/set-identical mask→count maps), across null-heavy + tie-heavy data.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - `core/kernels.py::denial_constraint_evidence(encoded_cols, null_masks, pred_spec, which_pass, sample_idx) -> dict[int,int]`: native-try (build pyarrow arrays, call `native_module().denial_constraint_evidence(...)`, rebuild dict from parallel lists) / except → pure-Python reference from `denial.evidence`. Add to `__all__`. Document the richer signature (predicate spec in, mask-map out) vs the column-only entries.
  - `_native_loader.py`: add `"denial_constraint": ("denial_constraint_evidence",)` to `_COMPONENT_SYMBOLS`.
  - `denial/evidence.py`: route `row_evidence`/`pair_evidence` through `core.kernels.denial_constraint_evidence` (keeping the pure-Python bodies as the fallback the kernel entry calls).

- [ ] **Step 4: Run → PASS.** Parity green both lanes (`GOLDENCHECK_NATIVE=1` and `=0`).

- [ ] **Step 5: Commit.** `feat(goldencheck): native-gated denial_constraint_evidence entry + parity`

---

### Task 10: Measure-first benchmark (kernel vs Polars cross-join) → gate decision

**Files:** Create `packages/python/goldencheck/benchmarks/denial_evidence_benchmark.py`

- [ ] **Step 1:** Write a bench that builds pair evidence three ways on a realistic table (S=2000, ~10 columns): (a) native kernel, (b) a **Polars cross-join baseline** (`df.join(df, how="cross")` → predicate expressions → bit-pack → `group_by(mask).len()`), (c) pure-Python reference (small S only). Report 5-run median wall for each.
- [ ] **Step 2: Run** and record numbers in the benchmark file header + the spec's "measure-first" note.
- [ ] **Step 3: Decision.** If native beats the Polars cross-join meaningfully (expected — avoids S² row materialization), keep native default-on (via the loader gate). If NOT, leave the loader gate but make the *default* engine the Polars cross-join path (add it as a third `evidence.py` branch) and mark the kernel opt-in — do NOT ship a kernel that lost (per `feedback_default_to_fast_path` + the Wave-0 lesson). Document the outcome.
- [ ] **Step 4:** No test; the bench is the artifact.
- [ ] **Step 5: Commit.** `bench(goldencheck): denial-constraint evidence — native vs Polars cross-join`

---

## WAVE C — surfaces + integration

### Task 11: Public API export

**Files:** Modify `goldencheck/__init__.py`; Test `tests/denial/test_public_api.py`

- [ ] **Step 1: Failing test:** `from goldencheck import discover_denial_constraints, DenialConstraint` works and `"discover_denial_constraints" in goldencheck.__all__`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3:** Add `discover_denial_constraints`, `DenialConstraint` to `goldencheck/__init__.py` `__all__` + imports (follow the existing lazy/eager pattern used for `create_baseline`/`functional_dependencies`).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit.** `feat(goldencheck): export discover_denial_constraints in public API`

---

### Task 12: CLI command + scan opt-in (`--denial`, NOT `--deep`)

**IMPORTANT — `--deep` is a scan-SCOPE flag, not a profiler hook.** Verified: `cli/main.py`
help = "Profile the full dataset (skip the 100K sample cap)"; `engine/scanner.py`
`sample = df if deep else maybe_sample(df, ...)` — its ONLY effect is input size, and every
`RELATION_PROFILERS` entry runs unconditionally. So DC discovery must NOT be added to
`RELATION_PROFILERS` (that runs on the default scan, breaking the opt-in promise), and must NOT be
silently gated on `--deep` (that would make anyone wanting full-population profiling of the
*existing* checks also pay DC cost with no opt-out). Instead:
- **Standalone `denial-constraints` CLI + the public `discover_denial_constraints()` API are the
  primary Stage-1 surfaces** (clean, dedicated opt-in).
- **Scan-path integration is behind a NEW dedicated `--denial` flag** (its own opt-in). `--deep`
  then only controls whether the DC engine (when `--denial` is on) runs Pass-1 over the full
  population vs the sample — matching the spec's "`--deep` widens Pass-1 to the full table."

This refines the spec's looser "--deep scan path" wording; the substance (opt-in, no default cost)
is unchanged.

**Files:** Modify `goldencheck/cli/main.py`, `goldencheck/engine/scanner.py`; Test `tests/cli/test_denial_cli.py`

- [ ] **Step 1: Failing test** (use `typer.testing.CliRunner().invoke(app, [...])` like `tests/cli/test_cli.py`; introspect click params rather than scraping Rich `--help`, per `feedback_no_scrape_rich_help_in_tests`): (a) a `denial-constraints` command exists taking a file path + `--min-confidence`/`--sample-size`/`--max-constraints`, and on a fixture with a planted DC prints the discovered rule; (b) `scan --denial` includes denial-constraint findings; (c) plain `scan` and `scan --deep` (without `--denial`) do NOT — no added default cost.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.** Add the `denial-constraints` Typer command (reads file via `read_file`, calls `discover_denial_constraints`, renders via the existing reporter/console). Add a `--denial` flag to the `scan` command, thread it into `scan_file`/`_scan_dataframe_impl` (the `deep` bool is already threaded there — add `denial` alongside it), and invoke `DenialConstraintProfiler.profile(df)` (from `mine.py`, Task 6) in a new `if denial:` branch (NOT in `RELATION_PROFILERS`). When `denial` is on, `deep` selects full-population vs sampled Pass-1. Keep the default scan and plain `--deep` untouched.

- [ ] **Step 4: Run → PASS.** Full denial suite green both lanes; plain `scan` AND `scan --deep` on a fixture show zero denial findings + no measurable slowdown; `scan --denial` shows them.

- [ ] **Step 5: Commit.** `feat(goldencheck): denial-constraints CLI + --denial scan opt-in`

---

### Task 13: Docs

**Files:** Modify `packages/python/goldencheck/CLAUDE.md` (+ README if it lists checks)

- [ ] **Step 1:** Add a "denial-constraint discovery" section to `CLAUDE.md`: the two-pass engine, the encoding (rank ids for order predicates), the native gate + measure-first outcome from Task 10, the opt-in surfaces (`--deep` + `denial-constraints` CLI + `discover_denial_constraints`), and the Stage-1 non-goals (cross-table, config pinning, SQL/WASM/MCP → later stages). Follow the terse style of the existing native section.
- [ ] **Step 2:** No test.
- [ ] **Step 3: Commit.** `docs(goldencheck): document denial-constraint discovery (Stage 1)`

---

## Done criteria (Stage 1 complete)

- [ ] `discover_denial_constraints(df)` mines single-tuple + cross-tuple approximate DCs; planted DCs recovered with correct g1 + exact violating rows (single-tuple); random data yields ~no spurious DCs.
- [ ] Pure-Python reference is the parity oracle; the native `dc.rs` kernel is byte/set-identical and gated (`GOLDENCHECK_NATIVE`), shipped default-on only if it beat the Polars cross-join baseline (Task 10 decision documented).
- [ ] Opt-in only: `--deep` + `denial-constraints` CLI + public API; the default scan is unchanged (no added cost).
- [ ] Both native and fallback lanes green; ruff + clippy clean.
- [ ] No Stage-2+ scope crept in (no cross-table, config pinning, incremental, or SQL/WASM/MCP surfaces).
