# Scale-envelope head-to-head v2 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `scripts/bench_er_headtohead/` harness into a reproducible 6-lane x 2-shape Splink-vs-GoldenMatch scale-envelope benchmark that runs on the 64 GB GitHub runner and records wall / peak RSS / scored-pairs / clusters / pairwise F1 / B-cubed.

**Architecture:** The orchestrator's "engine" becomes a "lane" (`{name, script, mode, env}`); the sweep iterates lanes x shapes x scales, one isolated subprocess per datapoint, results keyed by `(shape, lane, scale)` and flushed after every point. A new bibliographic fixture shape joins the existing person shape; two GoldenMatch lanes (`gm_probabilistic` numpy-forced, `gm_probabilistic_native`) plus a new converted-Splink lane are added. A merge step unions multi-dispatch artifacts into the final tables.

**Tech Stack:** Python 3.11+, numpy, pyarrow, polars, DuckDB, Splink 4.x (pinned), GoldenMatch 3.3.0 (workspace), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-14-scale-envelope-splink-v2-design.md` — read it first. Section references below (§N) point into it.

**Worktree:** `D:\show_case\goldenmatch\.worktrees\scale-envelope-v2` (branch `bench/scale-envelope-v2`, based on `origin/main` @ 3.3.0). All paths below are repo-relative.

**Windows/local test note:** run Python via the main venv with the worktree on `PYTHONPATH`, and set `POLARS_SKIP_CPU_CHECK=1` + `PYTHONIOENCODING=utf-8` (per `reference_polars_wmi_hang_windows` / `reference_py_worktree_test_native_skew`). Native is NOT built locally — GoldenMatch lanes run pure-Python locally via `--allow-pure-python`; the real native run is CI-only. Example prefix used throughout:
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH=packages/python/goldenmatch \
  <repo>/.venv/Scripts/python.exe <cmd>
```

**Commit discipline:** every task ends in a commit. Use `git add <exact paths>`. Do NOT stage unrelated files.

---

## File structure (what each file owns after this plan)

| File | Responsibility |
|---|---|
| `scripts/bench_er_headtohead/generate_fixture.py` | Streaming bounded-memory fixture generator; `--shape {person,biblio}`; person + biblio pool builders; projection block-size self-check helper. |
| `scripts/bench_er_headtohead/shapes.py` (new) | Single source of shape metadata: schema, blocking-key fields, blocking cardinality `C`, GM hand_built config builder, Splink settings builder. Imported by the generator + both GM runners + the Splink runner so no shape fact is defined twice. |
| `scripts/bench_er_headtohead/run_goldenmatch.py` | One GM datapoint; `--shape`; hand_built config from `shapes.py`; records `fs_native_eligible_matchkeys` / `fs_matchkeys_total`. |
| `scripts/bench_er_headtohead/run_splink.py` | One Splink datapoint; `--shape` selects settings from `shapes.py`; records `splink_version`. |
| `scripts/bench_er_headtohead/run_gm_converted.py` (new) | One converted-Splink datapoint over a fixture; builds the shape's Splink settings, `from_splink`-converts, dedupes; standard result JSON. |
| `scripts/bench_er_headtohead/orchestrate.py` | Lane x shape x scale sweep; `(shape,lane,scale)` keys; per-subprocess env; `{header,results}` object; lowered timeouts; rewritten `render_markdown`. |
| `scripts/bench_er_headtohead/merge_results.py` (new) | Union N run artifacts (later-`run_timestamp`-wins), render final tables. |
| `.github/workflows/bench-er-headtohead.yml` | `lanes`/`shapes`/`scales`/`run_tag` inputs, shape matrix, pinned Splink, merge job. |
| `scripts/bench_er_headtohead/test_scale_envelope.py` (new) | Smoke + guard tests (all lanes, native gate, FS telemetry, merge, block-size projection, eval-join dtype). |
| `scripts/bench_er_headtohead/README.md` | Refreshed lanes/shapes/dispatch/merge docs. |

**Design rule:** `shapes.py` is the single source of every shape fact. If any task is tempted to hardcode a column name, blocking field, or config in a runner, it goes in `shapes.py` instead. DRY.

---

## Phase 0: Baseline

### Task 0: Confirm the harness imports and the person generator still runs

**Files:**
- Read only: `scripts/bench_er_headtohead/*.py`

- [ ] **Step 1: Generate a tiny person fixture on the current (unmodified) harness**

Run:
```
POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 <repo>/.venv/Scripts/python.exe \
  scripts/bench_er_headtohead/generate_fixture.py --rows 2000 --dupe-rate 0.2 \
  --out .bench_tmp/p_2000.parquet --ground-truth .bench_tmp/p_2000.truth.parquet
```
Expected: prints `[generate] 2,000 rows / ... clusters ...`, writes both parquets.

- [ ] **Step 2: Confirm evaluate.py scores a trivial self-prediction**

This is a read-only baseline sanity check; no code change. If it runs, the toolchain (numpy/pyarrow/duckdb) is wired. No commit (nothing changed).

---

## Phase 1: `shapes.py` + biblio generator

### Task 1: Create `shapes.py` with person shape metadata (extracted, not rewritten)

**Files:**
- Create: `scripts/bench_er_headtohead/shapes.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Write the failing test**

```python
# test_scale_envelope.py
import importlib.util, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod

