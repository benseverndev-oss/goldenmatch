# Sketch-then-verify throughput execution plan (#1083) — design

**Issue:** #1083 (epic #1080, Training-Data Dedup at Scale)
**Date:** 2026-06-19
**Status:** Design approved, pre-implementation

## Problem

The engine today is accuracy-oriented: blocking produces candidate pairs and
every pair is verified by the full per-field fuzzy / Fellegi-Sunter scorer. For
LLM training-corpus / document-scale deduplication the user often wants the
opposite trade: a high-recall, low-cost tier that blocks with LSH/sketch and
*lightly* verifies, accepting some precision loss for throughput — and wants to
be told, honestly, what that trade buys.

#1081 shipped the MinHash/LSH sketch kernel; #1082 shipped the document/text
near-dup blocking path (`lsh` lexical + `simhash` semantic strategies, auto-routed
for detected text corpora). #1083 adds the **execution plan**: the controller can
pick a sketch-then-verify throughput plan via an opt-in recall knob, and reports
its recall/precision posture.

## Done bar (from the issue)

The controller can pick a throughput plan and report its recall/precision posture.

## Scope

**In scope (#1083), single-node:**
- An opt-in throughput knob on `GoldenMatchConfig` (recall target as the primary dial).
- A planner that emits a throughput `ExecutionPlan` (sketch-then-verify posture)
  composed orthogonally with the existing backend rules.
- Forcing LSH/sketch blocking on the text column when throughput is on.
- A light "sketch-distance" verify step that reuses the signatures/embeddings the
  blocker already computed (no per-field fuzzy/FS scoring).
- An honest posture report (LSH-theoretic expected recall + measured reduction),
  surfaced on the result, postflight report, and telemetry.

**Out of scope (deferred to epic siblings):**
- Distributed / billion-scale throughput → #1084. (The planner overlay composes
  with backend rules, but the distributed sketch path is its own issue.)
- Corpus parquet/jsonl adapters and a `corpus-dedupe` product/CLI surface → #1085.
- Throughput benchmark + CI perf gate, and any default-on auto-selection flip that
  gate would justify → #1086.
