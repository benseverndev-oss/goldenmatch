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
- [x] **Slice 2 — Rust `perceptual-core` kernel + golden parity.** `radial.rs`
      (`radial_variance`) reproduces `_radial_variance_python` **bit-for-bit** —
      reuses the crate's align-corners `bilinear_resize` + banker's-rounding
      `py_round` (now `pub(crate)`), and the golden-vector fixture carries the
      profile as **hex bit patterns** (`gen_perceptual_golden.py::_f64_bits`) so the
      parity oracle has zero decimal round-trip ambiguity. `tests/golden.rs`
      `rust_reproduces_radial_fixture` asserts exact f64 equality; `cargo test` +
      `clippy -D warnings` + `fmt` all green locally. The comparison stays Python.
      **Lessons:** (1) store golden floats as hex bits, not JSON decimals — a
      shortest-repr decimal drifts a ULP on parse and silently failed 4 entries;
      (2) materialise the squared deviations before summing so no mul-add fuses.
- [x] **Slice 2b — PyO3 binding + loader gating + parity sweep.** `perceptual_radial_variance`
      shim in `native/src/perceptual.rs` (registered in `lib.rs`); `radial_variance`
      already dispatches via `native_enabled("perceptual")` ("perceptual" is the
      existing not-gated component, reachable under `GOLDENMATCH_NATIVE=1`).
      `goldenmatch-native` bumped 0.1.11→0.1.12 (Cargo + pyproject in lockstep) so a
      republish carries the new symbol. `test_native_perceptual_parity.py` extended:
      native↔python radial sweep (60 grids) + golden fixture + forced-native dispatch.
      `cargo check`/`clippy -D warnings`/`fmt` green; runs for real in the `native` lane.
- [x] **Slice 3 — pipeline match feature.** A `radial` scorer (`core/scorer.py`,
      single + NxN matrix over the `radial_hex` column, aligned-Pearson) added to
      `VALID_SCORERS`; the geometric counterpart to `phash`. Auto-config
      (`perceptual_autoconfig.py`) detects the 96-char column form and appends a
      `radial` matchkey — a uniform-96 column reads as radial (audio fingerprints
      vary in length, so always-96 is the geometric profile). The rotation-aligned
      feature gets **no LSH blocking** (banded-LSH assumes positional bit-bands that
      rotation breaks) — scorer-first; rotation-robust blocking is a follow-up.
      Tests: `test_perceptual_radial.py` (scorer single + matrix),
      `test_perceptual_autoconfig.py` (detection + disambiguation + matchkey shape);
      `tuning.mdx` flag description updated.

## Status

The walk-tier image feature is **complete end-to-end** (reference → byte-parity
kernel → native binding → pipeline scorer + auto-config), the same arc the crawl
tier took in #1221. The rotation/crop blind spot finding 1 identified is closed
with a measured, parity-clean, no-ML feature. Remaining follow-up: rotation-robust
**blocking** for the radial feature (banded-LSH breaks under rotation) — today the
`radial` scorer relies on other blocking or all-pairs at modest N.

---
**Classification:** planning/active • **Last updated:** 2026-06-23
