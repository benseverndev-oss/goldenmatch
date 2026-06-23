# Multimodal Entity Resolution — brainstorm / planning

> **Status:** brainstorm (not yet a decision). Captures the agreed framing and a
> crawl→walk→run plan for resolving entities that appear in audio/visual form, not
> just text/structured data. Hardens into an ADR once v1 scope is locked.

## The premise

An **entity is modality-independent**. A person, an organization, a product, a
place — the *same* entity manifests as a row of text, a face in a photo, a
voiceprint in a call, a logo, a product shot, a scanned signature. GoldenMatch
today resolves entities only when they are *described in text/structured fields*.
The North Star ("the tool any developer reaches for *by default* for entity
resolution") does not have a "…as long as it's text" clause. Audio/visual is a
frontier where reaching for something else is still the easier choice — so it is
exactly the kind of gap the North Star says to close.

## Three problems hiding in "multimodal ER" (agreed priority)

| # | Problem | Shape | Priority |
|---|---|---|---|
| **3** | **Modality-as-evidence** — text/structured record stays the canonical entity; an attached photo/recording *contributes match signal*. The media is a high-signal **column**, not a new entity type. | A new feature/comparator on the existing record-linkage pipeline | **THE WEDGE (v1)** |
| **1** | **Within-modality ER** — dedupe a pile of faces, cluster voiceprints, match logos. "Same pipeline, comparison space is vectors not strings." | Same pipeline, vector blocking + vector comparator | **Natural second step** |
| **2** | **Cross-modal linkage** — *this voice = this customer row*. No shared comparison space; needs an anchor (transcribe→text ER) or a joint embedding space (CLIP-style). | New: joint/aligned encoders, cross-modal scoring | **Moonshot** |

Rationale for the order: #3 reuses the most existing machinery, has the least
black-box exposure (modality corroborates auditable text features rather than
deciding alone), and ships value on day one. #1 is #3 minus the text anchor. #2
needs aligned encoders we don't have and carries the worst explainability/privacy
load.

## The architectural key (and why the gap is small)

**GoldenMatch should never "understand" audio or video. It resolves entities
given `(vector + structured attributes)`.** The encoder lives *upstream* and is
pluggable — the same posture as `goldenmatch[native]` and the goldenmatch-kg
drop-in shims. The instant any modality collapses to `(embedding, metadata)`, it
is the *same ER problem* already solved.

Crucially, **the vector spine already exists and is modality-agnostic** (verified
in-tree, 2026-06-23):

| Stage | Existing component | Modality-ready? |
|---|---|---|
| encode | `embeddings/providers.py` — `EmbeddingProvider` Protocol (`model_id` + `embed(...) -> (n, dim) ndarray`); `NoneProvider` zero-vector zero-config default | **Output contract is vectors** — input type is the only thing text-bound |
| block | `core/ann_blocker.py` (`ANNBlocker`, FAISS inner-product), `core/simhash_blocker.py`, `core/lsh_blocker.py` | **Already operates on any `np.ndarray`** |
| score | `core/scorer.py`, `core/cross_encoder.py`, `core/llm_scorer.py` | Comparator family; cosine/IP path exists for embeddings |
| cluster | graph WCC | Unchanged |
| golden | survivorship | Exemplar/medoid for a media cluster (new selector) |
| explain | `core/explain.py`, `core/explainer.py` | Needs vector-match adaptation (see tension below) |

So the real v1 surface is **not** "build vector ER." It is:

1. **Non-text encoder providers** — generalize the `EmbeddingProvider` seam so a
   provider can take image bytes / audio frames / file paths, not only
   `list[str]`. Output contract (`(n, dim) ndarray`) is unchanged, so everything
   downstream just works. Optional extras: `goldenmatch[vision]`, `[audio]`, plus
   pure **bring-your-own-vectors** (a column already containing embeddings).
2. **Deterministic perceptual primitives** — the auditable *crawl* tier (below).
3. **Evidence fusion + provenance** — teach auto-config + the scorer to treat a
   media column as a first-class feature, and keep per-decision provenance so the
   match stays explainable.
4. **Explainability adapted to vectors** — calibrated probability + nearest
   exemplar, not a raw cosine.

## Crawl → walk → run

- **Crawl — deterministic perceptual primitives (no model, fully auditable).**
  Perceptual image hashes (pHash/dHash), audio fingerprints (Chromaprint /
  Shazam-style landmark hashes), simhash. Zero-config, no model download, fast,
  and **explainable** (hamming distance on a hash you can show). They slot
  *directly* into the existing matchkey/blocking machinery as "fuzzy hashes" — the
  same shape as today's matchkeys. This is the highest-fit, lowest-risk start and
  it sidesteps the black-box problem entirely. **NOT present in-tree today** — this
  is genuinely new code, but small and self-contained.
- **Walk — embeddings (bring-your-own / optional extras).** Semantic matching:
  cropped/re-encoded/relit images, different recordings of one voice. Reuses the
  *entire* existing embedding+ANN+score+explain spine; the only new code is the
  non-text providers.
- **Run — joint cross-modal space.** Selfie↔ID, voice↔record. Aligned encoders,
  cross-modal calibration. The moonshot (#2).

## North-Star tensions to design *around*

- **"Advanced, never black-box."** Embedding similarity is *the* black box —
  "cosine 0.91" is not auditable like "surname exact + DOB exact." Mitigations
  that should *constrain* the design: calibrated probabilities (not raw cosine),
  nearest-exemplar evidence surfaced, region/segment attribution, and keeping
  modality as **corroborating** evidence beside auditable text features rather than
  sole basis. (Another reason #3 is the wedge.) The crawl tier dodges this entirely.
- **"Correctness must be scale-invariant."** ANN blocking is *approximate by
  construction* — brute-force-exact on a laptop, HNSW-approximate at 100M ⇒
  different answers across scales. Head-on collision with a core commitment; needs
  its own decision (exact-below-N threshold? recall gate parity with the text ANN
  path?). Note the text ANN path already faces this — inherit its resolution.
- **Privacy / the PPRL tie-in (a differentiator).** Voiceprints/faceprints are
  *biometric special-category data* (GDPR/BIPA); clustering them is a compliance
  surface text ER never had. But biometric template protection / cancelable
  biometrics is a *sibling* of the PPRL/Bloom-CLK work GoldenMatch **already
  ships**. "Privacy-preserving cross-modal ER" is a coherent, differentiated
  direction that builds on existing assets rather than greenfield — potentially the
  thing that makes GM the *default* here specifically.

## v1 scope (LOCKED 2026-06-23)

Decided via brainstorm:

- **Modality:** **image AND audio together**, as an **in-house native kernel**
  (no third-party perceptual-hash lib — `imagehash`/`pyacoustid` are out; we own
  the algorithm, the same posture as `sketch-core`'s hand-rolled hash family).
- **Encoder posture (walk tier, NOT v1):** ship `goldenmatch[vision]`/`[audio]`
  optional extras that bundle an encoder. Recorded now; no v1 code.
- **Release contents:** **crawl tier only** — deterministic perceptual hashing,
  no ML. (Privacy guardrail and BYO-vector walk are fast-follows, not v1.)

### v1 build (the wedge, crawl tier)

1. **New pyo3-free `goldenmatch-perceptual-core` crate**, mirroring `sketch-core`:
   `phash.rs` (DCT-based image perceptual hash), `audio_fp.rs` (spectral landmark
   audio fingerprint), shared `hash.rs`, `lib.rs`. NOT `fingerprint-core` — that's
   the unrelated SHA-256 record-id canonicalizer.
2. **Kernel operates on *decoded* input** — a luma pixel grid (image) / mono PCM
   samples (audio) — so the core stays codec-free and parity-clean. Format
   decoding (PNG/JPEG/WAV→samples) is a thin Python-side adapter, consistent with
   the "encoder lives upstream" philosophy; BYO-decoded-input also works.
3. **Parity-by-construction** (ADR 0020 contract): a Python reference
   (`core/perceptual.py`) is authoritative; golden vectors
   (`tests/fixtures/perceptual_golden.json`) checked by Rust + Python, plus the
   `GOLDENMATCH_NATIVE=0/1` parity sweep. Native ships available but NOT in
   `_native_loader._GATED_ON` (conservative, like `sketch`/`pprl_bloom`).
4. **Blocking** `strategy="perceptual"` conforming to the `BlockResult` /
   `BlockingConfig` seam (mirrors `"lsh"`/`"simhash"`): bucket by hash prefix /
   banded hamming-LSH for candidate generation.
5. **Comparator** in the scorer: hamming-distance signal + provenance ("image
   pHash hamming=3/64") so `explain` shows *why* — keeping the never-black-box
   commitment without an ML model in the loop.
6. **Auto-config** recognizes a media column (path/bytes/decoded) and wires the
   perceptual feature with a sane default threshold — zero-config first run.
7. **Recall gate** (synthetic variant sets: re-encode/crop/relight for image,
   re-encode/trim/noise for audio), mirroring `test_lsh_recall.py`.

This ships modality-as-evidence on text + image + audio with **no model
dependency and full auditability**; the "walk" tier then reuses the entire
existing embedding+ANN+score+explain spine via the optional encoder extras.

## Deferred (fast-follows, not v1)

- **Biometric template-protection guardrail** tied to the existing PPRL/Bloom-CLK
  work (the privacy differentiator) — lands with or just after within-modality ER.
- **BYO-vector walk path** + the `[vision]`/`[audio]` encoder extras.
- **Within-modality ER (#1)** and **cross-modal (#2)** per the priority table.

## Next steps

- [x] Lock v1 scope.
- [x] Draft ADR 0022 — Multimodal ER (modality-as-evidence, perceptual crawl tier).
- [x] **Slice 1 — authoritative Python reference + golden vectors.**
      `core/perceptual.py` (stdlib-only DCT pHash + Haitsma-Kalker audio
      fingerprint, decoded-input contract), `scripts/gen_perceptual_golden.py`,
      `tests/fixtures/perceptual_golden.json`, `tests/test_perceptual_reference.py`
      (golden parity + invariance + validation). The algorithm params are now
      pinned by the fixture; the spec is the module docstring + this note.
- [x] **Slice 2 — Rust `goldenmatch-perceptual-core` crate** (pyo3-free, standalone
      `[workspace]`, mirrors `sketch-core`). `phash.rs` + `audio_fp.rs` reproduce
      the committed fixture **byte-for-byte** (`tests/golden.rs`); wired into the
      rust CI lane (cache workspace + explicit `cargo test`/`clippy`). Parity holds
      because the transforms are direct (no FFT) and mirror the Python op order, so
      on a shared libm the transcendental results are bit-identical.
- [x] **Slice 2b — pyo3 `native` wrapper + loader gating + parity sweep.**
      `native/src/perceptual.rs` exposes `perceptual_phash_image` /
      `perceptual_phash_batch` / `perceptual_fingerprint_audio`; `core/perceptual.py`
      dispatches to them via `native_enabled("perceptual")` (AttributeError →
      Python fallback for wheel skew). `"perceptual"` ships native-available but is
      NOT in `_native_loader._GATED_ON` (conservative, like `sketch`/`pprl_bloom`):
      reachable via `GOLDENMATCH_NATIVE=1`, default pure-Python under `auto`.
      `tests/test_native_perceptual_parity.py` asserts native↔python byte-identity
      over a randomized sweep + the fixture + validation (wired into the `native`
      CI lane). The default-on flip stays a perf/published-wheel decision.
- [x] **Slice 3 — image pHash as a pipeline match feature.** A `phash` scorer
      (hamming similarity over a hex perceptual hash; `core/scorer.py`, single +
      NxN matrix, provenance via `explain._SCORER_NAMES`) and a `perceptual`
      banded-hamming-LSH blocking strategy (`core/perceptual_blocker.py` +
      `PerceptualKeyConfig` + `BlockingConfig.strategy="perceptual"` + dispatch).
      `core/perceptual.phash_hex` produces the canonical 16-char column form. A
      media column is now a first-class block + score feature ("modality as
      evidence"). Verified end-to-end on real pHashes of image variants
      (`tests/test_perceptual_feature.py`). Frontend dropdown const updated; TS
      port of `phash`/`perceptual` is a follow-up.
- [x] **Slice 3b — audio-fp feature + auto-config rule + recall gate + extras.**
      (1) `audio_fp` scorer (`core/scorer.py`, single + matrix) over the
      offset-aligned BER (`perceptual.audio_ber_aligned` + `audio_fp_hex` column
      form) — the variable-length counterpart to image pHash. (2)
      `core/perceptual_autoconfig.py` detects fixed-width-hex media-hash columns
      and appends a `phash`/`audio_fp` matchkey (+ perceptual blocking for an image
      column when nothing else blocks); wired into `auto_configure_df` behind
      `GOLDENMATCH_PERCEPTUAL_AUTOCONFIG=1` (default OFF, fail-open,
      byte-identical when off). (3) Always-on recall gate over 5 image-variant
      pairs (`test_perceptual_feature.py`). (4) `[vision]`/`[audio]` pyproject
      extras backing the `decode_image_to_luma`/`decode_audio_to_mono` adapters.
      TS port of the `phash`/`perceptual`/`audio_fp` surfaces remains a follow-up.

## Bench-harness findings (first measured iteration, 2026-06-23)

The dispatch-only metrics harness (`scripts/bench_perceptual/`, PR #1229) produced
its first real numbers on the shipped kernel. They **refuted two of three** plausible
fixes and **confirmed one** — exactly what the harness is for. Numbers from the
deterministic image suite (30 bases × 7 transforms) + audio suite (12 bases × 3):

- **Finding 2 (blocking band-count) — CONFIRMED, FIXED.** `num_bands=8` recalls only
  **0.72** of true near-dup pairs (reduction 0.77); `num_bands=16` recalls **0.97**
  (reduction 0.28). For a near-dup *media* blocker recall is the priority (a missed
  dup is unrecoverable; the scorer filters extra candidates cheaply). Fix: the band
  count is now **recall-target-driven** — `perceptual_blocker.recommend_num_bands`
  derives it from the scorer threshold via the banded-LSH S-curve (mirrors the
  semantic-blocking move in #1090). `PerceptualKeyConfig.num_bands` default 8→16;
  the zero-config path computes it from the image threshold (0.85 → 16 bands @ 0.95
  recall target). Tests in `test_perceptual_feature.py` / `test_perceptual_autoconfig.py`.
- **Finding 1 (geometric robustness) — REFUTED, deferred to the walk tier.** The
  suite's `rotate` (8°) and `crop` (content) recall **0.0**; pHash is photometric,
  not geometric. The cheap candidate fix (dihedral-min canonical hash, min over the
  8 rotations/flips) was **measured net-negative**: it leaves 8°-rotate and crop at
  0.0 (neither is a dihedral symmetry) *and* degrades the photometric cases it
  should leave alone (brightness 1.0→0.97, noise 0.83→0.57, because min-over-
  orientations adds distance to near-identical pairs). Real geometric robustness
  (small-angle rotation, content crop) needs a different representation
  (feature-point / log-polar / learned) — i.e. the BYO-vector **walk tier**, not a
  crawl-tier hash trick. Not shipped; documented as a known limitation.
- **Finding 3 (audio additive noise) — RESOLVED: it was a DATASET artifact, fixed by
  the harness + the threshold (no kernel redesign).** The first read ("noise kills it,
  not a threshold problem") was measured on **pure tones**, which are pathological for
  Haitsma-Kalker: a 3-tone signal leaves most log-bands near-empty, so each bit is the
  sign of a ~zero energy difference = pure noise (tone-noise BER ~0.5, indistinguishable
  from unrelated — hence the apparent "no threshold separates it"). Re-measured on
  **realistic broadband audio** (40 sinusoids across the 300-2000 Hz band), the
  fingerprint **is** noise-robust: at 20 dB SNR a noisy copy scores **0.79–0.88**
  similarity vs **~0.49** for unrelated (clean separation, overlap 0). Two fixes, both
  measured: **(1) harness** — the bench audio suite now generates broadband signals +
  SNR-calibrated noise (`datasets.py`), so it reflects realistic degradation, not a tone
  artifact; **(2) threshold** — the `audio_fp` auto-config default drops **0.80 → 0.65**
  (the canonical Haitsma-Kalker BER ≤ 0.35 match point). On the broadband suite that
  lifts noise recall **0.0 → 1.0** at **P=0.96** (vs P=1.0/R=0.60 at 0.80 — a net F1
  win), while unrelated (~0.49) stays safely rejected. The earlier "lowering is harmful"
  was itself the tone artifact (no margin on tones; a real 0.79-vs-0.55 margin on
  broadband). Locked by `tests/test_perceptual_audio_noise.py` (incl. a test that the
  pure-tone case is NOT recoverable, documenting the artifact). **No new kernel** — the
  bit-reliability redesign that was floated is unnecessary at realistic SNR.

## Status (crawl tier)

The image-and-audio perceptual crawl tier is **complete end-to-end**: reference
(slice 1) → byte-parity Rust kernel (2) → PyO3 binding (2b) → pipeline match
feature (3) → audio feature + zero-config auto-wiring + recall gate + extras (3b),
plus the recall-target blocking fix from the first bench iteration (finding 2).
The wedge (#3 modality-as-evidence) is real. Remaining frontier per the priority
table: within-modality ER (#1) and cross-modal (#2); plus the biometric
template-protection / PPRL privacy guardrail and the BYO-vector "walk" tier — which
findings 1 & 3 now concretely motivate (geometric-robust image vectors + a
noise-robust audio representation are walk-tier, not crawl-tier, work).

---
**Classification:** planning/active • **Last updated:** 2026-06-23
