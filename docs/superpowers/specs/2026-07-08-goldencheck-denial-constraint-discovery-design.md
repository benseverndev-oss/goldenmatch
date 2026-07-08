# GoldenCheck denial-constraint discovery — Stage 1 (single-table, approximate)

Date: 2026-07-08
Status: design — approved in brainstorming, pending spec review
Base: branched off fresh `origin/main` (goldencheck 1.4.1)
Parent program: "Denial-constraint discovery" (5 stages; this is Stage 1)

## Context

GoldenCheck ("data validation that discovers rules from your data", v1.4.1) already
discovers several rule families: strict + approximate **functional dependencies**
(`relations/functional_dependency.py`, `relations/approx_fd.py`), **composite keys**
(`relations/composite_key.py`), regex **grammars**, distributions/bounds, correlations,
semantic types, and Benford conformance. Its native layer is a pyo3-free slice-based
`goldencheck-core` crate reused across Python (`goldencheck-native`), WASM
(`goldencheck-wasm`), DuckDB, and Postgres/pgrx via list-shaped entry points in
`goldencheck/core/kernels.py`.

The recurring gap in DQ tooling that GoldenCheck lacks is **conditional / denial
constraints** — the canonical "if-then" and cross-tuple invariants that generalize FDs
and if-then checks. This program adds DC discovery as a new discovered-rule family.

## The program (each stage its own spec → plan → build)

| Stage | Scope | Status |
|---|---|---|
| **1 (this spec)** | Single-table, **approximate** DC discovery engine; bounded predicate space (variable cross-tuple comparisons + bounded equality literals); ranked near-DCs + violating rows as `Finding`s; opt-in. | design |
| 2 | Scale: the O(n²) evidence problem — Hydra-style focused sampling, native-kernel perf, larger-than-sample recall. | future |
| 3 | Config + baseline integration: pin discovered DCs, validate incrementally, DC drift. | future |
| 4 | Cross-table DCs, numeric-threshold literals, richer predicates. | future |
| 5 | Surfaces: DuckDB/Postgres/WASM/MCP exposure. | future |

### Decisions fixed in brainstorming (apply to Stage 1)

- **Predicate space:** variable predicates (cross-tuple attribute comparisons) **AND** bounded
  constant (literal) predicates. Literals restricted to frequent values of low-cardinality
  columns — the explosion control.
- **Engine:** Approach A **sample-then-validate** — sample S rows, build the *exact* evidence
  set over S² pairs (native kernel), derive minimal candidate DCs, validate each candidate's
  g1 error against the real data, attach violating rows. Precision protected by validation;
  recall bounded by the sample (accepted first-cut trade; Hydra refinement is Stage 2).
- **Placement:** **opt-in** — not in the default scan. `--deep`, a `goldencheck
  denial-constraints` CLI, and a public `discover_denial_constraints(df, ...)` API.
- **Kernel:** slice-based `goldencheck-core` (`dc.rs`), reachable from `core/kernels.py`
  list-shaped surface, pure-Python fallback, `GOLDENCHECK_NATIVE`-gated, measure-first.

## Scope

### In scope (Stage 1)
Discovery AND violation-surfacing (mirrors `approx_fd`: find near-FDs + return violating rows).
Single-table. Approximate DCs (g1 threshold). Bounded predicate space (below). Opt-in entry
points. Native evidence kernel + Python fallback.

### Explicitly NOT in scope (later stages)
Cross-table DCs; numeric-threshold literals (only equality literals in Stage 1); config/baseline
pinning; incremental / DC-drift; DuckDB/Postgres/WASM/MCP surfaces; full-table *exact*
cross-tuple validation (bounded validation sample instead).

### Success criteria
- On a synthetic table with a planted DC + K known exceptions, discovery returns that DC with
  g1 within tolerance and, for single-tuple DCs, the exact K violating rows.
