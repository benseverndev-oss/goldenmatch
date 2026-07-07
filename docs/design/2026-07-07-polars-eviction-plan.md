# GoldenFlow: evict Polars as a mandatory dependency

**Date:** 2026-07-07 • **Status:** Plan • **Goal:** dependency weight /
embeddability, NOT speed.

## Why (and why the perf spikes don't apply)

Polars is a ~35 MB **mandatory** dependency (`polars>=1.0` in `pyproject.toml`).
That weight is the cost we're removing — a lighter, more embeddable `goldenflow`
install. The two perf spikes (frame-container, "Arrow everywhere") measured
WALL/RSS and correctly said NO-GO *on speed*. This is a different axis: we accept
a speed regression on the default path and recover it by iterating on the native
kernels (which already do the heavy lifting). Correctness first, speed second.

Precedent this mirrors:
- The **TypeScript port** is already fully Polars-free (pure `Row[]` + optional
  WASM). The logic exists Polars-free; we're bringing that architecture to Python.
- The suite's **native-optional** pattern (`goldenmatch` pure-Python, `[native]`
  extra) — Polars becomes the same: an optional accelerator, not a requirement.

## Coupling surface (measured)

694 Polars refs / 36 files: **transforms 562**, engine 45, connectors 18, cli 6,
mapping 4. Transform modes: 80 `series`, 30 `expr`, 4 `dataframe`. The key insight:
even `expr`-mode transforms wrap an inner `_series` function via
`pl.col(c).map_batches(_x_series, ...)` — the real work is a native kernel or a
pure-Python fallback over a COLUMN; Polars is the substrate + wrapper + I/O. Every
owned transform already has a pure-Python fallback proven byte-identical to its
Rust kernel (the cross-surface parity corpus).

## Target architecture (chosen: native/Arrow default, Polars optional)

**The native/Arrow substrate (goldenflow-native, ~5 MB Rust wheel) is the DEFAULT;
Polars becomes an OPTIONAL accelerator.** Lighter than Polars (~5 MB vs ~35 MB) and
keeps native speed, at the cost of requiring the compiled wheel on the default path
(a pure-Python fallback still exists for unsupported platforms / `native=0`).

- **`Frame`** — a backend-agnostic columnar container the engine operates on
  (`engine/frame.py`). Column type is the backend's: a native/Arrow column by
  default, `pl.Series` under the optional Polars backend, a plain `list` under the
  pure fallback.
- **Transforms** run on the native kernels (Arrow / the `Vec<String>` list entry
  points already built for WASM — `apply_chain_str` — so the native path needs
  **no pyarrow and no Polars**), with the pure-Python fallback where a kernel is
  absent.
- **I/O** — a native/stdlib CSV reader for the default; `[polars]` / `[pyarrow]`
  extras for Parquet/Excel/scan + fast bulk CSV.
