# 0047 — GoldenMatch is one product, two engines, many surfaces (governing architecture frame)

**Status:** Accepted. **Adopted:** 2026-07-25 (frame:
`context-network/architecture/one-product-two-engines.md`; wired into the always-loaded root
`CLAUDE.md` and the foundation "governing arc"; amends North Star commitment 3).

## Context

Recurring architecture discussions kept asking "which framework should GoldenMatch be built
around?" (Spark / DataFusion / Ray / Ballista / Sail / DuckDB / Polars) and drifting toward
"Arrow-native everything, one monolithic engine, every other language a thin adapter." That
framing hid two problems:

1. **It conflated two different systems.** Batch identity *compute* (throughput, vectorized,
   deterministic, stateless run-to-run) and durable identity *control* (stable IDs,
   transactions, merges/splits, provenance, audit) optimize for genuinely different things.
   Forcing both under one Arrow/columnar thesis produces misleading abstractions — the
   control plane is a state machine, not a columnar kernel.
2. **It conflicted with a checked-in commitment.** North Star commitment 3 read "every
   capability must reach every surface" (identical exposure everywhere) — which is precisely
   the pressure that produced the multi-runtime parity tax (`api_parity`,
   `check_native_symbols`, `check_scorer_coverage`, cross-surface fixtures) we now want to
   retire by single-sourcing.

Separately, `docs/design/` is invisible to Claude sessions unless searched, so a design doc
there governs nothing on its own — only `CLAUDE.md` auto-loads into every session.

## Decision

Adopt **one product, two engines, many surfaces** as the governing architecture frame, and
make it actually govern:

- **Two engines.** Identity Compute Engine (Arrow-native at bulk boundaries,
  Rust-authoritative, measured kernels, replaceable backends) + Identity Control Plane
  (transaction-native; SQLite/Postgres; stable IDs, merge/split, provenance, audit). The
  compute↔control seam is an explicit, versioned "resolution batch" that must carry evidence
  payloads and acknowledge a shared frame-residency budget (grounded in PR #2132).
- **Contracts over frameworks.** One authoritative semantic owner per capability (Rust where a
  clean shared core exists); **specification + conformance** define correctness, not a binary
  or a fixture set alone; conformance levels = exact / numerically-equivalent /
  semantically-equivalent / intentionally-divergent. Pure Python and standalone TS algorithms
  become classified, conformance-tested **fallbacks**, not co-equal sources of truth.
- **Arrow is the bulk boundary, not the universal calling convention.** Smallest stable
  primitive for scalar/small calls. DataFusion/Ray/Sail/Ballista/Spark are replaceable
  backends; none is synonymous with GoldenMatch.
- **Kernelize on measurement**, stating the motivation class (perf / semantic single-sourcing
  / portability / safety / maintainability).
- **Amend commitment 3** from "every capability must reach every surface" to "shared
  capabilities must conform; surface-specific gaps must be explicit, justified, declared."
- **Make it load-bearing.** The frame lives in the foundation/architecture tier
  (`context-network/architecture/one-product-two-engines.md`), is summarized in the foundation
  "governing arc," and is pointed to from the always-loaded root `CLAUDE.md` with its decision
  tests — so every session sees it. An architectural change that competes with the frame must
  conform, or amend the frame + this decision in the same PR.

## Consequence

- Architecture decisions now have a stated frame and decision tests every session loads,
  instead of re-litigating framework choice each time.
- The control plane is explicitly **out** of the Arrow-core thesis and gets its own roadmap
  (Tier 5 in the frame), including two OPEN seams the two-box picture doesn't home: evidence
  payload carriage across the compute↔control boundary, and incremental resolution (compute
  that reads persisted state).
- Reclassifying pure Python as a scoped fallback has a user-visible cost (what a no-wheel
  `pip install goldenmatch` guarantees) that must be stated in the fallback policy, not
  assumed.
- The parity tax becomes retire-able where single-sourcing lands, rather than a permanent
  justification for duplication.
- **Known limit:** the frame is a direction with real OPEN items (the two seams, the
  TypeScript-WASM migration, the Python fallback deprecation contract). Accepted means "this
  governs new architectural work," not "the codebase already conforms everywhere."
