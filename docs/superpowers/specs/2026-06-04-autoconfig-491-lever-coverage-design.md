# Auto-config lever coverage: probabilistic + qgram + ANN (#491) -- design

Date: 2026-06-04
Issue: benseverndev-oss/goldenmatch#491
Status: design (approved in brainstorming, pending spec review)

## Problem

The iterative `auto_configure_df` controller reaches only a subset of the repo's
levers. #491's genuine remaining gaps (after the audit: levenshtein/soundex_match
already reachable via the optimizer + the `phonetic_identity` heuristic; dice/jaccard
are PPRL-only and struck):

1. **Probabilistic matchkey type** -- reachable today ONLY via the standalone
   `auto_configure_probabilistic_df` entry point. The iterative controller
   (`dedupe_df(df)` zero-config) and the deterministic ConfigOptimizer proposer
   never *select* it. (The optimizer's `LLMProposer` already has a
   `matchkey_type -> probabilistic` op at `config_optimizer.py:301`, but the
   deterministic `CoordinateDescentProposer` does not, and the LLM path is opt-in.)
2. **qgram scorer** -- not in `_SCORER_MAP`/`build_matchkeys` and not in the
   optimizer's scorer family (`config_optimizer.py:334` lists token_sort, ensemble,
   levenshtein, soundex_match -- no qgram). Unreachable.
3. **ANN blocking** -- `ANNBlocker` is wired in `blocker.py` but `build_blocking`
   never emits `strategy="ann"` (it emits multi_pass / canopy / learned / static /
   adaptive). Unreachable.

## Design

Three independent levers. Scope = all three. Probabilistic gets BOTH homes
(controller heuristic + optimizer candidate); qgram gets heuristic + optimizer;
ANN is a heuristic gate in `build_blocking`.

### Lever A -- probabilistic as a selectable type (both homes)

**A1. Optimizer (deterministic):** wire the EXISTING `MatchkeyTypeSwap` edit
(`config_edits.py:167`, `weighted <-> probabilistic`, apply-revalidate-ready, with
`_PERTURBABLE_TYPES = ("weighted","probabilistic")`) into `CoordinateDescentProposer`
(`config_optimizer.py:307`) as a new `mktype` family in `_FAMILIES`/`_edits`. It is
currently deserialized by the LLM JSON path but NOT imported into the optimizer or
emitted by the deterministic proposer. So A1 is "import + add a family that emits
`MatchkeyTypeSwap` for each weighted matchkey," NOT a new edit class. The search
scores weighted-vs-probabilistic by the existing zero-label confidence / F1
objective and keeps the winner.

**A2. Controller heuristic:** new `rule_select_probabilistic_matchkey` in
`autoconfig_rules.py`, appended to `DEFAULT_RULES`. **Conservative trigger** (all
must hold): the committed config has a weighted matchkey with `>= 3` graded fuzzy
fields, the scoring sub-profile is recall-limited (unimodal score histogram / low
`mass_above_threshold`), and there is no strong exact-anchor matchkey. This is the
shape where Fellegi-Sunter per-field m/u weighting beats a flat weighted average.
The rule proposes converting that weighted matchkey to `type="probabilistic"`.
m/u are EM-trained at dedupe time (existing `core/probabilistic.py`); blocking is
already required for probabilistic (`autoconfig.py:2341`), so no blocking change.

**Ship-or-defer:** A2 is best-effort. If the pre-merge quality gate shows ANY
regression on NCVR / Febrl3 / DQbench T1-T3, **drop A2** and ship probabilistic
optimizer-only (A1) -- that still satisfies #491's "reachable from the auto
surface" acceptance. A1 carries no default-path risk (opt-in search).

### Lever B -- qgram scorer

**CORRECTION (plan review):** `qgram` is NOT currently a scorer. `VALID_SCORERS`
(`goldenmatch/config/schemas.py`) has no qgram; the only `qgram` is the `qgram:N`
*transform* (`utils/transforms.py`), which is **lossy/blocking-only** (it truncates
to the first 5 n-grams: `" ".join(grams[:5])`). And `dice`/`jaccard` operate on
hex-encoded **bloom filters** (PPRL-only), so they can't be repurposed. Real
q-gram *similarity* therefore needs a NEW scorer.

**B0. Implement the qgram scorer (prerequisite):** add a genuine character-n-gram
set-similarity scorer (q-gram Jaccard on raw strings: pad, generate the full set of
n-grams for each string, similarity = |A∩B| / |A∪B|) to `VALID_SCORERS` and to BOTH
dispatch paths in `core/scorer.py` (the single `_qgram_score_single(a, b)` ~line 89
and the NxN `_qgram_score_matrix(values)` ~line 419). Default n configurable (e.g.
`qgram` = trigram; allow `qgram:N` scorer-arg parsing if the scorer dispatch
supports args, else fixed n=3). Pure-Python set similarity, no bloom semantics.

