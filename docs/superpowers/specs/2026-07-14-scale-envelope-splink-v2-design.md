# Scale-envelope head-to-head v2: Splink vs GoldenMatch (6 lanes, 2 shapes)

Date: 2026-07-14
Status: approved design, pre-implementation
Base: `origin/main` @ goldenmatch 3.3.0 (release commit `14a1484fd`)
Branch: `bench/scale-envelope-v2`

## 1. Why

The published scale numbers are stale. `docs/scale-envelope.md` still quotes
v1.16.0; `main` is 3.3.0. Since the last scale initiative the engine changed
substantially: bucket became the default scorer, the native Arrow/fused kernels
landed, Polars was evicted from the zero-config path, the Fellegi-Sunter (FS)
probabilistic auto-config shipped, and a Splink -> GoldenMatch config converter
(`goldenmatch.config.from_splink`) shipped. None of these are reflected in a
single, reproducible, apples-to-apples scale-envelope benchmark against Splink.

The existing harness (`scripts/bench_er_headtohead/`, last touched 2026-07-13)
already does most of the measurement — identical per-scale parquet fixture,
subprocess-per-datapoint OOM tolerance, pairwise P/R/F1 + confusion + B-cubed
via a DuckDB contingency evaluator, on a 64 GB runner. But its orchestrator only
ever runs GoldenMatch's default `hand_built` path: the `probabilistic` and
`zeroconfig` modes already present in `run_goldenmatch.py` are unreachable from
the sweep, and `run_converted_splink.py` is a standalone single-dataset parity
gate not wired into the scale sweep.

This initiative refreshes and extends that harness into a proper, reproducible
scale-envelope run and publishes the results.

## 2. Goals / non-goals

### Goals
- One reproducible harness that sweeps **6 lanes x 2 fixture shapes x N scales**
  on a single 64 GB GitHub runner, recording wall, peak RSS, scored-pairs,
  cluster counts, pairs/sec, pairwise P/R/F1 + confusion, and B-cubed P/R/F1.
- Make GoldenMatch's **forced Fellegi-Sunter probabilistic** path a first-class
  lane, so it is compared like-for-like against Splink's FS model (both numpy
  and native-Rust FS kernel variants).
- Survive the runtime reality: the full matrix exceeds a single 600-minute job,
  so sharding across dispatches + a merge step are first-class, not bolted on.
- Publish: a committed results doc and a rewrite of `docs/scale-envelope.md`
  with current 3.3.0 numbers.

### Non-goals (YAGNI)
- **No distributed tier.** Single-node to the OOM ceiling only. Ray / Sail /
  DataFusion-spine paths are explicitly out of scope for this initiative.
- **No third fixture shape.** Person + one bibliographic shape only.
- **No per-scale Splink re-tuning.** One idiomatic, fixed Splink settings spec
  per shape. A Splink expert could squeeze more; the spec is "reasonable, not
  optimal", and that limitation is stated in the results doc.
- **No new metric families** beyond those listed above.

## 3. Current state (what exists on main, reused as-is or extended)

| File | Role | This initiative |
|---|---|---|
| `generate_fixture.py` | streaming person-shaped generator to 100M, bounded memory, seeded | **extend**: add `--shape {person,biblio}` |
| `run_goldenmatch.py` | 1 datapoint; modes `hand_built`/`zeroconfig`/`probabilistic`; native gate + FS-native telemetry | **extend**: add `--shape`, shape config registry, wire `GOLDENMATCH_FS_NATIVE` |
| `run_splink.py` | 1 datapoint; idiomatic Splink 4.x; `--input` (person) or `--dataset` | **extend**: add `--shape`, per-shape settings registry, record `splink.__version__` |
| `run_converted_splink.py` | standalone dataset parity gate (from_splink) | **reference**: reused logic, not wired to sweep directly |
| `run_gm_converted.py` | — | **new**: converted-Splink lane at scale (fixture input) |
| `orchestrate.py` | subprocess-per-datapoint sweep; engine==script, fixed mode | **refactor**: lane model, `(shape,lane,scale)` keys |
| `merge_results.py` | — | **new**: union multi-dispatch artifacts -> summary + envelope tables |
| `evaluate.py` | engine-agnostic pairwise + B-cubed via DuckDB contingency | **reuse** unchanged |
| `datasets.py` | real accuracy datasets (historical_50k, dblp_acm, febrl3, ncvr) | **reuse** unchanged (not on the scale path) |
| `bench-er-headtohead.yml` | 64 GB runner workflow | **extend**: lane/shape/scale inputs, shape matrix, pinned splink, merge job |

