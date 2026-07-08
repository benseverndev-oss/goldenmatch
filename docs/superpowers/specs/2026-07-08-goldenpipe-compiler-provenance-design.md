# GoldenPipe Compiler — SP2: Field-Level Provenance from the IR — Design

**Date:** 2026-07-08
**Status:** Approved (brainstorming), pending implementation plan
**Program:** GoldenPipe compiler (SP1 IR walking skeleton shipped, PR #1592). This is
**sub-project 2**. It is deliberately **NOT** a performance sub-project (see below).

## Why non-performance (the measured pivot)

The original SP2 plan was cross-stage columnar fusion. Measurement + code inspection
refuted every compiler *performance* lever on the core ER workload:
- **Columnar fusion** — the columnar stages (Check `Scan` + Flow `Map`) are ~8% of
  wall time on a 20k-row ER pipeline (Check 5.1% / Flow 3.0% / **Match 91.9%**), and
  Match dominates *more* at scale. goldenflow already fuses Map chains within a stage.
- **Auto-config opt-out** — measured ~3.7% of Match, and GoldenPipe already does it
  (`adapters/match.py` builds an explicit config from Check's `column_contexts` via
  `_build_config_from_contexts` and passes it to `dedupe_df`, skipping GoldenMatch's
  internal auto-config controller).
- **Emit Match to a scale engine** — already exists as goldenmatch `backend=` targets
  (`backends/datafusion_backend.py`, `duckdb_backend.py`, `score_duckdb.py`, the `sail/`
  and `distributed/` packages).

Every performance lever is already covered by goldenflow (fusion), GoldenPipe
(opt-out), or goldenmatch (scale backends). SP1's real value was never speed — it is
the portable, inspectable, cross-surface whole-*pipeline* plan. SP2 delivers a value
that only the IR can provide and that the suite lacks: **field-level provenance.**

## Goal

Turn a `CompiledPipeline` (SP1's IR) into a **field-level lineage report**: for each
column the pipeline touches, its journey through the plan — Check ops, ordered Flow
transforms, and matching role (blocking key / scorer input). A pure, deterministic
`provenance(CompiledPipeline) -> lineage` kernel function + a host wrapper that
attaches it, plus a human-readable explain string.

**Net-new (verified):** no cross-stage field provenance exists in the suite —
goldenmatch's `explain_pair`/`explain_cluster` explain *record pairs*; goldenpipe's
`reasoning` explains *stages*; this explains *fields end-to-end*. Only the IR holds the
per-column op graph across Check→Flow→Match.

**Value:** compliance/audit ("how was this golden field derived?"), debugging bad
merges ("which transform normalized the blocking key that caused this merge?"), and
trust (show the full column journey).

## The `FieldLineage` shape

```
provenance(CompiledPipeline) -> { "fields": [FieldLineage], "unmapped": [PipelineNote] }

FieldLineage {
  column: str,
  origin: "source" | "derived",   // "source" for all in SP1's op set (no column-creating
                                  // op yet; splits/renames mark "derived" later — kept fwd-compat)
  checks: [str],                  // ops from Scan nodes on this column (Check)
  transforms: [str],             // ops from Map nodes on this column, IN ORDER (Flow)
  blocking_key: bool,             // column ∈ any Partition.keys (Match blocking)
  scorer_input: bool,             // column referenced by any PairScore.scorer (Match scoring)
  node_ids: [int]                 // the IR nodes that mention this column (traceability)
}

PipelineNote { node_id: int, kind: str, note: str }   // Source / Connected / Barrier
```

## Derivation (pure, single pass over nodes)

- `Source` → a `PipelineNote` ("data loaded"); does not itself name columns.
- `Scan{column, ops}` → `checks[column] += ops`; record `node_id`.
- `Map{column, op}` → `transforms[column].append(op)` (iterate nodes in id order so the
  transform chain is ordered); record `node_id`.
- `Partition{keys}` → `blocking_key[k] = true` for each `k in keys`.
- `PairScore{scorer}` → resolve the scorer config's referenced columns → `scorer_input[c] = true`.
- `Connected` / `Barrier` → `PipelineNote` (clustering method / opaque stage).
- Column set = union of every named column; emit one `FieldLineage` per column,
  deterministic order (first-seen). Columns are matched by exact string.

`provenance` is a total pure function of the `CompiledPipeline` JSON; unknown node
kinds contribute a `PipelineNote` rather than erroring.

## Prerequisite — enrich the Match capture (fix SP1's placeholder)

SP1 captured Match nodes as placeholders (`GoldenMatchConfig.model_dump()` did not map
to `keys`/`scorer`, so `Partition.keys` was empty). For `blocking_key`/`scorer_input`
to be real, `capture.py`'s Match branch must normalize the real `GoldenMatchConfig`
into `{keys, scorer}`:
- `keys` = the blocking key column names, from the config's `matchkeys` / `blocking`
  fields.
- `scorer` = the scorer spec with its referenced column names (best-effort from the
  config; a coarse record is acceptable — provenance only needs the column refs).

**Contained:** this changes only the *recorded IR node configs*, not execution. The
SP1 equivalence gate compares *execution* artifacts (df/findings/profile/manifest/
clusters/golden) — untouched. `lower`'s match branch already handles populated
`keys`/`scorer` (the existing `lower.json` vectors cover it). So the enrichment cannot
break the equivalence gate.

## Host wiring

- `field_lineage(compiled) -> lineage` — a thin host wrapper calling the kernel
  `provenance`. The caller invokes it on the `compile_and_run` result; **no coupling to
  execution** (opt-in, read-only).
- `format_lineage(lineage) -> str` — host-side presentation (e.g.
  `email: checks[pattern_consistency] -> transforms[email_normalize] -> blocking-key, scorer-input`).
  The **structured** lineage is the cross-surface parity contract; the human string
  stays host Python (presentation, like reasoning lines).

## Kernel / host split (for the plan)

- **Kernel** (`goldenpipe-core`, Rust; Python pure mirror): `provenance` (build lineage
  objects as `serde_json::Value` by hand for byte-parity key order, like `lower`),
  `provenance_json` wrapper, `tests/vectors/provenance.json` golden vectors, wasm +
  native shim exports, `_native_loader.py` passthrough.
- **Host** (Python, box-runnable): the `capture.py` Match enrichment, `field_lineage`
  wrapper, `format_lineage`, and the real-pipeline lineage test.

## Scope boundary

**Column/plan-level, NOT row-level.** Provenance answers "how is field X *treated* by
the plan" (its transform chain + matching role), not "which row's value survived into
golden record R" (row-level survivorship needs the Match *output*, not the IR
structure). Row/cluster-level provenance is a clean future increment.

Also out of scope: no execution change, no new fusion/emit, no TS host (the kernel
`provenance` + Python mirror ship; a TS mirror is a later increment like SP1's).

## Error handling

- `provenance` is total: empty pipeline → `{fields: [], unmapped: []}`; unknown node
  kind → a `PipelineNote`, never raises.
- `field_lineage` on a `None`/empty compiled plan → empty lineage.
- The Match-capture enrichment degrades gracefully: if a config field is absent, the
  corresponding `keys`/`scorer` entry is empty (no crash) — provenance then reports
  `blocking_key: false` for that column, honestly reflecting the recorded plan.

## Testing

- **Kernel golden vectors** (`provenance.json`, replayed Rust + Python mirror via
  `test_planner_parity`): Scan+Map column → `checks`+ordered `transforms`; a
  `Partition`-key column → `blocking_key: true`; a scorer-referenced column →
  `scorer_input: true`; `Source`/`Connected` → `unmapped` notes; a Map-only column (no
  check); empty pipeline → empty.
- **Match-capture enrichment** (box): construct a `GoldenMatchConfig`, run the Match
  capture branch, assert real `{keys, scorer}` extracted (not the empty placeholder).
- **Real-pipeline lineage** (box): reuse the equivalence-gate fixture, `compile_and_run`
  the full `load→check→flow→match`, call `field_lineage(compiled)`, assert `email`'s
  `transforms` match what `manifest.records` actually applied and `blocking_key` columns
  match the resolved match config — proving lineage reflects the *actual* plan.
- **`format_lineage`** host test.

## Rollout

Pure-additive, opt-in. Zero execution change (classic runner + `compile_and_run`
untouched). The only edit to existing behavior is the `capture.py` Match enrichment,
which only affects recorded IR node configs; the equivalence gate stays green. No new
kernel symbols depended on by existing paths.
