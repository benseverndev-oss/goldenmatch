# GoldenDB: matrix-native entity resolution (fuzzy-join-as-matmul)

**Date:** 2026-06-07
**Status:** design + EXPERIMENTAL work-in-progress spike. The design was locked with
Ben; an experimental, NOT-production-ready implementation of the `backend="gpu"`
block scorer has since landed at `packages/python/goldenmatch/goldenmatch/core/goldendb/`
(char-ngram encode -> JAX cosine matmul -> GA2M weighted-average combine with exact
attribution + monotonicity + a `jax.grad` training step). Validated on CPU JAX
(`tests/test_goldendb_gpu_backend.py`); GPU wall-clock is NOT validated (no GPU in
env). See "Implementation status" below for what is and isn't wired.
**Scope:** `packages/python/goldenmatch/goldenmatch/core/` (blocker, ann_blocker, scorer, pipeline, embedder), `packages/python/goldenmatch/goldenmatch/embeddings/`, `packages/python/goldenmatch/goldenmatch/db/ann_index.py`, `packages/rust/extensions/{score-core,native,goldenembed}`. New surface lands behind a `backend="gpu"` block-scorer; no existing path changes. The GPU backend stops at edge generation -- `core/cluster.py` (clustering / `ClusterFrames`) and `core/golden.py` (field promotion) are consumed unchanged via the Arrow/`__row_id__` handoff.
**Related:**
  - `core/blocker.py`, `core/ann_blocker.py` -- the blocking/recall stages GoldenDB generalizes
  - `embeddings/inhouse/` + `rust/extensions/native/src/featurize.rs` -- the CharNGram featurizer reused as a block encoder
  - Factorized-DB / FAQ literature (Olteanu, Ngo; LMFAO) -- theoretical foundation for joins-as-semiring-matrix-products

## Why this document exists

Entity resolution in goldenmatch is fast but CPU-bound: 25M rows in ~6.5 min on the
vectorized `backend="bucket"` path. The dominant cost is *irregular* work -- string
similarities, sparse blocking, Union-Find -- not byte layout. This document records
a design that converts the expensive core into dense linear algebra so it runs on a
GPU/JAX backend, **without giving up the field-level audit trail** that KYC-grade ER
requires.

The reframing that makes this tractable:

> **Entity resolution is a fuzzy self-join, and a fuzzy join is a matrix multiply.**
> Relational engines assume *equality* joins (hash/B-tree) and bolt similarity on
> (`pg_trgm`, `pgvector`). An engine whose primitive join operator is *approximate*
> (`M @ M^T` + top-k) is a different algebra at the core.

The naive version of this loses explainability (matrix scores are opaque). The
design below recovers it *structurally* rather than post-hoc, so the audit is exact.

## Decisions that shape this design

1. **GoldenDB is the matrix/JAX compute engine ("warp drive"); the traditional
   Arrow/Rust DB is its durable source-of-truth + orchestration plane.** Two planes,
   one cheap one-directional feed (the source DB is the only writer). Verbatim fields
   live in the authoritative Arrow/Polars (Lance-on-disk) store keyed by
   `__row_id__`; GoldenDB holds the schema-block matrix + ANN index and OUTPUTS
   `(id_a, id_b, score)` edges -> `(id, cluster_id)` assignments, never field values
   (lossiness -> field promotion stays CPU-side in `core/golden.py`). **"Derived"
   describes GoldenDB's STATE (rebuildable from the source DB), not its importance:
   it is the engine -- a new matrix-native, JAX-driven, gradient-based probabilistic
   ER DB -- not a subordinate index.** Because ingest already runs through the
   existing Rust/Arrow featurization layer, feeding and sync'ing GoldenDB on
   insert/delete is a cheap shared-ingest feed, not a heavy CDC tax (see Incremental
   sync).

2. **GoldenDB produces candidates AND the score** (not candidates-only). The matmul
   output is the official match score. This creates an attribution debt, paid by
   decision 3.