`auto_configure_probabilistic_df` is confirmed present at
`packages/python/goldenmatch/goldenmatch/core/autoconfig.py:4651`.

## 4. Architecture: the lane model

The orchestrator's central abstraction becomes a **lane**, not an "engine".

```
Lane = {
  name:   str            # stable result key, e.g. "gm_probabilistic_native"
  script: str            # "run_goldenmatch.py" | "run_splink.py" | "run_gm_converted.py"
  mode:   str | None     # passed as --mode for run_goldenmatch.py
  env:    dict[str,str]  # extra env for the subprocess (e.g. GOLDENMATCH_FS_NATIVE=1)
}
```

The sweep iterates **lanes x shapes x scales**. Every datapoint is one isolated
subprocess (OS reclaims memory on exit; SIGKILL/OOM synthesizes an `OOM` result;
timeout ladder unchanged). Results are keyed by the triple `(shape, lane, scale)`
and flushed to `bench_results.json` after every datapoint, so a late
OOM/timeout never discards earlier points.

The lane registry (default full set of 6):

| Lane name | script | mode | env | Proves |
|---|---|---|---|---|
| `splink` | run_splink.py | — | — | Fellegi-Sunter reference engine |
| `gm_hand_built` | run_goldenmatch.py | hand_built | — | GM fast weighted bucket+native path (speed reference) |
| `gm_probabilistic` | run_goldenmatch.py | probabilistic | — | EM-trained FS, numpy — like-for-like vs Splink FS |
| `gm_probabilistic_native` | run_goldenmatch.py | probabilistic | `GOLDENMATCH_FS_NATIVE=1` | same, Rust FS kernel (telemetry proves no numpy fallback) |
| `gm_zeroconfig` | run_goldenmatch.py | zeroconfig | — | zero-effort controller vs a tuned Splink spec |
| `gm_converted_splink` | run_gm_converted.py | — | — | GM running Splink's own spec auto-converted via `from_splink` |

Lanes are selectable at the workflow level (`lanes` input), so any subset can be
dispatched. `splink` is not privileged in code — it is just another lane — but
the results doc always reports it as the reference column.

## 5. Fixture shapes

Two shapes share the streaming, bounded-memory, pooled-fancy-index generator
technique (rows flushed one row-group at a time via `pyarrow.ParquetWriter`;
all string columns produced by vectorised indexing into small precomputed pools;
fixed seed). Both write a records parquet + a `{record_id, cluster_id}` truth
parquet.

### 5.1 person (existing, unchanged schema)
`record_id int64, first_name str, surname str, dob str(YYYY-MM-DD), postcode str, city str`.
Duplicates: surname/first-name single-char typos, occasional nulls on weaker
fields; strong fields (dob, postcode) mostly agree. Blocking designed so
compound (surname + dob-year) yields small blocks at every scale.

### 5.2 biblio (new)
`record_id int64, title str, authors str, venue str, year str(YYYY)`.
Pools: title-word pool (to compose multi-word titles), author-surname pool
(to compose 1-4 author strings), venue pool (~a few hundred venues), year range
(~60 distinct years). Duplicates carry realistic bibliographic variation:
- title single-char typos + occasional word drop,
- author-list abbreviation ("Jane Smith" -> "J. Smith") and order permutation,
- venue full-name vs abbreviation,
- occasional null year.
Blocking must keep blocks small at scale: the compound key is
`(venue, year)` for GoldenMatch's bucket single-key path (its best single key
for this shape), and Splink unions `(venue, year)` + a title-token rule. Pool
sizes are chosen so `(venue, year)` cardinality grows with the year x venue
product, keeping candidate pairs ~linear in N rather than N^2 (documented in a
comment the same way the person generator documents its pool sizing).

The generator asserts, at a small smoke scale, that the largest block stays
under a sane ceiling for each shape (guards against an accidental N^2 fixture).

## 6. Shape config registries

The probabilistic / zeroconfig / converted lanes are **data-driven** — they
infer configuration from the data, so they run on either shape unchanged. Only
two lanes need shape-specific configuration:

- **`gm_hand_built`** — `run_goldenmatch.py` currently hardcodes a person config
  (postcode blocking, first_name/surname/dob weighted fields). Add a
  `_HAND_BUILT_BY_SHAPE` registry: `person` (existing config, extracted verbatim)
  and `biblio` (bucket on `(venue, year)`, weighted jaro_winkler on
  title/authors/venue). Selected by a new `--shape` flag.
