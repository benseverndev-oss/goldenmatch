# Follow-on: TS denial-constraints should dispatch to `goldencheck-core::dc.rs` (WASM)

**Status:** tracked follow-on (not started). Filed alongside the PY→TS denial-constraints port.

## Thesis context

The governing frame (`context-network/architecture/one-product-two-engines.md`,
decision 0047) requires **one authoritative semantic owner per capability** — the
Rust `-core` kernel where a clean shared core exists — with the Python and
TypeScript surfaces as **conformance-tested ports/fallbacks, not co-equal sources
of truth**.

## Current state (the gap)

The denial-constraint **evidence layer already has a Rust owner**:
`packages/rust/extensions/goldencheck-core/src/dc.rs` (`denial_constraint_evidence`),
exposed to Python via `goldencheck-native` and routed through
`goldencheck.core.kernels.denial_constraint_evidence` (native, gated on
`GOLDENCHECK_NATIVE`), with `evidence.py::_evidence_python` as its byte-parity
fallback.

The TS port (`packages/typescript/goldencheck/src/core/denial/evidence.ts`, landed
in the parity-gap-closing PR) **reimplements the `_evidence_python` fallback logic
directly in TypeScript** (bigint u64 masks). It does **not** dispatch to `dc.rs`.
So the evidence layer now has three implementations — Rust (authoritative), Python
fallback, and TS reimpl — where the thesis wants one owner + conformance ports.

**Mitigation already in place:** a committed cross-language conformance fixture
(`tests/fixtures/denial_constraints.{csv,expected.json}` +
`tests/parity/denial.parity.test.ts`) locks TS output == Python output on a
deterministic 180-row case, so the two cannot silently drift on that contract.
That is the right *discipline*; it does not make the TS runtime *be* the one core.

## The follow-on

Build a `goldencheck-dc-wasm` crate over `goldencheck-core::dc.rs` and dispatch the
Pass-1 / Pass-2 evidence build from `core/denial/evidence.ts` to it — mirroring the
established opt-in WASM pattern (`score-wasm` for goldenmatch, `analysis-wasm` for
goldenanalysis; see `packages/typescript/CLAUDE.md` "Shared opt-in WASM runtime").
Pure-TS stays the default + fallback; the `.wasm` is built in CI, never committed.

**Boundary shape.** `dc.rs` already takes plain interned id/null vectors + a
`pred_spec` list (`space_to_kernel_args` in `evidence.py` is exactly this
flattening), and returns a `mask -> count` histogram — a clean, Arrow-free
primitive boundary, so it fits the WASM thesis (small stable primitive, no Arrow
marshaling). The TS `space_to_kernel_args` equivalent already produces the same
shape.

**Verification.** The existing `denial.parity.test.ts` fixture is the drift guard;
extend it with the `fixture_drift` backstop once the wasm build script exists
(`build_*_wasm.mjs` is globbed automatically). Kernelize on measurement: only pursue
the WASM path if a real in-browser / edge DC-mining workload materializes — until
then the conformance-locked pure-TS port is the correct intermediate, exactly like
other `-core`/fallback pairs were staged.
