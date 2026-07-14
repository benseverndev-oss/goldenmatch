# Splink Migration Assistant: Data-Aware Upgrade Pass

**Date:** 2026-07-14
**Status:** Approved (design)
**Thesis phase:** Python POC (Rust/TS surfaces are later phases, out of scope)
**Predecessor:** the Splink config converter (specs/2026-07-13-splink-config-converter-design.md; shipped in goldenmatch 3.2.0)

## Problem

A faithful 1:1 Splink conversion is the trust anchor, not the end goal. The head-to-head bench
(D:\ER\splink_convert_dogfood\bench, goldenmatch 3.2.0 vs splink 4.0.16) surfaced three gaps that
are all fixable WITH THE USER'S DATA at migration time:

1. Imported `tf_adjustment=True` is inert -- Splink exports carry no term-frequency tables, so the
   flag does nothing until a retrain (measured recall gap on name fields).
2. The levenshtein distance->similarity approximation (`sim = 1 - d/10`) ignores real string
   lengths -- on ~20-char emails, Splink's `distance <= 1` is sim ~0.95, not 0.9 (bands too loose).
3. Link/review thresholds are engine defaults on both sides -- neither operating point is tuned
   (bench was defaults-vs-defaults; F1 deltas partly reflect this).

The converter cannot fix these: a settings file has no data. The upgrade pass runs AFTER
conversion, with the dataset in hand, and turns the conversion report into a migration report:
"here is your Splink-equivalent baseline, here is what we changed and why, here is the measured
delta on your data."

## Decisions (from brainstorming)

- **Levers (v1):** TF tables at import, measured distance thresholds, threshold calibration.
  Fan-out defenses / negative evidence = v2 (more opinionated).
- **Measurement:** the pass runs BOTH configs (baseline + upgraded) and reports the delta --
  bounded by `sample_cap` (default 100K rows; seeded subsample above; `measure=False` escape
  hatch). Running both is infeasible at full scale but sound up to the cap.
- **Reference:** optional `splink_clusters` (migrators have their old output) for agreement
  metrics; optional `labels` for true F1/B-cubed; cluster-shape sanity always.
- **Surfaces (POC):** library `upgrade_splink_conversion()` + CLI `import-splink --upgrade DATA`.
  MCP tool follows later once the shape settles.
- **Approach A:** separate pass over the conversion result. The converter stays pure,
  deterministic, and data-free (it is the trust anchor and the TS-port parity surface). Rejected:
  folding into `from_splink(data=...)` (contaminates the pure converter and the edge-safe TS
  parity); routing through `auto_configure` (abandons the explainable lever-by-lever migration
  story -- kept only as a pointer in the report).

## API

New module `goldenmatch/config/splink_upgrade.py` (sibling of `from_splink.py`):

```python
def upgrade_splink_conversion(
    conversion: SplinkConversion,
    data: pl.DataFrame | str | Path,
    *,
    sample_cap: int = 100_000,
    seed: int = 42,
    splink_clusters: DataFrame | str | Path | None = None,  # id -> cluster_id
    labels: DataFrame | str | Path | None = None,
    levers: set[str] | None = None,   # subset of {"tf_tables", "distance_thresholds", "calibration"}; None = all
    measure: bool = True,
) -> MigrationResult
```

```python
@dataclass
class MigrationResult:
    baseline_config: GoldenMatchConfig      # conversion.config, untouched
    upgraded_config: GoldenMatchConfig      # deep copy with lever changes applied
    em_model: EMResult | None               # upgraded copy (TF tables attached); baseline EMResult untouched
    report: ConversionReport                # conversion findings + new findings with "upgrade:"-prefixed splink_path
    measurement: MeasurementResult | None   # None when measure=False or measurement failed
```

Invariants:
- Copy-on-write everywhere: `conversion` is never mutated; baseline and upgraded coexist.
- One seeded subsample (when `len(data) > sample_cap`) feeds ALL levers and BOTH measurement runs.
- Upfront validation: every matchkey field must exist as a data column, else abort with a clear
  error before any lever runs.

## The three levers

Each lever is an independent, individually-testable transform that emits findings explaining what
it did (or why it was skipped). A lever that cannot run (missing column, unrecoverable input,
empty sample) emits a WARNING finding and is skipped -- levers never fail the pass.

### 1. TF tables (`tf_tables`)

For every field with `tf_adjustment=True` whose imported `EMResult` lacks `tf_freqs` for it:
compute value -> relative-frequency tables and the collision rate from the transform-applied data
column, attach to the upgraded `EMResult` copy. Implementation REUSES the existing module-level
helper `_build_tf_tables(df, mk)` in `core/probabilistic.py` (~line 944, built on
`tf_tables.value_frequencies`) -- the plan task is making it importable/callable for this use, not
an extraction. Finding per field: distinct values, collision rate.

**Bare-settings inputs (`conversion.em_model is None`):** levers 1 and 3 SKIP with an info note --
when no trained model was imported, GoldenMatch trains EM on the user's data at run time, which
already computes TF tables (for tf_adjustment fields) and calibrated thresholds natively. Lever 2
still applies (band thresholds are config-level, fixed before training). The migration report says
exactly this.

### 2. Measured distance thresholds (`distance_thresholds`)

