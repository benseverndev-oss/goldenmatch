# GoldenCheck Polars eviction — Relation ports R4 (DECLINE): age_validation + approx_duplicate + baseline/correlation

Date: 2026-07-09
Status: decision record — the decline pass that closes the relation front
Base: fresh `origin/main` (through R3 #1615; R1–R3 relation ports merged)
Parent program: goldencheck Polars eviction — relation front (see `2026-07-09-goldencheck-relation-ports-r1-design.md` for the R1–R4 roadmap)

## What this is

R4 is the **decline pass** — not a port. Three relation profilers are formally declined from the
Frame/Column seam eviction because their bodies are *inherently* Polars-shaped and have no
byte-identical non-Polars equivalent worth building at the seam. This document records that decision,
the per-file rationale, and what the later eviction stages (Stage-2 substrate, P4 deps-flip) must know.
The only code change R4 makes is a short module-docstring banner on each file; there is **no logic
change and no byte-identity risk**.

## Why these three are declined (not portable at the seam)

All three already `from goldencheck._polars_lazy import pl` (P0's package-wide lazy sweep), so
`import goldencheck` loads zero Polars and the import gate is green with them present. They are
**import-safe but Polars-coupled at runtime** — the opposite of R1–R3, which decoupled their bodies.
The coupling is in the Polars **expression API / relational engine**, which the Column-op seam
deliberately does not model (it models per-column reductions + element-wise ops, not `pl.col`
expression trees, joins, group-by, or pivots):

| File | Polars surface that blocks a seam port |
|---|---|
| `relations/age_validation.py` | A **Polars expression tree** — `df.select(actual=pl.col(age_col).cast(...), expected=((pl.lit(reference_date).cast(pl.Date) - dob_expr).dt.total_days() / 365.25))` where `dob_expr = pl.col(dob_col).str.to_date(...)`. `pl.col`/`pl.lit`/`.dt.total_days()`/date subtraction inside `select` are expression-API constructs with no Column-op equivalent. Date arithmetic is also intentionally excluded from the owned-kernel work elsewhere in the suite (dateutil/chrono non-determinism). |
| `relations/approx_duplicate.py` | `pl.concat_str` + `str.to_lowercase`/`replace_all`/`strip_chars` **expression chains** inside `df.select`, then `work.group_by("__norm__").len()` + **two real `.join()`s** (norm-counts and exact-counts) + `pl.col(...) >= 2` filter. Group-by + join are relational-engine ops the seam does not expose. |
| `baseline/correlation.py` | `sub.group_by([a, b]).agg(pl.len()).pivot(on=b, index=a, values="_cnt")` — a **`group_by().agg()` + a real `.pivot()`** — plus a hard **numpy + scipy** dependency (`pearsonr`, `chi2_contingency`) and `.to_numpy()`. Pivot has no Column-op equivalent; the profiler is numeric/stats-shaped, not row-mask-shaped. |

Routing any of these through the seam would mean either (a) adding pivot/join/group-by/expression-tree
ops to the seam (a different, much larger abstraction than the Column/Frame reductions built for R1–R3
and the column profilers), or (b) reimplementing the logic in pure Python byte-identically (large, and
for correlation impossible without numpy/scipy). Neither is justified: these are the **gnarly tail** the
program always planned to decline (mirrors [[project_goldenflow_polars_eviction]]'s "Polars as
accelerator, decline the pivot/FD-mining tail").

## Decision

- **Decline** `age_validation`, `approx_duplicate`, `baseline/correlation` from the seam port. They keep
  their raw-Polars bodies.
- **Mark them explicitly** with a module-docstring banner (below) so a future maintainer does not try to
  force them through the seam and knows they require Polars at runtime.
- They remain **import-safe** (lazy `pl`) — no change needed there; that is what keeps the import gate
  green and makes them P4-ready at *import* time.
- **The relation front (R1–R4) is complete** with this pass: R1 `identity_safe_pk`, R2 `composite_key`/
  `functional_dependency`/`approx_fd`+bridge, R3 `null_correlation`/`numeric_cross`/`temporal` ported;
  R4 the three above declined.

## The docstring banner (appended to each file's module docstring)

A concise note, tailored per file, of the form:

> **Polars-accelerator-only (declined from the seam eviction, R4).** This profiler's core is
> `<the blocking Polars surface>`, which has no byte-identical Column/Frame-seam equivalent, so it is
> **not** routed through the seam. It stays import-safe (lazy `pl`) but requires Polars at runtime. See
> `docs/superpowers/specs/2026-07-09-goldencheck-relation-ports-r4-decline.md`.

No `# noqa`, no logic change, no new import. (These files are intentionally NOT grep-clean of
`polars`/`pl.` — that is the point of the decline; there is no per-file grep-clean *test*, only the
import gate, which stays green.)

## What Stage-2 (substrate) and P4 (deps-flip) must know

- **Stage-2 non-Polars substrate:** these three profilers do NOT go through the seam, so a non-Polars
  `Frame`/`Column` backend cannot back them. They will run **only** when the Polars accelerator is
  present. A substrate build should either skip them or keep a Polars-backed path for them. If someday
  they must run substrate-native, that is a dedicated project (reimplement the expression tree /
  join+group-by / pivot+stats), not a seam port.
- **P4 deps-flip (`polars` → `[polars]` optional extra):** with Polars uninstalled, these three raise at
  **runtime** (they call `pl.` in their bodies), though `import goldencheck` still succeeds (lazy `pl`).
  A P4-era nicety (deferred, NOT in R4): wrap their `profile()` entry with a clear
  `"install goldencheck[polars] to run <check>"` error instead of a raw `ModuleNotFoundError` from the
  lazy proxy. The scanner that dispatches profilers may also want to skip declined profilers when Polars
  is absent. A grep-able sentinel (e.g. a shared marker constant) could enumerate the Polars-only files
  for that tooling — deferred until a consumer exists (YAGNI now).

## Scope

### In scope
The docstring banner on the three files + this decision record + the roadmap/memory update marking the
relation front complete.

### Explicitly NOT in scope
Any logic change to the three files; adding pivot/join/group-by/expression seam ops; a Python
reimplementation of any of the three; the P4 runtime guard / sentinel; the Stage-2 substrate; the reader;
the deps flip.

## Verification
- `import goldencheck` still loads zero Polars (import gate green — docstring edits are import-inert).
- The three modules import cleanly and their existing tests
  (`tests/relations/test_age_validation.py`, `tests/relations/test_approx_duplicate.py`, and the
  correlation/baseline tests) pass unchanged (docstring edits are behavior-inert).
- Full suite green.

## Non-goals (YAGNI)
Porting the tail; pivot/join/group-by/expression seam ops; the P4 runtime guard + sentinel; the Stage-2
backend; the reader; the deps flip.