- Random/independent columns yield few or no spurious DCs (min-support + minimality + ε gates).
- Native evidence set is byte/set-identical to the Python fallback (parity-tested).
- The default scan path is unchanged (zero added cost when the feature isn't invoked).

## Architecture

New subpackage `goldencheck/denial/` (small, single-responsibility modules):

| Module | Responsibility |
|---|---|
| `models.py` | `Predicate(col_a, op, col_b_or_const, kind)`, `DenialConstraint(predicates, g1, support, tuple_scope, exact)` |
| `predicates.py` | Build the bounded predicate space over typed columns; intern columns |
| `evidence.py` | Evidence-set construction (native kernel call + pure-Python fallback) |
| `discover.py` | Minimal-DC derivation — hitting-set / minimal-cover search over the evidence set; approximate (ε) DCs; interestingness ranking |
| `validate.py` | g1 validation on the real data + violating-row/pair extraction (Polars) |
| `mine.py` | Orchestrator: sample → predicates → evidence → discover → validate → rank → `Finding`s |

Native: `goldencheck-core/src/dc.rs` (evidence-set bitmask build, slice-based) +
`goldencheck-native` shim (Arrow → interned slices) + a list-shaped
`denial_constraint_evidence(...)` entry in `goldencheck/core/kernels.py`.

## The bounded predicate space (`predicates.py`)

For columns typed categorical / numeric / temporal, enumerate:
- **Variable predicates** over a tuple pair (t_α, t_β): for each *type-compatible* column pair
  (A, B), operators gated by type — `{=, ≠}` for categorical, `{=, ≠, <, ≤, >, ≥}` for
  numeric/temporal. Both **same-tuple cross-column** (`t_α.A op t_α.B`) and **cross-tuple**
  (`t_α.A op t_β.B`).
- **Constant predicates (bounded):** `t.A = c` only for `c` ∈ frequent values of
  **low-cardinality** columns (cardinality ≤ `MAX_LITERAL_CARD`, value support ≥ `MIN_SUPPORT`).

Columns are interned with the existing `intern_column` pattern (categorical → dense ids;
numeric/temporal kept as orderable values). **Hard bound: |P| ≤ 64**, so a per-pair
satisfaction mask fits one `u64` — the evidence kernel depends on it. If the raw predicate
space exceeds 64, keep the most-supported predicates first (support prefilter) and **report the
cap in the output** (never silent truncation).

## The evidence-set kernel (`evidence.py` + `dc.rs`)

Sample S ≈ 1–2K rows (configurable, seeded). For each of the S² ordered pairs (t_α, t_β),
compute a `u64` **bitmask** where bit *i* is set iff predicate *i* holds for that pair. Collect
the **distinct masks with pair-counts** → `FxHashMap<u64, u64>`. That map *is* the evidence set.

- Cost: S=2K → S² = 4M pairs × ≤64 bit-tests — sub-second in Rust.
- This is a natural `goldencheck-core` kernel: interned columns in, evidence map out, **no Polars
  equivalent** (all-pairs bitmasking doesn't vectorize) — unlike the checks that lost to Polars.
- Pure-Python fallback mirrors it (smaller S cap), parity-tested.

## Minimal-DC derivation (`discover.py`)

A denial constraint = a **minimal set of predicates never *fully* satisfied by any tuple pair**
(no evidence mask has all those bits set) — equivalently a minimal **hitting set** of the
complemented evidence masks. Run FastDC's minimal-cover search over the evidence map.

- **Approximate DCs:** a predicate set whose total satisfied-pair count across the evidence set
  is ≤ ε·S² (approximately never satisfied). Strict DCs (count 0) are usually trivial; near-DCs
  with small ε are where real data errors live.
- Keep only **minimal, non-redundant** DCs; rank by interestingness (support × succinctness);
  cap to top-N.

## g1 validation + violating rows (`validate.py`)

Sample-derived DCs are *candidates*; validate on the real data and ship only survivors:
- **Single-tuple DCs** (predicates reference only t_α, e.g. `¬(status=shipped ∧
  ship_date<order_date)`): validated **exactly on the full table**, O(n) vectorized Polars.
  Exact violating rows → the `Finding`.
- **Cross-tuple DCs** (reference t_α *and* t_β): full-table validation is itself O(n²), so
  validate on a **bounded validation sample** (min(n, 10–20K)) via the same bitmask kernel,
  reporting an **estimated g1 with a confidence note**; surface representative violating *pairs*,
  not an exhaustive row set. A candidate whose validation-sample g1 exceeds the threshold is
  dropped (precision protection).

The exact-vs-estimated distinction is explicit in `DenialConstraint.exact` and the Finding
metadata — single-tuple rules are exact with precise violating rows (the high-value, demo case);
cross-tuple rules are honestly labeled sample-estimated.

## Output shape & integration

Findings:
- **Near-DC with violations** → `Severity.WARNING`, `check="denial_constraint"`, plain-English
  message ("`if status = shipped then ship_date ≥ order_date` — holds 99.4%, 37 rows violate"),
  `metadata` = structured DC + `exact: bool` + g1 + support; `affected_rows`/`sample_values`
  from exact violating rows (single-tuple) or representative pairs (cross-tuple).
- **Strict DC** (g1 = 0) → `Severity.INFO` (a discovered invariant, not an error).

Ranked, capped to top-N (configurable). Entry points:
- `discover_denial_constraints(df, *, min_confidence=…, sample_size=…, max_constraints=…) ->
  list[DenialConstraint]` (public API, added to `__all__`).
- `--deep` scan path (opt-in) and a `goldencheck denial-constraints data.csv` CLI command.
- Config/baseline pinning + MCP tool are Stage 3/5.

## Native kernel, measure-first, fallback

`goldencheck-core::dc.rs` owns the evidence-set build (+ the cover search if it profiles hot);
`goldencheck-native` decodes Arrow → interned slices; `core/kernels.py` gets a list-shaped
`denial_constraint_evidence(...)` so the kernel is reachable from the SQL surfaces in Stage 5.
Pure-Python fallback is byte/set-identical (parity-tested via the `tests/core/test_kernels.py`
pattern), gated by `GOLDENCHECK_NATIVE`. Per the measure-first rule (and the Wave-0 stale-base
lesson): the kernel ships only after a benchmark shows the evidence build moves the wall vs the
Python baseline on realistic S — but a kernel is the *natural* home here since all-pairs
bitmasking has no Polars vectorization.

## Testing

TDD throughout.
- **Rust:** unit-test the evidence kernel + cover search on tiny hand-verified tables.
- **Python discovery:** synthetic tables with **injected DCs** — plant a rule plus a known number
  of exceptions; assert discovery finds it with g1 within tolerance and (single-tuple) the exact
  violating rows match.
- **False-positive guard:** random/independent columns → few or no spurious DCs.
- **Parity:** native evidence set == Python fallback (byte/set-identical).
- **Determinism:** seeded sampling → reproducible output.

## Risks

- **Predicate/evidence explosion** → |P| ≤ 64 `u64` cap + low-card/min-support literal gate +
  support prefilter; cap reported, never silent.
- **Sampling recall loss** — Stage 1 finds DCs present in the sample; rare-condition DCs may be
  missed. Documented; Hydra refinement is Stage 2.
- **Cross-tuple validation cost** — bounded validation sample + honest "estimated g1"; no O(n²)
  on full data.
- **DC glut / triviality** — minimality + interestingness ranking + top-N cap; strict-DC INFO vs
  near-DC WARNING split keeps signal high.
- **Measure-first on the kernel** — benchmark before default-on; gate like every native component.

## Non-goals (YAGNI)

Cross-table DCs; numeric-threshold literals; config/baseline pinning; incremental/DC-drift;
SQL/WASM/MCP surfaces; full-table exact cross-tuple validation. All deferred to later stages.