**B1. Heuristic:** add a "short-code" column-shape classifier and route it to
the new `qgram` scorer in `build_matchkeys`. Short-code shape: `avg_len` small (~3-12),
cardinality_ratio moderate-to-high, alphanumeric, and NOT already classified
name/email/phone/zip/date/identifier-surrogate. (SKUs, license plates, postal
codes, short product codes.) qgram (character n-gram overlap) beats token_sort
(token-order-based) and levenshtein on short codes with internal transpositions.
Implement as a refinement in the scorer-selection path, not a blanket `_SCORER_MAP`
entry (col_type stays the existing classification; the short-code refinement picks
qgram for the matchkey field).

**B2. Optimizer:** add `qgram` to the `CoordinateDescentProposer` scorer family
(`config_optimizer.py:334`) so the empirical search can pick it on data-dependent
shapes.

### Lever C -- ANN blocking (heuristic gate)

**`build_blocking`:** a new selection branch emitting `strategy="ann"` and setting
the FLAT `BlockingConfig` fields (`schemas.py:362`) -- `ann_column`, `ann_model`,
`ann_top_k` (there is NO `ANNConfig` class / nested block; `strategy="ann"` is
already a valid `Literal`, and `autoconfig.py:2349` already lists `ann` as a no-op
for the adaptive-promotion swap). Gated STRICTLY on BOTH:
- (a) **embeddings present**: the resolved matchkeys contain an `embedding` /
  `record_embedding` scorer field, OR an embedding-typed/description column that
  would carry vectors. ANN needs vectors -- it must NEVER fire without them.
- (b) **scale**: `n_rows_full >= ANN_MIN_ROWS` (the FAISS index build only pays
  off at scale; below it, exact/multi_pass blocking is cheaper). Default threshold
  TBD in the plan (start ~100K, env-overridable).

Reuse the embedding-bootstrap path already used to wire embedding scorers. When the
gate fails (no embeddings or below scale), behavior is unchanged.

## Testing and validation

- **Unit B (qgram):** short-code fixture -> `build_matchkeys` emits a qgram
  matchkey field; a name/email/date column does NOT get qgram (precision).
- **Unit A1 / B2 (optimizer):** `CoordinateDescentProposer.propose` yields a
  probabilistic-type candidate for a weighted matchkey, and a qgram candidate in
  the scorer family. (Assert the candidate set contains them; don't need the full
  search.)
- **Unit A2 (controller rule):** `rule_select_probabilistic_matchkey` fires on the
  target shape (>=3 graded fuzzy fields + recall-limited + no exact anchor) and
  does NOT fire on a DQbench-like shape (has an exact_email anchor) or a simple
  2-field weighted shape.
- **Unit C (ANN):** `build_blocking` emits `strategy="ann"` when embeddings present
  AND `n_rows_full >= threshold`; emits the normal strategy when no embeddings
  (most important: never `ann` without vectors) and when below scale.
- **Reachability tests (the #491 acceptance):** assert each lever is reachable from
  the auto surface (qgram via build_matchkeys; probabilistic via the controller
  rule output AND the optimizer candidate set; ann via build_blocking).
- **Quality gate (HARD pre-merge, the central risk):** A2 (and to a lesser extent
  B1) change default-path selection. Run the #528 in-house quality gate +
  DQbench T1/T2/T3 + NCVR + Febrl3. **Any regression on those drops A2** (ship
  A1-only) and re-examines B1's short-code precision. ANN and the optimizer
  additions don't touch the default zero-config path, so their regression risk is
  bounded to their own opt-in/gated surfaces.

## Risks

- **A2 over-fires -> benchmark regression** (primary). Mitigation: conservative
  multi-condition trigger + hard quality gate + ship-or-defer (A1 is the safety
  net for the "reachable" acceptance).
- **C fires without embeddings -> ANNBlocker crash on missing vectors.**
  Mitigation: strict embeddings-present gate + a test asserting `ann` is never
  emitted without an embedding scorer/column.
- **B1 qgram on the wrong columns -> noise.** Mitigation: precise short-code shape
  detector; the optimizer (B2) can always override empirically.

## Out of scope

- dice/jaccard scorers (PPRL/bloom-CLK path, intentionally separate -- struck per
  the audit).
- levenshtein/soundex_match (already reachable -- audit confirmed).
- `build_probabilistic_matchkeys` column admission for identifiers (that is #721 --
  WHICH columns probabilistic uses, distinct from whether the controller SELECTS
  probabilistic). This spec may interact with #721 but does not subsume it.
- Distributed/Ray paths.
