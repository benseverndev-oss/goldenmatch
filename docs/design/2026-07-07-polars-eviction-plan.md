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
