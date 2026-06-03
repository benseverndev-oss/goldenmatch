# DataFusion Spine (scale mode)

The embedded-DataFusion path: thread the relational ER stages through ONE Python
`datafusion.SessionContext` with out-of-core spill, the native string scorers as a Rust
`ScalarUDF` via `datafusion-ffi`, and Union-Find routed to the existing label-prop path.
Opt-in behind `mode="scale"`.

**Spec:** `docs/superpowers/specs/2026-06-03-datafusion-spine-design.md`
**Parent/roadmap:** `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md`

## Shape
```
[ one datafusion.SessionContext, fair_spill_pool(memory_limit) ]
   score:  block self-join (a.__block_key__=b.__block_key__ AND a.__row_id__<b.__row_id__),
           FFI scorer UDF over __value__, threshold-filter
   dedup:  SELECT a,b,max(score) GROUP BY a,b   (in-ctx, spills)
   -> RAW scored pairs
[ mirror the in-memory frames-out tail, Ray-free ]
   build_cluster_frames(RAW pairs, all_ids)  -> assignments + metadata   <-- UF break (NOT relational)
   ClusterPairScores.from_frames(...)         [id_prep]
   build_golden_records_from_frames(...)      [golden]
```
The UF break collects pairs to the driver — an **in-memory island the spill pool does
NOT cover**. This is the load-bearing fact behind the Stage E verdict
([../decisions/0003-stage-e-spill-honest-null.md](../decisions/0003-stage-e-spill-honest-null.md)).

## Entry points
- `backends/datafusion_spine.py::run_spine(blocked_candidates, config, *, memory_limit=None, target_partitions=None)` → `(golden_df, assignments, raw_pairs)`.
- `_validate_scale_mode_supported(config)` — the Stage D feature gate (called first).
- `backends/datafusion_backend.py` — `_validate_matchkey`, `_make_score_udf` (B1 Python UDF fallback), `_materialize_blocks_to_arrow`.
- `config/schemas.py::GoldenMatchConfig.mode` — the `{standard,scale}` opt-in.
- FFI crate: `packages/rust/extensions/datafusion-udf/`.

## Status by stage (all merged on `main` as of 2026-06-03)
| Stage | What | State |
|---|---|---|
| A | FFI ScalarUDF feasibility (add_one PyCapsule) | merged |
| B | native scorers as FFI ScalarUDFs (score-core) | merged |
| C | `run_spine` orchestration; parity-gated (Rand 1.0 + golden + edges) | merged (#700) |
| D | scale-mode contract: `mode` field, feature gate, determinism gate | merged (#702) |
| E | out-of-core spill bench | merged (#705 harness, #706 verdict) — **HONEST-NULL** |

## Key constraints (verify before extending)
- `run_spine` REQUIRES `config.mode == "scale"` (the gate raises `ValueError` otherwise).
- Scale mode supports ONLY single-field weighted matchkeys w/ supported scorers
  (jaro_winkler / levenshtein / token_sort); LLM/rerank/boost/NE/exotic/domain all
  raise `NotImplementedError`. See [../decisions/0002-scale-mode-contract.md](../decisions/0002-scale-mode-contract.md).
- The CI `python (goldenmatch)` lane builds the FFI UDF wheel + installs `datafusion>=53`;
  it does NOT build `_native`, so spine tests compare against python `rapidfuzz`.
- Known pre-existing bug (flagged, unfixed): `run_spine` on empty/all-singleton input
  hits a `SchemaError` in the frames-out tail (null vs i64 join key).

## Open follow-ups
- Relational-stages-only spill bench (score+dedup under a cgroup `MemoryMax` cap,
  excluding the UF collection) — to show relational spill survival crisply.
- Sail tier: route the UF break to distributed label-prop (≥50M), removing the in-memory
  island — the only thing that unlocks beyond-one-box scale + would let the default flip.

---
**Classification:** architecture/active • **Last updated:** 2026-06-03
