# 0046 — Cross-language phase-handoff is governed by a measured conformance harness, not assumed from surface parity

**Status:** Accepted. **Shipped:** 2026-07-24 (conformance harness + published limits; docs `concepts/cross-language-parity`, design note `docs/design/2026-07-24-cross-language-phase-conformance.md`).

## Context

The TypeScript port reached **surface parity** with Python — the same MCP tools,
CLI commands, and core operations exist in both, enforced by the `api_parity`
gate. A natural but wrong inference followed: that a user can therefore run any
pipeline phase in one language, hand the intermediate artifact to the other, and
resume seamlessly.

Surface parity (the same operations exist) is not artifact interoperability (a
phase's output round-trips byte-for-byte). Some boundaries genuinely do
round-trip; others are numerically tolerance-bounded (scores agree to 4 decimals,
so a pair on a threshold can flip); a few can't cross at all (Python-only
subsystems). Left undocumented, this is a correctness trap: a split pipeline can
silently reach a different result than a single-language run.

## Decision

Treat cross-language phase-handoff as a **measured** property, boundary by
boundary, and publish the limits:

- A runnable **conformance harness** (Python oracle → TS parity test) measures
  each boundary. Shipped boundaries: `score → cluster` (identical scored pairs →
  identical partition, incl. the oversized-cluster MST auto-split with tied edges)
  and the **end-to-end split-run** (Python runs a real pipeline via `MatchEngine`;
  TS clusters its scored pairs and reproduces Python's clusters; an independent
  all-TS run agrees). Scoring tolerance is pinned by the scorer ground-truth test.
- The **verdict table** is the source of truth and is published on every doc
  surface (README, `concepts/cross-language-parity`, `llms.txt`, this ADR):
  - **Byte-safe:** identity graph DB (+ cryptographic cross-verification),
    `score → cluster`, end-to-end split-run, cluster JSON, config YAML, Learning
    Memory, run log, `record_fingerprint`.
  - **Tolerance-bounded (4dp):** string scoring. Byte-identical Python↔TS only for
    scorers whose score is byte-exact under the shared Rust/WASM kernel; `ensemble`
    (id 12), `radial` (id 13), and the name scorers (ids 20/21) stay **~4dp even
    with the WASM scorer** (reduction/recomposition order, 1-ULP class) — see the
    2026-07-26 amendment.
  - **Divergent (not byte-portable):** standardize/dates (`dateutil` vs `chrono`),
    embeddings (no torch/Vertex; cosine-tolerance), auto-config controller commit,
    and **PPRL CLKs on float fields** (`str(5.0)`="5.0" vs `String(5.0)`="5" →
    non-equal filters; cast floats to strings before PPRL).
  - **Second source of truth (conformance-gated, not the shared kernel):** the
    default TS Fellegi-Sunter path is pure-TS `probabilistic.ts` (via `pipeline.ts`),
    **not** the `fs-wasm` kernel — which ships and is parity-gated
    (`fs-wasm.parity.test.ts`) but has no pipeline caller. The parameterized scorer
    modes `numeric_diff:abs|pct` and `array_intersect:overlap` likewise ride a
    per-pair / pure-TS mirror (the fixed-id `score_one` kernel can't carry the mode
    string), fixtured (`scorer-domain-comparators.json`) but not kernel-single-sourced.
  - **Python-only by architecture:** distributed/Ray/bucket, document (VLM) ingest,
    distributed routing.
- **Guidance:** hand off at the `cluster` or `identity` boundary (byte-safe); do
  not split across `standardize`/dates, embeddings, or the controller.

## Consequence

- Cross-language handoff is now honestly bounded rather than over-promised. Users
  know which boundaries are seamless, which can flip a threshold, and which can't
  cross.
- The harness keeps the claims true as code evolves (it is a parity test, so a
  regression that breaks a byte-safe boundary fails CI).
- **Known limit of the current evidence:** the split-run's clean independent-run
  agreement is dataset-specific (no pair sat exactly on the threshold). The 4dp
  tolerance can still flip a cluster on adversarial data. The next extension —
  tracked in the design note — is a split-run over a corrupted dataset engineered
  to sit pairs on the threshold, to find and quantify the flipping case. "Passed
  on a fair test" is not "can never flip", and the docs say so.