- Auto-selection of the throughput tier (controller picking it without the user
  asking). #1083 is **opt-in only**; auto-detection is deferred to the product
  surface (#1085).

## Decisions settled in brainstorming

1. **Selection model:** opt-in knob only. The controller wires the throughput plan
   when asked; it never silently trades accuracy.
2. **Verify step:** sketch-distance verify — confirm each candidate pair with the
   cheap signature distance already computed (estimated Jaccard from MinHash sigs
   for lexical; cosine from the embedding vectors for semantic), thresholded. No
   per-field fuzzy/FS scoring.
3. **Knob form:** a recall target (0,1), default ~0.95, as the primary dial. A
   sensible default near-dup similarity is chosen per metric (Jaccard 0.8 / cosine
   0.85); the similarity threshold is an advanced override. The honest report is
   derived from the LSH S-curve.

## Design

### 1. Config + knob threading

New nested model in `config/schemas.py`:

```python
class ThroughputConfig(BaseModel):
    enabled: bool = False
    recall_target: float = 0.95               # validated to (0, 1)
    similarity_threshold: float | None = None # advanced override; default per metric
```

`GoldenMatchConfig.throughput: ThroughputConfig | None = None`. Default `None`
means the engine behaves byte-identically to today.

Resolution mirrors `resolve_planning_effort` exactly (kwarg -> env -> default):

- `resolve_throughput_config(arg, config) -> ThroughputConfig | None`.
- `dedupe_df(..., throughput=...)` accepts `True` (enable with defaults), a `float`
  (interpreted as `recall_target`), or a `ThroughputConfig`.
- Env: `GOLDENMATCH_THROUGHPUT=1`, `GOLDENMATCH_THROUGHPUT_RECALL`,
  `GOLDENMATCH_THROUGHPUT_SIMILARITY`.
- Default near-dup similarity when `similarity_threshold` is None: **Jaccard 0.8**
  for the lexical (`lsh`) metric, **cosine 0.85** for the semantic (`simhash`)
  metric. The metric is implied by the blocking strategy chosen in step 3.

### 2. Planner integration (orthogonal overlay)

Throughput is an orthogonal dimension of the plan (what verify to run), not a
competing backend rule (how to run at scale). So it does not enter the first-match
rule registry; it overlays the plan after the backend rule fires.

`ExecutionPlan` (`core/execution_plan.py`) gains:

```python
verify_mode: Literal["full", "sketch_distance"] = "full"   # "full" == today
sketch_bands: int | None = None
sketch_rows: int | None = None
sketch_similarity: float | None = None
```

`apply_planner_rules` computes the base plan from `DEFAULT_RULES` as today, then,
when throughput is enabled, applies `apply_throughput_overlay`. The overlay is
**metric-aware** — the metric is derived from the blocking strategy resolved in
step 3 (`lsh` -> `jaccard`, `simhash` -> `cosine`) and passed in, because band
selection and the S-curve differ by metric:

```python
def apply_throughput_overlay(plan, cfg, *, metric, signature_len) -> ExecutionPlan:
    # metric in {"jaccard","cosine"}; signature_len = num_perms (lexical) or
    # num_planes (semantic), from the resolved blocking config.
    similarity = cfg.similarity_threshold or DEFAULT_SIMILARITY[metric]
    bands, rows = _optimal_bands_for_metric(metric, signature_len, similarity)
    bands, rows = _nudge_for_recall(metric, signature_len, bands, rows, cfg.recall_target)
    return dataclasses.replace(
        plan,
        verify_mode="sketch_distance",
        sketch_bands=bands, sketch_rows=rows, sketch_similarity=similarity,
    )
```

- **Lexical (jaccard):** `_optimal_bands_for_metric` calls the existing
  `optimal_bands(num_perms, similarity)` (MinHash Jaccard S-curve).
- **Semantic (cosine):** the per-band collision probability is
  `p = 1 - arccos(s)/pi`, not `s`, so the band split is computed against the cosine
  S-curve over `num_planes` bits. `SimHashKeyConfig` already accepts a cosine
  `threshold` and derives `num_bands`; the overlay reuses that derivation rather
  than the MinHash `optimal_bands`.

`recall_target` nudges banding looser (more bands -> the S-curve crosses its 0.5
point at a lower similarity -> more candidate pairs caught -> higher recall, lower
precision). **Invariant:** `_nudge_for_recall` picks only among *divisor* band
counts (`signature_len % bands == 0`, so `bands * rows == signature_len`) — both
`optimal_bands` and `band_hashes` require it, so the nudge is a discrete choice over
valid `(b, r)` splits, never a real-valued adjustment.

Backend / max_workers / clustering_strategy chosen by the scale rules are
preserved (so throughput composes with bucket/polars-direct/chunked single-node,
and later with the distributed backends in #1084).

### 3. Blocking forcing

When `throughput.enabled`, auto-config (`core/autoconfig.py`) routes the longest
text/string column to `lsh` (or `simhash` when an embedder is reachable), reusing
#1082's `_text_corpus_blocking` routing **but bypassing the `_is_text_corpus`
detector gate** — the user explicitly opted into the throughput tier, so we honor
the request rather than re-deciding whether the data "looks like" a corpus.

Column selection: the description/string column with the largest average length
(same rule `_text_corpus_blocking` already uses). If there is no text/string
column to sketch on, raise `ThroughputNotApplicableError` (see Error handling).

### 4. Sketch-distance verify module

New isolated module `core/throughput_verify.py`, single responsibility — confirm
candidate pairs by sketch distance, nothing else:

```python
def score_sketch_pairs(
    candidate_pairs,             # canonical (min, max) pairs from the blocker
    *,
    metric: Literal["jaccard", "cosine"],
    threshold: float,
    signatures=None,             # MinHash sigs (lexical) — reused from the blocker
    embeddings=None,             # vectors (semantic) — reused from the blocker
) -> list[tuple[int, int, float]]:   # (id_a, id_b, score) — cluster-stage contract
```

- **Lexical (jaccard):** `estimate_jaccard(sig_a, sig_b)` (existing `sketch.py`) per
  pair, keep `>= threshold`. Vectorized over the candidate array via numpy, not a
  per-pair Python loop.
- **Semantic (cosine):** cosine over the embedding vectors per pair, keep
  `>= threshold`.
- **Reuse, don't recompute:** the `lsh`/`simhash` blocker already computes
  signatures/embeddings for banding, but today neither `MinHashLSHBlocker` nor
  `SimHashLSHBlocker` *retains or returns* them — so exposing them is **new work in
  this issue** (add a retention/return path: the MinHash signatures from the lexical
  blocker, the embedding array from the semantic blocker). Verify consumes those, so
  the throughput pass adds approximately no new heavy compute. Fallback: if a blocker
  cannot supply them, verify recomputes from the column — correct but slower.

Pipeline dispatch (`core/pipeline.py`): where the block scorer is selected today,
add `if execution_plan.verify_mode == "sketch_distance": scored =
score_sketch_pairs(...)` instead of `find_fuzzy_matches` / `score_blocks_*`. The
clustering (union-find) and golden stages are unchanged — sketch verify emits the
same `(id_a, id_b, score)` contract. One new module + one dispatch branch; no
changes to clustering or golden.

### 5. Honest posture report

`ThroughputPosture` (frozen dataclass, e.g. in `core/throughput_verify.py`):

```python
@dataclass(frozen=True)
class ThroughputPosture:
    recall_target: float
    similarity_threshold: float
    metric: str                  # "jaccard" | "cosine"
    bands: int
    rows_per_band: int
    expected_recall: float        # metric-specific LSH S-curve at s = similarity_threshold
    reduction_ratio: float        # candidate_pairs / (N*(N-1)/2)
    candidate_pairs: int
    verified_pairs: int
    notes: str
```

- `expected_recall` is the analytic LSH S-curve probability that a pair at the
  configured similarity shares at least one band — a ground-truth-free estimate,
  which is precisely why it can be reported without labels. The formula is
  metric-specific: `1-(1-s**r)**b` for jaccard; `1-(1-p**r)**b` with
  `p = 1 - arccos(s)/pi` for cosine.
- `reduction_ratio` is measured post-blocking (candidate pairs vs N^2/2) — the
  cost/precision proxy.
- We deliberately do **not** claim a measured precision (the controller has no
  ground truth). `notes` states this plainly, e.g.: *"expected_recall is an
  LSH-theoretic estimate over pairs at/above the configured similarity, not a
  measured F1; precision is traded for throughput and is not directly measured
  here."* This honesty is the "honest reporting of the tradeoff" the issue asks
  for.
- A `notes` warning is added when `reduction_ratio` is above a sane ceiling
  (degenerate banding — everything colliding).

Surfaced via the single existing serializer path so every surface gets it without
per-surface UI work:

- `DedupeResult.throughput_posture` (None when throughput off).
- `PostflightReport.__str__` renders a throughput line.
- `web/controller_telemetry.py::serialize_telemetry` gains a `throughput` block
  (-> CLI panel, web tab, MCP, A2A delegate to this one serializer per the
  controller-telemetry single-source contract).

### 6. End-to-end data flow

`dedupe_df(df, throughput=0.95)`:

1. `resolve_throughput_config` -> `ThroughputConfig(enabled=True, recall_target=0.95)`.
2. `auto_configure_df` forces `lsh`/`simhash` blocking on the longest text column
   (embedder-aware), bypassing the corpus-detector gate.
3. Controller planner emits the base `ExecutionPlan`, then the throughput overlay
   sets `verify_mode="sketch_distance"` + `(bands, rows, similarity)`.
4. Pipeline blocks with the lsh/simhash blocker -> candidate pairs + the
   signatures/embeddings it already computed.
5. Pipeline sees `verify_mode="sketch_distance"` -> `score_sketch_pairs(metric,
   threshold)` -> scored pairs.
6. Cluster (union-find) + golden — unchanged.
7. `ThroughputPosture` computed -> attached to result + postflight + telemetry.

## Error handling & edge cases

- **No text/string column to sketch on** -> raise `ThroughputNotApplicableError`
  (explicit refuse, no silent fall-back to the accuracy tier — matches the
  `ControllerNotConfidentError` "refuse, don't silently degrade" ethos).
- **Semantic requested but no embedder reachable** -> fall back to lexical `lsh`
  and record it in `posture.notes`. This is a capability fallback *within* the
  throughput tier (mirrors #1082's `_text_corpus_blocking`), not a silent drop to
  the accuracy tier.
- **`recall_target` outside (0, 1)** -> Pydantic validation error at config build.
- **Degenerate banding** (recall_target so high banding collapses) -> not an error;
  `expected_recall` and a poor `reduction_ratio` are reported honestly, with a
  `notes` warning, so the user sees the cost.
- **Native gating** -> the sketch kernels run in pure-Python too (byte-identical),
  so #1083 needs no `_native_loader._GATED_ON` flip; native is a perf follow-up,
  noted, not required.
- **Off-by-default invariant** -> `throughput=None`/absent yields
  `verify_mode="full"` and every existing path is byte-identical (a tested
  regression fence).
- **Determinism** -> `seed` threaded through (sketch defaults `seed=0`); candidate
  pairs and posture are reproducible.

## Testing

All tests are CI-runnable on the pure-Python sketch path (no native build / no
embedder download required):

- **Unit — `score_sketch_pairs`:** known near-dup / distinct pairs for the
  Jaccard-estimate and cosine paths return the expected `(id_a, id_b, score)`;
  threshold cutoff is exact at the boundary.
- **Unit — `resolve_throughput_config`:** kwarg > env > default precedence;
  `True` / `float` / `ThroughputConfig` arg coercion.
- **Unit — planner overlay:** `verify_mode="sketch_distance"` set; base
  backend/workers/clustering preserved; `(bands, rows)` consistent with
  `optimal_bands(num_perms, similarity)`.
- **Unit — `ThroughputPosture` S-curve:** `expected_recall == 1-(1-s**r)**b` for
  known `(b, r, s)`; monotonic non-decreasing in `recall_target`.
- **Integration — `dedupe_df(throughput=0.95)`** on a synthetic near-dup text
  corpus (paraphrase + lexical variants): recovers the planted near-dups, returns a
  populated posture.
- **Edge:** no-text-column raises `ThroughputNotApplicableError`;
  semantic-without-embedder falls back to lexical and notes it.
- **Off-by-default guard:** `throughput=None` -> `verify_mode="full"`, output
  byte-identical to the accuracy path on the same fixture.

## Risks / open questions

- **`_nudge_for_recall` calibration.** Mapping `recall_target` -> a `(b, r)`
  adjustment is heuristic. The honest report makes the resulting `expected_recall`
  visible, so a miscalibration is observable rather than hidden, but the nudge
  function deserves its own unit test pinning the monotonic relationship.
- **Jaccard estimate vs exact.** Verify uses the MinHash *estimate* of Jaccard
  (consistent with blocking and cheap). For very short texts the estimate variance
  is higher; the default `num_perms=128` keeps it acceptable. Exact-Jaccard on
  shingle sets is a possible future precision lever, out of scope here.
- **Reduction ratio at scale.** Computing `candidate_pairs / (N^2/2)` is fine
  single-node; the distributed count is a #1084 concern.

## References

- Issue #1083; epic #1080.
- #1081 MinHash/LSH sketch kernel (`core/sketch.py`, `optimal_bands`,
  `estimate_jaccard`).
- #1082 document/text near-dup blocking (`core/simhash_blocker.py`,
  `_text_corpus_blocking`, `LSHKeyConfig` / `SimHashKeyConfig`).
- `planning_effort` knob threading precedent
  (`resolve_planning_effort`, `ControllerBudget.for_dataset`).
- `context-network/decisions/0020-minhash-lsh-sketch-tier.md` (sketch-tier ADR).
