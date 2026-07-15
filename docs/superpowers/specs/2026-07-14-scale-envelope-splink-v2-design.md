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
timeout ladder in §7.3). Results are keyed by the triple `(shape, lane, scale)`
and flushed to `bench_results.json` after every datapoint, so a late
OOM/timeout never discards earlier points.

**Hard implementation constraint — lane env is applied per subprocess, never to
the orchestrator's own environment.** Each child is spawned with
`subprocess.run(cmd, env={**os.environ, **lane.env})`. The orchestrator MUST NOT
`os.environ.update(lane.env)` — that would leak `GOLDENMATCH_FS_NATIVE=0` from the
`gm_probabilistic` lane into the following `gm_probabilistic_native` / `gm_zeroconfig`
datapoints in the same process and silently invalidate them (the exact failure the
numpy-lane force guards against, reintroduced via env plumbing).

The lane registry (default full set of 6):

| Lane name | script | mode | env | Proves |
|---|---|---|---|---|
| `splink` | run_splink.py | — | — | Fellegi-Sunter reference engine |
| `gm_hand_built` | run_goldenmatch.py | hand_built | — | GM fast weighted bucket+native path (speed reference) |
| `gm_probabilistic` | run_goldenmatch.py | probabilistic | `GOLDENMATCH_FS_NATIVE=0` | EM-trained FS, **numpy fallback** — like-for-like vs Splink FS |
| `gm_probabilistic_native` | run_goldenmatch.py | probabilistic | `GOLDENMATCH_FS_NATIVE=1` | same, **Rust FS kernel** (reference-mode default made explicit) |
| `gm_zeroconfig` | run_goldenmatch.py | zeroconfig | — | zero-effort controller vs a tuned Splink spec |
| `gm_converted_splink` | run_gm_converted.py | — | — | GM running Splink's own spec auto-converted via `from_splink` |

**FS-native is default-ON** (`_fs_native_enabled()` in `probabilistic.py` returns
native whenever the env is unset — Rust-is-the-reference mode, confirmed in the
package CLAUDE.md). So the env is set **explicitly on both FS lanes**, not left to
default: the numpy lane MUST force `GOLDENMATCH_FS_NATIVE=0`, and the native lane
sets `=1` to make the default explicit. Without `=0` on `gm_probabilistic` it
would silently run native and the two FS lanes would be native-vs-native, gutting
the "numpy fallback vs native kernel" comparison. The `zeroconfig` lane inherits
whatever the controller picks (default native) and does not force the env.

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
(to compose 1-4 author strings), **venue pool sized `N_VENUE≈3_500`**, year range
(`~60`). Duplicates carry realistic bibliographic variation on the **scored**
(non-blocking) fields:
- title single-char typos + occasional word drop,
- author-list abbreviation ("Jane Smith" -> "J. Smith") and order permutation,
- occasional null year.

`venue` and `year` are the block key and are kept **stable** on duplicates (a
null-year duplicate simply doesn't block, mirroring person's ~5% null postcode).
Realistic bibliographic ER *does* see venue abbreviation, but on a *blocking*
field it would just relocate the recall trap below; venue is held canonical, and
the discriminative work lives in title + author scoring — the mirror of person
(stable `postcode` block key, discriminative name scoring).

**Blocking-key cardinality `C` must be large enough that per-block size (`≈ N/C`)
stays bounded at the target N.** Both shapes use a fixed-cardinality key — this is
not "linear-in-N blocking," it is `O(N²/C)` with a large `C`. Person blocks on
`postcode` with `N_POSTCODE=200_000` (`generate_fixture.py`), so at 100M rows
blocks are ~500 rows each: bounded, which is exactly why 100M person is the
expected single-node OOM ceiling rather than a blow-up. The trap is a *small* `C`:
a `(venue, year)` key with only a few hundred venues x ~60 years is ~18K distinct
blocks, giving ~5,500 rows/block at 100M (`pairs ≈ N²/(2·18K)`, ~1.7e10 at 25M) —
that wedges biblio far below the person ceiling and is NOT a fair envelope.

