# 0035 — Denial-constraint discovery (Stage 1)

**Status:** Shipped • **Shipped:** 2026-07-08

## Context

GoldenCheck already discovers single- and cross-column rules: functional
dependencies, composite keys, temporal orders, numeric cross-column bounds,
approximate-FD violations. The gap was the general class of *if-then /
cross-tuple invariants* — `¬(status=shipped ∧ ship_date<order_date)` ("if
shipped, `ship_date` must be ≥ `order_date`") — that no existing profiler
expresses. These are **denial constraints** (DCs), `¬(p1 ∧ … ∧ pm)`: a
predicate set that must never all hold at once, either within a row or across a
pair of rows. They subsume FDs, keys, and ordering as special cases but are
strictly more expressive, and they're what a data-entry error most often
violates.

Mining DCs naively is expensive (the predicate space is combinatorial and the
cross-tuple evidence is O(n²)), so it can't ride the default sampled scan — it
has to be opt-in and it has to earn a native kernel the same way every other
deep-profiling check did.

## Decision

**Add denial-constraint discovery as a new, opt-in discovered-rule family**
(`goldencheck/denial/`), surfaced through a public API
(`discover_denial_constraints(df, ...)` + the exported `DenialConstraint`), a
`goldencheck denial-constraints` CLI command, and a `--denial` opt-in flag on
`goldencheck scan`. It is **not** part of the default scan.

Load-bearing design points:

- **Sample-then-validate.** The engine samples the table to nominate candidate
  DCs cheaply, then validates the survivors. Predicates use an
  **order-preserving RANK encoding** for numeric/temporal columns (so `<`/`≤`
  comparisons are meaningful) and first-seen encoding for categoricals —
  deliberately *not* `intern_column`, whose hash-order would destroy the
  ordering the comparison predicates need. Null operand ⇒ predicate false;
  equality literals are gated to low-cardinality / high-support columns.
- **Two evidence passes.** Pass-1 is **row-level, exact** (single-tuple DCs,
  O(n)) and yields the precise violating rows. Pass-2 is **pairwise over S²
  sampled ordered pairs** (cross/mixed DCs), so its violation fraction `g1` is
  sampled, not exact. Evidence is carried as `u64` bitmasks; the minimal cover
  is derived FastDC-style.
- **Native `dc.rs`, measure-first.** The evidence kernel lives in the pyo3-free
  `goldencheck-core::dc.rs` (slice-based), gated on `GOLDENCHECK_NATIVE`
  (`_COMPONENT_SYMBOLS["denial_constraint"] = ("denial_constraint_evidence",)`)
  and set/byte-parity tested against the pure-Python fallback. It cleared the
  same "beat Polars" gate as the other kernels — ~1.5–1.8× over a Polars
  cross-join and ~60–96× over pure Python at m=1500 — before defaulting on.
- **Findings.** DC findings surface as `check="denial_constraint"`, WARNING when
  violated / INFO for a strict invariant (`g1=0`), with `column` set to the
  joined predicate columns.
- **Stage-1 gates (configurable).** `arity_bound=2`,
  `require_order_comparison=True` (pure all-equality DCs are suppressed — those
  are the accepted-values / uniqueness / FD family and already covered), and
  self-column cross-tuple comparisons are dropped. `--deep` (with `--denial`)
  widens the row-level Pass-1 to the full population instead of the sample.

## Consequence

GoldenCheck gains a genuinely more expressive rule family without touching the
zero-config default surface — purely additive, opt-in, and byte/set-parity
between native and pure Python. It's the first of a **5-stage program**; Stage 1
is single-table, arity ≤ 2, order-comparison DCs. Deferred to later stages:
cross-table DCs, numeric-threshold literals, config/baseline pinning of accepted
DCs, DC drift, and the DuckDB / Postgres / WASM / MCP surfaces the other checks
already have.
