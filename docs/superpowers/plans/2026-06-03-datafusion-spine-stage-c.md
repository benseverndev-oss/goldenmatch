# DataFusion Spine — Stage C: spine orchestration Implementation Plan

> **For agentic workers:** REQUIRED: superpowers:subagent-driven-development.
> Checkbox (`- [ ]`). **CI-validated posture:** box HANGS on Python `import` of
> goldenmatch/polars/datafusion. Validate Python via `ruff check` + `py_compile`
> ONLY (and the goldenmatch lane enforces `I001` import-ordering — run `ruff
> check` and fix BEFORE pushing). NEVER import/pytest/uv/pyright. Real tests in
> CI. Branch off `main` (which now has Stage A+B merged: the FFI scorer UDFs +
> `goldenmatch-score-core`). benzsevern auth, NEVER benzsevern-mjh.
> `docs/superpowers/` gitignored.

**Goal:** Thread `score → dedup → [UF break] → id_prep → golden` on ONE Python
`datafusion` `SessionContext` with out-of-core spill, behind `mode="scale"`,
producing the SAME partition + golden as the in-memory pipeline (semantic parity,
not bit-identical).

**PRECONDITION (gate on this):** Stage A/B must be MERGED to main first (the
`goldenmatch_datafusion_udf` crate, `goldenmatch-score-core`, the CI wheel step,
and the `datafusion>=53,<54` pin). Until then they live only on
`feat/datafusion-ffi-udf`. If A/B is not yet merged, either wait, or scope C to
**B1** (`datafusion_backend.py::_make_score_udf`, the Python UDF, already on main)
and defer the B2/FFI seam — `_register_scorers` falls to B1 when
`goldenmatch_datafusion_udf` is absent, so state that, don't present FFI as live.

**Architecture (corrected after review — the spine REUSES the proven frames-out
path; only score+dedup is new DataFusion):** `run_spine(blocked_candidates,
config, *, memory_limit) -> (golden_df, assignments_df)`.
- **New DataFusion stages (ride the spilling ctx):** `score` (block self-join +
  scorer UDF) → `dedup` (in-ctx `max(score) GROUP BY a,b`). Output: the scored
  pairs.
- **Clustering → id_prep → golden = MIRROR the in-memory frames-out path
  (`pipeline.py:1503-1647` under `GOLDENMATCH_CLUSTER_FRAMES_OUT=1`), which is
  Ray-free + frames-native:** `build_cluster_frames(RAW all_pairs, all_ids, ...)`
  → `ClusterFrames` (the genuine one-box WCC; NOT `build_clusters_distributed`,
  which is Ray-only) → `from_frames(assignments, RAW all_pairs)` (id_prep) +
  `build_golden_records_from_frames(source, cluster_frames, rules)` (golden).
  No Ray, no `materialize_cluster_dict`, no DIY metadata assembly.
- Written against the SCORER-UDF interface so B1 (Python UDF) ↔ B2 (FFI UDF) is a
  swap. **RAW pairs (not the deduped frame) feed `build_cluster_frames` AND
  `from_frames`** — the in-memory path does exactly this (`pipeline.py:1505,1906`);
  the deduped set is only for the `scored_pairs` result field.

**Tech Stack:** Python datafusion 53, PyArrow, the Stage-B FFI scorer UDFs
(`goldenmatch_datafusion_udf`), `goldenmatch.distributed.clustering`,
`goldenmatch.core.golden`/`cluster_pairscores`.

**Spec:** `docs/superpowers/specs/2026-06-03-datafusion-spine-design.md` (Stage C).

---

## Stage entry-points (grounded — read these before wiring)

- **score:** `backends/datafusion_backend.py::score_blocks_datafusion` (block
  self-join + scorer UDF). `_make_score_udf` is B1 (Python). For B2 register the
  Stage-B FFI UDFs (`goldenmatch_datafusion_udf.{JaroWinklerUDF,TokenSortUDF,
  LevenshteinUDF}` via `datafusion.udf(...)`). Wire BOTH behind one interface.
- **dedup:** **in-ctx** `SELECT a,b,max(score) AS score GROUP BY a,b` (rides the
  spill pool — do NOT use `dedup_pairs_max_score_arrow`, which pulls the full
  scored set to the driver as a Polars frame, defeating spill at this stage).
