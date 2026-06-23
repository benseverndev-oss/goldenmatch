# Walk tier — geometric-robust image feature (ADR 0022, finding 1)

The crawl-tier image hash (`phash_image`) is **photometric, not geometric**: the
bench harness (#1229) measured **0.0 recall on rotation and crop** (finding 1).
This is the walk-tier feature that closes that blind spot — built only after the
algorithm was **validated on the harness**, because two cheaper ideas were
measured-refuted first.

## What was refuted (don't re-try)

- **Dihedral-min canonical pHash** (min over the 8 rotations/flips): leaves 8°-rotate
  and content-crop at 0.0 (neither is a dihedral symmetry) **and** degrades the
  photometric cases (min-over-orientations adds distance to near-identical pairs).
- **Rotation-INVARIANT descriptors** (ring energies of |FFT|; per-ring angular
  harmonics): rotation/crop-invariant but **near-zero discrimination** —
  match/non-match separation ≤ 0.006 across three descriptor variants × two
  comparison metrics. Invariance discards exactly the orientation information that
  distinguishes these images; that tradeoff is the crawl/walk boundary.

## What works (validated, then built)

**Radial-variance profile + angular-aligned comparison** — the rotation-AWARE
method (pHash's own `ph_image_digest` radial hash). A per-angle pixel-variance
vector keeps orientation (so it discriminates), and the comparison **slides the
profile to its best cyclic angular shift** (rotation just rotates the profile),
structurally identical to `audio_ber_aligned`'s time-offset search. Measured on
the bench image suite (20 bases × 7 transforms):

| metric | pHash | radial-variance |
|---|---|---|
| best F1 / separation | 0.48 / overlap | **0.999 / 0.46** |
| rotate recall | **0.0** | **0.95** |
| crop recall | **0.0** | **1.00** |
| photometric (bright/contrast/blur/noise) | 0.4–1.0 | **1.00** |
| unrelated similarity (max) | — | 0.61 |

## Slices

- [x] **Slice 1 — Python reference + geometric-recovery test.** `core/perceptual.py`:
      `radial_variance` (per-angle variance over the align-corners resize; `_python`
      reference + native dispatch stub), `radial_align_similarity` (max Pearson over
      cyclic angular shifts, in `[0,1]`, pure-Python like `audio_ber_aligned`), and
      the canonical column form `radial_hex` / `radial_from_hex` (z-normalise +
      int8 quantise → 96-char fixed-width; affine-invariant comparison makes the
      quantise lossless for scoring). `tests/test_perceptual_radial.py` locks the
      blind-spot closure (rotate/crop strongly recalled, separated from unrelated),
      photometric non-regression, alignment-beats-raw, and hex round-trip.
- [ ] **Slice 2 — Rust `perceptual-core` kernel.** `perceptual_radial_variance`
      byte-parity with `_radial_variance_python` (same banker's-rounding nearest-
      neighbour sampling the kernel already uses for the audio bins); extend the
      golden-vector fixture from the reference. The comparison stays Python.
- [ ] **Slice 2b — PyO3 binding + loader gating + parity sweep.** Shim in
      `native/src/perceptual.rs`; `radial_variance` already dispatches via
      `native_enabled("perceptual")`. Parity test in the `native` lane.
- [ ] **Slice 3 — pipeline match feature.** A `radial` scorer (aligned similarity
      over the `radial_hex` column) + auto-config detection of the 96-char column
      form; the geometric counterpart to the `phash` scorer. Blocking for a
      rotation-aligned feature is its own problem (banded-LSH assumes positional
      bit-bands, which rotation breaks) — scorer-first; blocking is a follow-up.

## Status

Slice 1 is in-tree and validated: the walk-tier image feature exists as the
authoritative reference + column form, with the blind-spot closure locked by test.
The Rust kernel (slice 2), binding (2b), and pipeline wiring (3) follow the exact
arc the crawl tier took in #1221.

---
**Classification:** planning/active • **Last updated:** 2026-06-23