- **`splink`** — `run_splink.py`'s `--input` path hardcodes
  `_default_person_settings`. Add a `_SETTINGS_BY_SHAPE` registry mirroring the
  person spec and a biblio spec (blocking union of `(venue, year)` + title-token;
  comparisons: JaroWinkler on title/authors/venue, exact/DL on year). Selected by
  `--shape`.
- **`gm_converted_splink`** — `run_gm_converted.py` builds the shape's Splink
  settings dict (reusing `run_splink.py`'s per-shape builder), serializes it with
  `SettingsCreator.create_settings_dict(sql_dialect_str="duckdb")`, runs it
  through `from_splink`, and dedupes the fixture with the converted config. It
  imports the builder, never copies it, so the converted lane always tracks the
  exact spec the `splink` lane ran.

## 7. Orchestration, sharding, and merge

### 7.1 The runtime problem
6 lanes x 2 shapes x 5 scales = up to 60 datapoints. The 25M and 100M tiers can
each run many minutes to hours per lane (25M person hand_built ~6.5 min; FS numpy
at 25M is far slower; Splink-DuckDB at 100M may complete via spill but slowly).
A single job will exceed the 600-minute cap.

### 7.2 Strategy
- **Workflow `strategy.matrix` over shape** — `person` and `biblio` run as two
  parallel jobs on two 64 GB runners, `fail-fast: false`, each uploading its own
  artifact (`er-headtohead-<shape>-<run_tag>`).
- **Everything selectable** — `lanes`, `shapes`, `scales`, `dupe_rate`,
  `threshold`, `runner`, `run_tag`. A full matrix can therefore be split across
  several dispatches (e.g. a small-scale band `100000 1000000 5000000` and a
  heavy band `25000000 100000000`) without losing comparability, because results
  are keyed by the stable `(shape, lane, scale)` triple.
- **`merge_results.py`** unions any set of run artifacts into the final
  `summary.md` + the scale-envelope tables. A dependent **merge job** runs it
  automatically for a single dispatch (downloads all shape artifacts, merges,
  uploads `er-headtohead-merged-<run_tag>` + writes `$GITHUB_STEP_SUMMARY`). For
  a multi-dispatch run, `merge_results.py` is re-run over the downloaded
  artifacts locally or via a `merge-only` dispatch mode.
- Merge is **idempotent and last-writer-wins per key**: re-running a datapoint in
  a later dispatch overwrites the earlier value for that `(shape, lane, scale)`.

### 7.3 Recommended dispatch plan (documented in README)
1. Dispatch A: `shapes=person biblio`, `scales=100000 1000000 5000000`, all lanes.
2. Dispatch B: `shapes=person biblio`, `scales=25000000 100000000`, all lanes
   (long; the 100M GM bucket lanes are expected OOM ceilings).
3. Merge A+B artifacts -> final doc.

## 8. Metrics recorded per datapoint

Standardized result schema (superset of what each runner already emits):

- **status**: `ok | OOM | timeout | refused | skipped | error | fixture_failed`
- **wall**: `dedupe_wall_seconds` end-to-end. Splink also records sub-phase
  `train_wall_seconds / predict_wall_seconds / cluster_wall_seconds`. GoldenMatch
  records `load_wall_seconds` separately.
- **peak_rss_mb**: per-process high-water mark (Linux `ru_maxrss`).
- **scored_pairs**: candidate pairs actually scored (blocking-aggressiveness is
  visible, not hidden).
- **cluster_count** (total resolved entities incl. singletons, matching Splink's
  `count(distinct cluster_id)`) + **multi_member_clusters**.
- **pairs/sec**: derived (`scored_pairs / dedupe_wall_seconds`).
- **accuracy** (from `evaluate.py`, identical code both engines): pairwise
  precision/recall/F1 + confusion (TP/FP/FN/TN), and B-cubed P/R/F1.
- **lane telemetry** (GoldenMatch): `native_loaded`, `native_block_scoring`,
  `fs_native_requested`, `fs_native_symbol_present`. Proves the
  `gm_probabilistic_native` lane really executed the Rust FS kernel and did not
  silently fall back to numpy.

### Reproducibility header (one per run, in `bench_results.json`)
`goldenmatch_version`, **pinned** `splink_version` (recorded from
`splink.__version__`), `git_sha`, `runner_label`, `cpu_count`, `total_ram_gb`,
`dupe_rate`, `threshold`, `seed`. The workflow pins the Splink version
(`uv pip install splink==<pinned>`), so the reference engine is fixed run to run.

## 9. Deliverables (file-by-file)

1. `scripts/bench_er_headtohead/orchestrate.py` — refactored to the lane model,
   `(shape, lane, scale)` keys, per-datapoint flush, reproducibility header.
2. `scripts/bench_er_headtohead/merge_results.py` — new; union + render.
3. `scripts/bench_er_headtohead/generate_fixture.py` — `--shape person|biblio`.
4. `scripts/bench_er_headtohead/run_goldenmatch.py` — `--shape` + hand_built
   registry; FS-native env respected.
5. `scripts/bench_er_headtohead/run_splink.py` — `--shape` + settings registry;
   record `splink.__version__`.
6. `scripts/bench_er_headtohead/run_gm_converted.py` — new; converted-Splink lane
   over a fixture.
7. `.github/workflows/bench-er-headtohead.yml` — lane/shape/scale inputs, shape
   matrix, pinned splink, merge job, `run_tag`.
8. `scripts/bench_er_headtohead/README.md` — refreshed (lanes, shapes, dispatch
   plan, merge).
9. After the run: `docs/benchmarks/2026-07-14-scale-envelope-splink-6lane.md` —
   committed results doc.
10. `docs/scale-envelope.md` — rewritten with current 3.3.0 numbers + the 6-lane
    table (replaces the v1.16.0 picker).

Items 1-8 are the harness change (one PR). Items 9-10 land after the run
produces numbers (a second, data-only PR / commit).

## 10. Testing strategy

- **Unit-ish smoke** (`test_bakeoff.py` sibling, runs in CI's normal lane): at a
  tiny scale (e.g. 2000 rows) per shape, assert each of the 6 lanes produces a
  result JSON with `status=ok` (or `skipped` for splink when uninstalled), a
  non-null `dedupe_wall_seconds`, and an `accuracy` block with finite F1. This is
  a plumbing test (does every lane run end-to-end and get scored), not an
  accuracy-threshold test.
- **Merge test**: `merge_results.py` over two synthetic partial-artifact JSONs
  asserts union + last-writer-wins per `(shape, lane, scale)` key.
- **Generator block-size guard**: at a smoke scale, assert the largest block for
  each shape stays under the ceiling (catches an accidental N^2 fixture).
- **Native-gate honesty**: assert `gm_hand_built` with `--require-native` refuses
  (non-zero exit, no ok result) when the native runtime is absent — the existing
  guard, kept under test.
- The real scale run is `workflow_dispatch` only (never on PR CI); the smoke
  tests are what gate the harness PR.

## 11. Caveats carried into the results doc

- **Blocking asymmetry is reported, not hidden.** GoldenMatch's bucket path does
  single-key blocking (its best single key per shape, ~0.94 pair coverage on
  person `postcode`); Splink unions rules (~0.99). The recall gap is a genuine
  property of each engine's fast path, surfaced via the accuracy columns and the
  `scored_pairs` column.
- **FS-native vs numpy may differ marginally.** FS's discrete comparison levels
  amplify tiny rapidfuzz float differences at exact thresholds, so
  `gm_probabilistic` (numpy) and `gm_probabilistic_native` are reported as two
  separate lanes rather than one silently substituting the other.
- **Splink spec is idiomatic, not maximally tuned**, and fixed per shape.
- **100M GM bucket lanes are expected OOM** on a single 64 GB box — that ceiling
  is the headline single-node result, not a failure.

## 12. Open risks

- **Biblio at 25M/100M is the most expensive, least-proven part.** The person
  generator is battle-tested to 100M; the biblio generator is new. Mitigation:
  the block-size guard + a mandatory biblio smoke datapoint at 100k before the
  heavy tiers, and the dispatch plan runs the small band first so a bad biblio
  fixture surfaces cheaply.
- **Splink at 100M may run for hours or OOM its own DuckDB temp.** Handled by the
  timeout ladder + `OOM`/`timeout` status; a non-completing Splink 100M is itself
  a recorded envelope result.
- **from_splink coverage on the biblio spec.** If a biblio comparison type isn't
  yet convertible, the converted lane records the `ConversionReport` findings and
  a `refused`/`error` status rather than crashing the sweep; the other 5 lanes
  still produce the biblio envelope.
- **Runner availability.** Larger GitHub-hosted runners can queue; the workflow
  keeps `runner` selectable so the run can move to whichever 64 GB label is free.
