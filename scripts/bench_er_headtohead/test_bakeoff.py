import json
import os
import subprocess
import sys
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
RUN_GM = HERE / "run_goldenmatch.py"

def _tiny_parquet(tmp_path):
    df = pl.DataFrame({
        "record_id": ["r0","r1","r2","r3","r4","r5"],
        "first_name": ["ann","ann","bob","bob","cara","dan"],
        "surname":    ["lee","lee","kim","kim","ng","ono"],
        "dob":        ["1990-01-01","1990-01-01","1985-02-02","1985-02-02","1972-03-03","1965-04-04"],
        "postcode":   ["AA1","AA1","BB2","BB2","CC3","DD4"],
    })
    p = tmp_path / "tiny.parquet"; df.write_parquet(p); return p, df

def _run(p, mode, tmp_path):
    out = tmp_path / "res.json"; pred = tmp_path / "pred.parquet"
    proc = subprocess.run(
        [sys.executable, str(RUN_GM), "--input", str(p), "--rows", "6",
         "--mode", mode, "--out", str(out), "--pred-out", str(pred),
         "--allow-pure-python", "--threshold", "0.85"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "POLARS_SKIP_CPU_CHECK": "1", "PYTHONIOENCODING": "utf-8"},
    )
    return proc, out, pred

def test_zeroconfig_emits_string_record_id_preds(tmp_path):
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "zeroconfig", tmp_path)
    assert out.exists(), proc.stderr
    res = json.loads(out.read_text())
    assert res["mode"] == "zeroconfig"
    assert res["status"] in ("ok", "refused")
    if res["status"] == "ok":
        t = pq.read_table(pred)
        assert t.column_names == ["record_id", "pred_cluster_id"]
        rids = set(t.column("record_id").to_pylist())
        assert rids <= set(df["record_id"].to_list())
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
    p, df = _tiny_parquet(tmp_path)
    proc, out, pred = _run(p, "hand_built", tmp_path)
    res = json.loads(out.read_text())
    assert res["mode"] == "hand_built"
    if res["status"] == "ok":
        t = pq.read_table(pred)
        assert pa.types.is_integer(t.schema.field("record_id").type)


def test_bakeoff_table_assembly_and_missing_engine():
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_bakeoff", HERE / "run_bakeoff.py")
    rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)
    stub = {
      "febrl3": {
        "gm_zeroconfig": ({"status":"ok","dedupe_wall_seconds":3.1,"peak_rss_mb":900.0,"scored_pairs":12000},
                          {"pairwise":{"precision":0.99,"recall":0.98,"f1":0.985},"bcubed":{"f1":0.97}}),
        "gm_probabilistic": ({"status":"ok","dedupe_wall_seconds":4.0,"peak_rss_mb":950.0,"scored_pairs":15000},
                          {"pairwise":{"precision":0.99,"recall":0.99,"f1":0.991},"bcubed":{"f1":0.98}}),
        "splink": ({"status":"ok","dedupe_wall_seconds":8.0,"peak_rss_mb":1200.0,"scored_pairs":20000},
                          {"pairwise":{"precision":0.97,"recall":0.96,"f1":0.965},"bcubed":{"f1":0.95}}),
      },
      "dblp_acm": {
        "gm_zeroconfig": ({"status":"ok","dedupe_wall_seconds":2.0,"peak_rss_mb":800.0,"scored_pairs":9000},
                          {"pairwise":{"precision":0.9,"recall":0.86,"f1":0.879},"bcubed":{"f1":0.86}}),
        "gm_probabilistic": ({"status":"ok","dedupe_wall_seconds":2.2,"peak_rss_mb":810.0,"scored_pairs":9100},
                          {"pairwise":{"precision":0.9,"recall":0.86,"f1":0.879},"bcubed":{"f1":0.86}}),
        "splink": ({"status":"skipped","error":"bibliographic out of scope"}, None),
      },
    }
    rows = rb.build_rows(stub)
    skips = [r for r in rows if r["dataset"]=="dblp_acm" and r["engine"]=="splink"]
    assert skips and skips[0]["status"]=="skipped" and skips[0].get("f1") in (None,"")
    # throughput computed when wall+pairs present
    febrl_gm = [r for r in rows if r["dataset"]=="febrl3" and r["engine"]=="gm_zeroconfig"][0]
    assert febrl_gm["throughput_pairs_per_s"] == round(12000/3.1)
    md = rb.render_md(rows)
    assert "febrl3" in md and "peak" in md.lower() and ("throughput" in md.lower() or "pairs/s" in md.lower())
    assert "ratio" in md.lower() or "delta" in md.lower()  # GM-vs-Splink delta block


def test_bakeoff_tolerates_null_rss(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_bakeoff", HERE / "run_bakeoff.py")
    rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)
    stub = {"febrl3": {"gm_zeroconfig": ({"status":"ok","dedupe_wall_seconds":3.0,"peak_rss_mb":None,"scored_pairs":100},
                       {"pairwise":{"precision":1.0,"recall":1.0,"f1":1.0},"bcubed":{"f1":1.0}})}}
    rows = rb.build_rows(stub)
    md = rb.render_md(rows)  # must not crash on null RSS
    assert "febrl3" in md
