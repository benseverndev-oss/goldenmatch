# 0032 — GoldenFlow: compiled zero-Python DuckDB extension + cross-surface required gates

**Status:** Accepted • **Shipped:** `goldenflow-duckdb-v0.1.1` (74 UDFs, 5 platforms); all goldenflow-core surfaces now required CI gates

## Context

The "Rust is the reference" program (ADR
[0031](0031-goldenflow-reference-mode-identifiers-wasm.md)) made `goldenflow-core`
the single owned source of truth, with Python (native wheel + pure fallback) and
TypeScript/WASM as conforming surfaces proven byte-identical by a shared corpus.
The cross-surface roadmap named DuckDB and Postgres as further targets ("one
`-core` on Python / TS-WASM / DuckDB / PG").

A DuckDB surface for GoldenFlow already existed — `duckdb/goldenmatch_duckdb/goldenflow.py`
— but it predated the owned kernels: it dispatches the *Python* transform
registry per value (a 1-element `pl.Series` per row) and never touches Rust. It
is not the reference and cannot be zero-Python.

Separately, of the surfaces that consume `goldenflow-core`, only some were
`ci-required` (blocking) — the pure-Python fallback and TS/WASM were, but a core
change could still merge with a surface's parity red.

## Decision

Build **`goldenflow-duckdb`** — a compiled Rust **loadable DuckDB extension**
(cdylib, `duckdb` crate `vscalar` + `#[duckdb_entrypoint_c_api]`) that links
`goldenflow-core` directly. No CPython in the DuckDB process; each SQL function is
a thin `VScalar` running the reference kernel over a data chunk. It is a *thin
binding*, not a reimplementation, so unlike the Python/TS pure fallbacks it can't
drift in implementation.

- **Coverage:** 74 UDFs `goldenflow_<kernel>` — essentially the whole
  single-record transform surface (single-arg VARCHAR/nullable/BOOLEAN/DOUBLE/
  BIGINT, the identifier family, multi-output splits as component UDFs, and
  multi-arg phone/truncate/pad/merge). `category_auto_correct` is **not** exposed
  (column-wide/aggregate — a DuckDB aggregate/table function, a separate surface);
  `date_*` stays excluded suite-wide (non-byte-portable `dateutil` reference).
- **Parity by the same oracle:** a bundled in-process test threads the full
  shared `identifiers_corpus.jsonl` (the exact Python/TS oracle) through real
  DuckDB; multi-arg UDFs assert against the kernel directly.
- **Portability:** the metadata footer encodes the **stable C API version
  (v1.2.0)**, not the DuckDB release, so one build loads on **DuckDB >= 1.3.0**
  (a CI version-sweep proves it across 1.3.0 / 1.3.2 / 1.4.0 / 1.5.4). Floor is
  1.3.0 because 1.2.x used the `linux_amd64_gcc4` platform string.
- **Distribution:** `goldenflow-duckdb-v*` tag builds 5 platforms
  (linux amd64/arm64, macOS arm64/amd64-via-Rosetta, windows amd64), each LOAD-
  smoked, published as per-platform **zips** (the loadable file must keep the
  basename `goldenflow_duckdb.duckdb_extension` — DuckDB derives the init symbol
  from the filename).
- **Close the gate seam:** make every `goldenflow-core` consumer a required
  `ci-required` lane — `rust` (the reference's own tests),
  `python_goldenflow_fallback`, `wasm_flow`, `rust_pgrx`, the new
  `goldenflow_duckdb`, and `native_flow`. A core change can no longer merge with
  any surface red.

## Consequence

- GoldenFlow transforms now run natively inside DuckDB with zero Python, byte-
  identical to Python/TS/WASM by the shared corpus — a fourth conforming surface
  that falls out of the Rust kernel.
- Source-level lockstep is now enforced across every surface: a kernel change
  fans out to re-run all surface parity gates, and all block the merge. The
  committed corpus makes an un-propagated change (or a stale corpus) red the
  gates. Remaining non-gate caveat (inherent to versioned artifacts): the wheel /
  npm / DuckDB release don't auto-republish on a green main — those are deliberate
  re-cuts.
- Hard-won gotchas recorded in `packages/rust/extensions/CLAUDE.md` + the memory
  file `project_goldenflow_duckdb_extension`: underscore package name, the
  filename→init-symbol coupling (which shipped a broken v0.1.0 before the
  version-sweep caught it), C-API-vs-DuckDB version, and DuckDB scalar
  null-propagation (`merge_name`).
