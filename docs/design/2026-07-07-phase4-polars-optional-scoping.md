# Phase 4 scoping: make Polars an optional accelerator

**Date:** 2026-07-07 • **Status:** Scoping (not approved for build) • **Goal:**
dependency weight / embeddability — a `pip install goldenflow` that does **not**
pull Polars.

This is the finish line of the Polars-eviction arc (Phases 0-3 shipped). It is a
**restructure, not a config flip**, so this doc scopes it into staged, shippable,
parity-gated sub-phases and surfaces the load-bearing decisions before any code.

---

## 1. Why (unchanged from the arc)

Polars is a **mandatory** dependency (`polars>=1.0` in `pyproject.toml`). The goal is
a lighter, more embeddable `goldenflow`: the default install runs the owned
transforms on the native/Arrow substrate (already built, Phases 1-3), with Polars
demoted to an **optional accelerator** (`goldenflow[polars]`) for its genuinely-fast
bulk vectorized paths. Mirrors the **TypeScript port**, which is already fully
Polars-free (`Row = Record<string, unknown>`, `Row[]`, pure-TS transforms + optional
WASM) — Phase 4 brings that architecture to Python.

> **Weight measured (4a).** On the dev env: **polars 7.8 MB**, its transitive
> **numpy 23.4 MB**, **pyarrow 84.5 MB**. Reframing: (1) the DEFAULT install's Polars
> cost is polars + numpy ≈ **31 MB** (numpy comes only via polars — confirmed gone
> from `import goldenflow` after 4a); (2) the giant is **pyarrow (84.5 MB)**, but
> pyarrow is only in the `[native]` extra, and Phases 1b-3 made the columnar path
> **pyarrow-free** — so a **separate, larger win** is dropping `pyarrow>=10` from
> `[native]` once the older `to_arrow`/`from_arrow` fused-apply path is migrated to
> the pyarrow-free `Column` C-Data interop. Track that as **4g** (native-extra
> pyarrow eviction); it may deliver more MB than the polars eviction itself.

---

## 2. Where we are (grounded in the current coupling)