def test_person_shape_metadata():
    shapes = _load("shapes")
    s = shapes.SHAPES["person"]
    assert s.name == "person"
    assert s.columns == ["record_id", "first_name", "surname", "dob", "postcode", "city"]
    assert s.blocking_fields == ["postcode"]
    assert s.blocking_cardinality == 200_000   # C, for the projection guard
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py::test_person_shape_metadata -v`
Expected: FAIL (`shapes.py` doesn't exist).

- [ ] **Step 3: Create `shapes.py` with the `Shape` dataclass + person entry**

```python
"""Single source of truth for every fixture-shape fact used by the head-to-head
bench: schema, blocking key, blocking cardinality C (for the projection guard),
and the GoldenMatch hand_built config + Splink settings builders. Every runner
and the generator import from here so no shape fact is defined twice."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Shape:
    name: str
    columns: list[str]
    blocking_fields: list[str]      # composite bucket key for GM hand_built
    blocking_cardinality: int       # C: distinct block count (fixed-cardinality key)
    # builders are attached below to avoid importing goldenmatch/splink at module load
    gm_hand_built: Callable         # (threshold) -> GoldenMatchConfig
    splink_settings: Callable       # (s: dict) -> (SettingsCreator, training_rules)

# builder functions are defined lower in this file; SHAPES is assembled at the end.
```
Add a `_person_gm_hand_built(threshold)` that returns the EXACT config currently
inline in `run_goldenmatch.py:407-428` (bucket, n_buckets=256, postcode blocking,
first_name/surname/dob weighted jaro_winkler, `rerank=False`). Add
`_person_splink_settings(s)` copied from `run_splink.py::_default_person_settings`.
Assemble `SHAPES = {"person": Shape("person", [...], ["postcode"], 200_000, _person_gm_hand_built, _person_splink_settings)}`.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py::test_person_shape_metadata -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_er_headtohead/shapes.py scripts/bench_er_headtohead/test_scale_envelope.py
git commit -m "feat(bench): shapes.py single-source shape registry (person)"
```

### Task 2: Add biblio shape metadata + a projection block-size guard helper

**Files:**
- Modify: `scripts/bench_er_headtohead/shapes.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_biblio_shape_metadata():
    shapes = _load("shapes")
    s = shapes.SHAPES["biblio"]
    assert s.columns == ["record_id", "title", "authors", "venue", "year"]
    assert s.blocking_fields == ["venue", "year"]
    # N_VENUE (~3500) x ~60 years, per spec 5.2 (C ~ 210K, mirrors person)
    assert 150_000 <= s.blocking_cardinality <= 260_000

def test_projected_block_size_guard_flags_small_C():
    shapes = _load("shapes")
    # A key with only 18K distinct blocks is an N^2 trap at 100M (spec 5.2).
    assert shapes.projected_max_block_size(rows=100_000_000, cardinality=18_000) > 4_000
    # The real biblio C keeps projected block size bounded (comparable to person).
    biblio_C = shapes.SHAPES["biblio"].blocking_cardinality
    person_C = shapes.SHAPES["person"].blocking_cardinality
    assert shapes.projected_max_block_size(100_000_000, biblio_C) < \
           2 * shapes.projected_max_block_size(100_000_000, person_C)
```

- [ ] **Step 2: Run and confirm failure**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py -k "biblio_shape or projected_block" -v`
Expected: FAIL (no `biblio` entry, no `projected_max_block_size`).

- [ ] **Step 3: Implement**

Add to `shapes.py`:
```python
# Named generator constants (also imported by generate_fixture for pool sizing).
N_VENUE = 3_500
N_YEAR = 60            # ~60 distinct publication years

def projected_max_block_size(rows: int, cardinality: int, skew: float = 3.0) -> float:
    """Extrapolated max block size at target N for a fixed-cardinality uniform key,
    scaled by a skew factor (real keys aren't perfectly uniform). This is the
    design-time guard from spec 5.2 -- it does NOT depend on the smoke scale."""
    return skew * rows / max(1, cardinality)
```
Add `_biblio_gm_hand_built(threshold)` -> `GoldenMatchConfig(backend="bucket",
n_buckets=256, blocking=BlockingConfig(max_block_size=5000, skip_oversized=False,
keys=[BlockingKeyConfig(fields=["venue", "year"], transforms=["strip"])]),
matchkeys=[MatchkeyConfig(name="paper", type="weighted", threshold=threshold,
rerank=False, fields=[MatchkeyField(field="title", scorer="jaro_winkler", weight=0.6,
transforms=["lowercase"]), MatchkeyField(field="authors", scorer="jaro_winkler",
weight=0.4, transforms=["lowercase"])])])`.
Add `_biblio_splink_settings(s)`: blocking union `block_on("venue", "year")` +
`block_on("substr(authors,1,8)", "year")`; comparisons JaroWinklerAtThresholds on
`title`/`authors`, ExactMatch on `venue`, DamerauLevenshteinAtThresholds on `year`;
training rules `[block_on("venue","year")]`.
Append the biblio `Shape` to `SHAPES` with `blocking_cardinality=N_VENUE*N_YEAR`.

- [ ] **Step 4: Run tests to confirm pass**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py -k "biblio_shape or projected_block" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_er_headtohead/shapes.py scripts/bench_er_headtohead/test_scale_envelope.py
git commit -m "feat(bench): biblio shape metadata + projection block-size guard"
```

### Task 3: Add the biblio pool builder + generator branch (`--shape biblio`)

**Files:**
- Modify: `scripts/bench_er_headtohead/generate_fixture.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

Study `generate_fixture.py` first: pools are built once (`_build_pools`), the
`[base|typo]` combined-array trick gives exact-or-typo via one fancy-index, and
duplicates carry vectorised masks (`generate` loop). The biblio branch mirrors
this. **Blocking fields `venue`+`year` MUST stay stable on duplicates** (spec 5.2);
corruption applies only to `title`/`authors`.

- [ ] **Step 1: Write the failing tests**

```python
import pyarrow.parquet as pq

def test_generate_biblio_schema_and_stable_block_key(tmp_path):
    gen = _load("generate_fixture")
    out, truth = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(rows=3000, dupe_rate=0.3, out=out, truth=truth, seed=42,
                 batch=1000, shape="biblio")
    t = pq.read_table(out)
    assert t.column_names == ["record_id", "title", "authors", "venue", "year"]
    # Within every truth cluster, venue+year (block key) must be identical across
    # members -- that's the stability guarantee that avoids the recall trap.
    import polars as pl
    df = pl.read_parquet(out).join(pl.read_parquet(truth), on="record_id")
    per_cluster = df.group_by("cluster_id").agg(
        pl.col("venue").n_unique().alias("nv"), pl.col("year").n_unique().alias("ny"))
    # allow year null (dropna) but non-null year must be unique per cluster; venue always unique
    assert per_cluster["nv"].max() == 1

def test_generate_biblio_titles_actually_vary(tmp_path):
    gen = _load("generate_fixture")
    out, truth = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(3000, 0.3, out, truth, 42, 1000, shape="biblio")
    import polars as pl
    df = pl.read_parquet(out).join(pl.read_parquet(truth), on="record_id")
    multi = df.filter(pl.col("cluster_id").is_in(
        df.group_by("cluster_id").len().filter(pl.col("len") > 1)["cluster_id"]))
    # at least some multi-member clusters have >1 distinct title (corruption happened)
    assert multi.group_by("cluster_id").agg(pl.col("title").n_unique().alias("nt"))["nt"].max() > 1
```

- [ ] **Step 2: Run and confirm failure**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py -k biblio -v`
Expected: FAIL (`generate()` has no `shape` kwarg).

- [ ] **Step 3: Implement the biblio branch**

- Add `import` of `N_VENUE`, `N_YEAR` from `shapes` (path-import sibling, like `run_splink._load_datasets_module`).
- Add `BIBLIO_SCHEMA = pa.schema([("record_id", pa.int64()), ("title", pa.string()), ("authors", pa.string()), ("venue", pa.string()), ("year", pa.string())])`.
- Add `_build_pools_biblio(seed)`: title-word pool (`_syllable_pool(20_000, ...)`) + parallel typo array (`[base|typo]` trick, same as names); author-surname pool (reuse `_load_real_names` surnames); `venues = _syllable_pool(N_VENUE, ...)`; years as `str(1965..1965+N_YEAR)`.
- Add `def generate(rows, dupe_rate, out, truth, seed, batch, shape="person")`; keep the existing person body under `if shape == "person"`, add an `elif shape == "biblio"` body that mirrors the loop but: picks a stable `venue_idx`, `year_idx` per identity (broadcast to rows, NEVER offset for duplicates); composes `title` from K title-word indices where **only non-first words get the typo offset** on duplicate rows (first word kept stable is optional here since we block on venue, not title -- but keep authors/title corruption on duplicates); composes `authors` from 1-3 surname indices with abbreviation/reorder on duplicates; nulls `year` on ~5% of duplicates. Write via `BIBLIO_SCHEMA`.
- `main()` gains `--shape` (choices person/biblio, default person) threaded into `generate`.

Keep memory bounded: same batched `ParquetWriter` pattern, all columns via numpy fancy-index; no per-row Python in the hot loop beyond the small title/author string joins (acceptable at bench scales, but prefer vectorised `np.char`/object-array joins).

- [ ] **Step 4: Run tests to confirm pass**

Run: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py -k biblio -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bench_er_headtohead/generate_fixture.py scripts/bench_er_headtohead/test_scale_envelope.py
git commit -m "feat(bench): biblio fixture shape (--shape biblio), stable (venue,year) block key"
```

### Task 4: Generator projection self-check (`--check-block-size`)

**Files:**
- Modify: `scripts/bench_er_headtohead/generate_fixture.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test**

```python
def test_generator_projection_check_rejects_small_C():
    gen = _load("generate_fixture")
    # A hypothetical biblio-with-tiny-venue C would project a huge block at 100M.
    ok, projected = gen.check_block_size("biblio", target_rows=100_000_000, ceiling=2000)
    assert ok is True and projected < 2000
    ok2, _ = gen.check_block_size_for_cardinality(cardinality=18_000,
                                                  target_rows=100_000_000, ceiling=2000)
    assert ok2 is False
```

- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3:** Add `check_block_size_for_cardinality(cardinality, target_rows, ceiling)` (uses `shapes.projected_max_block_size`) and `check_block_size(shape, target_rows, ceiling)` (looks up `shapes.SHAPES[shape].blocking_cardinality`). Wire a `--check-block-size <target_rows>` flag into `main()` that prints and exits non-zero if projected > a `--ceiling` (default 5000).
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): generator projection block-size self-check`.

---

## Phase 2: Shape-aware runners

### Task 5: `run_goldenmatch.py --shape` + hand_built config from `shapes.py`

**Files:**
- Modify: `scripts/bench_er_headtohead/run_goldenmatch.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test (hand_built biblio produces an ok result)**

```python
import subprocess, sys, json, os

def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = "packages/python/goldenmatch"
    e["POLARS_SKIP_CPU_CHECK"] = "1"; e["PYTHONIOENCODING"] = "utf-8"
    return e

def test_run_goldenmatch_handbuilt_biblio(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="biblio")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--mode", "hand_built", "--shape", "biblio",
        "--allow-pure-python"], env=_env()).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["status"] == "ok" and r["dedupe_wall_seconds"] is not None
    assert r["shape"] == "biblio"
```

- [ ] **Step 2: Run, confirm fail** (unknown `--shape`).
- [ ] **Step 3: Implement**
  - Add `--shape` arg (choices person/biblio, default person).
  - Delete the inline person config block (lines ~407-428); replace the `if args.mode == "hand_built":` body's config construction with `config = shapes.SHAPES[args.shape].gm_hand_built(args.threshold)` (import `shapes` via the sibling-path pattern).
  - Add `"shape": args.shape` to the `result` dict.
  - Leave `probabilistic`/`zeroconfig` branches unchanged (data-driven — shape-agnostic).
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): run_goldenmatch --shape + hand_built from shapes.py`.

### Task 6: `run_splink.py --shape` + settings from `shapes.py` + record version

**Files:**
- Modify: `scripts/bench_er_headtohead/run_splink.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test** — run `run_splink.py --input <biblio fixture> --shape biblio ...`; assert result `status in {"ok","skipped"}` (skipped iff splink not installed) and, when ok, `result["splink_version"]` is a non-empty string and `result["shape"] == "biblio"`.
- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**
  - Add `--shape` arg.
  - In the `--input` (non-dataset) path, replace `_default_person_settings(s)` with `shapes.SHAPES[args.shape].splink_settings(s)`.
  - Record `result["splink_version"] = getattr(splink, "__version__", "?")` (import `splink` lazily where it's already imported) and `result["shape"] = args.shape`.
  - Keep the `--dataset` path and its `_SETTINGS_BY_DATASET` untouched (real-dataset accuracy panel, out of scope).
- [ ] **Step 4: Run, confirm pass** (locally likely `skipped` unless splink installed — assert accepts both).
- [ ] **Step 5: Commit** `feat(bench): run_splink --shape from shapes.py + record splink_version`.

---

## Phase 3: FS-native eligibility telemetry (runner-side, no scoring-path change)

### Task 7: Record `fs_native_eligible_matchkeys` / `fs_matchkeys_total` in `run_goldenmatch.py`

**Files:**
- Modify: `scripts/bench_er_headtohead/run_goldenmatch.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

Per spec §8: count per-matchkey `probabilistic._fs_native_eligible(mk)` over the
RESOLVED config after `auto_configure_probabilistic_df`. Numpy lane (env `=0`)
must yield `fs_native_eligible_matchkeys == 0`. **No change to `probabilistic.py`.**

- [ ] **Step 1: Failing test (numpy lane => 0 eligible)**

```python
def test_probabilistic_numpy_lane_zero_native_eligible(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    e = _env(); e["GOLDENMATCH_FS_NATIVE"] = "0"     # numpy lane
    rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--mode", "probabilistic", "--shape", "person"],
        env=e).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["status"] == "ok"
    assert r["fs_native_eligible_matchkeys"] == 0     # forced off
    assert r["fs_matchkeys_total"] >= 1
```

- [ ] **Step 2: Run, confirm fail** (keys absent).
- [ ] **Step 3: Implement** — in the `probabilistic` branch, right after `cfg = auto_configure_probabilistic_df(df)` (and the existing `rerank=False` loop), add:
```python
from goldenmatch.core.probabilistic import _fs_native_eligible
mks = cfg.get_matchkeys()
result["fs_matchkeys_total"] = len(mks)
result["fs_native_eligible_matchkeys"] = sum(1 for mk in mks if _fs_native_eligible(mk))
result["fs_native_gate"] = bool(__import__("goldenmatch.core.probabilistic",
    fromlist=["_fs_native_enabled"])._fs_native_enabled())
```
(Guard the import in a `try/except` so a future rename degrades to `None`, not a crash.)
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): FS-native per-matchkey eligibility telemetry (runner-side)`.

---

## Phase 4: Converted-Splink lane

### Task 8: `run_gm_converted.py` — converted-Splink over a fixture

**Files:**
- Create: `scripts/bench_er_headtohead/run_gm_converted.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

Reuse conversion logic from `run_converted_splink.py` but make it fixture-keyed,
shape-aware, and emit the standard result JSON + pred parquet (STRING record_id
remap, mirroring `run_goldenmatch.py` autoconfig branch). Contract identical to
the other runners: `--input --rows --out --pred-out --threshold --shape`.

- [ ] **Step 1: Failing test**

```python
def test_run_gm_converted_person(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    rc = subprocess.run([sys.executable, str(HERE / "run_gm_converted.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--shape", "person"], env=_env()).returncode
    r = json.loads(out.read_text())
    # ok when splink is installed; skipped (exit 0) when it isn't -- never a crash.
    assert r["status"] in {"ok", "skipped", "refused"}
    assert r["lane"] == "gm_converted_splink" and r["shape"] == "person"
    if r["status"] == "ok":
        assert r["dedupe_wall_seconds"] is not None and pred.exists()
```

- [ ] **Step 2: Run, confirm fail** (file doesn't exist).
- [ ] **Step 3: Implement** `run_gm_converted.py`:
  - argparse `--input/--rows/--out/--pred-out/--threshold/--shape`; `_peak_rss_mb`/`_atomic_write` copied from `run_goldenmatch.py`.
  - Lazy Splink import -> on ImportError write `status="skipped"` result (exit 0), like `run_splink._write_skip`.
  - Build the shape's Splink `SettingsCreator` via `shapes.SHAPES[shape].splink_settings(s)`; `settings.create_settings_dict(sql_dialect_str="duckdb")`; `from goldenmatch.config.from_splink import from_splink`; `conversion = from_splink(settings_dict)`; on a conversion that yields no usable config, write `status="refused"` with the `ConversionReport` summary (spec §12).
  - `pl.read_parquet(input)`, `dedupe_df(df, config=conversion.config)` timed; write pred parquet with the STRING record_id remap (copy the autoconfig branch from `run_goldenmatch.py:501-522`).
  - Result dict includes `lane="gm_converted_splink"`, `shape`, `status`, wall, peak_rss, scored_pairs/cluster_count from the bench blob, and the `ConversionReport` summary string.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): run_gm_converted converted-Splink lane over a fixture`.

---

## Phase 5: Lane-model orchestrator

### Task 9: Lane registry + `(shape,lane,scale)` command building

**Files:**
- Modify: `scripts/bench_er_headtohead/orchestrate.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing tests (pure functions, no subprocess)**

```python
def test_lane_registry_and_cmd():
    orch = _load("orchestrate")
    lanes = orch.LANES
    assert set(lanes) == {"splink", "gm_hand_built", "gm_probabilistic",
        "gm_probabilistic_native", "gm_zeroconfig", "gm_converted_splink"}
    # numpy lane forces the env off; native lane forces it on (spec 4)
    assert lanes["gm_probabilistic"].env["GOLDENMATCH_FS_NATIVE"] == "0"
    assert lanes["gm_probabilistic_native"].env["GOLDENMATCH_FS_NATIVE"] == "1"
    cmd = orch.build_cmd(lanes["gm_probabilistic"], input="f.parquet", rows=100,
                         out="o.json", pred="p.parquet", threshold=0.85, shape="person")
    assert "run_goldenmatch.py" in " ".join(cmd)
    assert "--mode" in cmd and "probabilistic" in cmd and "--shape" in cmd

def test_lane_env_is_merged_not_mutating(monkeypatch):
    orch = _load("orchestrate")
    monkeypatch.setenv("SENTINEL", "keep")
    env = orch.lane_env(orch.LANES["gm_probabilistic"])
    assert env["SENTINEL"] == "keep" and env["GOLDENMATCH_FS_NATIVE"] == "0"
    import os as _os
    assert "GOLDENMATCH_FS_NATIVE" not in _os.environ  # never mutated the parent
```

- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**
  - Add a `Lane` dataclass `{name, script, mode, env}` and the `LANES` dict (spec §4 table). `splink`->run_splink.py, `gm_*`->run_goldenmatch.py (with mode), `gm_converted_splink`->run_gm_converted.py.
  - `lane_env(lane)` -> `{**os.environ, **lane.env}` (NEW dict; never mutate `os.environ` — spec §4 hard constraint).
  - `build_cmd(lane, *, input, rows, out, pred, threshold, shape)` -> arg list: base `--input/--rows/--out/--pred-out/--threshold/--shape`, append `--mode <lane.mode>` when set, append `--allow-pure-python` only for `gm_hand_built` when `allow_pure_python` (keep the existing flag path).
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): orchestrate lane registry + per-subprocess env`.

### Task 10: Sweep over lanes x shapes x scales with `(shape,lane,scale)` keys + `{header,results}`

**Files:**
- Modify: `scripts/bench_er_headtohead/orchestrate.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test (end-to-end tiny sweep, GM lanes only so no splink dep)**

```python
def test_sweep_person_two_lanes_smoke(tmp_path):
    orch = _load("orchestrate")
    res = orch.run_sweep(scales=[1500], shapes=["person"],
        lanes=["gm_hand_built", "gm_probabilistic"], workdir=tmp_path,
        dupe_rate=0.3, threshold=0.85, allow_pure_python=True, seed=42)
    agg = json.loads((tmp_path / "bench_results.json").read_text())
    assert set(agg) == {"header", "results"}          # object, not list
    assert agg["header"]["run_timestamp"]              # present
    keys = {(r["shape"], r["lane"], r["rows_requested"]) for r in agg["results"]}
    assert ("person", "gm_hand_built", 1500) in keys
    assert all(r.get("shape") and r.get("lane") for r in agg["results"])
```

- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement**
  - Add `run_sweep(*, scales, shapes, lanes, workdir, dupe_rate, threshold, allow_pure_python=False, seed=42, run_tag="local")` factored out of `main()`.
  - For each `(shape, scale)`: `generate(...)` with `--shape shape`; write truth per shape/scale. For each `lane`: `run_engine(lane, shape, scale, ...)` builds cmd via `build_cmd`, runs `subprocess.run(cmd, env=lane_env(lane), timeout=_timeout_for(scale))`, loads/synthesizes the result, stamps `shape`/`lane`/`rows_requested`, runs `evaluate_datapoint` (unchanged), appends to `results`, flushes `{header, results}` after every datapoint.
  - Header built once at sweep start: `run_timestamp` (`time.time()`), `git_sha` (`git rev-parse HEAD`), `runner_label`/`cpu_count`/`total_ram_gb` (env + `os.cpu_count()` + `/proc/meminfo` best-effort), `dupe_rate`, `threshold`, `seed`, `goldenmatch_version` (import), `splink_version` (from any splink result, else None).
  - `_load_or_synthesize` gains `shape`/`lane` in its synthesized dict.
  - `main()` now parses `--lanes` (default all 6), `--shapes` (default `person biblio`), `--run-tag`, delegates to `run_sweep`.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): lane x shape x scale sweep, {header,results} aggregate`.

### Task 11: Lowered per-datapoint timeout ladder

**Files:**
- Modify: `scripts/bench_er_headtohead/orchestrate.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test** — assert the summed timeouts of the heaviest single-shape single-lane band fit the cap:
```python
def test_timeout_ladder_fits_cap():
    orch = _load("orchestrate")
    # 25M + 100M for ONE lane must fit under ~560 min (spec 7.3)
    assert orch._timeout_for(25_000_000) + orch._timeout_for(100_000_000) <= 560 * 60
    assert orch._timeout_for(100_000) < orch._timeout_for(5_000_000)  # monotone
```
- [ ] **Step 2: Run, confirm fail** (current ladder tops at 8h for 100M alone).
- [ ] **Step 3: Implement** — retune `TIMEOUT_BY_ROWS` so a per-lane heavy band (25M+100M) fits ~560 min (e.g. 100K:900, 1M:1800, 5M:5400, 25M:9000, 100M:18000 -> 25M+100M=450 min). A datapoint hitting the cap is already recorded `timeout` (spec §7.3).
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `perf(bench): lower per-datapoint timeout ladder to fit 600-min cap`.

### Task 12: Rewrite `render_markdown` for shape x lane x scale

**Files:**
- Modify: `scripts/bench_er_headtohead/orchestrate.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test (renders from a hand-built results object, no run)**

```python
def test_render_markdown_shape_lane_sections():
    orch = _load("orchestrate")
    results = [
        {"shape": "person", "lane": "splink", "rows_requested": 100000,
         "status": "ok", "dedupe_wall_seconds": 10.0, "peak_rss_mb": 500,
         "scored_pairs": 1000, "cluster_count": 50,
         "accuracy": {"pairwise": {"precision":1,"recall":1,"f1":1,
             "confusion":{"tp":1,"fp":0,"fn":0,"tn":0}},
             "bcubed":{"precision":1,"recall":1,"f1":1}}},
        {"shape": "person", "lane": "gm_probabilistic", "rows_requested": 100000,
         "status": "ok", "dedupe_wall_seconds": 5.0, "peak_rss_mb": 400,
         "scored_pairs": 900, "cluster_count": 50,
         "accuracy": {"pairwise": {"precision":1,"recall":0.9,"f1":0.95,
             "confusion":{"tp":1,"fp":0,"fn":0,"tn":0}},
             "bcubed":{"precision":1,"recall":1,"f1":1}}},
    ]
    md = orch.render_markdown(results, {"dupe_rate": 0.2})
    assert "## person" in md
    assert "splink" in md and "gm_probabilistic" in md
    # head-to-head is per GM lane vs splink (reference column)
    assert "vs splink" in md.lower() or "GM/Splink" in md
```

- [ ] **Step 2: Run, confirm fail** (current `render_markdown` keys on `engine`).
- [ ] **Step 3: Implement** — new `render_markdown(results, header)`:
  - group by `shape`; one `## <shape>` section each.
  - per shape: a main table (rows = `(lane, scale)`; cols = status, wall, peak RSS, scored pairs, clusters, pairs/sec, pairwise F1, B³ F1) sorted by `(scale, lane)` with `splink` first per scale.
  - an accuracy-detail table (pw P/R/F1, B³ P/R/F1, TP/FP/FN).
  - a head-to-head table: per `(scale, gm_lane)`, `gm_wall / splink_wall` ratio + RSS ratio vs the `splink` row at that scale.
  - Drop all `by_rows.get("goldenmatch")` hardcoding.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): render_markdown shape/lane/scale sections + per-lane deltas`.

---

## Phase 6: Merge

### Task 13: `merge_results.py` — union artifacts, later-timestamp-wins, render

**Files:**
- Create: `scripts/bench_er_headtohead/merge_results.py`
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test**

```python
def test_merge_later_timestamp_wins(tmp_path):
    merge = _load("merge_results")
    a = {"header": {"run_timestamp": 100.0, "git_sha": "aaa"},
         "results": [{"shape":"person","lane":"splink","rows_requested":100,
                      "status":"ok","dedupe_wall_seconds":9.0}]}
    b = {"header": {"run_timestamp": 200.0, "git_sha": "bbb"},
         "results": [{"shape":"person","lane":"splink","rows_requested":100,
                      "status":"ok","dedupe_wall_seconds":7.0}]}
    (tmp_path/"a"/"bench_results.json").parent.mkdir(parents=True); (tmp_path/"a"/"bench_results.json").write_text(json.dumps(a))
    (tmp_path/"b"/"bench_results.json").parent.mkdir(parents=True); (tmp_path/"b"/"bench_results.json").write_text(json.dumps(b))
    merged = merge.merge_dir(tmp_path)
    got = {(r["shape"],r["lane"],r["rows_requested"]): r for r in merged["results"]}
    assert got[("person","splink",100)]["dedupe_wall_seconds"] == 7.0   # later run wins
    assert len(merged["runs"]) == 2                                     # both headers kept
```

- [ ] **Step 2: Run, confirm fail.**
- [ ] **Step 3: Implement** `merge_results.py`:
  - `merge_dir(root)`: glob `*/bench_results.json` (spec §7.2 one-dir-per-artifact), parse each `{header, results}`, keep `runs=[header,...]`, fold `results` into a dict keyed `(shape,lane,rows_requested)` keeping the entry whose source header `run_timestamp` is larger. Return `{"runs":[...], "results":[...]}`.
  - `main()`: `--artifacts-dir`, `--out-json`, `--out-md`; writes merged json + `orchestrate.render_markdown(merged["results"], merged["runs"][0] if merged["runs"] else {})`; appends md to `$GITHUB_STEP_SUMMARY` if set.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `feat(bench): merge_results union + later-timestamp-wins + render`.

---

## Phase 7: Workflow + README + remaining guard tests

### Task 14: Add the eval-join explicit-cast lock test

**Files:**
- Modify: `scripts/bench_er_headtohead/evaluate.py` (cast) + test
- Test: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Failing test** — build a pred parquet with STRING `record_id` and a truth parquet with INT64 `record_id` (same ids), call `evaluate.evaluate(pred, truth)`, assert F1 == 1.0 (they describe the same clustering).
- [ ] **Step 2: Run, confirm fail or pass** — DuckDB may implicitly cast (test could pass immediately). If it passes, still add the explicit cast for robustness (spec §10) and keep the test as the lock.
- [ ] **Step 3: Implement** — change the join to `ON CAST(p.record_id AS VARCHAR) = CAST(t.record_id AS VARCHAR)`.
- [ ] **Step 4: Run, confirm pass.**
- [ ] **Step 5: Commit** `fix(bench): explicit record_id cast in eval join + lock test`.

### Task 15: Native-gate refusal test (hand_built without the flag)

**Files:**
- Test only: `scripts/bench_er_headtohead/test_scale_envelope.py`

- [ ] **Step 1: Write the test** — run `run_goldenmatch.py --mode hand_built --shape person` WITHOUT `--allow-pure-python` in an env where native isn't built; assert non-zero exit AND the result JSON (if written) has `status != "ok"` (spec §10). Skip the assertion path with `pytest.skip` if `native_loaded` is true in the environment (belt-and-suspenders).
- [ ] **Step 2: Run, confirm it passes** (guard already exists in `run_goldenmatch.py:378-387`). This test locks the existing behavior.
- [ ] **Step 3: Commit** `test(bench): lock hand_built native-gate refusal`.

### Task 16: Update `bench-er-headtohead.yml`

**Files:**
- Modify: `.github/workflows/bench-er-headtohead.yml`
- Verify: `python -c "import yaml, sys; yaml.safe_load(open('.github/workflows/bench-er-headtohead.yml'))"`

- [ ] **Step 1:** Rewrite inputs: `lanes` (default all 6, space-sep), `shapes` (default `person biblio`), `scales` (default `100000 1000000`), `dupe_rate`, `threshold`, `runner` (default `large-new-64GB`), `run_tag` (default `manual`). Add workflow-level `env: { ARROW_DEFAULT_MEMORY_POOL: system, GOLDENMATCH_AUTOCONFIG_MEMORY: "0" }`.
- [ ] **Step 2:** `bench` job gains `strategy: { fail-fast: false, matrix: { shape: <from inputs.shapes> } }` (parse the space list into a matrix via a `fromJSON` over a prep step, or a small matrix `include`). Each job: checkout, rust-toolchain, rust-cache, setup-uv, `uv sync --all-packages`, `uv run python scripts/build_native.py`, install Splink **pinned** (`uv pip install splink==<PIN>`) `if: contains(inputs.lanes,'splink') || contains(inputs.lanes,'gm_converted_splink')`, install duckdb, then `orchestrate.py --scales ${{inputs.scales}} --shapes ${{matrix.shape}} --lanes ${{inputs.lanes}} --dupe-rate ... --threshold ... --run-tag ${{inputs.run_tag}} --workdir .bench_er`. Upload artifact `er-headtohead-${{matrix.shape}}-${{inputs.run_tag}}` with `.bench_er/bench_results.json` + `.bench_er/results/*.json`.
- [ ] **Step 3:** Add a `merge` job `needs: bench, if: always()`: download all artifacts into `merged/` **without `merge-multiple`** (one dir per artifact), `uv run python scripts/bench_er_headtohead/merge_results.py --artifacts-dir merged --out-json merged.json --out-md merged.md`, upload `er-headtohead-merged-${{inputs.run_tag}}`.
- [ ] **Step 4:** Validate YAML (command above); confirm no tab chars, no `[skip ci]` in any string (per CLAUDE.md gotchas). Pin the Splink version to the latest 4.x that resolves (record it in the workflow comment).
- [ ] **Step 5: Commit** `ci(bench): 6-lane x 2-shape matrix, pinned splink, merge job`.

### Task 17: Refresh `README.md`

**Files:**
- Modify: `scripts/bench_er_headtohead/README.md`

- [ ] **Step 1:** Rewrite: the 6 lanes table, the 2 shapes (person + biblio incl. the `(venue,year)` stable-block-key rationale), the metrics list, the sharded dispatch plan (spec §7.3), the merge step, and the honest caveats (spec §11). Note the FS numpy-vs-native lane distinction and the `fs_native_eligible_matchkeys` proof field.
- [ ] **Step 2: Commit** `docs(bench): refresh README for lanes/shapes/dispatch/merge`.

### Task 18: Full local smoke of the whole harness (GM lanes, ASCII-only)

**Files:**
- None (verification task)

- [ ] **Step 1:** Run `run_sweep` (or `orchestrate.py`) locally for `shapes=person biblio`, `scales=1500`, `lanes=gm_hand_built gm_probabilistic gm_zeroconfig gm_converted_splink` (the four that don't strictly need splink installed; `gm_converted_splink` will `skipped` without splink), `--allow-pure-python`. Confirm `bench_results.json` is a `{header, results}` object with `(shape,lane,scale)`-keyed rows and no crash.
- [ ] **Step 2:** Run `merge_results.py` over a dir holding that one artifact; confirm it renders `## person` and `## biblio` sections.
- [ ] **Step 3:** Run the full test file: `<venv> -m pytest scripts/bench_er_headtohead/test_scale_envelope.py -v`. All green.
- [ ] **Step 4: Commit** any test-only fixes discovered. If nothing changed, no commit.

---

## Phase 8: PR

### Task 19: Open the harness PR

- [ ] **Step 1:** Push `bench/scale-envelope-v2`; open a PR to `main` titled `bench: reproducible 6-lane x 2-shape Splink scale envelope`. Body: link the spec, summarize the lane model, note it's `workflow_dispatch`-only (no PR-CI cost) and the smoke tests gate it. Use the PR template if present.
- [ ] **Step 2:** Confirm CI green (the smoke tests run in the normal python lane; the scale workflow does NOT run on PR). Arm auto-merge per the standing SOP (`gh pr merge --auto --squash`) and STOP polling (per `feedback_dont_poll_ci_arm_automerge`).

---

## Post-merge (data-dependent, NOT part of the harness PR)

These land AFTER the workflow runs produce numbers (spec §9 items 9-10), as a second data-only change. Do NOT block the harness PR on them.

### Task 20: Run the sweep on CI, capture artifacts

- [ ] Dispatch per the plan (spec §7.3): cheap band (100K/1M all lanes both shapes), then 5M, then per-lane heavy band (25M/100M). Download the merged artifact.
- [ ] Sanity-check the FS lanes: `gm_probabilistic` rows show `fs_native_eligible_matchkeys=0`; `gm_probabilistic_native` rows show `>0`. If not, the run is invalid (spec §8) — investigate before publishing.

### Task 21: Write the results doc + rewrite `docs/scale-envelope.md`

- [ ] Create `docs/benchmarks/2026-07-14-scale-envelope-splink-6lane.md` from the merged tables + caveats (spec §11).
- [ ] Rewrite `docs/scale-envelope.md` with the current 3.3.0 numbers + the 6-lane picker, replacing the stale v1.16.0 content.
- [ ] Run `rollout-docs-sweep` skill for any other stale doc surface.
- [ ] Commit + PR `docs: scale-envelope v2 results (3.3.0, 6 lanes, 2 shapes)`.

---

## Notes for the executor

- **Never mutate `os.environ` in the orchestrator** (spec §4). Always `subprocess.run(cmd, env=lane_env(lane))`.
- **`shapes.py` is the only place a shape fact lives.** If two files would need the same column list / blocking field / config, put it in `shapes.py`.
- **Local runs are pure-Python** (`--allow-pure-python` on `gm_hand_built`; other GM lanes tolerate missing native). The native FS proof (`fs_native_eligible_matchkeys>0` on the native lane) only holds where `build_native.py` ran — CI, not local.
- **ASCII only** in any printed/emitted string (Windows cp1252 + `gh release`/API constraints).
- **Don't run the full pytest suite** (OOMs the box). Run only `scripts/bench_er_headtohead/test_scale_envelope.py`.
- Reference skills: @superpowers:test-driven-development, @superpowers:verification-before-completion, @superpowers:requesting-code-review before the PR.
