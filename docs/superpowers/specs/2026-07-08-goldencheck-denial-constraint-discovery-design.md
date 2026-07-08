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
Single-table. Approximate DCs (g1 threshold). Bounded predicate space (below), partitioned into
single-tuple and cross-tuple predicates driving **two evidence passes** (row-level + pairwise).
Opt-in entry points. Native evidence kernels + Python fallback. A distinct order-preserving column
encoding for the ordered (`<`/`>`) predicates.

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

Predicates **partition by arity** — this partition is load-bearing (it drives the two evidence
passes below):
- **Single-tuple predicates** (reference exactly one tuple): constant predicates `t.A = c`, and
  same-tuple cross-column comparisons `t.A op t.B`. Truth depends only on one row.
- **Cross-tuple predicates** (reference both tuples): `t_α.A op t_β.B`.

Operators are type-gated — `{=, ≠}` for categorical, `{=, ≠, <, ≤, >, ≥}` for numeric/temporal.
Constant predicates are **bounded**: `t.A = c` only for `c` ∈ frequent values of
**low-cardinality** columns (cardinality ≤ `MAX_LITERAL_CARD`, value support ≥ `MIN_SUPPORT`) —
the literal-explosion control.

**Column encoding (NOT plain `intern_column`).** The existing `intern_column`
(`goldencheck-native/src/keys.rs`) assigns *first-seen* dense ids — deliberately **not
order-preserving** — which is fine for the equality-only FD/key kernels but breaks the DC
`{<, ≤, >, ≥}` predicates. So Stage 1 owns a distinct encoding:
- **Categorical columns** → first-seen dense ids (equality only) — same as `intern_column`.
- **Numeric/temporal columns** → an **order-preserving rank encoding** (dense rank of the sorted
  distinct values) so `<`/`>` on the `u64` ids matches value order.
- **Null-comparison semantics** are defined explicitly: a predicate involving a null operand is
  **not satisfied** (three-valued → treated as false for evidence), so nulls never spuriously
  satisfy an order predicate. (The FD kernels' null-id-0 convention is equality-only and does not
  transfer to ordering.)

**Hard bound: |P| ≤ 64 per evidence pass**, so each pass's satisfaction mask fits one `u64`. The
budgets differ by pass:
- **Pass 1 (row-level):** one bit per single-tuple predicate → `|P_pass1| = s ≤ 64` (s = number of
  single-tuple predicates).
- **Pass 2 (pairwise):** each single-tuple predicate needs **two** bits — `t_α.A=c` and `t_β.A=c`
  are independent truth values both required to discover a *mixed* DC — plus one bit per cross-tuple
  predicate. So `|P_pass2| = 2·s + c ≤ 64` (c = number of cross-tuple predicates). The plan's mask
  layout must reserve the two per-tuple slots explicitly.

If a pass's raw predicate space exceeds its budget, keep the most-supported predicates first
(support prefilter, applied to that pass's *effective* predicate count including the Pass-2
doubling) and **report the cap in the output** (never silent truncation).

## The evidence-set kernels (`evidence.py` + `dc.rs`) — two passes

Single-tuple and cross-tuple DCs live in different evidence spaces; conflating them into one
S²-pair loop would replicate each single-tuple predicate S-fold and muddle the g1 accounting.
Stage 1 therefore runs **two evidence passes** (each a slice-based kernel, each ≤64 predicates →
one `u64` mask):

**Pass 1 — row-level evidence (single-tuple DCs, the headline case).** Over the **n rows** (the
scan sample or full table), each row → a `u64` mask over the *single-tuple* predicates. Collect
distinct masks with **row-counts**. O(n·|P_s|). Discovers pure if-then / check-constraint DCs
(`¬(status=shipped ∧ ship_date<order_date)`); g1 here = violating **rows** / n.

**Pass 2 — pairwise evidence (cross-tuple + mixed DCs).** Sample S ≈ 1–2K rows (seeded); over the
S² ordered pairs (t_α, t_β), each pair → a `u64` mask over the pairwise predicate set (single-tuple
predicates evaluated on each of t_α and t_β, plus the cross-tuple predicates — this is how FastDC
discovers *mixed* DCs). Collect distinct masks with **pair-counts**. O(S²·|P|). g1 here =
violating **pairs** / S².

- Cost: Pass 1 is linear; Pass 2 at S=2K → 4M pairs × ≤64 bit-tests, sub-second in Rust.
- Both are natural `goldencheck-core` kernels: interned columns + predicate specs in, evidence map
  (`FxHashMap<u64, u64>`) out.
