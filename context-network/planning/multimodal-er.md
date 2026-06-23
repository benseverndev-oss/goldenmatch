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

## Proposed v1 first slice (the wedge, crawl tier)

Smallest end-to-end value that respects every commitment:

1. A `perceptual` matchkey/blocker family — pHash for an image-path/bytes column,
   exposed through the existing `BlockingConfig` strategy seam (mirrors how
   `strategy="lsh"`/`"simhash"` were added, ADR 0020).
2. A hamming-distance comparator in the scorer, emitting a **calibrated** signal +
   provenance ("image pHash hamming=3/64") so `explain` can show *why*.
3. Auto-config recognizes a media column and wires the perceptual feature with a
   sane default threshold — zero-config first run.
4. A parity/recall gate (synthetic image set: re-encode/crop/relight variants),
   mirroring the `test_lsh_recall.py` posture.

This ships modality-as-evidence on text+image with **no model dependency and full
auditability**, then "walk" reuses the spine by adding non-text providers.

## Open questions (to steer next)

- **v1 modality:** image (pHash) first, or audio (fingerprint) first? Image has the
  cleaner deterministic primitive.
- **Encoder posture:** strictly bring-your-own-vectors for "walk," or do we ship
  `[vision]`/`[audio]` extras that bundle an encoder (zero-config tension vs. wheel
  size / GPU)?
- **Scope of v1:** crawl tier only (deterministic, no ML), or crawl + a BYO-vector
  walk path in the same release?
- **Privacy line:** do we make biometric template protection a v1 guardrail, or a
  fast-follow once within-modality (#1) lands?

## Next steps

- [ ] Lock v1 scope (the four open questions above).
- [ ] Confirm the `BlockingConfig` strategy seam + scorer comparator seam are the
      right insertion points (read `core/blocker.py`, `core/scorer.py`).
- [ ] Draft ADR 0022 — Multimodal ER (modality-as-evidence) once scope is locked.

---
**Classification:** planning/active • **Last updated:** 2026-06-23