- **`polars`** moves from a hard dep to a `goldenflow[polars]` extra: an optional
  fast backend (its vectorized `str.*` path is genuinely faster on clean bulk
  data — keep it, don't lose it).

## Phases (each shippable, each parity-gated)

0. **`Frame` seam.** Introduce `engine/frame.py` (`Frame` over `dict[str, Column]`)
   and route the engine through it, with a Polars-backed adapter so behavior is
   unchanged. Pure refactor, no eviction yet — de-risks everything after.
1. **Pure-Python columnar engine for the OWNED transforms**, behind
   `GOLDENFLOW_ENGINE=columnar`. The owned string/numeric/nullable/name/… families
   (which have native + pure-Python impls) run with NO Polars. Gate: a new
   engine-parity test asserts `columnar == polars` output + manifest, byte-identical,
   over the existing corpus.
2. **Polars-free I/O** — `Frame.read_csv` (stdlib) + writer; Parquet/Excel behind
   the optional extras.
3. **Port the remaining transforms** (the `expr`/`series`/`dataframe` funcs that
   still return `pl.Expr`/`pl.Series`) to the `Column` signature — mechanical, one
   family at a time, each parity-gated.
4. **Flip the default to the pure engine; move `polars` to `[polars]` extra.** The
   default `pip install goldenflow` no longer pulls Polars; `[polars]` and
   `[native]` are opt-in accelerators. Update the suite/docs.

## Correctness gate

The whole arc is protected by parity, not trust: (a) the existing cross-surface
corpus (native == pure-Python == pinned), and (b) a NEW engine-parity test
(columnar engine output + manifest == polars engine, byte-identical) run over the
same corpus + realistic frames. A transform can't move to the columnar engine
until it passes both.

## Non-goals / guardrails

- NOT a speed project — the default path may be slower initially; `[native]` /
  `[polars]` recover it. Measure the default-path regression per phase and log it,
  but don't block on it.
- Keep Polars as a first-class OPTIONAL backend (don't delete the fast path).
- No output change ever — byte-identical to today, gated by the parity tests.

## Progress + Phase 1b unblock (2026-07-07)

**Shipped:** P0 (Frame seam, #1525) + P1 (`engine/columnar.py`, owned string
transforms Polars-free via the native arrow-free `apply_chain_str_list`, #1527) —
both byte-identical, parity-gated.

**Measured, honest:** the P1 *list* substrate is ~3.3× SLOWER than Polars at 2M
rows — the cost is Python-list marshaling (`Polars→list→Rust→list→Polars`), NOT the
kernels. It is the correctness floor + a zero-dep fallback, not the perf substrate.

**The "lighter AND faster" bar is reachable — key facts:**
- **Weight math.** `[native]` today pulls polars(~35 MB) + pyarrow(~40 MB) +
  native(~5 MB) ≈ **80 MB**. Evicting Polars but keeping pyarrow barely moves it,
  and the fused path's speed uses `to_arrow`/`from_arrow`, which **require
  pyarrow**. The real target is a native `Column` that holds Arrow buffers and
  ingests them **without pyarrow** — then the stack is native ~5 MB alone.
- **Unblock (verified).** Polars 1.40 `Series`/`DataFrame` expose
  **`__arrow_c_stream__`** (the Arrow PyCapsule / C-Data interface) returning a
  `PyCapsule` with **no pyarrow**; arrow-rs has the matching `ffi`/`ffi_stream`
  import side. So the native layer can ingest Polars' Arrow **zero-copy and
  pyarrow-free** — the linchpin for lighter-*and*-faster.

**Phase 1b (the perf substrate) — precise plan.** A native `Column` in
`goldenflow-native`:
1. **Ingest:** read the `arrow_array_stream` PyCapsule → arrow-rs
   `ArrowArrayStreamReader` → `ArrayData` (`arrow::ffi_stream`, `unsafe`).
2. **Process:** `apply_chain` on the `ArrayData`, Column→Column, zero-copy — no
   `from_arrow` back to Polars between transforms.
3. **Egress:** wrap the result `ArrayData` in an `FFI_ArrowArrayStream` PyCapsule
   (native implements `__arrow_c_stream__`); Polars / consumers import it.

**Build discipline for 1b.** This is deep `unsafe` Arrow-FFI (raw C-Data pointers,
PyCapsule lifetime) — a bug is a segfault, not a failed assert. Verify correctness
by **parity** (safe); defer the **speed** number to **CI** (the dev box is too
noisy — 263–362 ms for the same call). Do it as a focused unit, not rushed.
`native-flow`'s arrow crate is `features = ["pyarrow"]`; 1b uses `arrow::ffi`
(no new dep) or `pyo3-arrow`. Also: `goldenflow-native 0.16.0` (`apply_chain_str_list`)
is not republished yet — batch that republish with 1b so the columnar path ships whole.

## Phase 2 — native Arrow I/O (where lighter-AND-faster lands)

**Shipped 1b/1c (#1530/#1531):** the columnar engine now runs the owned chain on a
native Arrow `Column` (pyarrow-free), measured at **parity** vs Polars. Why only
parity, not a win: the caller still hands the engine a `pl.DataFrame`, so the path
pays `df.select` ingest + `pl.from_arrow` egress + `with_columns` — boundary
conversions that roughly match Polars' own `to_arrow`/`from_arrow`. As long as
Polars owns the frame, the native path can only *tie* it.

**Phase 2 removes the frame.** The file→transform→file path never constructs a
`pl.DataFrame`: a single native call reads the CSV into Rust-owned string columns,
applies the owned chain, and writes the CSV back — **one FFI crossing, zero
per-column Python round-trip, zero Polars, zero pyarrow.** This is the shape where
native genuinely *beats* Polars (no rival frame to tie against) and where
`pip install goldenflow` stops needing Polars for the CSV pipeline at all.

**Surface (native-flow `csvio.rs`, one function):**
`transform_csv(in_path, out_path, specs) -> per-column-per-op manifest records`,
where `specs = [(column, [(op, [params])])]`. Internally: (1) read CSV via the
tiny pure-Rust `csv` crate (no arrow-csv schema inference), header → column names,
every field as a string → one `LargeStringArray` per column; (2) for each spec
column, apply the ops **one kernel at a time** (mirrors the Python columnar
manifest loop exactly — captures per-op `affected_rows` + null-preserving 3-row
`sample_before`/`sample_after`); (3) write every column (transformed replace
originals, passthrough columns unchanged) back to CSV. Python builds the `Manifest`
from the returned records without ever touching the data.

**Semantics decision — all columns are strings (documented, opt-in only).** The
native reader reads every column as Utf8 (no type inference); an empty field maps
to null (matches Polars' default null-on-empty). This is deliberate: the owned
transforms are string-in/string-out, and reading numbers as strings avoids Polars'
lossy float reformatting (`1.50`→`1.5`). It is a semantic difference from the
default Polars read (which infers `Int64`/`Float64`), so it lives **only** behind
`GOLDENFLOW_ENGINE=columnar` + a columnar-ready config — the default path is
byte-unchanged. No default-path regression, per the guardrail.

**Parity contract (the load-bearing decision).** The reference is the Polars engine
reading the SAME file **with inference off** (`pl.read_csv(infer_schema_length=0)`
→ all Utf8), transforming, and writing. Two axes:
- **Manifest — byte-identical.** `affected_rows` / `total_rows` / 3-row samples must
  match the Polars engine exactly (same kernels, same order, null-preserving).
- **Output DATA — cell-identical, not byte-identical.** Compare the two output CSVs
  **parsed back to rows**, cell-by-cell. Native's CSV *serialization* is its own
  (RFC4180 via the `csv` crate); two writers won't emit identical bytes (quoting,
  null rendering), so byte-parity on the file is the wrong contract — data-parity
  is the honest one. The transform *values* are byte-identical; only the writer's
  framing differs.

**Empty/null CSV edges** (quoted-empty vs unquoted-empty, embedded newlines) are a
bounded, documented boundary for v1 — pinned on the common cases, matching Polars'
null-on-empty; exotic quoting parity is not claimed.

**Deps/build.** Adds the pure-Rust `csv` crate (tiny, no pyo3/arrow-csv churn — the
`arrow` feature set is unchanged). Bump `native-flow` 0.17.0 → 0.18.0. **Republish
0.16/0.17/0.18 together** so the whole zero-copy substrate (list binding + `Column`
+ native I/O) ships to PyPI in one wheel — until then the file path only works from
an in-tree build.

**Speed — MEASURED, honest (200k rows, 4+2 owned ops, noisy dev box).** The thesis
("no `pl.DataFrame` → native-file beats polars-file") was **half right and worth
recording precisely**:

| stage | native | polars |
|---|---|---|
| **transform only** | ~47 ms | ~66 ms |
| **CSV I/O only** (read+write) | ~60–70 ms | ~15 ms |
| **full file path** | ~125–160 ms | ~75–95 ms |

The **transform is faster natively** (the owned single-pass chain beats Polars'
per-op `map_batches`) — the boundary thesis holds for the compute. But **Polars'
CSV reader/writer is multithreaded SIMD and ~4× faster** than a single-threaded
Rust `csv`-crate path, and at these sizes the file is **I/O-bound**, so the full
native path is **~1.6× slower**. Optimizing the reader (build Arrow buffers
directly, no per-field `String`, reused record) already **halved** native I/O
(124 ms → ~60 ms); closing the rest needs **parallel CSV parsing** (rayon over
row-chunk boundaries, with the #688 sequential-below-threshold guard) — the named
follow-on to reach lighter-AND-faster.

**Verdict for this increment: ship it as the WEIGHT win, honestly.** It decisively
advances the primary goal — the CSV pipeline (read→transform→write) now runs with
**zero Polars, zero pyarrow**, byte-identical (data + manifest, parity-gated), and
the transform itself is faster. The CSV-I/O speed regression is the kind the user
pre-authorized ("evicting a heavy dependency… the regress we can handle by
iterating"); parallel parsing is Phase 2b. Do **not** claim "faster" — the number
is a measured tie-to-regression on I/O-bound CSV, a win on transform.