Fix: the biblio bucket single-key is the **`(venue, year)` composite** with the
venue pool sized `N_VENUE≈3_500`, so `C ≈ 3_500 x 60 ≈ 210K` — matching person's
`C` so the block-size-vs-N curves, and the two OOM ceilings, are comparable.
Splink unions that `(venue, year)` key + `(author_surname, year)`. `N_VENUE` is a
named generator constant so the projection guard has a concrete `C` to assert.
(An earlier draft used a `title_token` block key; blocking on `venue` uses real
columns, needs no first-token transform, and makes the "block key stays stable
under corruption" guarantee trivial — venue is simply never corrupted.)

**The block-size guard must extrapolate, not assert-at-smoke-scale.** A 100K smoke
check (~6 rows/block) says nothing about 100M. The generator computes each shape's
blocking-key cardinality `C` and asserts the **projected** max block size at the
target N (`≈ N / C`, scaled by the key's skew factor) stays under a ceiling —
catching a too-small-`C` (effectively N²) key at design time regardless of scale.

## 6. Shape config registries

The probabilistic / zeroconfig / converted lanes are **data-driven** — they
infer configuration from the data, so they run on either shape unchanged. Only
two lanes need shape-specific configuration:

- **`gm_hand_built`** — `run_goldenmatch.py` currently hardcodes a person config
  (postcode blocking, first_name/surname/dob weighted fields). Add a
  `_HAND_BUILT_BY_SHAPE` registry: `person` (existing config, extracted verbatim)
  and `biblio` (bucket on composite `(venue, year)`, weighted jaro_winkler on
  title/authors — the discriminative fields). Selected by a new `--shape` flag.
- **`splink`** — `run_splink.py`'s `--input` path hardcodes
  `_default_person_settings`. Add a `_SETTINGS_BY_SHAPE` registry mirroring the
  person spec and a biblio spec (blocking union of `(venue, year)` +
  `(author_surname, year)`; comparisons: JaroWinkler on title/authors, exact on
  venue, DL on year). Selected by `--shape`.
- **`gm_converted_splink`** — `run_gm_converted.py` builds the shape's Splink
  settings dict (reusing `run_splink.py`'s per-shape builder), serializes it with
  `SettingsCreator.create_settings_dict(sql_dialect_str="duckdb")`, runs it
  through `from_splink`, and dedupes the fixture with the converted config. It
  imports the builder, never copies it, so the converted lane always tracks the
  exact spec the `splink` lane ran. **This is a full new runner, not a light
  wire-up of the existing `run_converted_splink.py`** — that reference module is
  dataset-keyed (`--dataset`, `datasets.load_dataset`) and returns a parity-gate
  exit code, whereas the sweep lane must be **fixture-keyed** (`--input`),
  **shape-aware** (`--shape`), emit the **standard `(shape,lane,scale)` result
  JSON** (status / wall / peak_rss / scored_pairs / cluster_count / accuracy
  pred-parquet), and obey the same subprocess contract as every other runner
  (`--input/--rows/--out/--pred-out/--threshold`). It reuses
  `run_converted_splink.py`'s conversion logic but is budgeted as its own runner.
  **Splink must be installed for this lane** (it builds a real `SettingsCreator`),
  so the workflow installs Splink when the lane set contains `splink` OR
  `gm_converted_splink` — see §7.

## 7. Orchestration, sharding, and merge

### 7.1 The runtime problem
6 lanes x 2 shapes x 5 scales = up to 60 datapoints. The 25M and 100M tiers can
each run many minutes to hours per lane (25M person hand_built ~6.5 min; FS numpy
at 25M is far slower; Splink-DuckDB at 100M may complete via spill but slowly).
A single job will exceed the 600-minute cap.

### 7.2 Strategy
- **Workflow `strategy.matrix` over shape** — `person` and `biblio` run as two
  parallel jobs on two 64 GB runners, `fail-fast: false`, each uploading its own
  artifact `er-headtohead-<shape>-<run_tag>`.
- **Everything selectable** — `lanes`, `shapes`, `scales`, `dupe_rate`,
  `threshold`, `runner`, `run_tag`. A full matrix can be split across several
  dispatches **by scale band AND by lane** (see §7.3) without losing
  comparability, because results are keyed by the stable `(shape, lane, scale)`
  triple.
- **`bench_results.json` is an object, not a list.** To carry the per-run
  reproducibility header (§8) it becomes
  `{"header": {...}, "results": [ {shape, lane, scale, ...}, ... ]}`. The header
  includes a `run_timestamp` (wall-clock at orchestrator start; Python `time`, not
  a workflow-script clock) which is the merge tiebreak key.
- **`merge_results.py`** unions any set of run artifacts. Merge semantics are
  explicit: results are keyed by `(shape, lane, scale)`; on a key collision the
  entry from the artifact with the **later `run_timestamp` wins** (deterministic
  last-writer-wins, not artifact-order- or mtime-dependent). Merge keeps every
  distinct header under a `runs[]` list so the final doc can show that different
  bands ran at different `git_sha`s / timestamps. It renders the final `summary.md`
  + envelope tables and writes `$GITHUB_STEP_SUMMARY`.
- A dependent **merge job** runs it automatically for a single dispatch
  (downloads all shape artifacts, merges, uploads `er-headtohead-merged-<run_tag>`).
  For a multi-dispatch run, `merge_results.py` re-runs over the downloaded
  artifacts (locally or via a `merge-only` dispatch mode).
- **Artifact download layout must not collide.** Each shape/band job uploads its
  own `bench_results.json` at the same relative path, so the merge job downloads
  **one directory per artifact** (no `merge-multiple`) and `merge_results.py`
  globs `*/bench_results.json` — otherwise the two shapes' files clobber.

### 7.3 Runtime budget vs the 600-minute cap
The job cap is `timeout-minutes: 600` and datapoints run **sequentially** within a
job; the per-datapoint timeout ladder currently tops out at 4h (25M) / 8h (100M).
A single job doing `scales=25M 100M` x all 6 lanes for one shape can therefore
blow the cap or let one hung 100M lane (8h) starve the other 11 datapoints. So:
- **Per-datapoint timeouts are lowered** so no single datapoint can consume the
  whole job (target: the sum of a job's datapoint timeouts fits under ~560 min with
  headroom). A datapoint that hits its (lower) cap is recorded as `timeout` — a
  legitimate "did not finish in budget" envelope result, same as `OOM`.
- **The heavy tiers are dispatched at coarse lane granularity.** Because `lanes` is
  a workflow input, the heavy band is split per-lane (or per lane-pair) so each
  job holds only a few long datapoints and stays under the cap.

Recommended dispatch plan (documented in the README):
1. Dispatch A: `shapes=person biblio`, `scales=100000 1000000`, all 6 lanes
   (cheap band; surfaces a bad biblio fixture early, well under the cap).
2. Dispatch B: `shapes=person biblio`, `scales=5000000`, all 6 lanes — 5M is split
   OUT of the cheap band because FS-numpy at 5M is slow (§7.1) and 6 lanes at 5M
   could otherwise crowd the cap; its per-datapoint timeout is sized to the
   measured FS-numpy 5M wall so a legitimately-slow point isn't cut to `timeout`.
3. Dispatch C..N: `shapes=person biblio`, `scales=25000000 100000000`, **one or two
   lanes per dispatch** (heavy band; the 100M GM bucket lanes are expected OOM
   ceilings, Splink 100M may run for hours or OOM its own DuckDB temp).
4. Merge all dispatch artifacts -> final doc.

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
  `fs_native_requested`, `fs_native_symbol_present`, and — new — a per-matchkey
  FS-dispatch signal. `fs_native_gate` = the global gate `_fs_native_enabled()`
  (env + symbol). `fs_native_eligible_matchkeys` / `fs_matchkeys_total` = counted
  by the runner calling `probabilistic._fs_native_eligible(mk)` over the
  **resolved** matchkeys after `auto_configure_probabilistic_df`. The global gate
  is NOT sufficient proof: native FS is decided **per matchkey** by
  `_fs_native_eligible` (`probabilistic.py:2264`), which declines any matchkey with
  a non-native scorer (an `ensemble` NE field — autoconfig's default for unknown
  columns) or a `tf_adjustment` field to numpy. Since these lanes run
  `auto_configure_probabilistic_df` under FS-autoconfig-v2 (which can add NE/TF
  fields), `gm_probabilistic_native` can pass the global gate yet score some
  matchkeys on numpy. The per-matchkey count is the honest proof, and — crucially —
  **it is computed in the runner from the resolved config, touching NOTHING in the
  scoring path** (no hot-loop instrumentation, no change to the reference kernel or
  its parity gates). `gm_probabilistic` (env `=0`) must show
  `fs_native_eligible_matchkeys=0` (`_fs_native_enabled()` short-circuits to False);
  if it is ever non-zero the run is invalid. `gm_probabilistic_native` reports its
  actual eligible/total split, so the doc can state honestly "N of M matchkeys
  FS-native-eligible."

### Reproducibility header (one per run, top-level in `bench_results.json`)
`bench_results.json` is `{"header": {...}, "results": [...]}` (not a bare list —
see §7.2). Header fields: `run_timestamp` (orchestrator-start wall clock, the
merge tiebreak), `goldenmatch_version`, **pinned** `splink_version` (recorded from
`splink.__version__`), `git_sha`, `runner_label`, `cpu_count`, `total_ram_gb`,
`dupe_rate`, `threshold`, `seed`. The workflow pins the Splink version
(`uv pip install splink==<pinned>`), so the reference engine is fixed run to run.
The merge step (§7.2) preserves every band's header under `runs[]`.

## 9. Deliverables (file-by-file)

1. `scripts/bench_er_headtohead/orchestrate.py` — refactored to the lane model,
   `(shape, lane, scale)` keys, per-datapoint flush, `{header, results}` object,
   lowered per-datapoint timeouts. **`render_markdown` must be rewritten** — it
   currently hardcodes `by_rows[rows].get("goldenmatch")` / `.get("splink")` and
   sorts on `(engine, rows)`; the 6-lane x 2-shape output needs one section per
   shape, one row per `(lane, scale)`, `splink` as the fixed reference column, and
   head-to-head deltas computed per GM lane vs `splink`. Non-trivial; called out
   so it isn't discovered mid-implementation.
2. `scripts/bench_er_headtohead/merge_results.py` — new; union + render.
3. `scripts/bench_er_headtohead/generate_fixture.py` — `--shape person|biblio`.
4. `scripts/bench_er_headtohead/run_goldenmatch.py` — `--shape` + hand_built
   registry; FS-native env respected.
5. `scripts/bench_er_headtohead/run_splink.py` — `--shape` + settings registry;
   record `splink.__version__`.
6. `scripts/bench_er_headtohead/run_gm_converted.py` — new; converted-Splink lane
   over a fixture.
7. `.github/workflows/bench-er-headtohead.yml` — lane/shape/scale inputs, shape
   matrix (`fail-fast: false`), **pinned** splink (installed when lanes contain
   `splink` OR `gm_converted_splink`), merge job (one-dir-per-artifact download,
   no `merge-multiple`), `run_tag`, and `ARROW_DEFAULT_MEMORY_POOL=system` at the
   workflow `env:` level (this lane `pip install`s splink/duckdb from PyPI and runs
   the pipeline — the documented mimalloc-SIGSEGV guard).
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
  non-null `dedupe_wall_seconds`, and an `accuracy` block with finite F1. **The
  ok-smoke runs the `gm_hand_built` lane with `--allow-pure-python`** — the normal
  pytest lane does NOT run `build_native.py`, and `gm_hand_built` with the default
  `--require-native` deliberately *refuses* without the native ext, so a
  native-required smoke would always fail. This is a plumbing test (does every
  lane run end-to-end and get scored), not an accuracy-threshold test.
- **Native-gate honesty (separate case)**: assert `gm_hand_built` **without**
  `--allow-pure-python` refuses (non-zero exit, no `ok` result) when the native
  runtime is absent — the existing guard, kept under test. This and the ok-smoke
  are distinct cases; they don't contradict because the ok-smoke passes the flag.
- **FS-lane telemetry assertion**: at smoke scale assert `gm_probabilistic`
  reports `fs_native_eligible_matchkeys=0` (the `=0` force works even without the
  native ext). The `gm_probabilistic_native` -> `fs_native_eligible_matchkeys > 0`
  half is **conditional on `native_loaded`**: the normal pytest lane doesn't build
  native, so that assertion is skipped there and is exercised in the bench workflow
  (which runs `build_native.py`). This is the §8 proof-of-execution — asserted on
  per-matchkey `_fs_native_eligible` from the resolved config, not the global gate.
- **Merge test**: `merge_results.py` over two synthetic partial-artifact JSONs
  (differing `run_timestamp`) asserts union + later-timestamp-wins per
  `(shape, lane, scale)` key, and that the `{header, results}` object shape and
  `runs[]` header-preservation hold.
- **Generator block-size guard**: assert the **projected** max block size at the
  target N (`≈ N / cardinality`, per §5.2) stays under the ceiling for each shape
  — extrapolated, NOT asserted at smoke scale (a fixed-cardinality N^2 key passes
  a smoke-scale check but fails the projection).
- **Eval-join dtype**: the autoconfig/probabilistic/converted GM lanes write a
  STRING `record_id` pred parquet while the generator truth is INT64. DuckDB casts
  implicitly and joins correctly (verified), but the join in `evaluate.py` (or the
  runners) casts `record_id` explicitly on both sides to match the parity gate's
  posture — locked by a test so an implicit-cast change can't silently break it.
- The real scale run is `workflow_dispatch` only (never on PR CI); the smoke
  tests are what gate the harness PR.

## 11. Caveats carried into the results doc

- **Blocking asymmetry is reported, not hidden.** GoldenMatch's bucket path does
  single-key blocking (its best single key per shape, ~0.94 pair coverage on
  person `postcode`); Splink unions rules (~0.99). The recall gap is a genuine
  property of each engine's fast path, surfaced via the accuracy columns and the
  `scored_pairs` column.
- **Biblio's single blocking key is composite `(venue, year)`, kept STABLE under
  corruption** (§5.2). Both engines' biblio recall is therefore capped by
  `(venue, year)` coverage; the doc states biblio's coverage number explicitly
  rather than folding it into the generic person-shape asymmetry above, since the
  two shapes cap recall differently.
- **FS-native vs numpy may differ marginally.** FS's discrete comparison levels
  amplify tiny rapidfuzz float differences at exact thresholds, so
  `gm_probabilistic` (numpy) and `gm_probabilistic_native` are reported as two
  separate lanes rather than one silently substituting the other.
- **Splink spec is idiomatic, not maximally tuned**, and fixed per shape.
- **100M GM bucket lanes are expected OOM** on a single 64 GB box — that ceiling
  is the headline single-node result, not a failure.

## 12. Open risks

- **Biblio at 25M/100M is the most expensive, least-proven part.** The person
  generator is battle-tested to 100M; the biblio generator is new, and its
  blocking-key cardinality is the make-or-break variable (§5.2 — a fixed-cardinality
  key is a silent N^2 trap). Mitigation: the **projection-based** block-size guard
  (not smoke-scale), the `(venue, year)` key with `N_VENUE≈3_500` sized to mirror
  person's ~200K distinct blocks, a mandatory biblio smoke datapoint, and the dispatch plan
  running the fast band first so a bad biblio fixture surfaces cheaply before any
  heavy-tier runner-hours are spent.
- **Splink at 100M may run for hours or OOM its own DuckDB temp.** Handled by the
  timeout ladder + `OOM`/`timeout` status; a non-completing Splink 100M is itself
  a recorded envelope result.
- **from_splink coverage on the biblio spec.** If a biblio comparison type isn't
  yet convertible, the converted lane records the `ConversionReport` findings and
  a `refused`/`error` status rather than crashing the sweep; the other 5 lanes
  still produce the biblio envelope.
- **Runner availability.** Larger GitHub-hosted runners can queue; the workflow
  keeps `runner` selectable so the run can move to whichever 64 GB label is free.
