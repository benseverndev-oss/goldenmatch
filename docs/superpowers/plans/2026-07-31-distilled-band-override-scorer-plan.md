# Plan: distilled band-override scorer

- **Spec:** `docs/superpowers/specs/2026-07-31-distilled-band-override-scorer-design.md`
- **Date:** 2026-07-31
- **Posture:** validation-first. Each phase is committable + reversible; the runtime
  path stays behind an opt-in flag until the gates pass. We train + ship; users consume.

## Phase 0 — offline harness (THIS phase; also the generalization-gate machinery)

Turn the scratchpad probes into a real, tested in-repo harness under
`scripts/er_matcher/band_student/`. This is what runs the gates AND the foundation
the runtime kernel later consumes.

- **`features.py`** — `band_features(row_a, row_b, fields, fs_score)`: generic
  comparison-feature vector `[jaro_winkler, token_sort, exact, both_present]` per
  field + the FS score. Order-invariant, domain-agnostic (the transfer thesis).
- **`student.py`** — `BandStudent` wrapping a GBDT (`HistGradientBoostingClassifier`);
  `fit` / `predict` / `save_json` / `load_json`. (Portable export = Phase 2.)
- **`evaluate.py`** — `distillation_fidelity` (student-vs-teacher, student-vs-gold)
  + `end_to_end_override` (best-threshold pair-F1 over a fixed candidate universe,
  band pairs overridden). Reproduces historical_50k: ceiling 0.967 / distill 0.880 /
  e2e +3.1–4.8.
- **`test_band_student.py`** — self-contained SYNTHETIC test (no external dataset):
  build a tiny separable band, assert train→predict→eval runs + the override never
  regresses a confident-FS no-op case. Deterministic, CI-safe.
- **TDD:** failing test (feature shape + override no-op) → impl → commit.

## Phase 1 — runtime scorer (opt-in, Python v0)

- `core/band_student.py` runtime loader + `score_buckets` hook: on band pairs
  (adaptive window around the operating threshold), predict with the pinned student;
  else FS. Env-gated `GOLDENMATCH_BAND_STUDENT=1`, default OFF, byte-identical when off.
- Cluster-level F1 measured here (**gate #2**) — plug the override into the live
  pipeline + re-cluster, not just candidate-set pair-F1.

## Phase 2 — pinned distribution (we train, users consume)

- Train centrally on a diverse corpus (synthetic + eval-only, the 1.5B posture);
  serialize to a PORTABLE format; register + pin (url + sha256) like
  `core/er_matcher/registry.py`. `resolve_band_student()` downloads + verifies.
- Modal/CI training workflow (teacher = the 1.5B, offline).

## Phase 3 — score-core kernel + parity (perf + closure)

- Reimplement student inference in `score-core` (Rust) → native/WASM/TS parity
  fixtures, quantized/deterministic — the "one kernel, all surfaces" thesis.
- NNUE-style per-record accumulator (block-N² reuse + streaming edits).

## Validation gates (spec §Open questions) — block default-on

1. **Transfer** (load-bearing): diverse-train / held-out-hard eval + schema
   normalization + CI transfer-panel. 2. Cluster-level F1. 3. Calibration.
   4. Label-budget curve. 5. Explainability (feature attribution).

## Non-goals

Replacing zero-config FS (opt-in tier); any query-time model (offline teacher only).