- **UF (clustering):** `core/cluster.py::build_cluster_frames(all_pairs, all_ids,
  *, max_cluster_size, weak_cluster_threshold, auto_split)` → `ClusterFrames`
  (assignments `cluster_id,member_id` + the 9-col metadata `cluster_id,size,
  confidence,quality,oversized,bottleneck_pair_a,bottleneck_pair_b,min_edge,
  avg_edge`). **Ray-free, one-box, the in-memory path's clustering** (`pipeline.py
  :1505`). NOT `build_clusters_distributed` (Ray-only). `all_ids` Arrow-derived
  (not a Python `list[int]`). RAW `all_pairs` (it dedups internally via
  `_columnar_presplit`).
- **id_prep:** `core/cluster_pairscores.py::ClusterPairScores.from_frames(
  cluster_frames.assignments, RAW all_pairs)` (#696 group-by.agg; RAW pairs per
  its docstring contract — last-wins, NOT the max-deduped set).
- **golden:** `core/golden.py::build_golden_records_from_frames(source_df,
  cluster_frames, rules, ...)` — pass the `ClusterFrames` from
  `build_cluster_frames` STRAIGHT through (it carries `size`/`oversized` that
  `_multi_df_from_frames` filters on). No assembly. Mirror `pipeline.py:1641-1647`.

## File structure

- Create `packages/python/goldenmatch/goldenmatch/backends/datafusion_spine.py`
  — `run_spine(...)` + helpers (one `SessionContext`, stage threading, the UF
  break). One responsibility: the spine orchestration. Reuses
  `datafusion_backend` for the score stage.
- Create `packages/python/goldenmatch/tests/test_datafusion_spine_parity.py`
  — run_spine vs in-memory parity.
- Modify `.github/workflows/ci.yml` if the spine test needs the datafusion-udf
  wheel + datafusion runtime (it does — the Stage A/B build step already wires
  this for the goldenmatch lane; confirm it covers the new test path).

---

### Task C1: spine skeleton — ctx + score + dedup

**Files:**
- Create: `goldenmatch/backends/datafusion_spine.py`
- Test: `tests/test_datafusion_spine_parity.py`

- [ ] **Step 1: Write a failing skeleton test.** On a tiny fixture (a few blocked
  candidate rows), `run_spine(candidates, config, memory_limit=None)` returns
  `(golden_df, assignments_df)` that are non-empty pyarrow/polars frames with the
  expected columns (`assignments`: `member_id, cluster_id`). (Full parity is C4.)

- [ ] **Step 2: Build the ctx + spill.** `_make_spine_ctx(memory_limit,
  target_partitions=None)`: `SessionConfig()` (+ `.with_target_partitions(n)` if
  given); if `memory_limit`: `RuntimeEnvBuilder().with_disk_manager_os()
  .with_fair_spill_pool(memory_limit)` passed as `SessionContext(config=cfg,
  runtime=builder)` (the Stage-A/B confirmed v53 API). Register the scorer UDFs:
  an `_register_scorers(ctx, config, *, use_ffi)` that registers either the FFI
  UDFs (B2, default when `goldenmatch_datafusion_udf` importable) or falls back
  to B1 (`_make_score_udf`). This is the UDF-interface seam.

- [ ] **Step 3: score + dedup.** Register `blocked_candidates` (Arrow) on the
  ctx. Score: a block-self-join SQL using the scorer UDF(s) (mirror
  `score_blocks_datafusion`'s self-join shape `a.block_key=b.block_key AND
  a.id<b.id`; reuse `_materialize_blocks_to_arrow` for the `__block_key__/
  __row_id__/__value__` shape; if registering FFI token_sort, replicate the
  `/100.0` normalization `_make_score_udf` applies), threshold-filter →
  **RAW scored pairs** `(a,b,score)`. KEEP the raw scored pairs (for
  `build_cluster_frames` + `from_frames`). Dedup (in-ctx, rides spill): `SELECT
  a,b,max(score) GROUP BY a,b` → the deduped set, used ONLY for the
  `scored_pairs` result field — NOT fed to clustering/id_prep.

- [ ] **Step 4: Validate** (`ruff check` incl I001 + `py_compile`). Commit.
  `feat(spine): datafusion_spine ctx + score(UDF) + dedup stages`

### Task C2: clustering via build_cluster_frames (one-box, Ray-free)

- [ ] **Step 1:** Collect the RAW scored pairs to the driver as `all_pairs`
  (`list[(a,b,score)]` or the Arrow it accepts) and derive `all_ids` as an Arrow
  array (NOT a Python `list[int]` rehydration). Call `build_cluster_frames(
  all_pairs, all_ids, max_cluster_size=config..., weak_cluster_threshold=...,
  auto_split=...)` → `cluster_frames` (`ClusterFrames`). This is exactly what
  `pipeline.py:1503-1509` does under frames-out. NO Ray,
  `build_clusters_distributed`, or `materialize_cluster_dict`. (The driver-side
  collect here is the "in-memory island" the spec scopes — bound below scipy's
  ~50M envelope in Stage E.)

- [ ] **Step 2: Validate + commit.** `feat(spine): clustering via build_cluster_frames (Ray-free ClusterFrames)`

### Task C3: id_prep + golden (mirror pipeline.py:1641)

- [ ] **Step 1:** id_prep: `pair_score_view = ClusterPairScores.from_frames(
  cluster_frames.assignments, RAW all_pairs)` (RAW pairs per the docstring
  contract). golden: `golden_df = build_golden_records_from_frames(source_df,
  cluster_frames, golden_rules, quality_scores=None, provenance=...)` — pass
  `cluster_frames` straight through (NO assembly; its 9-col metadata carries
  `size`/`oversized` golden filters on). Return `(golden_df,
  cluster_frames.assignments)`.

- [ ] **Step 2: Validate + commit.** `feat(spine): id_prep view + golden from cluster_frames (mirror frames-out)`

### Task C4: parity harness (the gate)

**Files:**
- Test: `tests/test_datafusion_spine_parity.py`

- [ ] **Step 1: Write the parity test.** A representative fixture (realistic-
  person shape, small N). Comparand: run the in-memory pipeline with
  `GOLDENMATCH_CLUSTER_FRAMES_OUT=1` so BOTH sides derive the partition the SAME
  way — the in-memory `cluster_frames.assignments` vs the spine's
  `assignments_df` (both from `build_cluster_frames`, so this should be near-
  identical; the only difference is the DataFusion score/dedup feeding it). Assert
  SEMANTIC parity (NOT bit-identical):
  - **cluster partition: Rand index 1.0** — frozenset of `__row_id__` per cluster,
    SINGLETONS handled CONSISTENTLY on both sides (`build_cluster_frames` emits
    size-1 clusters for singletons, so include them on both, or exclude on both).
  - **golden content:** same surviving values per multi-member non-oversized
    cluster (golden's scope; the partition check is broader — keep the scopes
    distinct).
  - **id_prep edge sets:** per-cluster `for_cluster(cid)` edge sets match (set
    equality) — passes only if RAW pairs fed `from_frames` (C3), not deduped.
  `confidence_required=False`. Entity-ids are not literal-comparable (use the
  partition). Confidence as ε.

- [ ] **Step 2: Validate (ruff I001 + py_compile) + commit.**
  `test(spine): run_spine vs in-memory semantic parity (Rand 1.0 + golden + edges)`

---

## Execution order & gates

1. C1 → C2 → C3 → C4 on a branch off main. Push (benzsevern), PR.
2. CI gate: the spine parity test GREEN (Rand 1.0 partition + golden + edge sets).
   The datafusion-udf wheel + datafusion runtime build in the goldenmatch lane
   (Stage A/B wiring) — confirm the spine test runs (hard deps present), not skip.
3. If GREEN: Stage C done → Stage D (scale-mode contract: determinism across
   `target_partitions`, feature-gating LLM/rerank/boost/NE/exotic → error) and
   Stage E (out-of-core spill bench: relational stages survive where in-memory
   OOMs; bound the UF collection below scipy's ~50M envelope).

No default flips (run_spine is behind `mode="scale"`, default standard; nothing
in the default pipeline path changes until a later cutover).

## Final review

After C1-C4: a code-reviewer over the diff (the UF-break frame-native round-trip —
no `list[int]`/`materialize_cluster_dict` rehydration; the `ClusterFrames`
contract golden needs; the parity harness's partition comparison correctness),
then declare Stage C done.
