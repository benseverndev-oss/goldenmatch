# Splink (hand-rolled) vs GoldenMatch (autoconfig) Bake-off Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible accuracy + performance bake-off across the ER benchmark datasets pitting hand-rolled expert Splink against GoldenMatch's two zero-tuning autoconfig modes, emitting one unified table (P/R/F1 + B3 + wall + peak RSS + throughput per engine x dataset), run on a Linux CI runner.

**Architecture:** Reuse the working `scripts/bench_er_headtohead/` flow. Add `--mode` to `run_goldenmatch.py` so GM runs in its own subprocess (isolated perf) under zero-config and probabilistic-autoconfig. New `run_bakeoff.py` orchestrates the 3 engines per dataset (mirroring `run_panel.py`'s proven Utf8-truth + string-record_id eval), collects accuracy + perf, emits `bakeoff.md`/`.json`. A non-gating CI lane runs it; results commit to `docs/benchmarks/`.

**Tech Stack:** Python, polars, DuckDB (Splink + evaluate), splink, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-09-splink-bakeoff-design.md`

---

## Conventions

- Run tests (Windows): `$env:POLARS_SKIP_CPU_CHECK=1; $env:PYTHONIOENCODING="utf-8"; .venv\Scripts\python.exe -m pytest <path> -v`. Do NOT run the full suite (OOMs the box). Hang >2min -> kill zombies `powershell.exe -Command "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"`.
- Branch off latest `origin/main`. Commit code+tests only; NEVER `git add docs/`. ASCII commit messages.
- `splink` is pip-installed in the venv. `resource` is Linux-only -> perf-capture code paths are exercised in CI, but the unit tests must NOT depend on `resource` returning a real RSS (they assert structure, not Linux RSS values).
- **Read `scripts/bench_er_headtohead/run_panel.py` before Tasks 1-2** -- it is the working reference for the Utf8-truth write (`:358`), the string-record_id GM pred (`_gm_predictions_path`, esp. `:96`), and the Splink subprocess eval (`_run_splink`). The bake-off LIFTS these patterns; do not reinvent them.

## Background (current state)

- `run_goldenmatch.py`: takes `--input <parquet>` + `--rows` + `--pred-out` + `--threshold`; runs an EXPLICIT hand-built bucket+native+weighted config; `--pred-out` writes `record_id` as int64 `__row_id__` (valid only when record_id == row index, i.e. the synthetic fixture). Self-times wall + `_peak_rss_mb()` (Linux `resource`). `except BaseException ... raise` re-raises after writing the result JSON.
- `run_splink.py`: `--dataset <name>` loads `(records, truth)` via `datasets.load_dataset`; per-dataset hand-rolled `_SETTINGS_BY_DATASET`; writes `--pred-out` as `record_id, cluster_id AS pred_cluster_id` (REAL record_id); self-captures perf; skips dblp_acm.
- `run_panel.py`: accuracy-only; runs GM INLINE (`_gm_predictions_path`, string record_id) + Splink subprocess; writes truth Utf8 (`:358`); evals via `evaluate.evaluate(pred, truth) -> {pairwise, bcubed}`. **Stays UNTOUCHED.**
- `evaluate.evaluate(pred, truth)`: DuckDB join `p.record_id = t.record_id`; returns `{"pairwise": {precision,recall,f1}, "bcubed": {f1,...}}`.

## File Structure

- **Modify** `scripts/bench_er_headtohead/run_goldenmatch.py`: add `--mode {hand_built,zeroconfig,probabilistic}` (default `hand_built`, byte-unchanged). Bake-off modes run the autoconfig path + write string-record_id preds + handle refuse + force rerank off.
- **New** `scripts/bench_er_headtohead/run_bakeoff.py`: the unified orchestrator.
- **New** tests: `scripts/bench_er_headtohead/test_bakeoff.py` (or `packages/python/goldenmatch/tests/test_bench_bakeoff.py`).
- **Modify** `.github/workflows/bench-probabilistic.yml`: add the `bake-off` lane.
- **(Task 4, post-merge)** `docs/benchmarks/2026-06-09-splink-bakeoff.md` (results) + doc links.

---

## Task 1: `--mode` on run_goldenmatch.py (zeroconfig + probabilistic autoconfig)

**Files:**
- Modify: `scripts/bench_er_headtohead/run_goldenmatch.py`
- Test: `scripts/bench_er_headtohead/test_bakeoff.py`

- [ ] **Step 1: Write failing tests.** Create the test file. Use a tiny person-shaped parquet with a STRING `record_id` column (so the bake-off remap is exercised). Invoke the runner as a subprocess.

```python
import json, subprocess, sys
from pathlib import Path
import polars as pl
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
RUN_GM = HERE / "run_goldenmatch.py"

def _tiny_parquet(tmp_path):
    # string record_ids (like historical_50k/febrl3/dblp_acm), 2 obvious dup pairs
    df = pl.DataFrame({
        "record_id": ["r0", "r1", "r2", "r3", "r4", "r5"],
        "first_name": ["ann", "ann", "bob", "bob", "cara", "dan"],
        "surname":    ["lee", "lee", "kim", "kim", "ng", "ono"],
        "dob":        ["1990-01-01", "1990-01-01", "1985-02-02", "1985-02-02", "1972-03-03", "1965-04-04"],
        "postcode":   ["AA1", "AA1", "BB2", "BB2", "CC3", "DD4"],
    })
    p = tmp_path / "tiny.parquet"; df.write_parquet(p); return p, df

def _run(p, mode, tmp_path):
    out = tmp_path / "res.json"; pred = tmp_path / "pred.parquet"
    proc = subprocess.run(
        [sys.executable, str(RUN_GM), "--input", str(p), "--rows", "6",
         "--mode", mode, "--out", str(out), "--pred-out", str(pred),
         "--allow-pure-python", "--threshold", "0.85"],
        capture_output=True, text=True, timeout=300,
        env={**__import__("os").environ, "POLARS_SKIP_CPU_CHECK": "1", "PYTHONIOENCODING": "utf-8"},
    )
    return proc, out, pred

def test_zeroconfig_emits_string_record_id_preds(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "zeroconfig", tmp_path)
    assert out.exists(), proc.stderr
    res = json.loads(out.read_text())
    assert res["mode"] == "zeroconfig"
    assert res["status"] in ("ok", "refused")  # refuse is a valid recorded outcome
    if res["status"] == "ok":
        t = pq.read_table(pred)
        assert t.column_names == ["record_id", "pred_cluster_id"]
        rids = set(t.column("record_id").to_pylist())
        assert rids <= set(df["record_id"].to_list())   # REAL string ids, not 0..N
        assert all(isinstance(x, str) for x in rids)

def test_probabilistic_mode_runs_and_string_preds(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "probabilistic", tmp_path)
    assert out.exists(), proc.stderr
    res = json.loads(out.read_text())
    assert res["mode"] == "probabilistic" and res["status"] == "ok", res.get("error")
    t = pq.read_table(pred)
    assert all(isinstance(x, str) for x in t.column("record_id").to_pylist())

def test_hand_built_mode_unchanged_int_ids(tmp_path):
    # hand_built keeps int64 __row_id__ preds (orchestrate.py back-compat)
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "hand_built", tmp_path)
    res = json.loads(out.read_text())
    assert res["mode"] == "hand_built"
    if res["status"] == "ok":
        t = pq.read_table(pred)
        import pyarrow as pa
        assert pa.types.is_integer(t.schema.field("record_id").type)
