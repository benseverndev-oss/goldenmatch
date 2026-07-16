# 0040 -- Embeddings as first-class Fellegi-Sunter scorers (vectorized-only, EM E-step included)

**Status:** Accepted. **Shipped:** goldenmatch main, unreleased (PR #1806; FS-audit batch with #1800 / #1801)

## Context

`embedding` and `record_embedding` did not work on the probabilistic
(Fellegi-Sunter) path at all -- they did not silently degrade, they **crashed**.
Both EM training's E-step (`train_em` -> `_build_comparison_matrix` ->
`comparison_vector` -> `score_field`) and the scalar block scorer route through
`score_field`, which has no embedding branch and raises `ValueError: Unknown
scorer`. The vectorized scorer *could* build embedding similarity matrices
(`_fuzzy_score_matrix` embedding branch; `_record_embedding_score_matrix`) but
`vectorized_scorer_supported` hard-excluded the model-backed scorers, forcing
the crashing scalar path. Separately, the TUI (`tui/engine.py`) called
`score_probabilistic` directly, bypassing the native/vectorized router, so it
ran every FS config on the scalar path.

The FS audit framed this as "embedding silently forces scalar (quality
divergence)"; verification overturned that -- it is a crash / unsupported
config, so the fix is to make embeddings work end-to-end, not to add a scalar
embedding path (embeddings are matrix-only by nature; `score_field` stays
untouched).

## Decision

**Embeddings are first-class on FS through the vectorized matrix in both
directions (train and score); scalar is never their path.** Four pieces:

1. **EM E-step vectorization.** `comparison_vector` gains an optional
   `field_sims` param; `_build_comparison_matrix` precomputes embedding cosine
   similarity for the sampled rows once (`_embedding_pair_sims`, O(rows) model
   calls, not O(pairs)) and feeds it through the SAME comparison-level
   thresholds as every scorer, so training and scoring assign levels
   identically (pinned by a train<->score level-parity test). `_record_concat_value`
   mirrors `_record_embedding_score_matrix`'s concatenation byte-for-byte.
2. **Un-gate scoring.** `vectorized_scorer_supported` returns True for every
   regular-field scorer; `score_probabilistic_vectorized` gains a
   `record_embedding` branch (record-level cosine matrix, no single-field
   null-mask, no TF). Negative-evidence routing is UNCHANGED: a model-backed
   NE scorer still forces scalar (`_ne_scorer_vectorizable`) -- the NE matrix
   path has no `record_embedding` branch, and expanding it is a deferred
   follow-up.
3. **Kill-switch safety.** `probabilistic_block_scorer` forces the vectorized
   path when any field scorer is model-backed, regardless of
   `GOLDENMATCH_FS_VECTORIZED=0` -- scalar literally cannot run them.
4. **TUI router.** `tui/engine.py` routes FS through
   `probabilistic_block_scorer` instead of the scalar function, so it gets
   native/vectorized (and embeddings work there too).

## Consequence

An FS matchkey carrying `embedding` / `record_embedding` now trains and scores
end-to-end; the same scorers previously raised `Unknown scorer`. Because they
are matrix-only, `GOLDENMATCH_FS_VECTORIZED=0` only affects string scorers.
Testing uses a deterministic fake embedder (torch-free, OOM-safe) for all
determinism-sensitive coverage; the real sentence-transformers path is an
opt-in e2e (`GOLDENMATCH_RUN_EMBEDDER_TESTS=1`, skipped locally). Open
follow-ups: NE + model-backed scorers (NE matrix path lacks a record_embedding
branch), and a firmer `record_embedding` FS config convention (`field="__record__"`
placeholder + `columns=[...]`).
