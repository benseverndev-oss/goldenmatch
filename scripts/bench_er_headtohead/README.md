# ER head-to-head scale-envelope bench

A reproducible **6-lane x 2-shape x N-scale** Splink-vs-GoldenMatch scale
envelope, run on one 64 GB machine with an identical per-scale parquet fixture so
wall + peak RSS are comparable. Built because no published apples-to-apples
wall+RSS comparison exists between these engines (Splink publishes 7M / ~2 min /
1B-pairs; GoldenMatch publishes 25M / 6.5 min / 57.7 GB - different pair counts,
different operations, not comparable).

Every datapoint is one isolated subprocess keyed by the stable triple
`(shape, lane, scale)`; the aggregate is flushed after each point, so a late
OOM/timeout never discards earlier results.

## The 6 lanes

The orchestrator's central abstraction is a **lane**
(`{name, script, mode, env}`). The default set is all six; any subset is
selectable via the `lanes` workflow input. `splink` is not privileged in code -
it is just another lane - but the results doc always reports it as the reference
column.

| Lane | script | mode | env | Proves |
|---|---|---|---|---|
| `splink` | `run_splink.py` | - | - | Fellegi-Sunter reference engine (pinned `splink==4.0.16`) |
| `gm_hand_built` | `run_goldenmatch.py` | hand_built | - | GM fast weighted bucket+native path (speed reference) |
| `gm_probabilistic` | `run_goldenmatch.py` | probabilistic (`--fs-basic-scorers`) | `GOLDENMATCH_FS_NATIVE=0` | EM-trained FS, **numpy fallback** - like-for-like vs Splink FS |
| `gm_probabilistic_native` | `run_goldenmatch.py` | probabilistic (`--fs-basic-scorers`) | `GOLDENMATCH_FS_NATIVE=1` | same, **Rust FS kernel** (reference-mode default, made explicit) |
| `gm_zeroconfig` | `run_goldenmatch.py` | zeroconfig | - | zero-effort controller vs a tuned Splink spec |
| `gm_converted_splink` | `run_gm_converted.py` | - | - | GM running Splink's own spec, auto-converted via `from_splink` |

**Lane env is applied per subprocess, never to the orchestrator's own
environment** (`subprocess.run(cmd, env={**os.environ, **lane.env})`). The
orchestrator MUST NOT `os.environ.update(...)` - that would leak
`GOLDENMATCH_FS_NATIVE=0` from the numpy lane into the following native/zeroconfig
datapoints in the same process and silently invalidate them.

### The two FS lanes: numpy vs native (and why both)

FS-native is **default-ON** (`_fs_native_enabled()` returns native whenever the
env is unset - Rust-is-the-reference mode). So the env is set **explicitly on
both FS lanes**: the numpy lane forces `GOLDENMATCH_FS_NATIVE=0`, the native lane
sets `=1` to make the default explicit. Without `=0` on `gm_probabilistic` the
two lanes would be native-vs-native, gutting the comparison. FS's discrete
comparison levels amplify tiny rapidfuzz float differences at exact thresholds,
so the two are reported as separate lanes rather than one silently substituting
the other.

**Both FS lanes pass `--fs-basic-scorers` (name fields forced to `jaro_winkler`).**
Autoconfig picks specialized name scorers (`given_name_aliased_jw` for first_name,
`name_freq_weighted_jw` for surname) that the native FS kernel does NOT implement
(its set is `{exact, jaro_winkler, levenshtein, token_sort}`), so
`_fs_native_eligible()` declines the matchkey and the native lane silently runs
numpy - identical to the numpy lane. `--fs-basic-scorers` rewrites any field scorer
outside the kernel set to `jaro_winkler`, which makes the matchkey native-eligible
so the two lanes form a real matched numpy-vs-native pair that actually exercises
the Rust kernel. It also makes the FS model JaroWinkler-comparable to Splink's own
JaroWinkler FS. The rewrite runs before the eligibility telemetry below, so the
counts reflect the config actually scored; the rewritten `(field, old_scorer)`
pairs are recorded on `fs_basic_scorers_rewritten`.