Mechanism (NO finding-message parsing): every `scorer="levenshtein"` field in a converted config
can only have come from the converter's `_DIST_RE` path with the constant `_LEV_ASSUMED_LEN = 10`,
so the original Splink distance inverts exactly: `d = round((1 - t) * 10)` per threshold (the
converter itself performs this same reconstruction for its warning text). For each such field:
measure the mean POST-TRANSFORM string length L of the data column on the sample, recompute
`sim = max(0, 1 - d/L)` per band, replace `level_thresholds` (or `partial_threshold` for 2-level
fields). `jaro_similarity`-approx fields are NOT touched (different approximation, no distance to
recover). Re-validate ordering (dedupe / strictly-descending; if two bands collapse, merge their
imported m/u the way the converter's import_em does and warn). Finding per band: old -> new
threshold, d, measured L. Skipped (warning) when L cannot be measured (empty column) or the
recomputed thresholds would leave no valid band.

### 3. Threshold calibration (`calibration`)

Compute raw Fellegi-Sunter weights for BLOCKED candidate pairs on the sample using the upgraded
model (post levers 1-2) -- NOT via `score_probabilistic` (which filters at the link threshold and
returns only survivors) but via the underlying weight computation over candidate pairs with no
cut, normalized the same way scoring does. Feed that full distribution to
`compute_thresholds(em_result, scored_weights=...)` -- note this lever is the FIRST real consumer
of the `scored_weights` branch (nothing in the codebase passes it today; the branch also requires
len > 50, below which the lever skips with a warning). Set explicit
`link_threshold`/`review_threshold` on the upgraded matchkey. **Posterior calibration mode**
(`GOLDENMATCH_FS_CALIBRATED=posterior`): `compute_thresholds` deliberately returns fixed
absolute cuts (0.99/0.50) and ignores the distribution -- the lever detects this mode and skips
with an info note. Finding: chosen thresholds + distribution evidence (percentiles, n pairs).
Runs after levers 1-2 by design (calibrates the model users will actually run).

## Measurement

Runs `dedupe_df` twice on the same sample: baseline config, then upgraded config.

**Model injection:** `dedupe_df` consumes trained models via FILE (`model_path` on the matchkey /
`fs_model_path`); `SplinkConversion.em_model` is in-memory. The measurement stage therefore writes
BOTH EMResults (baseline as-imported; upgraded with TF tables) to temp files (`save_json`) and
runs each config copy with its `model_path` pointed at its own temp file -- otherwise `dedupe_df`
would silently retrain EM on the sample and measure the wrong models. Temp files are cleaned up
after measurement; the CLI's persistent model files are separate (see CLI section).

```python
@dataclass
class MeasurementResult:
    sample_rows: int
    sampled: bool                       # True when data exceeded sample_cap
    baseline: RunStats                  # cluster shape + wall per run
    upgraded: RunStats
    vs_splink: PairwiseAgreement | None # both runs vs splink_clusters, when provided
    vs_labels: TruthMetrics | None      # pairwise + b-cubed, when labels provided
```

Reports:

- Per run: cluster count, multi-record clusters, max cluster size, singleton count, wall time,
  and a snowball flag (max cluster size > 10x the reference max, where reference = splink_clusters
  max when provided, else the run's own p99).
- Against `splink_clusters` (when provided): pairwise agreement P/R/F1 for both runs (restricted
  to the sampled ids).
- Against `labels` (when provided): true pairwise P/R/F1 + B-cubed for both runs.
- Neither provided: shape-only comparison + info note saying measurement had no external reference.

Measurement failure (e.g. dedupe crash on the sample) downgrades the result to transform-only:
error finding recorded, `measurement=None`, upgraded config still returned.

## CLI

```
goldenmatch import-splink settings.json -o out.yaml --model-out model.json \
    --upgrade data.parquet [--splink-clusters old.parquet] [--labels truth.csv] \
    [--sample-cap N] [--no-measure]
```

- With `--upgrade`: the written `out.yaml` / `model.json` are the UPGRADED artifacts; the faithful
  baseline is written alongside as `out.baseline.yaml` / `model.baseline.json` (the trust anchor
  stays on disk). `model_path` wiring is per-pair: `out.yaml` points at `model.json`,
  `out.baseline.yaml` at `model.baseline.json`. The existing config-before-model write ordering
  and partial-model refusal apply to EACH pair (the current CLI logic handles one pair; the plan
  extends it to two -- baseline pair first, upgraded pair second, so a failure mid-way never
  leaves an upgraded config without its baseline).
- `--upgrade` with a trained-model input REQUIRES `--model-out` (TF tables need a model file).
- Output: the existing findings table (now including upgrade findings) + a compact
  baseline -> upgraded delta table from the measurement.

## Error handling summary

- Missing data columns for matchkey fields: hard error before any work.
- Per-lever failures: warning finding + skip (pass continues).
- Measurement failure: error finding + transform-only result.
- `measure=False` / `--no-measure`: skip measurement silently (info note).

## Testing / success bar

- Unit tests per lever on synthetic frames with known frequencies / string lengths / score
  distributions; copy-on-write invariants (baseline objects unchanged); skip paths.
- Integration: the wild-corpus bench pairs (D:\ER\splink_convert_dogfood) re-run through
  `--upgrade`.
- **Success bar:** on `fake_1000` + `real_time_settings` (worst baseline gap: GM pairwise F1
  0.482 vs Splink 0.601), the upgraded config closes >= half the pairwise-F1 gap to Splink, and NO
  bench pair regresses below its baseline F1.

## Out of scope

- Fan-out defenses / negative-evidence suggestions (v2 lever).
- MCP surface (follows once the shape settles; needs inline-data handling).
- Rust/TS ports (thesis phases 2-3 for this feature).
- Full-scale (unbounded) measurement; `auto_configure` integration beyond a report pointer.
