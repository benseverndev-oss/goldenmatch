# 0007 — GoldenCheck → GoldenMatch: fail-open quality bridges, default-OFF + benchmark-gated

**Status:** accepted (2026-06-07, Ben) • **Shipped:** #793/#794/#795 merged; #797/#798 open • **Architecture:** [../architecture/goldencheck-native-kernel.md](../architecture/goldencheck-native-kernel.md), [../architecture/goldencheck-goldenmatch-integration.md](../architecture/goldencheck-goldenmatch-integration.md)

## Context
GoldenCheck and GoldenMatch shared no code, yet GoldenCheck's data-quality
assessment is exactly the kind of signal that should improve entity resolution —
which value survives a merge, which columns make safe blocking keys, which
disagreements are decisive, which confident merges rest on suspect data. Two
prerequisites had to land first: (a) GoldenCheck needed to be fast enough to run
in an ER pipeline (the Arrow-native expansion, #793), and (b) the coupling had to
not compromise either tool's independence or GoldenMatch's heavily-tuned
accuracy.

## Decision
1. **Couple through fail-open optional-dep bridges, nothing else.** All coupling
   lives in `goldenmatch/core/quality.py` functions that are `_goldencheck_available()`
   -guarded and reuse GoldenCheck **public APIs** (`cell_quality`,
   `functional_dependencies`). `goldenmatch[quality]` stays optional; without
   goldencheck every door is a no-op. New GoldenCheck-side API is added only when
   reuse isn't possible (`cell_quality`, `functional_dependencies`).
2. **Additive + default-OFF + byte-identical when off.** Each door only adds
   (a survivorship weight, a blocking pass, an NE field, a review item) and never
   removes a match/key/decision. Each is env-gated and OFF by default (the #662
   kill-switch precedent); the flag-off path is asserted byte-identical against
   the pre-existing regression suites.
3. **The gate is a MEASURED accuracy sweep, not "more quality must help".** A
   default only flips ON after the wired ER benchmarks (DBLP-ACM / Febrl3 / NCVR,
   DQbench for NE) show the intended win with no regression — the same
   "measure-the-wall" discipline that governs the native kernels. The sweep runs
   in CI (datasets are gitignored), not in the dev sandbox.
4. **Hold the DQ ↔ ER boundary.** GoldenCheck owns value/column-level quality and
   exposes signals; GoldenMatch owns entity resolution. Whole-ROW fuzzy matching
   stays in GoldenMatch (it's ER), not GoldenCheck — even though GoldenCheck does
   value-level fuzzy clustering.

## Consequences / honest flags
- **The doors' value is real but bounded, and each spec records where it isn't.**
  Blocking #1 no-ops on the already-soundex'd person path; FD-NE #3 misses
  perfectly-unique keys (FD discovery treats them as trivial determinants);
  quality-gated review #5 is wired only into the `review` CLI so far. None of
  these are hidden — they're in the per-door specs (`docs/design/2026-06-07-*`).
- **No accuracy number is claimed from the sandbox.** The behavioural + parity
  tests prove the mechanism; the composite/recall/precision deltas are CI's to
  produce. Defaults stay OFF until then — honest-default over assumed-win.
- **Door #2 (transform selection) is deferred** as the weakest remaining door
  (overlaps blocking #1 + GoldenFlow + the `standardize` stage).
- **Process lesson re-confirmed:** stacked PRs across squash-merges go `dirty`;
  the reliable recovery is to let the base merge to `main`, then rebase the child
  onto `main` keeping only its own commits (done for #794→#797).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-07