**Proof-of-execution field (`fs_native_eligible_matchkeys`).** The global gate is
NOT sufficient proof that a `gm_probabilistic_native` row actually ran the kernel:
native FS is decided **per matchkey** by `probabilistic._fs_native_eligible(mk)`,
which declines any matchkey with a non-native scorer (an `ensemble` NE field -
autoconfig's default for unknown columns) or a `tf_adjustment` field to numpy.
Since these lanes run `auto_configure_probabilistic_df` (which can add NE/TF
fields), the native lane can pass the global gate yet score some matchkeys on
numpy. The runner counts `_fs_native_eligible` over the **resolved** matchkeys
after autoconfig and records `fs_native_eligible_matchkeys` / `fs_matchkeys_total`
- computed entirely from the resolved config, touching **nothing** in the scoring
path. `gm_probabilistic` (env `=0`) must show
`fs_native_eligible_matchkeys=0`; if it is ever non-zero the run is invalid.
`gm_probabilistic_native` reports its actual eligible/total split, so the doc can
state honestly "N of M matchkeys FS-native-eligible".

## The 2 shapes

Both shapes share the streaming, bounded-memory, pooled-fancy-index generator
(rows flushed one row-group at a time via `pyarrow.ParquetWriter`; all string
columns produced by vectorised indexing into small precomputed pools; fixed
seed). Each writes a records parquet + a `{record_id, cluster_id}` truth parquet.
Select with `--shape` (generator + all runners); `shapes.py` is the single source
of every shape fact (schema, blocking key, cardinality `C`, GM config, Splink
settings).

### person
`record_id int64, first_name str, surname str, dob str(YYYY-MM-DD), postcode str, city str`.
Duplicates: surname/first-name single-char typos + occasional nulls on weaker
fields; strong fields (dob, postcode) mostly agree. Blocks on `postcode`
(`N_POSTCODE=200_000`), so at 100M rows blocks are ~500 rows each - bounded, which
is why 100M person is the expected single-node OOM ceiling rather than a blow-up.

### biblio
`record_id int64, title str, authors str, venue str, year str(YYYY)`.
Pools: title-word pool, author-surname pool, **venue pool `N_VENUE~3_500`**, year
range (`~60`). Duplicates carry realistic bibliographic variation on the
**scored** (non-blocking) fields: title typos + word drop, author-list
abbreviation ("Jane Smith" -> "J. Smith") + order permutation, occasional null
year.

**`(venue, year)` is the block key and is kept STABLE on duplicates** (a null-year
duplicate simply doesn't block, mirroring person's ~5% null postcode). Realistic
ER does see venue abbreviation, but on a *blocking* field it would just relocate
the recall trap; venue is held canonical and the discriminative work lives in
title + author scoring - the mirror of person (stable `postcode` block key,
discriminative name scoring).

**Blocking-key cardinality `C` must be large enough that per-block size
(`~ N/C`) stays bounded at the target N.** This is not "linear-in-N blocking"; it
is `O(N^2/C)` with a large `C`. The trap is a *small* `C`: a `(venue, year)` key
with only a few hundred venues x ~60 years is ~18K distinct blocks, giving
~5,500 rows/block at 100M - wedging biblio far below the person ceiling. Fix:
`N_VENUE~3_500` so `C ~ 3_500 x 60 ~ 210K`, matching person's `C` so the
block-size-vs-N curves and the two OOM ceilings are comparable. The generator's
projection guard extrapolates the max block size to the target N
(`~ N / C`, skew-scaled) and rejects a too-small-`C` key **at design time,
regardless of the smoke scale** (`--check-block-size <target_rows>`; a 100K smoke
check says nothing about 100M).

## Metrics recorded per datapoint

Standardized result schema (`(shape, lane, scale)`-keyed), superset of what each
runner emits:

- **status**: `ok | OOM | timeout | refused | skipped | error | fixture_failed`.
- **wall**: `dedupe_wall_seconds` end-to-end. Splink also records
  `train_/predict_/cluster_wall_seconds`; GoldenMatch records `load_wall_seconds`.
- **peak_rss_mb**: per-process high-water mark (Linux `ru_maxrss`).
- **scored_pairs**: candidate pairs actually scored (blocking aggressiveness is
  visible, not hidden) + derived **pairs/sec**.
- **cluster_count** (total resolved entities incl. singletons, matching Splink's
  `count(distinct cluster_id)`) + **multi_member_clusters**.
- **accuracy** (`evaluate.py`, identical code both engines): pairwise
  precision/recall/F1 + confusion (TP/FP/FN/TN), and B-cubed P/R/F1. Computed from
  a DuckDB contingency table - no pair materialization, so it stays
  memory-bounded at 25M/100M.
- **lane telemetry** (GoldenMatch): `native_loaded`, `native_block_scoring`,
  `fs_native_requested`, `fs_native_symbol_present`, `fs_native_gate`, and the
  per-matchkey `fs_native_eligible_matchkeys` / `fs_matchkeys_total` proof above.

### Reproducibility header

`bench_results.json` is `{"header": {...}, "results": [...]}` (an object, not a
bare list). The header carries `run_timestamp` (orchestrator-start wall clock, the
merge tiebreak), `goldenmatch_version`, pinned `splink_version`, `git_sha`,
`runner_label`, `cpu_count`, `total_ram_gb`, `dupe_rate`, `threshold`, `seed`.

## Sharded dispatch plan

6 lanes x 2 shapes x 5 scales is up to 60 datapoints; the 25M/100M tiers can each
run minutes-to-hours per lane and a full matrix exceeds the single 600-minute job
cap. Per-datapoint timeouts are lowered so no single point can consume a whole job
(a point hitting its cap is recorded `timeout` - a legitimate envelope result,
same as `OOM`), and the heavy tiers are dispatched at coarse lane granularity.

The workflow runs `person` and `biblio` as two parallel matrix jobs
(`fail-fast: false`), each uploading `er-headtohead-<shape>-<run_tag>`. Everything
is selectable (`lanes`, `shapes`, `scales`, `dupe_rate`, `threshold`, `runner`,
`run_tag`), so a full matrix splits across dispatches **by scale band AND by
lane** without losing comparability (results key on the stable
`(shape, lane, scale)` triple):

1. **Dispatch A** - `shapes=person biblio`, `scales=100000 1000000`, all 6 lanes.
   Cheap band; surfaces a bad biblio fixture early, well under the cap.
2. **Dispatch B** - `shapes=person biblio`, `scales=5000000`, all 6 lanes. 5M is
   split out because FS-numpy at 5M is slow and 6 lanes at 5M could crowd the cap.
3. **Dispatch C..N** - `shapes=person biblio`, `scales=25000000 100000000`, **one
   or two lanes per dispatch**. Heavy band; the 100M GM bucket lanes are expected
   OOM ceilings, and Splink 100M may run for hours or OOM its own DuckDB temp.
4. **Merge** all dispatch artifacts -> final doc.

Splink is installed (pinned) only when the lane set contains `splink` OR
`gm_converted_splink`.

## The merge step

`merge_results.py` unions any set of run artifacts. Results are keyed by
`(shape, lane, scale)`; on a key collision the entry from the artifact with the
**later `run_timestamp` wins** (deterministic last-writer-wins - not artifact
order or file mtime). Every distinct header is kept under `runs[]`, so the final
doc can show that different bands ran at different `git_sha`s / timestamps.

The dependent `merge` job downloads **one directory per artifact** (NO
`merge-multiple`, so the two shapes' `bench_results.json` files don't clobber) and
`merge_results.py` globs `*/bench_results.json`. For a multi-dispatch run, re-run
it over the downloaded artifacts:

```bash
python scripts/bench_er_headtohead/merge_results.py \
    --artifacts-dir <dir-of-artifact-dirs> --out-json merged.json --out-md merged.md
```

## Running it

CI (recommended - the heavy tiers need 64 GB):

> GitHub -> Actions -> **bench-er-headtohead** -> Run workflow.

Locally (small scales; native must be built for the GM lanes to run
native-required, else pass `--allow-pure-python`):

```bash
# generator needs only numpy + pyarrow
python scripts/bench_er_headtohead/generate_fixture.py --rows 100000 --shape person \
    --out /tmp/b/bench_person_100000.parquet \
    --ground-truth /tmp/b/bench_person_100000.truth.parquet

# lane x shape x scale sweep (build native first: python scripts/build_native.py)
python scripts/bench_er_headtohead/orchestrate.py \
    --scales 100000 1000000 --shapes person biblio \
    --lanes gm_hand_built gm_probabilistic gm_zeroconfig \
    --workdir /tmp/bench_er
```

## Files

| File | Role |
|---|---|
| `shapes.py` | Single source of every shape fact: schema, blocking key + cardinality `C`, GM hand_built config, Splink settings. Imported by the generator + all runners. |
| `generate_fixture.py` | Streaming, bounded-memory generator; `--shape {person,biblio}`; `--check-block-size` projection self-check. |
| `run_goldenmatch.py` | One GM datapoint; `--shape`; hand_built config from `shapes.py`; FS-native eligibility telemetry. |
| `run_splink.py` | One Splink datapoint; `--shape` selects settings from `shapes.py`; records `splink_version`. |
| `run_gm_converted.py` | One converted-Splink datapoint over a fixture (`from_splink` on the shape's Splink settings). |
| `evaluate.py` | Engine-agnostic accuracy: pairwise P/R/F1 + confusion + B-cubed, via DuckDB contingency table. |
| `orchestrate.py` | Lane x shape x scale sweep; subprocess-per-datapoint; `{header, results}` aggregate; per-shape/lane markdown. |
| `merge_results.py` | Union N dispatch artifacts (later-timestamp-wins); render final tables. |
| `../../.github/workflows/bench-er-headtohead.yml` | CI lane (64 GB runner shape matrix, builds native, pinned splink, merge job). |

## Honest caveats (carried into the results doc)

- **Blocking asymmetry is reported, not hidden.** GoldenMatch's bucket path does
  single-key blocking (its best single key per shape, ~0.94 pair coverage on
  person `postcode`); Splink unions rules (~0.99). The recall gap is a genuine
  property of each engine's fast path, surfaced via the accuracy + `scored_pairs`
  columns.
- **Biblio's single blocking key is composite `(venue, year)`, kept STABLE under
  corruption.** Both engines' biblio recall is therefore capped by `(venue, year)`
  coverage; the doc states biblio's coverage number explicitly rather than folding
  it into the person-shape asymmetry, since the two shapes cap recall differently.
- **FS-native vs numpy may differ marginally** (discrete comparison levels amplify
  tiny rapidfuzz float differences at exact thresholds) - hence two lanes, not one.
- **The Splink spec is idiomatic, not maximally tuned**, and fixed per shape. A
  Splink expert could squeeze more; the spec is "reasonable, not optimal".
- **100M GM bucket lanes are expected OOM** on a single 64 GB box - that ceiling
  is the headline single-node result, not a failure.
