"""Smoke + guard tests for the scale-envelope v2 head-to-head harness."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_person_shape_metadata():
    shapes = _load("shapes")
    s = shapes.SHAPES["person"]
    assert s.name == "person"
    assert s.columns == ["record_id", "first_name", "surname", "dob", "postcode", "city"]
    assert s.blocking_fields == ["postcode"]
    assert s.blocking_cardinality == 200_000  # C, for the projection guard


def test_shapes_import_does_not_drag_goldenmatch():
    # shapes.py must import cleanly without pulling goldenmatch into sys.modules
    # (run_splink + the generator import it and must stay GM-free at import time).
    for m in [k for k in list(sys.modules) if k == "goldenmatch" or k.startswith("goldenmatch.")]:
        del sys.modules[m]
    _load("shapes")
    assert not any(k == "goldenmatch" or k.startswith("goldenmatch.") for k in sys.modules)


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


def test_generate_biblio_schema_and_stable_block_key(tmp_path):
    import pyarrow.parquet as pq

    gen = _load("generate_fixture")
    out, truth = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(rows=3000, dupe_rate=0.3, out=out, truth=truth, seed=42,
                 batch=1000, shape="biblio")
    t = pq.read_table(out)
    assert t.column_names == ["record_id", "title", "authors", "venue", "year"]
    # Within every truth cluster, venue+year (block key) must be identical across
    # members -- that's the stability guarantee that avoids the recall trap.
    from collections import defaultdict

    truth_t = pq.read_table(truth)
    rids = t.column("record_id").to_pylist()
    venue_by_id = dict(zip(rids, t.column("venue").to_pylist()))
    year_by_id = dict(zip(rids, t.column("year").to_pylist()))
    cl_venues: dict = defaultdict(set)
    cl_years: dict = defaultdict(set)
    for r, c in zip(truth_t.column("record_id").to_pylist(),
                    truth_t.column("cluster_id").to_pylist()):
        cl_venues[c].add(venue_by_id.get(r))
        y = year_by_id.get(r)
        if y is not None:  # allow year null (dropna equivalent)
            cl_years[c].add(y)
    # venue always unique per cluster; non-null year unique per cluster
    assert max(len(v) for v in cl_venues.values()) == 1
    assert max(len(y) for y in cl_years.values()) == 1   # year stable where non-null


def test_generate_biblio_titles_actually_vary(tmp_path):
    gen = _load("generate_fixture")
    out, truth = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(3000, 0.3, out, truth, 42, 1000, shape="biblio")
    import pyarrow.parquet as pq
    from collections import defaultdict

    t = pq.read_table(out)
    truth_t = pq.read_table(truth)
    title_by_id = dict(zip(t.column("record_id").to_pylist(),
                           t.column("title").to_pylist()))
    cl_members: dict = defaultdict(list)
    for r, c in zip(truth_t.column("record_id").to_pylist(),
                    truth_t.column("cluster_id").to_pylist()):
        cl_members[c].append(r)
    # at least some multi-member clusters have >1 distinct title (corruption happened)
    max_titles = 0
    for members in cl_members.values():
        if len(members) > 1:
            max_titles = max(max_titles, len({title_by_id.get(m) for m in members}))
    assert max_titles > 1


def test_generator_projection_check_rejects_small_C():
    gen = _load("generate_fixture")
    # A hypothetical biblio-with-tiny-venue C would project a huge block at 100M.
    ok, projected = gen.check_block_size("biblio", target_rows=100_000_000, ceiling=2000)
    assert ok is True and projected < 2000
    ok2, _ = gen.check_block_size_for_cardinality(cardinality=18_000,
                                                  target_rows=100_000_000, ceiling=2000)
    assert ok2 is False


# ---------------------------------------------------------------------------
# Phase 2: shape-aware runners
# ---------------------------------------------------------------------------
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402


def _env():
    e = dict(os.environ)
    e["PYTHONPATH"] = "packages/python/goldenmatch"
    e["POLARS_SKIP_CPU_CHECK"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
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


def test_probabilistic_numpy_lane_zero_native_eligible(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    e = _env(); e["GOLDENMATCH_FS_NATIVE"] = "0"     # numpy lane
    rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--mode", "probabilistic", "--shape", "person",
        "--allow-pure-python"],  # REQUIRED: native not built locally; without it
                                 # GOLDENMATCH_NATIVE=1 makes native_enabled() RAISE
        env=e).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["status"] == "ok"
    assert r["fs_native_eligible_matchkeys"] == 0     # forced off (FS_NATIVE=0)
    assert r["fs_matchkeys_total"] >= 1


def test_fs_basic_scorers_rewrite_engages_native(tmp_path):
    # --fs-basic-scorers must rewrite autoconfig's specialized name scorers
    # (given_name_aliased_jw / name_freq_weighted_jw) to jaro_winkler, which the
    # native FS kernel implements -- so with native LOADED the matchkey becomes
    # _fs_native_eligible and the Rust kernel engages.
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    e = _env(); e["GOLDENMATCH_FS_NATIVE"] = "1"       # native lane
    rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--mode", "probabilistic", "--shape", "person",
        "--fs-basic-scorers", "--allow-pure-python"],  # allow-pure-python so a
                                 # missing native build doesn't RAISE; the kernel
                                 # still engages when native IS loaded
        env=e).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    assert r["status"] == "ok"
    # The two name scorers get rewritten regardless of whether native is built.
    assert r["fs_basic_scorers_rewritten"], "expected name scorers to be rewritten"
    # Only when native is actually loaded can the kernel become eligible.
    if r.get("native_loaded"):
        assert r["fs_native_eligible_matchkeys"] >= 1, (
            "basic scorers should make >=1 matchkey native-eligible when native "
            "is loaded -- something else is declining it"
        )


def test_run_splink_shape_biblio(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "b.parquet", tmp_path / "b.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="biblio")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    rc = subprocess.run([sys.executable, str(HERE / "run_splink.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.95", "--shape", "biblio"], env=_env()).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    # skipped iff splink not installed; ok when it is. Never a crash.
    assert r["status"] in {"ok", "skipped"}
    if r["status"] == "ok":
        assert r["shape"] == "biblio"
        assert isinstance(r["splink_version"], str) and r["splink_version"]


# ---------------------------------------------------------------------------
# Phase 4: converted-Splink lane
# ---------------------------------------------------------------------------
def test_run_gm_converted_person(tmp_path):
    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")
    out, pred = tmp_path / "r.json", tmp_path / "r.pred.parquet"
    rc = subprocess.run([sys.executable, str(HERE / "run_gm_converted.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out), "--pred-out", str(pred),
        "--threshold", "0.85", "--shape", "person"], env=_env()).returncode
    assert rc == 0
    r = json.loads(out.read_text())
    # ok when splink is installed; skipped (exit 0) when it isn't -- never a crash.
    assert r["status"] in {"ok", "skipped", "refused"}
    assert r["lane"] == "gm_converted_splink" and r["shape"] == "person"
    if r["status"] == "ok":
        assert r["dedupe_wall_seconds"] is not None and pred.exists()


# ---------------------------------------------------------------------------
# Phase 5: lane-model orchestrator
# ---------------------------------------------------------------------------
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


def test_fs_basic_scorers_flag_only_on_fs_lanes():
    orch = _load("orchestrate")
    lanes = orch.LANES
    kw = dict(input="f.parquet", rows=100, out="o.json", pred="p.parquet",
              threshold=0.85, shape="person")
    # BOTH FS lanes carry --fs-basic-scorers ...
    for name in ("gm_probabilistic", "gm_probabilistic_native"):
        assert "--fs-basic-scorers" in orch.build_cmd(lanes[name], **kw), name
    # ... and no other lane does.
    for name in ("gm_hand_built", "gm_zeroconfig", "splink", "gm_converted_splink"):
        assert "--fs-basic-scorers" not in orch.build_cmd(lanes[name], **kw), name


def test_lane_env_is_merged_not_mutating(monkeypatch):
    orch = _load("orchestrate")
    monkeypatch.setenv("SENTINEL", "keep")
    env = orch.lane_env(orch.LANES["gm_probabilistic"])
    assert env["SENTINEL"] == "keep" and env["GOLDENMATCH_FS_NATIVE"] == "0"
    import os as _os
    assert "GOLDENMATCH_FS_NATIVE" not in _os.environ  # never mutated the parent


def test_sweep_person_two_lanes_smoke(tmp_path):
    orch = _load("orchestrate")
    orch.run_sweep(scales=[1500], shapes=["person"],
        lanes=["gm_hand_built", "gm_probabilistic"], workdir=tmp_path,
        dupe_rate=0.3, threshold=0.85, allow_pure_python=True, seed=42)
    agg = json.loads((tmp_path / "bench_results.json").read_text())
    assert set(agg) == {"header", "results"}          # object, not list
    assert agg["header"]["run_timestamp"]              # present
    keys = {(r["shape"], r["lane"], r["rows_requested"]) for r in agg["results"]}
    assert ("person", "gm_hand_built", 1500) in keys
    assert all(r.get("shape") and r.get("lane") for r in agg["results"])


def test_resume_skips_recorded_datapoints_and_reuses_header(tmp_path, monkeypatch):
    # A restored partial aggregate (as after a preempted runner) must be RESUMED:
    # datapoints already recorded are skipped (not recomputed), the prior header is
    # kept, and only the pending lane actually runs an engine subprocess.
    orch = _load("orchestrate")
    (tmp_path / "bench_results.json").write_text(json.dumps({
        "header": {"run_timestamp": 111.0, "run_tag": "prior", "splink_version": "x",
                   "goldenmatch_version": None, "splink": None},
        "results": [{"shape": "person", "lane": "gm_hand_built", "rows_requested": 1500,
                     "status": "ok", "sentinel": "kept"}],
    }))

    ran = []
    def _fake_run_engine(lane, shape, fixture, rows, results_dir, threshold, **kw):
        ran.append(lane.name)
        return ({"shape": shape, "lane": lane.name, "rows_requested": rows,
                 "status": "ok"}, tmp_path / "nope.pred.parquet")
    monkeypatch.setattr(orch, "run_engine", _fake_run_engine)
    monkeypatch.setattr(orch, "generate", lambda *a, **k: tmp_path / "f.parquet")
    monkeypatch.setattr(orch, "evaluate_datapoint", lambda *a, **k: None)

    agg = orch.run_sweep(scales=[1500], shapes=["person"],
        lanes=["gm_hand_built", "gm_probabilistic"], workdir=tmp_path,
        dupe_rate=0.3, threshold=0.85, allow_pure_python=True, seed=42)

    # Only the pending lane ran an engine; the recorded one was skipped.
    assert ran == ["gm_probabilistic"]
    assert agg["header"]["run_timestamp"] == 111.0        # prior header reused
    by_lane = {r["lane"]: r for r in agg["results"]}
    assert by_lane["gm_hand_built"].get("sentinel") == "kept"  # not overwritten
    assert by_lane["gm_probabilistic"]["status"] == "ok"       # newly computed
    assert len(agg["results"]) == 2                            # no duplicate


def test_timeout_ladder_fits_cap():
    orch = _load("orchestrate")
    # 25M + 100M for ONE lane must fit under ~560 min (spec 7.3)
    assert orch._timeout_for(25_000_000) + orch._timeout_for(100_000_000) <= 560 * 60
    assert orch._timeout_for(100_000) < orch._timeout_for(5_000_000)  # monotone


def test_render_markdown_shape_lane_sections():
    orch = _load("orchestrate")
    results = [
        {"shape": "person", "lane": "splink", "rows_requested": 100000,
         "status": "ok", "dedupe_wall_seconds": 10.0, "peak_rss_mb": 500,
         "scored_pairs": 1000, "cluster_count": 50,
         "accuracy": {"pairwise": {"precision": 1, "recall": 1, "f1": 1,
             "confusion": {"tp": 1, "fp": 0, "fn": 0, "tn": 0}},
             "bcubed": {"precision": 1, "recall": 1, "f1": 1}}},
        {"shape": "person", "lane": "gm_probabilistic", "rows_requested": 100000,
         "status": "ok", "dedupe_wall_seconds": 5.0, "peak_rss_mb": 400,
         "scored_pairs": 900, "cluster_count": 50,
         "accuracy": {"pairwise": {"precision": 1, "recall": 0.9, "f1": 0.95,
             "confusion": {"tp": 1, "fp": 0, "fn": 0, "tn": 0}},
             "bcubed": {"precision": 1, "recall": 1, "f1": 1}}},
    ]
    md = orch.render_markdown(results, {"dupe_rate": 0.2})
    assert "## person" in md
    assert "splink" in md and "gm_probabilistic" in md
    # head-to-head is per GM lane vs splink (reference column)
    assert "vs splink" in md.lower() or "GM/Splink" in md


# ---------------------------------------------------------------------------
# Phase 6: merge
# ---------------------------------------------------------------------------
def test_merge_later_timestamp_wins(tmp_path):
    merge = _load("merge_results")
    a = {"header": {"run_timestamp": 100.0, "git_sha": "aaa"},
         "results": [{"shape": "person", "lane": "splink", "rows_requested": 100,
                      "status": "ok", "dedupe_wall_seconds": 9.0}]}
    b = {"header": {"run_timestamp": 200.0, "git_sha": "bbb"},
         "results": [{"shape": "person", "lane": "splink", "rows_requested": 100,
                      "status": "ok", "dedupe_wall_seconds": 7.0}]}
    (tmp_path / "a" / "bench_results.json").parent.mkdir(parents=True)
    (tmp_path / "a" / "bench_results.json").write_text(json.dumps(a))
    (tmp_path / "b" / "bench_results.json").parent.mkdir(parents=True)
    (tmp_path / "b" / "bench_results.json").write_text(json.dumps(b))
    merged = merge.merge_dir(tmp_path)
    got = {(r["shape"], r["lane"], r["rows_requested"]): r for r in merged["results"]}
    assert got[("person", "splink", 100)]["dedupe_wall_seconds"] == 7.0   # later run wins
    assert len(merged["runs"]) == 2                                       # both headers kept


# ---------------------------------------------------------------------------
# Phase 7: eval-join dtype lock
# ---------------------------------------------------------------------------
def test_eval_join_casts_record_id_dtype_mismatch(tmp_path):
    # The autoconfig/probabilistic/converted GM lanes write a STRING record_id
    # pred parquet while the generator truth is INT64 (spec 10). The join must
    # cast both sides to VARCHAR so the same clustering scores F1 == 1.0 even
    # across the dtype mismatch -- locked here so an implicit-cast change can't
    # silently break it.
    import pyarrow as pa
    import pyarrow.parquet as pq

    evaluate = _load("evaluate")

    # Same 6 records, same clustering (two 3-member clusters). pred uses STRING
    # record_id, truth uses INT64 record_id -- describing the identical grouping.
    ids = ["0", "1", "2", "3", "4", "5"]
    pred_clusters = [10, 10, 10, 20, 20, 20]
    truth_clusters = [100, 100, 100, 200, 200, 200]

    pred = tmp_path / "pred.parquet"
    truth = tmp_path / "truth.parquet"
    pq.write_table(pa.table({
        "record_id": pa.array(ids, pa.string()),
        "pred_cluster_id": pa.array(pred_clusters, pa.int64()),
    }), pred)
    pq.write_table(pa.table({
        "record_id": pa.array([int(i) for i in ids], pa.int64()),
        "cluster_id": pa.array(truth_clusters, pa.int64()),
    }), truth)

    m = evaluate.evaluate(pred, truth)
    assert m["n_records_evaluated"] == 6          # the join matched every record
    assert m["pairwise"]["f1"] == 1.0             # identical clustering scores perfectly


# ---------------------------------------------------------------------------
# Phase 7: native-gate refusal lock (test-only)
# ---------------------------------------------------------------------------
def test_hand_built_refuses_without_pure_python_flag(tmp_path):
    # run_goldenmatch.py --mode hand_built WITHOUT --allow-pure-python must REFUSE
    # (non-zero exit, no `ok` result) when the native runtime is absent -- the
    # existing guard (run_goldenmatch.py:131-140), locked under test. If native
    # IS built in this environment the guard passes and the run succeeds, so we
    # detect that (belt-and-suspenders) and skip the assertion.
    import pytest

    gen = _load("generate_fixture")
    fx, tr = tmp_path / "p.parquet", tmp_path / "p.truth.parquet"
    gen.generate(2000, 0.3, fx, tr, 42, 2000, shape="person")

    # Belt-and-suspenders: probe once WITH the flag. If the runner returns ok with
    # native active, the environment has native built -> the refusal path can't be
    # exercised, so skip.
    probe_out = tmp_path / "probe.json"
    probe_pred = tmp_path / "probe.pred.parquet"
    probe_rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(probe_out),
        "--pred-out", str(probe_pred), "--threshold", "0.85",
        "--mode", "hand_built", "--shape", "person", "--allow-pure-python"],
        env=_env()).returncode
    if probe_rc == 0 and probe_out.exists():
        probe = json.loads(probe_out.read_text())
        if probe.get("status") == "ok" and probe.get("native_loaded"):
            pytest.skip("native runtime is built in this environment; "
                        "the pure-Python refusal path cannot be exercised")

    # Native absent (the local / normal-pytest reality): refuse without the flag.
    out = tmp_path / "r.json"
    pred = tmp_path / "r.pred.parquet"
    rc = subprocess.run([sys.executable, str(HERE / "run_goldenmatch.py"),
        "--input", str(fx), "--rows", "2000", "--out", str(out),
        "--pred-out", str(pred), "--threshold", "0.85",
        "--mode", "hand_built", "--shape", "person"],  # NO --allow-pure-python
        env=_env()).returncode
    assert rc != 0                                   # refused -> non-zero exit
    if out.exists():
        r = json.loads(out.read_text())
        assert r.get("status") != "ok"              # never reports a pure-Python number as ok