```

- [ ] **Step 2: Run, verify fail** (`--mode` unknown / `mode` not in result). `.venv\Scripts\python.exe -m pytest scripts/bench_er_headtohead/test_bakeoff.py -v`

- [ ] **Step 3: Implement.** In `run_goldenmatch.py`:
  - Add `ap.add_argument("--mode", choices=["hand_built","zeroconfig","probabilistic"], default="hand_built")`. Put `result["mode"] = args.mode` early.
  - Branch the dedupe by mode:
    - `hand_built`: the EXISTING explicit-config `dedupe_df(df, config=config)` path -- LEAVE IT EXACTLY AS-IS, and keep the EXISTING int64 `__row_id__` `--pred-out` write for this mode only.
    - `zeroconfig`: `from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError` (**IMPORTANT: it lives in `autoconfig_controller`, NOT `autoconfig` -- importing from the wrong module silently breaks the refuse path; do NOT wrap this import in a try/except that would bind a never-matching sentinel**). Wrap `ded = dedupe_df(df)` in `try/except ControllerNotConfidentError as e: result.update(status="refused", error=str(e)); _atomic_write(args.out, result); print(...); return` (exit 0, do NOT re-raise -- handle BEFORE the generic `except BaseException ... raise`). NOTE: refuse only fires at `df.height >= 100_000`, so none of the 4 panel datasets (max = historical_50k at 50K) will actually refuse -- this is a defensive guard, exercised only if the scale follow-up adds a >=100K dataset. (`auto_configure_probabilistic_df` IS correctly importable from `goldenmatch.core.autoconfig`.)
    - `probabilistic`: `from goldenmatch.core.autoconfig import auto_configure_probabilistic_df`; `cfg = auto_configure_probabilistic_df(df)`; then HARD-force rerank off: `for mk in cfg.get_matchkeys(): if getattr(mk, "type", None) == "weighted": mk.rerank = False`; `ded = dedupe_df(df, config=cfg)`.
  - **Pred-out remap (bake-off modes only):** factor the pred-out write into a helper. For `zeroconfig`/`probabilistic`, remap to the input df's real record_id as a STRING column (mirror `run_panel.py::_gm_predictions_path`):
    ```python
    rid = df["record_id"].to_list()  # the input parquet's real record_id column
    rids, cids = [], []
    for cid, c in clusters.items():
        members = c["members"] if isinstance(c, dict) else c.members
        for m in members:
            rids.append(str(rid[m])); cids.append(cid)
    pq.write_table(pa.table({"record_id": pa.array(rids, pa.string()),
                             "pred_cluster_id": pa.array(np.asarray(cids, dtype=np.int64))}),
                   args.pred_out, compression="zstd")
    ```
    For `hand_built`, keep the existing int64 write untouched.
  - Native gate: the bake-off modes can run with `--allow-pure-python` (autoconfig may not pick the native bucket path); keep `--require-native` default-on for `hand_built` only, or relax the native-required RuntimeError to `hand_built` mode. (The bake-off CI run will pass `--require-native` off for the autoconfig modes since the controller chooses the backend.)

- [ ] **Step 4: Run tests -- green.** `.venv\Scripts\python.exe -m pytest scripts/bench_er_headtohead/test_bakeoff.py -v` then `ruff check scripts/bench_er_headtohead/run_goldenmatch.py scripts/bench_er_headtohead/test_bakeoff.py`. NOTE: these tests run a real (tiny, 6-row) GM dedupe -- set the polars env vars. If `probabilistic` mode can't build a matchkey on the tiny fixture, enrich the fixture (more distinct names) rather than weaken the assert.

- [ ] **Step 5: Commit.** `git add scripts/bench_er_headtohead/run_goldenmatch.py scripts/bench_er_headtohead/test_bakeoff.py && git commit -m "feat(bench): run_goldenmatch --mode {hand_built,zeroconfig,probabilistic} for the bake-off (string-record_id preds, refuse-handling, rerank guard)"`

---

## Task 2: `run_bakeoff.py` orchestrator (accuracy + perf, 3 engines x all datasets)

**Files:**
- Create: `scripts/bench_er_headtohead/run_bakeoff.py`
- Test: `scripts/bench_er_headtohead/test_bakeoff.py` (append)

- [ ] **Step 1: Write failing tests** for the pure table-assembly logic (no live engines). Factor the orchestrator so a `build_rows(per_engine_results)` / `render_md(rows)` are unit-testable with STUB inputs:

```python
def test_bakeoff_table_assembly_and_missing_engine():
    from run_bakeoff import build_rows, render_md  # import the sibling module
    # stub: dataset -> engine -> (result_dict, metrics_dict-or-None)
    stub = {
      "febrl3": {
        "gm_zeroconfig": ({"status":"ok","dedupe_wall_seconds":3.1,"peak_rss_mb":900.0,"scored_pairs":12000},
                          {"pairwise":{"precision":0.99,"recall":0.98,"f1":0.985},"bcubed":{"f1":0.97}}),
        "gm_probabilistic": ({"status":"ok","dedupe_wall_seconds":4.0,"peak_rss_mb":950.0,"scored_pairs":15000},
                          {"pairwise":{"precision":0.99,"recall":0.99,"f1":0.991},"bcubed":{"f1":0.98}}),
        "splink": ({"status":"ok","dedupe_wall_seconds":8.0,"peak_rss_mb":1200.0,"scored_pairs":20000},
                          {"pairwise":{"precision":0.97,"recall":0.96,"f1":0.965},"bcubed":{"f1":0.95}}),
      },
      "dblp_acm": {  # splink skips
        "gm_zeroconfig": ({"status":"ok","dedupe_wall_seconds":2.0,"peak_rss_mb":800.0,"scored_pairs":9000},
                          {"pairwise":{"precision":0.9,"recall":0.86,"f1":0.879},"bcubed":{"f1":0.86}}),
        "gm_probabilistic": ({"status":"ok","dedupe_wall_seconds":2.2,"peak_rss_mb":810.0,"scored_pairs":9100},
                          {"pairwise":{"precision":0.9,"recall":0.86,"f1":0.879},"bcubed":{"f1":0.86}}),
        "splink": ({"status":"skipped","error":"bibliographic out of scope"}, None),
      },
    }
    rows = build_rows(stub)
    # one row per (dataset, engine); splink/dblp_acm is a 'skipped' row with no F1.
    # NOTE: use "skipped" to match run_splink.py's actual status string (run_panel.py
    # maps it to row["status"]="skipped"); keep build_rows/render_md + this stub in sync.
    skips = [r for r in rows if r["dataset"]=="dblp_acm" and r["engine"]=="splink"]
    assert skips and skips[0]["status"]=="skipped" and skips[0].get("f1") in (None,"")
    md = render_md(rows)
    assert "febrl3" in md and "throughput" in md.lower() and "peak" in md.lower()
    # a delta line exists somewhere (GM vs Splink) for febrl3
    assert "ratio" in md.lower() or "delta" in md.lower()
