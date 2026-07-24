# Cross-language phase-handoff conformance (Python ↔ TypeScript)

**Question:** now that the TS port is at surface parity, can a user hand a
pipeline *phase* from one language to the other and back and get the same
result?

**Short answer:** it depends on the boundary. Surface parity (same ops exist)
is not artifact interoperability (a phase's output round-trips byte-for-byte).
This doc records **measured** verdicts per boundary — byte-safe, tolerance-
bounded, or divergent — rather than assuming.

The pipeline: `ingest → standardize → matchkeys → block → score → cluster →
golden → output`.

## Verdict table

| Boundary / artifact | Verdict | Evidence |
|---|---|---|
| **Identity graph DB** (`.goldenmatch/identity.db`) | ✅ **byte-safe + cryptographically cross-verifiable** | schema byte-identical; audit seal/entry-hash cross-verify (`tests/parity/audit-hash.parity.test.ts`) |
| **score → cluster** (scored pairs → clusters) | ✅ **byte-safe** (measured) | `tests/parity/cluster-conformance.parity.test.ts` — identical partition on identical pairs across 5 scenarios incl. the oversized-cluster MST auto-split (unambiguous **and** tied-weakest-edge) |
| **end-to-end split-run** (Python `standardize→block→score` → TS `cluster`) | ✅ **reproduces all-Python** (measured) | `tests/parity/split-run.parity.test.ts` — TS clustering Python's REAL pipeline scored pairs == all-Python clusters; and an independent all-TS run reached the same partition with 0 threshold-flips and max score-delta <1e-3 on the test dataset |
| **Cluster JSON** (`compare-clusters` interchange) | ✅ **byte-safe** | shared `parseClustersJson`; `cluster-conformance` above |
| **Config YAML** | ✅ **portable** | language-neutral, shared schema + `config-edits`/`config-optimizer` parity |
| **Learning Memory** corrections + **run log** + **`record_fingerprint`** | ✅ **portable** | `memory_export`/`import`, `list_runs`/`rollback` ported; fingerprint parity fixture |
| **string scoring** (a scorer's score) | 🟡 **tolerance-bounded (4dp)** | `tests/parity/scorer-ground-truth.test.ts` asserts to 4 decimals — a pair near a threshold **can flip** the match decision. Shared WASM kernels (`score-wasm`/`fs-wasm`) make jaro_winkler/levenshtein/token_sort/exact + FS byte-identical **when the WASM backend is enabled** |
| **standardize / transforms** | 🟠 **divergent (not byte-portable)** | Python polars-native standardizers vs TS impls; **dates cannot be byte-ported** (`dateutil` fuzzy vs `chrono`) |
| **embeddings** | 🟠 **cosine-tolerance only** | TS has no torch/Vertex; vectors are caller-supplied, ~1e-7 cosine drift, not byte-equal |
| **auto-config / controller commit** | 🟠 **structural, not byte-equal** | controller parity is structural on most fixtures; the two can commit different configs on the same data |
| **distributed / Ray / bucket backend, documents/VLM ingest, routing** | ⛔ **Python-only by architecture** | no TS execution path (declared) |

## What this means for "handing off phases"

- **Handoff at a durable-artifact boundary is seamless.** Score/cluster in
  either language, persist the identity graph or cluster JSON, resume in the
  other — same answer. Identity is the flagship: it even cross-*verifies*.
- **Handoff mid-numeric-pipeline is only as strong as the weakest link.** If a
  chain includes `standardize`, `score` (without the shared WASM kernel), or an
  embedding step, the resumed pipeline can reach a *different* result than an
  all-one-language run. The failure mode is a threshold decision flipping on a
  4th-decimal score delta, or a date/standardization value not reproducing.

**Guidance:** hand off at the **cluster** or **identity** boundary (byte-safe).
If you must hand off at the **score** boundary, enable the shared WASM scorer on
the TS side (byte-identical for the covered scorers) and avoid re-thresholding
across the boundary. Do **not** split a pipeline across `standardize`/dates,
embeddings, or the controller and expect reproduction.

## The harness

- `packages/python/goldenmatch/scripts/emit_cluster_conformance_fixture.py` —
  Python oracle: emits scored-pair scenarios + Python's clustering partition.
- `packages/typescript/goldenmatch/tests/parity/cluster-conformance.parity.test.ts`
  — reruns each through TS `buildClusters` and asserts the identical partition.
- `packages/python/goldenmatch/scripts/emit_split_run_fixture.py` +
  `packages/typescript/goldenmatch/tests/parity/split-run.parity.test.ts` — the
  **end-to-end split-run**: Python runs a REAL pipeline (`standardize→block→score`)
  via `MatchEngine`, emits its scored pairs + clusters; the TS test (a) clusters
  Python's real scored pairs and asserts it reproduces Python's own clusters
  (handoff fidelity), and (b) runs a full independent all-TS `dedupe` and asserts
  the same partition + a bounded scored-pairs delta (no threshold flip). Blocking
  is neutralized (shared key) so any divergence would be scoring/standardize.

**Extending it** (next boundaries, same pattern — Python oracle → TS parity
test): a *scoring* conformance emitter that reports max |Δscore| + threshold-flip
count across a corruption sweep + more scorers; a *standardize* emitter that diffs
standardized cells (esp. dates) to quantify the known divergence; and a split-run
over a **corrupted** dataset engineered to sit pairs on the threshold, to find the
case where the 4dp tolerance actually flips a final cluster (the split-run here
agrees cleanly, but that is dataset-specific).
