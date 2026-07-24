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
  - **Tolerance-bounded (4dp):** string scoring — byte-identical only with the
    shared Rust/WASM scorer.
  - **Divergent (not byte-portable):** standardize/dates (`dateutil` vs `chrono`),
    embeddings (no torch/Vertex; cosine-tolerance), auto-config controller commit.
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