3. **One schema-block matrix per record; the score is computed block-wise.** Each
   record encodes to a fixed partition of subspaces (one block per field/field-group).
   The master matrix is 2-D `[N, sum(block_widths)]` with a known partition map. The
   score is a weighted combine of *per-block* similarities -- never a single dot
   product over the concatenated vector (that re-entangles the blocks and destroys
   attribution). The per-block similarities ARE the audit.

4. **The combine model is a GA2M / Explainable Boosting Machine**, not a linear sum
   and not a black-box MLP. This is a no-compromise choice justified below.

## Scope and the id-column / Arrow-handoff contract (LOCKED -- two-plane: Arrow/Rust source-of-truth + GoldenDB warp drive)

A hard constraint bounds the scope choice: **embeddings are lossy; the audit
requires verbatim fields; therefore a non-matrix authoritative store of the raw
fields is mandatory, which makes the matrix necessarily DERIVED.** "Matrix as sole
truth" is information-theoretically impossible for an auditable ER store -- you
cannot reconstruct "John Smith, 123 Main St" from a cosine-optimized vector.

The resolution (Ben's framing): **the GPU never sees or returns field values.** It
consumes only an **identifier column + per-block embeddings**, does the clustering,
and hands an `(id, cluster_id)` table (plus per-block audit edges) back **over
Arrow** to the CPU, where **field promotion / survivorship** runs against the
authoritative lossless columns. The id column is the join key that reattaches
verbatim fields. This honors the lossiness constraint structurally -- the matrix is
asked to *group ids*, never to *store fields* -- and it lands on machinery
goldenmatch already has:

- **Identifier column** = `__row_id__` (int64), already canonical across every stage
  (`core/pipeline.py`).
- **Clustering handoff table** = `ClusterFrames.assignments`, a columnar
  `(cluster_id, member_id)` Polars frame (`core/cluster.py:cluster_dict_to_frames`,
  `ClusterFrames` @ ~1767). This IS the `(id, cluster_id)` table.
- **Arrow seam is free** -- the pipeline is Polars, and Polars is Arrow-backed
  (`pl.from_arrow`, zero-copy). The GPU->CPU handoff is a Polars/Arrow frame, not a
  serialization step.
- **Field promotion / survivorship** = `core/golden.py` (`merge_field`,
  `build_golden_records_batch`, strategies `most_complete` / `majority_vote` /
  `source_priority` / `most_recent`, `FieldProvenance`) -- **unchanged**. This is an
  exact columnar group-by over lossless values, correctly a CPU job, not linalg.

**Net build boundary:** the GPU backend replaces *blocking + scoring + edge
generation* only, emitting `(id_a, id_b, score)` + per-block sims. Everything from
`build_clusters` onward -- clustering, `ClusterFrames`, `core/golden.py` field
promotion, provenance -- is the existing CPU Polars/Arrow path, reused as-is.

This sits deliberately between the original scope tiers: **two planes** -- a durable
Arrow/Rust **source-of-truth + orchestration** DB and the GoldenDB **JAX matrix
compute engine** ("warp drive") fed from it. It has Tier-A *topology* (two
components, a feed) but NOT Tier-A's tax: because ingest already flows through the
existing Rust/Arrow layer, the feed is a cheap one-directional sync, not the heavy
bidirectional CDC reconciliation that made a plain relational-sidecar unattractive.
And GoldenDB is the headline engine (a new matrix-native, JAX-driven, gradient-based
probabilistic ER DB), not the demoted "fuzzy-join accelerator" -- "derived" refers
only to its rebuildable state. The further north star remains **Tier C**
(generalize ALL relational operators to semiring-matrix ops per factorized-DB/FAQ so
exact+fuzzy share one algebra -- the "not Postgres" endgame, but a multi-year DBMS
build competing with RAPIDS/HeavyDB; named as destination, not first build).

## Architecture

### Two-plane dataflow: source DB feeds the GPU engine; CPU promotes fields (Arrow seam)

```
  ARROW/RUST SOURCE-OF-TRUTH DB (Lance on disk)  -- lossless, auditable, owns CRUD
        (cheap one-directional feed: insert=append+ANN, delete=tombstone)
  +-----------------------------------------------+
  | __row_id__ | fields... | __source__ | dates   |
  +------------------+----------------------------+
        |  encode (id + per-block embeddings only; fields stay home)
        v
  GPU MATRIX/ANN VIEW (derived, rebuildable)
     Stage A  RECALL : coarse concat vector -> GPU-ANN top-k  --> shortlist of pairs
     Stage B  SCORE  : block-partitioned cosine matmul on shortlist
                       -> per-block sim vector [n_blocks] per pair
                       -> GA2M combine -> score + exact per-term attribution
        |  Arrow (zero-copy): (id_a, id_b, score) + per-block sims    -- NO fields
        v
  CPU CLUSTER + PROMOTE (existing path, unchanged)
     build_clusters / ClusterFrames   -> (cluster_id, member_id)   [core/cluster.py]
     core/golden.py field promotion    -> golden records + FieldProvenance
        (join cluster_id back onto authoritative columns via __row_id__)
```

The N^2 cost lives only in Stage A and is bounded by ANN (brute-force GPU is fine
to ~1e5-1e6; GPU-ANN beyond). Stage B runs only on the shortlist and sees a tiny
`[n_blocks]` input per pair -- this is why the combine model can be expressive AND
interpretable at once (see GA2M rationale). The GPU output crossing the Arrow seam
is just integer id columns + scores; verbatim fields never leave the column store,
so survivorship operates on lossless values and the audit trail forms CPU-side.

### Block encoding (the "down" translator: schema -> matrix)

Each field/field-group is a block with a type-appropriate encoder and a
type-appropriate similarity kernel. **Hybrid kernels: do not cosine a date.**

| Block (example) | Encoder | Kernel | Width | sim_i meaning |
|---|---|---|---|---|
| first+last name | char-ngram embed (CharNGram featurizer) | cosine (matmul) | ~64 | fuzzy name agreement |
| address (street+city) | token embed | cosine (matmul) | ~64 | address agreement |
| email | token/structural embed | cosine (matmul) | ~32 | handle+domain agreement |
| dob / dates | structured numeric (NOT embedded) | exact / off-by-digit | 1-3 | date agreement |
| numeric / categorical | normalized scalar / one-hot | exact / numeric-gap | 1-k | direct agreement |
| value frequency (per high-card block) | corpus count -> IDF feature | feeds GA2M | 1 | rarity signal (u-probability) |

High-cardinality string blocks are the GPU matmul hot path; cheap blocks are
elementwise GPU ops. This decomposes the hard "embed a heterogeneous record"
problem into per-field encoders -- the same feature engineering classical ER
already does, landed in continuous subspaces.

### Score and audit (the "up" translator: matrix -> field-level explanation)

Per shortlisted pair, Stage B yields a similarity vector `sim = [sim_1 .. sim_n]`.
The combine model is a Generalized Additive Model with pairwise interactions (GA2M,
a la Microsoft's Explainable Boosting Machine):

```
  score = sigma( SUM_i f_i(sim_i)  +  SUM_{i<j} f_ij(sim_i, sim_j)  -  tau )
```

- Each `f_i` is a readable 1-D shape function (curve); each `f_ij` a readable 2-D
  surface. The audit is **exact** -- it is the model's own additive decomposition,
  not a SHAP approximation.
- **Interactions** captured: `f_ij` expresses "name match only counts when address
  agrees."
- **Frequency / u-probabilities** captured: a frequency feature per high-card block
  makes `f(name_sim, name_freq)` the learned u-probability surface ("rare-name match
  strong, common-name match weak").
- **Monotonicity can be hard-constrained** (more agreement never lowers the score) --
  a guarantee a free MLP cannot give; matters for audit/legal defensibility.

This is a continuous, learnable, GPU-native **generalization of Fellegi-Sunter**:
classic F-S match weight is `SUM_i log(m_i/u_i)` -- a GAM with interactions off and
binary agreement. GoldenDB turns the interaction terms on and makes agreement a
continuous per-block cosine.

### Why GA2M is no-compromise here (and where the cost actually is)

The linear-vs-GAM-vs-MLP trilemma (interpretability vs interactions vs accuracy) is
*false in this position*: the combiner is not the scaling bottleneck. The matmul +
ANN absorb the N^2 and the high-dim embeddings; the combiner consumes only
`n_blocks` scalars per *shortlisted* pair. Low-dimensional, low-volume tabular input
is exactly the regime where an interpretable model matches the black box (Rudin),
so no accuracy is traded for interpretability.

Residual cost (no free lunch -- it moved off accuracy/interpretability):
- Exactness holds only up to the interaction order materialized. Pairwise is a
  readable surface; 3-way+ grows combinatorially and stops being eyeball-able.
  Pairwise + frequency likely captures ~all real ER signal -- but it is a bounded
  claim, not "all interactions."
- More JAX/GPU machinery: shape functions as monotone splines/lattices
  (TF-Lattice-style differentiable lattice net) + interaction-term selection (EBM
  automates via boosted FAST detection; a from-scratch JAX version makes it a step)
  vs. a one-layer `sigma(w . sim)`.

### Training (why JAX, not just cupy)

The whole scorer is differentiable, so field weights / shape functions / threshold
are learned by gradient descent on a labeled set, replacing both F-S m/u estimation
and goldenmatch's controller sample-and-guess budget. Recall (Stage A) and score
(Stage B) ideally train jointly; absent that, Stage A sets a recall ceiling that
caps end-to-end accuracy -- this is the primary open risk (see Verification).

### Incremental sync (source-of-truth DB -> GoldenDB warp drive)

The Arrow/Rust DB owns durability and CRUD; GoldenDB is fed from it. Because ingest
already flows through the existing Rust/Arrow featurization layer, maintaining parity
is a cheap one-directional feed (single writer), not a bidirectional CDC
reconciliation. Costs are recall/latency only -- never correctness, since the source
DB re-validates and ER tolerates a lagging feed:

- **Insert (trivial):** encode the new row's blocks -> append a matrix row + insert
  one ANN vector. `__row_id__` is the shared key.
- **Delete (trivial):** tombstone/mask the row id -> excluded from the next matmul;
  physical compaction on a schedule (Lance).
- **Update (real edge #1):** a field change = delete+reinsert in embedding space, so
  the ANN index must support deletion (FreshVamana/DiskANN; FAISS `IndexFlatIP` is
  append-only and recall-degrades without a rebuild).
- **Encoder/version change (real edge #2):** a new featurizer/field re-encodes the
  affected blocks -- a rebuild of the derived view, flagged by an embedding-version
  column + a backfill pass, not a correctness event.
- **Human must-not-link decisions** persist in the source DB and apply as a mask on
  the next matmul -- corrections are data, constraints apply matricially.
- **Stable logical ids, not row offsets** (offsets break on delete/compaction).
  `__row_id__` is already preserved across stages; Lance gives stable addressing +
  versioning + compaction on disk.

## Reuse map (what already exists in goldenmatch)

GoldenDB is one new `backend="gpu"` block-scorer, not a rewrite. Existing pieces:

| Need | Existing component |
|---|---|
| Backend dispatch (plug point) | `core/pipeline.py:_get_block_scorer()` |
| Coarse recall / ANN (Stage A) | `core/ann_blocker.py` (FAISS `IndexFlatIP`), `db/ann_index.py` |
| Identifier column (the join key) | `__row_id__` (int64), preserved across all stages in `core/pipeline.py` |
| Cluster assignment + Arrow handoff table | `core/cluster.py` (`build_clusters`, `ClusterFrames` `(cluster_id, member_id)`, `cluster_dict_to_frames`) |
| Field promotion / survivorship (CPU) | `core/golden.py` (`merge_field`, `build_golden_records_batch`, strategies, `FieldProvenance`) -- consumed unchanged |
| Learned/multi-pass blocking patterns | `core/blocker.py`, `core/learned_blocking.py`, `core/block_analyzer.py` |
| Block encoder (name) | `embeddings/inhouse/{model,featurizer}.py` + `rust/extensions/native/src/featurize.rs` (`qgram_features` CharNGram) |
| Embedder routing (Vertex / in-house / ST) | `core/embedder.py`, `embeddings/providers.py` |
| In-house ONNX embedding (no cloud creds) | `rust/extensions/goldenembed/src/`, `embed-py/` |
| Canonical scalar scorers (cheap-block kernels) | `rust/extensions/score-core/src/lib.rs` (`jaro_winkler`, `levenshtein`, `token_sort`, exact; `score_one`) |
| Symbolic re-score fallback / audit on shortlist | `negative_evidence` + weighted matchkeys (`core/scorer.py`) |
| Config surface (blocks, kernels, weights) | `config/schemas.py` (`GoldenMatchConfig`, `MatchkeyConfig`, `BlockingConfig`) |

## Verification

Because there is no GPU in this environment, verification is defined for the future
spike, ordered by what most cheaply kills the idea:

1. **Recall ceiling (kills it fastest).** On a standard record-linkage benchmark
   (e.g. North Carolina voter / DBLP-ACM / a goldenmatch fixture), measure
   **recall@k** of Stage A: does the ANN shortlist contain the true pairs the full
   scorer would accept? If recall@k is low, nothing downstream matters. Gate:
   recall@k within a small delta of `backend="bucket"` candidate recall.
2. **Score parity.** GA2M score vs. the existing symbolic scorer on the same pairs:
   precision/recall/F1 within tolerance, and the per-block attribution must be
   human-legible on spot-checked pairs.
3. **Wall-clock crossover.** Per CLAUDE.md's perf-audit lesson, measure 5-run median
   wall on real shapes; find the scale where `backend="gpu"` beats `backend="bucket"`
   end-to-end (encode + recall + score), not just the matmul in isolation.
4. **Audit exactness.** Confirm `score == SUM f_i + SUM f_ij` (model decomposition)
   so the displayed attribution is the score, not an approximation.
5. **Monotonicity guarantee.** Adversarially perturb one block's sim upward; assert
   score never decreases.

## What this design explicitly does NOT do

- Not a from-scratch database (no storage engine, planner, txns, durability,
  recovery) -- that is Tier C, the north star, not this build. The Arrow/Polars
  (Lance) column store holds the authoritative fields; the matrix/ANN is a
  rebuildable derived view that the GPU uses to group ids only.
- Not field-storing on the GPU: the matrix never holds or returns verbatim values;
  it emits `(id, cluster_id)` + scores, and field promotion stays CPU-side in
  `core/golden.py`.
- Not a flat-concat embedding scorer (forbidden -- destroys attribution).
- Not a single universal record embedding (blocks stay per-field).
- No higher-than-pairwise interaction terms by default.

## Implementation status (EXPERIMENTAL / work in progress)

An experimental `backend="gpu"` scorer has landed at
`packages/python/goldenmatch/goldenmatch/core/goldendb/` (`_encode.py`,
`_combine.py`, `scorer.py`), wired into `core/pipeline.py:_get_block_scorer()` and
installable via `pip install goldenmatch[goldendb]`. It is loudly marked
work-in-progress (module banner + a one-time runtime warning + CHANGELOG note) and
is NOT production-validated.

Landed (CPU-JAX validated in `tests/test_goldendb_gpu_backend.py`, 22 tests):
- char-ngram hashed per-field encoding -> L2-normalised matrices
- per-field cosine via a JAX `matmul` (the GPU path; runs on CPU here)
- GA2M weighted-average combine with EXACT additive attribution + monotonicity
- a closed gradient-based training loop (`training.py`): `fit_field_weights` learns
  per-field weights from labeled pairs by `jax.grad` BCE descent, and
  `apply_field_weights` writes them back onto the matchkey so the normal
  weighted-average scorer consumes the trained weights (replaces F-S m/u estimation
  / the controller's weight search). Verified end-to-end: training upweights the
  label-predictive field and widens the match/non-match score gap.
- **Stage A recall**: coarse-vector brute-force top-k shortlist
  (`recall.py`) + a vectorised Stage B that scores only the shortlist. Two entry
  points: (a) auto-engaged inside large blocks (`n > ANN_THRESHOLD`) so the dense
  N^2 path is avoided, and (b) `resolve_dataset_gpu(df, mk)` -- **blocker-free**
  resolution over a whole dataset, finding duplicates a blocking key would separate
  (Catherine/Katherine). With `k >= n-1` the recall path equals the dense path
  (parity-tested).
- block-scorer-contract output `(id_a, id_b, score)` feeding the unchanged
  `core/cluster.py` -> `core/golden.py` path via `__row_id__`
- **end-to-end**: `run_dedupe_df(df, cfg)` with `backend="gpu"` runs the full
  pipeline (block -> GPU score -> cluster -> golden) and clusters duplicates
  correctly (`tests/test_goldendb_gpu_backend.py::test_full_pipeline_dedupe_with_gpu_backend`)

NOT yet wired (future work):
- a true GPU-ANN index (FAISS / DiskANN) -- recall is brute-force top-k today
  (good to ~1e5-1e6 vectors per the design doc); larger datasets and streaming
  inserts need the indexed path
- trained shape functions / pairwise interaction terms (structure is linear today)
- negative-evidence penalties (ignored with a warning if configured)
- GPU wall-clock crossover measurement (the recall@k gate above remains the first
  thing to run on real GPU hardware)
- scores are char-ngram cosine + an untrained combine, NOT calibrated against the
  production scorers

## Future direction

A spike would add `backend="gpu"` at `core/pipeline.py:_get_block_scorer()`:
schema-block featurize -> coarse ANN recall -> block-partitioned cosine matmul ->
GA2M combine -> existing cluster/golden stages; benchmark against `backend="bucket"`
for the crossover scale and run the recall@k gate first. Storage substrate: Lance
(stable locators + versioning + compaction); streaming ANN via FreshVamana/DiskANN
for high-mutation inserts.

## References

- `packages/python/goldenmatch/goldenmatch/core/pipeline.py` -- `_get_block_scorer()` plug point
- `packages/python/goldenmatch/goldenmatch/core/ann_blocker.py`, `db/ann_index.py` -- Stage A recall
- `packages/python/goldenmatch/goldenmatch/core/blocker.py`, `core/learned_blocking.py`, `core/block_analyzer.py`
- `packages/python/goldenmatch/goldenmatch/core/scorer.py`, `core/embedder.py`, `embeddings/` -- encoders + symbolic scorer
- `packages/rust/extensions/score-core/src/lib.rs`, `native/src/{score,featurize}.rs`, `goldenembed/src/` -- kernels + featurizer
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` -- config surface
- Factorized databases / FAQ (Olteanu, Ngo; LMFAO) -- joins+aggregations as semiring matrix products
- Lou, Caruana et al., "Accurate Intelligible Models with Pairwise Interactions" (GA2M); Microsoft InterpretML EBM
- Rudin, "Stop Explaining Black Box Models for High-Stakes Decisions" -- interpretable == accurate in low dimensions
