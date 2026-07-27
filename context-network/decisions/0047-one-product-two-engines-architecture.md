# 0047 — GoldenMatch is one product, two engines, many surfaces (governing architecture frame)

**Status:** Accepted. **Adopted:** 2026-07-25 (frame:
`context-network/architecture/one-product-two-engines.md`; wired into the always-loaded root
`CLAUDE.md` and the foundation "governing arc"; amends North Star commitment 3).
**Amended:** 2026-07-27 — conformance v2 (behavioral-equivalence + default-routing tests,
deferral re-validation); see the amendment at the end of this decision.

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

## Amendment (2026-07-27): conformance v2 — behavioral, default-routing, deferral re-validation

**Status:** Accepted. **Trigger:** the thesis-conformance scorecard reached its floor — 10
low, 0 critical/medium, 0 undeclared, and T5 records no weakness at all. A frame whose audit
only ever reports "all low" has finished catching the class it was built for (structural
single-sourcing) and is now under-instrumenting the next class (behavioral + temporal
conformance). This amendment sharpens the conformance INSTRUMENTATION and two decision tests.
It does **not** rewrite the tenets — the "one product, two engines, many surfaces" spine and
the five tenets' wording stand.

### What the closing work exposed

- **A correct owner the default path doesn't use is a *latent* second source.** The TS
  Fellegi-Sunter kernel (`fs-core`) was byte-parity-proven yet wired opt-in, with pure-TS as
  the default — two correct implementations coexisting, the wrong one shipping. T1 ("no second
  source of truth") was satisfied the moment an owner existed; it never inspected the routing.
- **Byte-parity on a fixture ≠ behavioral equivalence on the workload.** That same kernel
  passed 6dp parity *and* would have shipped a changed default dedupe F1 (a shifted operating
  point). "Has a test" satisfied T2 while the test measured the wrong axis. (The first F1
  measurement was itself a degenerate-EM harness artifact — a false divergence — underscoring
  that the behavioral check must run on representative inputs to mean anything.)
- **Deferral premises rot silently.** The goldenanalysis frame-kernel deferral sat "correctly
  low" for weeks after #1788 had already obsoleted its Arrow-coupling premise. T3 classifies a
  deferral's reason once; it never re-checks whether the reason still holds.
- **Resolved items go vacuous but stay live.** The deferral-provenance weakness emptied out
  (every remaining deferral is model-backed, so "kernelize on measurement" has nothing to
  measure) yet remained a permanent "low." Ten permanent lows dilute the signal.

### Decision (conformance v2) — tenet WORDING unchanged, decision TESTS sharpened

1. **T1 → default-routing.** One authoritative owner AND the default caller path routes to it.
   A correct-but-unwired kernel (opt-in while a second implementation is the default) is a
   *latent second source of truth*, not a resolved item. Conformance requires the shared owner
   to be the default, with an escape hatch for the classified fallback — not the reverse.
2. **T2 → behavioral equivalence, not fixture parity.** A conformance test for a two-engine
   capability must check behavioral equivalence on the workload of interest (operating point /
   decision output — e.g. dedupe F1-neutrality), not only byte/tolerance parity on canned
   inputs. Fixture parity is necessary, not sufficient; the behavioral check is what catches a
   shifted default, and it must run on a representative, non-degenerate workload.
3. **T3 → deferrals carry a re-validation trigger.** Every deferral states not just its reason
   but the explicit condition that would UN-defer it (the blocker that must lift), and that
   premise is periodically re-checked, not assumed permanent. A deferral whose premise has
   already lifted is an OPEN divergence, not a low.
4. **Process → the audit hunts the frontier, and prunes.** With the structural board at its
   floor, the audit's job shifts to surfacing emergent/behavioral drift the deterministic
   static harvest cannot see (the adversarial multi-agent re-audit), and to RETIRING
   resolved-and-stable / vacuous items so the live list is the actual risk surface, not a
   museum of closed wins.
5. **Two housekeeping calls.** (a) T5 (Arrow-at-bulk-boundaries) has never recorded a weakness
   — declare it *won* (and stop scoring it) or *instrument* it; a silent tenet is a measurement
   gap, not a solved problem, until proven. (b) Promote "many surfaces" **intentional
   asymmetry** to a first-class principle: the edge-safe TS subset deliberately omits heavy
   Python surfaces (distributed / Ray / GPU / full REST+web UI), so those surface gaps are
   declared-by-design, not weaknesses to re-litigate every audit.

### Consequence

- Conformance graduates from *structural* (an owner exists; a fixture test exists) to
  *behavioral + temporal* (the owner is the default; the test is workload-equivalent; deferrals
  are re-validated). This is the natural next phase now that single-sourcing has largely landed.
- **Counter-held:** a governance frame's value is its stability. This amendment deliberately
  leaves the five tenets' wording intact and changes only their tests plus the audit process;
  churning the spine would cost more than it buys.
- **Known limit:** behavioral equivalence and frontier-finding are harder to make deterministic
  than the static harvest — they lean on measurement and adversarial review, which are advisory
  by nature. The static gates stay as the floor; the behavioral layer augments them, it does not
  replace them.
- **Propagation (follow-on, same amendment):** the paired instrumentation lands in
  `context-network/architecture/one-product-two-engines.md` (the decision-tests section),
  `parity/thesis_conformance.yaml` (per-weakness `un_defer:` triggers + a `default_routed`
  check for T1), and `scripts/check_thesis_conformance.py` (retire vacuous items; flag a
  deferral whose named blocker has lifted). This ADR amendment is the governing statement;
  those are its mechanical wiring.