`import polars` appears in **34 modules** (top-level) + 4 lazy. The coupling has
**three roles** (per the arc's analysis), at different stages of eviction:

| Role | What it is | Status |
|---|---|---|
| **Substrate** | the in-memory columnar container (`pl.DataFrame`/`pl.Series`) the engine operates on | Native `Column` + native CSV frame exist (Phases 1b-3); in-memory path still *builds* a `pl.DataFrame` at the boundary |
| **Wrapper** | transform bodies: `pl.col(c).map_batches(_series_fn)` around a native kernel / pure-Python fallback | Kernels are Polars-free (Rust core); the **wrapper + registration signatures are Polars-shaped** |
| **I/O** | `read_csv`/`write_csv`/parquet/excel | Native CSV read+write shipped (Phase 2); parquet/excel still Polars |

**The eager-import linchpin:** `import goldenflow` imports the transform registry,
which imports every transform module (`address`/`dates`/`email`/…), each with a
**top-level `import polars`**. So even a user who only touches the columnar path pays
the Polars import at `import goldenflow`. **Nothing is Polars-optional until this
chain is broken.**

**Depth:** **274** transform functions are typed on `pl.Series`/`pl.Expr`/
`pl.DataFrame`. For the **owned** transforms the real work is already in the Rust
core (the signature is a thin `map_batches` wrapper); for the **non-owned** residual
(`date_iso8601`/`phone_e164` via `dateutil`/`phonenumbers`, and the `dates` family
which is a documented owned-kernel NO-GO) the body needs a *column*, not Polars
specifically — but it's currently written against `pl.Series`.

**Already an asset:** the **`Frame` seam** (`engine/frame.py`, Phase 0) is the
abstraction point — a `Frame` Protocol + a `PolarsFrame` backend. Phase 4 adds a
`NativeFrame` backend and routes the engine + public API through `Frame` without a
hard Polars import. The seam means the engine internals largely don't change; the
work is at the backend + transform-signature + I/O + public-API layers.

---

## 3. The load-bearing decisions (surface before building)

These are genuine design choices, not settled. Each needs a call (recommendation
given); they shape everything downstream.

### D1 — The public API contract
`transform_df(df: pl.DataFrame) -> pl.DataFrame` puts Polars **in the public
signature**. Options:
- **(a) Keep `transform_df` Polars-typed but lazy** — it works only when
  `goldenflow[polars]` is installed; add a new Polars-free primary entry point
  (`transform(data)`) that accepts a path / `dict[str, list]` / Arrow table / (if
  present) a `pl.DataFrame`, returning a backend-agnostic `TransformResult`.
- **(b) Redefine `transform_df` to accept/return the native `Frame`** and provide
  `pl.DataFrame` interop only under the extra. Breaking change → a 2.0.
- **Recommendation: (a).** Non-breaking for existing Polars users; the new entry
  point is the Polars-free default. `transform_df` stays as a thin
  Polars-backend adapter over the native path.

### D2 — The default in-memory container
What does a Polars-free user's data live in between transforms? Options: a native
`Frame` over `dict[str, ArrowColumn]`; over `dict[str, list]` (the Phase-1 list
path, simplest, slowest); or the native Arrow `Column` set (Phases 1b-3, fastest,
pyarrow-free). **Recommendation:** native Arrow `Column` frame (already built +
parity-proven), with the `dict[str, list]` path as the zero-dep fallback for
platforms without the wheel.

### D3 — The 274 Polars-typed transform signatures
Owned transforms: rewrite the wrapper to dispatch on a `Column` (the kernel already
exists) — mechanical, one family at a time, parity-gated (the pattern from Phase 3).
Non-owned residual (`dates`, phone/date `map_elements`): these need a per-row Python
lib; rewrite them to take a plain column (`list`/Arrow) instead of `pl.Series` — the
body is unchanged, only the container type. **Decision:** do owned families first
(they're already columnar-covered); the residual is a smaller, self-contained batch.

### D4 — I/O under the extra
Native CSV read+write shipped. Parquet/Excel/`scan_*`/database (`connectorx`) are
Polars/pyarrow-specific. **Recommendation:** keep them behind `goldenflow[polars]` /
`goldenflow[parquet]` extras with a graceful `ImportError` ("install
goldenflow[parquet]"), exactly as the cloud connectors already do for `boto3`.

### D5 — Graceful degradation contract
A Polars-free user who invokes something not yet columnar-covered (a `dataframe`-mode
transform outside the covered set, an `expr` combination, dedup/filter/rename frame
ops) must get a **clear, actionable error** ("this needs goldenflow[polars]"), never
a crash or silent wrong answer. This is the safety guarantee that lets us ship
incrementally: coverage grows, and everything else declines *loudly*.

---

## 4. Target architecture

```
pip install goldenflow            -> native/Arrow substrate + pure-Python fallback, NO polars
pip install goldenflow[native]    -> + the compiled goldenflow-native wheel (default fast path)
pip install goldenflow[polars]    -> + polars, as an optional bulk-vectorized backend + parquet/excel I/O
```

- **`Frame`** is the engine's container; backends: `NativeFrame` (default),
  `PolarsFrame` (optional), `ListFrame` (zero-dep fallback).
- **Transforms** dispatch on a `Column`; owned families run the Rust kernel, the
  residual runs its pure-Python body over the column.
- **Public API:** a Polars-free `transform(...)` primary; `transform_df(pl.DataFrame)`
  as an optional-backend adapter (D1a).
- **`polars` moves to `[polars]`** — kept as a first-class optional accelerator, not
  deleted (its bulk `str.*` path is genuinely faster on clean data).

---

## 5. Staged sub-phases (each shippable + parity-gated)

The guardrail throughout: **no output change ever** (byte-identical, gated by the
existing cross-surface + engine-parity corpus), and the default path may be slower —
recovered by `[native]`.

- **4a — Measure + lazy-import audit. SHIPPED.** Measured the weight (above). Added a
  lazy Polars proxy (`goldenflow/_polars_lazy.py`) that imports Polars on first
  attribute access, and routed all 22 eager-chain modules through it (swapped
  `import polars as pl` → `from goldenflow._polars_lazy import pl`); also fixed the
  one module-level dereference (`connectors/file.py`'s `{".csv": pl.read_csv}` reader
  map, now resolved lazily by attribute name). Result: **`import goldenflow` loads no
  polars/numpy/pyarrow** (537 → 384 modules), byte-identical behavior (Polars loads on
  first actual use). Gated by `tests/test_lazy_polars_import.py` (subprocess asserts
  `'polars' not in sys.modules` after `import goldenflow`, + a transparency check that
  a real transform still works). Landed under the current hard `polars` dep (pure
  refactor). This is the enabler for 4b-4f.
- **4b — Polars-free execution core. SHIPPED.** The CSV file path (`transform_file`)
  was already Polars-free (Phases 2-3). 4b closed the in-memory path's last coupling:
  `Column.from_pylist` (Polars-free, pyarrow-free ingest from a Python list) +
  numeric egress in `Column.to_pylist` (Int64/Float64 → int/float) + a new
  `columnar.transform_columns_native(dict[str, list], config) -> (dict[str, list],
  Manifest)` that runs a covered config (string / numeric / split) through
  `from_pylist → owned kernels → to_pylist` with **Polars never imported**. Also
  finished the 4a lazy-import sweep — 13 more modules (connectors/domains/api/
  streaming/llm/`_chain`) that weren't loaded at `import goldenflow` time (so 4a's
  runtime scan missed them) now use the lazy proxy; `_chain` was the load-bearing one
  (on the columnar import chain). native-flow 0.24 → 0.25. Gated by
  `tests/engine/test_native_inmemory_polars_free.py` (subprocess: a
  string+numeric+split config runs in-memory AND via CSV with `'polars' not in
  sys.modules`, byte-identical). This is the first end-to-end proof that goldenflow
  transforms data (covered configs) with Polars uninstalled — the Layer-3 milestone.
  `transform_columns_native` is a standalone core (not yet wired into the default
  `transform_df`); **4c** wires it behind the public entry point.
- **4c — Polars-free public entry point. SHIPPED.** Added `goldenflow.transform(data,
  config=None)` accepting a `dict[str, list]`, returning a `ColumnarResult`
  (`.columns` dict + `.manifest`, opt-in `.to_polars()`). A covered config runs on the
  4b native core with **Polars never imported**; an uncovered config (or `config=None`
  zero-config) declines to the Polars engine via the existing `transform_df` (so it's
  byte-identical), raising a clear `install goldenflow[polars]` if Polars is absent.
  `transform_df(pl.DataFrame)` is unchanged for existing callers (D1a chosen —
  non-breaking). Gate: `tests/engine/test_public_transform_polars_free.py`
  (`transform(dict)` == `transform_df(pl.DataFrame)` for covered configs incl. splits;
  uncovered parity; pl.DataFrame-input TypeError; `to_polars()` bridge; subprocess
  polars-free check). `import goldenflow` stays Polars-free (columnar now loads at
  import for `ColumnarResult`, verified clean).
- **4d — Transform signature port. IN PROGRESS (family by family).** Mechanism: a
  transform registers an optional pure-Python `scalar` (`str|None -> value`, the SAME
  per-element fn the Polars `series` path calls via `map_elements`), and the in-memory
  columnar engine applies it op-by-op over a list — composing with owned Rust-kernel
  ops in one chain — **Polars-free**. Byte-identical because, for owned families, the
  native kernel (Polars engine) and the pure-Python scalar (columnar) are parity-gated
  equal. The CSV path stays Rust-only (can't call a Python scalar): a scalar spec is
  in-memory-ready but not file-ready. **Wave 1 (address):** `register_transform(scalar=)`
  + `TransformInfo.scalar` + a scalar-chain tier in both in-memory paths
  (`transform_columns_native` dict + `_transform_via_columns` frame); wired the 7
  address transforms (`address_standardize`/`address_expand`/`state_abbreviate`/
  `state_expand`/`zip_normalize`/`country_standardize`/`unit_normalize`). Gated by
  `tests/engine/test_columnar_scalar_chain.py` (transform(dict) == transform_df ==
  columnar-engine transform_df, data + manifest; in-memory-ready-not-file-ready;
  subprocess Polars-free).
  **Wave 2 (dates) — "own the source of truth."** Dates first looked *unportable*:
  `date_iso8601("1995")` = `"1995-01-01"` (its year-string fast path) but the pure
  `dateutil` scalar = `"1995-07-07"` — dateutil fills a missing month/day with
  **today**, a latent non-determinism bug AND inconsistent with the fast path. Rather
  than treat the (buggy, non-deterministic) Polars-engine output as the oracle, we
  OWNED the semantics: pin the fill to **Jan 1** (`dates._DEFAULT_DATE`,
  `dateutil.parse(val, default=…)`). That (1) fixes the non-determinism, (2) makes the
  residual agree with the fast path, and (3) makes the date scalars byte-reproducible →
  the str-returning date transforms (`date_iso8601`/`date_parse`/`date_us`/`date_eu`/
  `datetime_iso8601`/`extract_day_of_week`) run on the columnar path via the wave-1
  `scalar=` mechanism (no engine change — just registering the scalars). **Intentional
  (bug-fixing) output change** for partial-date inputs (non-deterministic before);
  `test_fastpath_parity.py` references updated to the deterministic spec (now assert
  engine == owned scalar). Gated by `tests/transforms/test_dates_deterministic.py`.
  Known separate edge (out of scope): an ambiguous "Month Year" partial (`"March 1995"`)
  hits a pre-existing Polars `to_date(strict=False)` looseness in the fast path
  (`"0095-03-19"`) where the columnar scalar is *more* correct. The int/bool-returning
  date transforms (`extract_year`/…/`date_validate`) + parameterized (`date_shift`/
  `age_from_dob`) await dtype-aware scalar egress (a later wave). Remaining clean
  families (categorical, identifiers formatters, names remainder) are thin
  `scalar=`-wiring PRs.
- **4e — I/O extras.** Native CSV is default; parquet/excel/scan/database move behind
  `[polars]`/`[parquet]` with graceful `ImportError`. Gate: fallback-path tests
  (mirror the existing cloud-connector pattern).
- **4f — Flip the default + move `polars` to `[polars]`.** Drop `polars>=1.0` from
  `[project.dependencies]`; add the `[polars]` extra. Update the suite floors, docs,
  `[all]`, and the golden-suite bundle. Gate: a **no-polars CI lane** (`pip install
  goldenflow` in a clean env, run the covered surface, assert it works with
  `'polars' not in sys.modules`). This is the only genuinely-breaking step and
  is a **major version** (goldenflow 2.0) with a migration note.

---

## 6. Risks + non-goals

- **Risk: coverage gaps become loud failures for Polars-free users.** Mitigated by D5
  (graceful `ImportError`) + the fact that anything uncovered already declines to the
  Polars engine today — under Phase 4 it declines to a clear "install
  goldenflow[polars]" instead. The covered surface (Phases 1-3: string/phonetic/
  nullable/numeric/splits, CSV + in-memory) is what runs Polars-free; the rest needs
  the extra until ported.
- **Risk: `transform_df` behavior drift.** Mitigated by keeping it a thin adapter over
  the same native path the parity corpus already covers.
- **Non-goal: speed.** The default path may regress; `[native]`/`[polars]` recover it.
  Measure per sub-phase, don't block on it (same rule as the arc).
- **Non-goal: dates.** The `dates` family stays a documented owned-kernel NO-GO
  (`dateutil` fuzzy parsing is non-byte-portable); it runs on its pure-Python body
  over a column under 4d, and needs `[polars]` only if the user wants the bulk fast
  path.
- **Non-goal: deleting Polars.** It stays a first-class optional accelerator.

---

## 7. Effort shape

- **4a** (lazy-import) is the single highest-leverage step — mostly mechanical, lands
  under the current hard dep, and is independently valuable (faster `import
  goldenflow`). Do it first regardless of whether the full flip proceeds.
- **4b-4c** are moderate (the Frame backend + one new entry point; the native pieces
  exist).
- **4d** is the long pole — 274 signatures, but mechanical + parity-gated, one family
  per PR (the Phase-3 rhythm).
- **4e-4f** are small but 4f is the breaking release (2.0) + suite lockstep + a
  no-polars CI lane.

**Recommendation:** approve **4a** now as a standalone (measure + kill the eager
Polars import) — it's low-risk, independently valuable, and de-risks the rest by
proving the import chain can be broken. Treat 4b-4f as a sequenced program gated on
the 4a weight number actually justifying the 2.0.