## Amendment (2026-07-26): scorer-level conformance caveats

Thesis-conformance audit (decision 0047; weakness `undeclared-cross-surface-divergences`)
surfaced scorer- and package-level divergences the original verdict table did not
name — verified against `main` before recording:

- **`ensemble`/`radial`/name scorers are ~4dp even with the shared WASM scorer**, not
  byte-exact. Documented at `packages/typescript/goldenmatch/src/core/wasm/backend.ts`
  (ensemble id 12 maxes over an un-normalized `score_one(2)`; radial id 13 does a
  left-to-right f64 reduction; name ids 20/21 are WASM-rapidfuzz-JW vs pure-TS JW).
  Reduction/recomposition order, same 1-ULP class as native↔pure-Python. The prior
  row read "byte-identical … with the shared Rust/WASM scorer", which over-claimed for
  these ids.
- **FS scoring: the batteries `goldenmatch` import now runs the shared kernel by
  default** (RESOLVED, Wave D, 2026-07-27). `pipeline.ts` reroutes probabilistic
  block scoring onto the shared `fs-core` kernel via the `fsScoreBackend` registry,
  and the batteries root entry (`src/index.ts`) auto-enables it on import — so the
  DEFAULT bare-`goldenmatch` FS path is byte-aligned with Python-native (the #1854
  fixed full-field operating point), measured F1-neutral vs the pure-TS path. The
  lean `goldenmatch/core` entry keeps the pure-TS `probabilistic.ts` path as the
  classified fallback (4-dp tolerance) unless `enableFsWasmScoring()` is called, and
  for configs the kernel can't express (embedding / name-refdata / TF fields). Closed
  weakness `fs-default-ts-path-unwired-second-source`.
- **`array_intersect` `overlap` MODE is pure-TS-only.** The base `array_intersect`
  scorer is kernel-backed, but its `overlap` mode is not expressible through the
  fixed-id `score_matrix` boundary, so that mode runs pure-TS on both surfaces. A
  per-mode divergence, not a whole-scorer one.
- **PPRL CLKs diverge on float fields.** `str(5.0)`="5.0" (Python) vs `String(5.0)`="5"
  (JS) yields non-equal bloom filters; the `pprl.json` fixtures dodge it by using
  string fields. Guidance: cast floats to strings before PPRL if cross-language CLK
  equality matters. (`packages/typescript/goldenmatch/CLAUDE.md`, PPRL parity note.)
- **Out of this ADR's scope, tracked elsewhere:** goldenanalysis frame-kernel Python↔TS
  parity (no WASM, Wave 1b deferred) is a *goldenanalysis* boundary, locked by
  `frame_kernels_adversarial.json`; it belongs in goldenanalysis's own conformance docs,
  not this goldenmatch phase-handoff ADR.

Guidance addition: PPRL float fields join `standardize`/dates, embeddings, and the
controller as boundaries not to split across without casting/tolerance.

**Standardize now has a runnable characterization (was prose-only).** The verdict
table listed `standardize` divergent but with no test — the only non-exact boundary
lacking one. Two corrections + a test:
- goldenmatch ships **no date standardizer** — date parsing (`dateutil` vs `chrono`)
  is *GoldenFlow's* transform, with its own year-guard fixtures. The goldenmatch
  standardize divergence is the standardizer SET, not dates. The "dates" framing in
  the table's divergent row refers to that cross-package transform, not a goldenmatch
  standardizer.
- The measured Python↔TS standardizer divergence (54 of 150 cells over 10
  standardizers) is now pinned by `standardizer-conformance.json` +
  `tests/parity/standardizer-conformance.parity.test.ts` (TS) +
  `tests/test_standardizer_conformance.py` (Python), regenerated by
  `scripts/emit_standardizer_conformance_fixture.py`. **Dominant, previously-undeclared
  divergence: Python standardizers return `None` for non-matching input, TS returns
  `""`** (email/phone/zip5 = 38 of 54); the rest is whitespace handling (name_*) and
  title-casing (`name_proper`/`address`: `O'Brien`→`O'brien`, `4B`→`4b`, `E2E`→`E2e`).
  `state`/`strip`/`trim_whitespace` are byte-identical. The pair pins both sides so the
  divergence cannot silently widen.