```

- [ ] **Step 2: Run, verify fail** (ImportError / build_rows missing).

- [ ] **Step 3: Implement `run_bakeoff.py`.** Structure:
  - `_import_sibling(name)` (copy from run_panel.py) for datasets/attribution/evaluate.
  - `ENGINES = ["gm_zeroconfig", "gm_probabilistic", "splink"]`; `DATASETS = ["historical_50k","febrl3","synthetic_person","dblp_acm"]`.
  - Per dataset: `records, truth = datasets.load_dataset(name)`; write `records` to `ds_dir/records.parquet` (preserve the real `record_id` column verbatim); write truth Utf8: `truth.with_columns(pl.col("record_id").cast(pl.Utf8)).write_parquet(ds_dir/"truth.parquet")` (copy run_panel.py:358).
  - For `gm_zeroconfig` / `gm_probabilistic`: subprocess `run_goldenmatch.py --input records.parquet --rows <h> --mode <zeroconfig|probabilistic> --allow-pure-python --pred-out gm_<mode>_pred.parquet --out gm_<mode>_res.json --threshold 0.85`, with a per-engine timeout (reuse run_panel's `_TIMEOUT`). On `status=ok`, `metrics = evaluate.evaluate(pred, truth_path)`; on `refused`/`error`/timeout, metrics=None (record the status).
  - For `splink`: subprocess `run_splink.py --dataset <name> --pred-out splink_pred.parquet --out splink_res.json --threshold 0.85` (copy run_panel.py::_run_splink). On ok, `evaluate.evaluate(splink_pred, truth_path)`; a dataset Splink doesn't support records `status=skips`.
  - `build_rows(per_engine_results)`: flatten to one row per (dataset, engine) with `{dataset, engine, status, precision, recall, f1, bcubed_f1, dedupe_wall_seconds, peak_rss_mb, scored_pairs, throughput_pairs_per_s = round(scored_pairs/wall) if both, rows}`.
  - `render_md(rows)`: a markdown table (engine columns grouped per dataset) + a per-dataset delta block (GM-zeroconfig & GM-probabilistic vs Splink: wall ratio GM/Splink, RSS ratio, F1 delta). Plus the honest-framing footer (pairwise under one evaluator; ~0.97 is cluster-level; Splink skips dblp_acm).
  - `main()`: `--out-dir`, `--datasets` (default all), write `bakeoff.json` (the rows) + `bakeoff.md` (render_md). Print a one-line-per-cell progress log.

- [ ] **Step 4: Run tests + ruff -- green.** (The assembly tests use stubs; no live engines.) `.venv\Scripts\python.exe -m pytest scripts/bench_er_headtohead/test_bakeoff.py -v`. Optionally do ONE tiny live smoke: `run_bakeoff.py --datasets febrl3 --out-dir .profile_tmp/bakeoff_smoke` IF febrl3 + splink run locally in reasonable time (skip if slow; CI is the real run).

- [ ] **Step 5: Commit.** `git add scripts/bench_er_headtohead/run_bakeoff.py scripts/bench_er_headtohead/test_bakeoff.py && git commit -m "feat(bench): run_bakeoff.py -- unified accuracy+perf bake-off (GM zeroconfig+probabilistic vs hand-rolled Splink)"`

---

## Task 3: CI bake-off lane in bench-probabilistic.yml

**Files:**
- Modify: `.github/workflows/bench-probabilistic.yml`

- [ ] **Step 1: Read** the existing `panel` / `panel-v1-v2` jobs in `bench-probabilistic.yml` to copy the setup (checkout, python, `pip install -e packages/python/goldenmatch[bench]`, native build via `scripts/build_native.py` if used, the Leipzig DBLP-ACM fetch step, `runs-on: large-new-64GB`).

- [ ] **Step 2: Add a `bake-off` job** (workflow_dispatch-gated, mirror the panel job's `if`/inputs toggle, e.g. `run_bakeoff` input). Steps: setup (as panel) + build native runtime + fetch DBLP-ACM + `python scripts/bench_er_headtohead/run_bakeoff.py --out-dir bakeoff_out` + `actions/upload-artifact` of `bakeoff_out/bakeoff.md` + `bakeoff.json`. Non-gating (no required check). Add the `run_bakeoff` toggle to the workflow's `changes`/dispatch inputs consistently with the other bench lanes.

- [ ] **Step 3:** Validate YAML locally if a linter is available (`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-probabilistic.yml'))"`). No unit test for the workflow itself.

