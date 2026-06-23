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
      on a shared libm the transcendental results are bit-identical. **Still TODO
      for slice 2b:** the pyo3 `native` wrapper + `_native_loader` gating +
      `GOLDENMATCH_NATIVE=0/1` Python↔native parity sweep.
- [ ] Slice 3 — `BlockingConfig` strategy seam + scorer comparator seam (read
      `core/blocker.py`, `core/scorer.py`), auto-config media-column rule, recall gate.
- [ ] Decode adapters wired to the optional `[vision]`/`[audio]` extras.

---
**Classification:** planning/active • **Last updated:** 2026-06-23
