# 0034 — GoldenFlow: fused columnar apply (Pillar-1 execution fusion), default-on

**Status:** Accepted • **Shipped:** `goldenflow-native 0.12.0` (fused kernel on PyPI), `goldenflow 1.14.0`; `GOLDENFLOW_FUSED_APPLY` default-ON (opt-out)

## Context

The owned-kernel program (ADRs
[0031](0031-goldenflow-reference-mode-identifiers-wasm.md) /
[0032](0032-goldenflow-duckdb-compiled-extension.md)) made `goldenflow-core` the
single owned reference for every transform. But it moved Rust to *authoritative
for logic*, not *for execution*: the engine (`engine/transformer.py`) still
applied N transforms to a column as N separate Polars ops, each one a full
Series→Arrow export + native kernel + Arrow→Series import + `with_columns` + a
full-column affected-count scan. The kernels were owned; the orchestration still
rode Polars, crossing the Python/Polars/Arrow boundary once per transform.

Pillar-1 of the "Rust is the reference" thesis is the *Great Polars Eviction* —
start pulling execution off Polars. The apply loop is the first tractable target
(the frame *container* is a much larger, separate step).

## Decision

**Fuse a column's chain of owned kernels into one native pass.**
`goldenflow-core::chain::apply_chain(arr, &[Kernel])` threads each row through a
maximal run of owned, no-arg, **total** (never-null) string→string kernels using
two reused scratch buffers, in a single Arrow round-trip, returning the
transformed array plus per-kernel affected-row counts. Wired via
`native-flow::apply_chain_arrow`; the host (`engine/transformer.py::_apply_column_ops`)
detects fusable runs and applies each in one call, everything else unchanged.

Load-bearing design points:
- **Parity by construction.** Each `Kernel` dispatches to the *exact* owned core
  fn the per-transform path calls; composition of pure functions is associative,
  so fused output is byte-identical to sequential — including the audit manifest
  (per-step records + exact counts + samples). A coverage guard asserts the host
  `FUSABLE_KERNELS` set mirrors the native `Kernel` table.
- **Generic over offset width.** Polars exports strings as **LargeUtf8** (i64
  offsets), so `apply_chain` (and the single-kernel columnar helpers) are
  `GenericStringArray<O>` — an i32-only path would silently never fire on real
  Polars data.
- **Scope.** 25 fusable kernels (text + email + name-normalizer families).
  Excluded on purpose: `Option`-returning kernels (URL/company — need a
  null-aware executor), parameterized (`truncate`/`pad`), numeric f64, and
  residual-tier (`phone`/date). They take the per-transform path automatically.
- **Default-ON, opt-out.** `GOLDENFLOW_FUSED_APPLY=0` forces per-transform;
  otherwise fuse whenever the native fused kernel is present. Requires
  `goldenflow-native >= 0.12.0` (the version that ships `apply_chain_arrow`);
  older wheels / native-absent installs fall back gracefully.

## Consequence

**Measured, and the honest verdict is: the win is RSS, not wall.** End-to-end at
scale (`bench-goldenflow-fused`): wall speedup **1.07–1.27×** (config-dependent —
*diluted* by compute-heavy kernels, since fusion only removes orchestration
overhead, not per-row work) and **peak RSS −22% at 5M rows** (−615 MB), growing
with row count, byte-identical output. The RSS win is the durable one (fusion
avoids materializing one intermediate column per transform). So it's positioned
as a memory play for at-scale / RSS-sensitive pipelines, safe to default because
it's never a wall regression and degrades gracefully.

Two "measure beats intuition" lessons banked: the boundary-crossing *count*
over-predicted the wall win (4× framing → 1.25× measured), and *widening* the
fusable set to compute-heavy kernels *lowered* the ratio rather than raising it.

Two CI footguns caught: a `ci.yml` YAML startup failure (a `- run:` line ending
in `::` → 0 jobs → the required gate never reports → PR sits BLOCKED looking like
a slow queue), and the stale maturin/rust-cache (editing `goldenflow-core/src`
without bumping its version → native links the old core; fix = bump the core
version to change the lock hash).

Follow-ups (none are default-flip levers): a null-aware executor for
URL/company, parameterized + numeric chains, wasm/duckdb fused surfaces. The
frame container stays Polars — evicting it is the next, larger Pillar-1 step.