- [ ] **Step 4: Commit.** `git add .github/workflows/bench-probabilistic.yml && git commit -m "ci(bench): add workflow_dispatch bake-off lane (run_bakeoff.py on large-new-64GB)"`

---

## Final checks before PR

- [ ] Run `test_bakeoff.py` individually (not the full suite). ruff clean on the 3 changed/new files.
- [ ] Confirm `--mode hand_built` is byte-unchanged (orchestrate.py path): diff `run_goldenmatch.py` hand_built branch vs the original; the int64 pred-out write is untouched.
- [ ] PR with the spec link. Squash-merge via `benzsevern` auth (benseverndev-oss), switch back to benzsevern-mjh.

## Task 4: Run + record (post-merge; no code)

- [ ] After the PR merges to main (workflow_dispatch lanes only register on the default branch), dispatch the `bake-off` lane: `gh workflow run bench-probabilistic.yml --repo benseverndev-oss/goldenmatch -f run_bakeoff=true` (benzsevern auth).
- [ ] Watch it; download the `bakeoff.md`/`.json` artifacts.
- [ ] Commit the results to `docs/benchmarks/2026-06-09-splink-bakeoff.md` (create the dir; include the runner + commit SHA for provenance) and add a link from `context-network/architecture/fellegi-sunter-splink-parity.md` + `docs-site/reference/vendor-comparison.mdx`. (This doc commit is the one place `docs/` content lands -- it is NOT under `docs/superpowers/`, so it commits normally.)
- [ ] DECISION POINT: read the numbers honestly. If GM zero-config refuses or trails badly on a dataset, RECORD it as-is (the bake-off's value is honesty). Surface the headline (does zero-config / probabilistic-autoconfig beat hand-rolled Splink on accuracy, and how does perf compare) to the user.

## Follow-ups (NOT in this plan)

- Synthetic 10K/100K/1M throughput curve (the "at scale" story).
- NCVR adapter (needs synthesized corrupted-duplicate ground truth).
- Re-tuning the hand-rolled Splink configs (reuse as-is for now).
