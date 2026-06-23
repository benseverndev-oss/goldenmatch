# 0022 — Multimodal ER: the perceptual crawl tier (perceptual-core)

**Status:** proposed • **Brainstorm:** [../planning/multimodal-er.md](../planning/multimodal-er.md)

## Context

GoldenMatch resolves entities only when they are described in text/structured
fields, yet an **entity is modality-independent** — the same person/org/product
appears as a face, a voiceprint, a logo, a product shot. The North Star ("the
default tool for entity resolution") has no "…as long as it's text" clause, so
audio/visual is a frontier where reaching for something else is still the easier
choice.

The brainstorm split "multimodal ER" into three sub-problems and fixed the order:
**(3) modality-as-evidence** (media as a high-signal *column* on the existing
record, the wedge), **(1) within-modality ER** (second step), **(2) cross-modal**
(moonshot). It also established that the **vector spine is already
modality-agnostic** in-tree (`embeddings/providers.py` `EmbeddingProvider`
Protocol, `core/ann_blocker.py`, the LSH/SimHash blockers, scorer, explain) — so
the semantic "walk" tier is mostly the addition of non-text encoder providers, not
new ER machinery.

This ADR covers **v1 only**: the deterministic *crawl* tier of the wedge.

## Decision

Add an **in-house, deterministic perceptual-hash crawl tier** for **image and
audio** as the v1 of modality-as-evidence ER. No ML model in the loop; fully
auditable.

**New pyo3-free `goldenmatch-perceptual-core` crate**, structured like
`sketch-core` (NOT `fingerprint-core`, which is the unrelated SHA-256 record-id
canonicalizer): `phash.rs` (DCT-based image perceptual hash), `audio_fp.rs`
(spectral-landmark audio fingerprint), shared `hash.rs`, `lib.rs`. Exposed on
Python (pyo3 `native` module + pure-Python fallback) and, where cheap, TS — same
host/kernel split as `sketch-core` (the kernel computes per-item signatures; the
host groups buckets via the existing blocking infrastructure).

**In-house = we own the hashing algorithm**, the same posture as `sketch-core`'s
hand-rolled hash family (no third-party `imagehash`/`pyacoustid` — version skew
breaks byte-parity). **Format decoding is NOT in the kernel**: the kernel operates
on *decoded* input (a luma pixel grid for image, mono PCM samples for audio), so
`perceptual-core` stays codec-free and parity-clean. PNG/JPEG/WAV → samples is a
thin Python-side adapter (consistent with "the encoder lives upstream"); raw
decoded input is also a first-class entrypoint (BYO-decoded).

**Parity-by-construction** (the ADR 0020 contract): a Python reference
(`core/perceptual.py`) is authoritative; golden vectors
(`tests/fixtures/perceptual_golden.json`) are checked by the Rust crate and the
Python impl, plus the `GOLDENMATCH_NATIVE=0/1` native↔python sweep. Determinism is
the whole point of the crawl tier, so output is byte-identical across surfaces.

**Native gating.** The `perceptual` component ships native-available but is NOT in
`_native_loader._GATED_ON` (the conservative posture shared with `sketch` /
`pprl_bloom`): reachable via `GOLDENMATCH_NATIVE=1`, default pure-Python under
`auto`. Output is deterministic, so a default-on flip is a perf/published-wheel
decision, not an accuracy one.

**Pipeline wiring** (reuses existing seams, no new pipeline stages):
- **Block** — `strategy="perceptual"` conforming to the `BlockResult` /
  `BlockingConfig` seam (mirrors `"lsh"`/`"simhash"`): banded hamming-LSH over the
  hash bits for candidate generation, recall tunable via the band split.
- **Score** — a hamming-distance comparator emitting a **calibrated** signal +
  provenance (`"image pHash hamming=3/64"`) so `core/explain.py` shows *why*. The
  media stays **corroborating** evidence beside auditable text features, never the
  sole basis — preserving never-black-box without an ML model.
- **Auto-config** — recognizes a media column (path / bytes / decoded array) and
  wires the perceptual feature at a sane default threshold (zero-config first run).

**Recall gate.** An always-on synthetic gate (`test_perceptual_recall.py`):
image re-encode/crop/relight variants and audio re-encode/trim/noise variants must
re-block to their source above the pinned threshold — mirroring `test_lsh_recall.py`.

## Consequences

- New crate `goldenmatch-perceptual-core` wired into the rust CI lane and the
  `native` pyo3 module; new blocking `strategy="perceptual"` + `PerceptualKeyConfig`
  (re-exported); a scorer comparator + provenance; an auto-config media-column rule.
- **Scale-invariant tension (inherited):** banded hamming-LSH is approximate; the
  resolution follows the existing text-ANN path's recall-gate posture rather than a
  new mechanism (noted in the planning doc).
- **Deferred, explicitly out of v1:** the `[vision]`/`[audio]` encoder extras and
  the BYO-vector *walk* tier; the biometric template-protection guardrail (the
  PPRL/Bloom-CLK privacy differentiator); within-modality (#1) and cross-modal (#2).
- **Rejected:** third-party perceptual-hash libraries (byte-parity / version-skew
  footgun, and they bury the algorithm we want to keep auditable); putting codec
  decoding inside the kernel (drags image/audio codec deps into a crate that must
  stay pyo3-free and parity-clean across surfaces).
