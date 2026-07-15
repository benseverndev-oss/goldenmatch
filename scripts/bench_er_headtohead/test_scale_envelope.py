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
    import polars as pl
    df = pl.read_parquet(out).join(pl.read_parquet(truth), on="record_id")
    per_cluster = df.group_by("cluster_id").agg(
        pl.col("venue").n_unique().alias("nv"),
        pl.col("year").drop_nulls().n_unique().alias("ny"))
    # allow year null (dropna) but non-null year must be unique per cluster; venue always unique
    assert per_cluster["nv"].max() == 1
    assert per_cluster["ny"].max() == 1   # year stable per cluster where non-null


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