- **Polars baseline for measure-first:** an all-pairs mask *is* expressible in Polars (a self
  `join(how="cross")` → predicate expressions → bit-pack → `group_by(mask).count()`), but it
  materializes S² rows. The kernel must be **benchmarked against that cross-join baseline** before
  shipping default-on (per the repo's measure-first rule) — the kernel's edge is avoiding the S²
  materialization, not a missing Polars capability.
- Pure-Python fallback mirrors both passes (smaller S cap), parity-tested.

## Minimal-DC derivation (`discover.py`)

A denial constraint = a **minimal set of predicates never *fully* satisfied by any evidence
element** (no evidence mask has all those bits set) — equivalently a minimal **hitting set** of
the complemented evidence masks. Run FastDC's minimal-cover search over each pass's evidence map
(row-level masks for single-tuple DCs, pairwise masks for cross/mixed DCs).

- **Complement masks are bounded to |P| bits:** `complement = (!mask) & ((1u64 << p) - 1)` — the
  naive `!mask` sets the phantom high `64−|P|` bits, which would let every DC spuriously "hit" via
  a nonexistent predicate and corrupt the cover search. Explicit low-bit AND is required.
- **Approximate DCs:** a predicate set whose total satisfied-element count is ≤ ε·N (N = n for the
  row-level pass, S² for the pairwise pass) — approximately never satisfied. "Satisfied" = the
  element has *all* the DC's predicate bits set = the element **violates** the DC. So
  g1 = violating-element-count / N. Strict DCs (count 0) are usually trivial; near-DCs with small
  ε are where real data errors live.
- Keep only **minimal, non-redundant** DCs; rank by interestingness (support × succinctness);
  cap to top-N.

## g1 validation + violating rows (`validate.py`)

Sample-derived DCs are *candidates*; validate on the real data and ship only survivors. The two
passes validate differently:
- **Single-tuple DCs** (from Pass 1, e.g. `¬(status=shipped ∧ ship_date<order_date)`): the Pass-1
  evidence is already over the full n rows (or the scan sample), so these are validated **exactly**,
  O(n) vectorized Polars, with **exact violating rows** → the `Finding`. (When Pass 1 ran on the
  scan sample rather than the full file, `--deep` widens it to the full table.)
- **Cross-tuple / mixed DCs** (from Pass 2): full-table pairwise validation is O(n²), so validate
  on a **bounded validation sample** (min(n, 10–20K)) via the same pairwise kernel, reporting an
  **estimated g1 with a confidence note**; surface representative violating *pairs*, not an
  exhaustive row set. A candidate whose validation-sample g1 exceeds the threshold is dropped
  (precision protection).

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

`Finding.column` is required but a DC spans multiple columns; set it to the **joined
predicate-column string** (e.g. `"status,ship_date,order_date"`), mirroring how the FD findings
put the determinant there. The structured per-predicate columns live in `metadata`.

Ranked, capped to top-N (configurable). Entry points:
- `discover_denial_constraints(df, *, min_confidence=…, sample_size=…, max_constraints=…) ->
  list[DenialConstraint]` (public API, added to `__all__`).
- `--deep` scan path (opt-in) and a `goldencheck denial-constraints data.csv` CLI command.
- Config/baseline pinning + MCP tool are Stage 3/5.

## Native kernel, measure-first, fallback

`goldencheck-core::dc.rs` owns both evidence-set builds (+ the cover search if it profiles hot);
`goldencheck-native` decodes Arrow → the DC encoding (categorical first-seen ids + numeric/temporal
rank ids); `core/kernels.py` gets list-shaped entries so the kernel is reachable from the SQL
surfaces in Stage 5. Note the `core/kernels.py` entry is **richer than the existing column-only
entries** (`discover_functional_dependencies` etc. take only columns): `denial_constraint_evidence`
must also receive the **predicate specification** (the encoded column pairs, operators, and literal
ids that define bits 0..|P|−1) and returns the **evidence map** (distinct `u64` mask → count) for a
pass. The plan must define this signature concretely — it is not a drop-in like the column-only
entries, and the mask-map return diverges from the "plain parallel lists" contract the DuckDB/pgrx
surfaces consume (reconciled in Stage 5).

Pure-Python fallback is byte/set-identical (parity-tested via the `tests/core/test_kernels.py`
pattern), gated by `GOLDENCHECK_NATIVE` (new `_COMPONENT_SYMBOLS` entry
`"denial_constraint": ("denial_constraint_evidence",)`). Per the measure-first rule (and the Wave-0
stale-base lesson): the kernel ships only after a benchmark shows the evidence build beats the
Polars cross-join baseline (see the evidence-kernels section) on realistic S.

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
- **Measure-first on the kernel** — benchmark the evidence build against the Polars cross-join
  baseline before default-on; gate like every native component.
- **Order-encoding + null semantics are new** (not `intern_column` reuse) — the numeric/temporal
  rank encoding and the "null operand ⇒ predicate not satisfied" rule are load-bearing for `<`/`>`
  correctness; parity-test them against the Python fallback on null-heavy + tie-heavy data.

## Non-goals (YAGNI)

Cross-table DCs; numeric-threshold literals; config/baseline pinning; incremental/DC-drift;
SQL/WASM/MCP surfaces; full-table exact cross-tuple validation. All deferred to later stages.

**Reporting gates fixed during implementation (Stage-1 defaults, all configurable):**
- **`arity_bound` default 2** (`MAX_REPORT_ARITY`): DCs are capped at 2 predicates by default —
  arity 3-4 conjunctions of independent comparisons coincidentally fall under ε on random data
  (spurious DCs) and blow up the discover search. Raise via the `arity_bound` kwarg to opt into
  wider DCs.
- **`require_order_comparison` default True:** only DCs containing ≥1 order (`<,≤,>,≥`) predicate
  are reported. Pure all-equality DCs (`¬(A=x ∧ B=y)`, `¬(A=B)`) are the accepted-values / FD /
  uniqueness family, better served by goldencheck's existing profilers and noisy to mine
  per-literal; set `require_order_comparison=False` to include them.
- **Self-column cross predicates dropped** (`tα.A op tβ.A`, incl. uniqueness `¬(tα.A=tβ.A)`) —
  already covered by the `uniqueness`/`composite_key` profilers; full cross-tuple uniqueness is
  Stage 2+.
